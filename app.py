import logging
import os
import re
import json
from uuid import uuid4
import psycopg2 # ⬅️ اضافه شدن کتابخانه اتصال به PostgreSQL
from contextlib import contextmanager

# ⬅️ وارد کردن پکیج‌های لازم برای ساختار Webhook و Flask
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.error import BadRequest
import telegram

# --------------------------------------------------------------------------------------------------
# ۱.
# تنظیمات و متغیرهای کلیدی
# --------------------------------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("BOT_TOKEN")
MANAGER_CHAT_ID = os.environ.get("MANAGER_ID")

# ⚠️ متغیر محیطی کلیدی رندر: DATABASE_URL
DATABASE_URL = os.environ.get("DATABASE_URL")

PROJECT_DATA = {} # دیکشنری سراسری که داده‌ها را نگه می‌دارد.

logging.basicConfig(
    format=
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s - %(funcName)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------------------
# ۱.۵.
# 🚀 توابع مدیریت داده (ذخیره سازی دائمی در PostgreSQL رندر)
# --------------------------------------------------------------------------------------------------

@contextmanager
def db_connect():
    """Context Manager برای مدیریت اتصال به دیتابیس و تراکنش‌ها."""
    if not DATABASE_URL:
        logger.error("❌ متغیر محیطی DATABASE_URL تنظیم نشده است.")
        raise ConnectionError("DATABASE_URL is not set.")
        
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        yield conn
        conn.commit() # اگر خطایی رخ ندهد، تراکنش ثبت می‌شود.
    except psycopg2.Error as e:
        if conn:
            conn.rollback() # در صورت بروز خطا، تراکنش لغو می‌شود.
        logger.error(f"❌ خطای دیتابیس: {e}")
        raise
    finally:
        if conn:
            conn.close()


def initialize_db():
    """تضمین می‌کند که جدول bot_state وجود دارد."""
    if not DATABASE_URL:
        return
    try:
        with db_connect() as conn:
            with conn.cursor() as cursor:
                # ایجاد جدول با فیلد JSONB برای ذخیره دیکشنری پروژه
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bot_state (
                        id INTEGER PRIMARY KEY,
                        data JSONB
                    );
                    """
                )
            logger.info("✅ جدول bot_state بررسی و در صورت نیاز ایجاد شد.")
    except Exception as e:
        logger.error(f"❌ خطای راه‌اندازی دیتابیس: {e}")


def load_project_data():
    """
    بارگذاری داده‌های پروژه از دیتابیس خارجی (PostgreSQL).
    """
    global PROJECT_DATA
    if not DATABASE_URL:
        PROJECT_DATA = {}
        logger.warning("⚠️ DATABASE_URL تنظیم نشده است. با داده خالی شروع می‌شود.")
        return

    logger.info("📡 تلاش برای بارگذاری داده‌ها از دیتابیس PostgreSQL...")
    
    try:
        with db_connect() as conn:
            with conn.cursor() as cursor:
                # ID = 1 را برای داده سراسری ربات می‌خواند
                cursor.execute("SELECT data FROM bot_state WHERE id = 1;")
                result = cursor.fetchone()
                
                if result and result[0]:
                    # داده‌های JSONB را به دیکشنری پایتون تبدیل می‌کند
                    PROJECT_DATA = result[0]
                    logger.info(
                        f"✅ داده‌های پروژه از دیتابیس با موفقیت بارگذاری شدند. ({len(PROJECT_DATA)} پروژه)"
                    )
                else:
                     logger.info(
                        f"⚠️ داده‌ای در جدول bot_state یافت نشد. با داده خالی شروع می‌شود.")
                     PROJECT_DATA = {}
             
    except Exception as e:
        logger.error(f"❌ خطای اتصال/خواندن از دیتابیس: {e}. با داده خالی ادامه می‌یابد.")
        PROJECT_DATA = {}
        


def save_project_data():
    """
    ذخیره‌سازی داده‌های پروژه در دیتابیس خارجی (PostgreSQL) با استفاده از Upsert.
    """
    global PROJECT_DATA
    if not DATABASE_URL:
        logger.warning("⚠️ DATABASE_URL تنظیم نشده است. داده‌ها ذخیره نمی‌شوند.")
        return

    logger.info("💾 تلاش برای ذخیره‌سازی داده‌ها در دیتابیس PostgreSQL...")

    try:
        with db_connect() as conn:
            with conn.cursor() as cursor:
                # Upsert: اگر ID=1 وجود دارد، به‌روزرسانی کند. اگر وجود ندارد، درج کند.
                # داده‌ها به صورت JSON (str) برای ذخیره‌سازی در JSONB ستون ارسال می‌شوند.
                cursor.execute(
                    """
                    INSERT INTO bot_state (id, data) 
                    VALUES (1, %s)
                    ON CONFLICT (id) 
                    DO UPDATE SET data = EXCLUDED.data;
                    """,
                    (json.dumps(PROJECT_DATA),)
                )
        logger.info(f"✅ داده‌های پروژه با موفقیت در دیتابیس ذخیره شدند.")
    except Exception as e:
        logger.error(f"❌ خطای ذخیره‌سازی داده‌ها در دیتابیس: {e}")


# --------------------------------------------------------------------------------------------------
# ۱.۶.
# توابع کمکی (برای دسترسی و اعتبارسنجی)
# --------------------------------------------------------------------------------------------------


def get_project_and_validate(project_id):
    """اعتبارسنجی وجود پروژه."""
    if project_id not in PROJECT_DATA:
        return None, f"❌ پروژه P{project_id} یافت نشد."
    return PROJECT_DATA[project_id], None


def is_manager(chat_id):
    """بررسی اینکه آیا کاربر مدیر است یا خیر."""
    return str(chat_id) == str(MANAGER_CHAT_ID)


# --------------------------------------------------------------------------------------------------
# ۲.
# توابع Handlers (مدیریت جریان کار)
# --------------------------------------------------------------------------------------------------


async def smart_guidance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پاسخ هوشمند به پیام‌های خارج از دستور با نمایش دکمه‌های راهنما بر اساس نقش پویا."""

    if update.callback_query:
        return

    user_chat_id = str(update.effective_chat.id)

    if context.user_data.get('state') and is_manager(user_chat_id):
        # اگر مدیر در حال ثبت پروژه باشد، پیام متنی او باید توسط handle_message پردازش شود، نه اینجا.
        return

    keyboard = []
    guidance_message = "🤔 *نقش نامشخص / کاربر ناشناس.* من این دستور را نمی‌شناسم. لطفاً از دستورات مجاز استفاده کنید."
    is_manager_user = is_manager(user_chat_id)
    is_editor = any(
        data.get('editor_chat_id') == user_chat_id
        for data in PROJECT_DATA.values())
    is_client = any(
        data.get('client_chat_id') == user_chat_id
        for data in PROJECT_DATA.values())

    if is_manager_user:
        guidance_message = "✅ *شما مدیر هستید.* لطفاً از لیست زیر اقدام کنید:"
        keyboard = [[
            InlineKeyboardButton("📊 داشبورد مدیریتی",
                                 callback_data='menu_dashboard')
        ],
                    [
                        InlineKeyboardButton("➕ ثبت پروژه جدید",
                                             callback_data='menu_new_project')
                    ],
                    [
                        InlineKeyboardButton("📄 *لیست کامل پروژه‌ها*",
                                             callback_data='list_all')
                    ]]

    elif is_editor:
        guidance_message = "🛠️ *شما ادیتور تعیین شده هستید.* لطفاً از لیست زیر اقدام کنید یا محتوای ادیت شده را به همراه کد پروژه (`P[ID]`) در کپشن ارسال کنید."
        keyboard = [[
            InlineKeyboardButton("📝 پروژه‌های من",
                                 callback_data='editor_my_projects')
        ],
                    [
                        InlineKeyboardButton("📢 راهنمای ارسال محتوا",
                                             callback_data='editor_send_guide')
                    ]]

    elif is_client:
        guidance_message = "🤝 *سلام کارفرما، خوش آمدید.* پیام‌های شما یک دستور نیستند."
        keyboard = [[
            InlineKeyboardButton("❓ سوالات متداول کارفرما",
                                 callback_data='client_faq')
        ]]

    if update.message:
        if keyboard:
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(guidance_message,
                                            reply_markup=reply_markup,
                                            parse_mode='Markdown')
        else:
            await update.message.reply_text(guidance_message,
                                            parse_mode='Markdown')


async def start(update: Update, context):
    """پاسخ به دستور /start."""
    await update.message.reply_text(
        "👋 خوش آمدید! من ایجنت هوشمند مدیریت پروژه‌های شما هستم. "
        "مدیر گرامی، برای شروع از /dashboard یا /new_project استفاده کنید.")


async def new_project(update: Update, context):
    """شروع فرآیند ثبت پروژه جدید و درخواست نام."""

    if isinstance(update, Update):
        message = update.message
        is_callback = False
    elif isinstance(update, telegram.CallbackQuery):
        message = update.message
        is_callback = True
    else:
        return

    if not is_manager(message.chat.id):
        await message.reply_text(
            "⛔️ دسترسی محدود: این دستور فقط برای مدیر تیم است.")
        return

    if is_callback:
        # اگر از دکمه داشبورد آمده
        await message.edit_text("💡 لطفاً نام کامل پروژه جدید را وارد کنید:")
    else:
        # اگر با دستور /new_project آمده
        await message.reply_text("💡 لطفاً نام کامل پروژه جدید را وارد کنید:")

    context.user_data['state'] = 'awaiting_project_name'


async def handle_message(update: Update, context):
    """مدیریت پیام‌های متنی در طول فرآیند ثبت پروژه، تغییر نقش و بازخورد."""
    user_chat_id = str(update.effective_chat.id)
    state = context.user_data.get('state')

    # --- فرآیند مدیریت (فقط برای مدیر) ---
    if is_manager(user_chat_id) and state:

        if state == 'awaiting_project_name':
            context.user_data['temp_project_name'] = update.message.text
            context.user_data['state'] = 'awaiting_client_chat_id'
            # ⬅️ حل مشکل راهنمایی مدیر: نمایش پیام صحیح بعدی
            await update.message.reply_text(
                "👤 لطفاً *شناسه عددی (Chat ID)* تلگرام کارفرما را وارد کنید:")
            return

        elif state == 'awaiting_client_chat_id':
            try:
                client_chat_id = str(int(update.message.text))
                context.user_data['temp_client_chat_id'] = client_chat_id
                context.user_data['state'] = 'awaiting_editor_chat_id'
                # ⬅️ حل مشکل راهنمایی مدیر: نمایش پیام صحیح بعدی
                await update.message.reply_text(
                    "✂️ لطفاً *شناسه عددی (Chat ID)* تلگرام ادیتور این پروژه را وارد کنید:"
                )
            except ValueError:
                await update.message.reply_text(
                    "⚠️ لطفا یک شناسه عددی معتبر وارد کنید.")
            return

        elif state == 'awaiting_editor_chat_id':
            try:
                editor_chat_id = str(int(update.message.text))
            except ValueError:
                await update.message.reply_text(
                    "⚠️ لطفا یک شناسه عددی معتبر وارد کنید.")
                return

            project_name = context.user_data.pop('temp_project_name')
            client_chat_id = context.user_data.pop('temp_client_chat_id')

            if PROJECT_DATA:
                last_id = max(int(k) for k in PROJECT_DATA.keys())
                project_id = str(last_id + 1)
            else:
                project_id = '1'

            PROJECT_DATA[project_id] = {
                "name": project_name,
                "status": "ReadyForEditSubmission",
                "client_chat_id": client_chat_id,
                "editor_chat_id": editor_chat_id,
                "submissions": []
            }
            context.user_data['state'] = None

            save_project_data() # ⬅️ ذخیره دائمی در دیتابیس

            # ⬅️ حل مشکل عدم اطلاع به ادیتور
            try:
                await context.bot.send_message(
                    chat_id=editor_chat_id,
                    text=
                    f"🔔 *پروژه جدید:* مدیر پروژه '{project_name}' (*P{project_id}*) را برای شما ثبت کرد. لطفاً اولین محتوای ادیت شده را با ذکر کد *P{project_id}* در کپشن ارسال کنید."
                )
            except BadRequest:
                # این پیام به مدیر نشان داده می شود
                await update.message.reply_text(
                    f"❌ اخطار: پیام ثبت پروژه برای ادیتور ارسال نشد. (ربات را بلاک کرده است.)"
                )

            await update.message.reply_text(
                f"✅ پروژه '{project_name}' (*P{project_id}*) ثبت شد و به ادیتور اطلاع داده شد."
            )
            return

        # 2. مرحله تغییر نقش (ادیتور یا کارفرما)
        elif state.startswith('awaiting_new_role_'):
            parts = state.split('_')
            project_id = parts[3][1:]
            role_type = parts[4]

            project_data, error = get_project_and_validate(project_id)
            if error:
                await update.message.reply_text(error)
                context.user_data['state'] = None
                return

            try:
                new_chat_id = str(int(update.message.text))
            except ValueError:
                await update.message.reply_text(
                    "⚠️ شناسه عددی (Chat ID) نامعتبر است. لطفاً فقط عدد وارد کنید."
                )
                return

            if role_type == 'editor':
                old_id = project_data.get('editor_chat_id')
                project_data['editor_chat_id'] = new_chat_id
                role_name = "ادیتور"
            else:
                old_id = project_data.get('client_chat_id')
                project_data['client_chat_id'] = new_chat_id
                role_name = "کارفرما"

            save_project_data() # ⬅️ ذخیره دائمی در دیتابیس
            context.user_data['state'] = None

            await update.message.reply_text(
                f"✅ *پروژه P{project_id} ({project_data['name']}):* نقش *{role_name}* با موفقیت تغییر کرد.\n"
                f"*شناسه قدیمی:* `{old_id}`\n"
                f"*شناسه جدید:* `{new_chat_id}`")
            return

    # --- مدیریت دریافت بازخورد از کارفرما (نیاز به ریپلای) ---

    if update.message.reply_to_message:
        replied_message_id = update.message.reply_to_message.message_id

        target_submission = None
        target_project_id = None

        for pid, pdata in PROJECT_DATA.items():
            if pdata.get('client_chat_id') == user_chat_id:
                for sub in pdata['submissions']:
                    if sub.get('media_message_id') == replied_message_id:
                        target_submission = sub
                        target_project_id = pid
                        break
                if target_submission:
                    break

        if target_submission:
            # ⬅️ حل مشکل عدم هشدار ریپلای دوم (محدودیت یک ریپلای برای بازخورد کارفرما)
            if target_submission.get('status') != 'AwaitingFeedback':
                await update.message.reply_text(
                    "❌ *اخطار:* شما قبلاً روی این محتوا بازخورد ثبت کرده‌اید. "
                    "لطفاً به یاد داشته باشید که *تمام تغییرات مورد نیاز* باید در *یک ریپلای واحد* و در همان بار اول اعلام شوند."
                )
                return

            if update.message.text:

                target_submission['feedback'].append(update.message.text)
                target_submission[
                    'status'] = 'ClientReviewed'  # وضعیت تغییر می‌کند و ریپلای دوم مجاز نیست.
                save_project_data() # ⬅️ ذخیره دائمی در دیتابیس

                try:
                    # حذف دکمه‌های تایید/رد پس از ثبت بازخورد
                    await context.bot.edit_message_reply_markup(
                        chat_id=user_chat_id,
                        message_id=replied_message_id,
                        reply_markup=None)
                except BadRequest as e:
                    logger.warning(
                        f"Error editing message markup (removing buttons) for client feedback: {e}"
                    )

                await update.message.reply_text(
                    "💬 *بازخورد شما ثبت شد!* این محتوا برای تصمیم‌گیری مدیر ارسال شده است. نتیجه به شما اطلاع داده خواهد شد."
                )

                project_name = PROJECT_DATA[target_project_id]['name']
                await send_to_manager_for_review(context, target_project_id,
                                                 target_submission,
                                                 project_name,
                                                 'feedback_submitted')

                return
            else:
                await update.message.reply_text(
                    "⚠️ لطفاً بازخورد خود را به صورت متن بنویسید.")
                return

    await smart_guidance(update, context)


async def handle_media(update: Update, context):
    """[وظیفه Ediitor]: مدیریت ارسال فایل‌های رسانه‌ای، عکس، ویدیو و سند (Document) همراه با کپشن."""

    user_chat_id = str(update.effective_chat.id)
    caption = update.message.caption if update.message.caption else ""

    is_editor = any(
        data.get('editor_chat_id') == user_chat_id
        for data in PROJECT_DATA.values())
    if not is_editor:
        await update.message.reply_text(
            "⛔️ شما به عنوان ادیتور هیچ پروژه‌ای تعیین نشده‌اید.")
        return

    match = re.search(r'P(\d+)', caption, re.IGNORECASE)
    if not match:
        await update.message.reply_text(
            "⚠️ *کد پروژه یافت نشد.* لطفاً در کپشن فایل، حتماً کد پروژه (مثل *P1*) را ذکر کنید."
        )
        return

    project_id = match.group(1)
    project_data, error = get_project_and_validate(project_id)

    if error:
        await update.message.reply_text(error)
        return

    if project_data.get('editor_chat_id') != user_chat_id:
        await update.message.reply_text(
            "⛔️ شما ادیتور تعیین شده برای این پروژه نیستید.")
        return

    # بررسی وضعیت (فقط در حالت آماده یا بازگشت داده شده)
    if project_data['status'] not in [
            'ReadyForEditSubmission', 'ReturnedForRevision'
    ]:
        await update.message.reply_text(
            f"❌ وضعیت پروژه *P{project_id}* اجازه سابمیت جدید را نمی‌دهد. وضعیت فعلی: *{project_data['status']}*"
        )
        return

    # استخراج File ID بر اساس نوع رسانه
    file_id = None
    if update.message.photo:
        # بزرگترین سایز عکس را انتخاب می‌کند
        file_id = update.message.photo[-1].file_id
        media_type = 'photo'
    elif update.message.video:
        file_id = update.message.video.file_id
        media_type = 'video'
    elif update.message.document:
        file_id = update.message.document.file_id
        media_type = 'document'
    else:
        # این حالت نباید رخ دهد چون فیلتر Attachment اعمال شده است.
        await update.message.reply_text(
            "⚠️ لطفاً یک فایل (عکس، ویدیو یا سند) ارسال کنید.")
        return

    # ثبت سابمیشن جدید
    submission_id = str(uuid4())
    new_submission = {
        'submission_id': submission_id,
        'file_id': file_id,
        'media_type': media_type,
        'caption': caption,
        'status': 'AwaitingFeedback',
        'editor_message_id': update.message.message_id,
        'feedback': [],
        'date': str(update.message.date)
    }

    project_data['submissions'].append(new_submission)
    project_data['status'] = 'AwaitingClientReview'  # وضعیت پروژه را تغییر می‌دهد
    save_project_data() # ⬅️ ذخیره دائمی در دیتابیس

    # 1. اطلاع‌رسانی به کارفرما
    client_chat_id = project_data['client_chat_id']
    review_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ تأیید نهایی (محتوا خوب است)",
            callback_data=f'client_approve_{project_id}_{submission_id}'),
        InlineKeyboardButton(
            "↩️ نیاز به بازبینی",
            callback_data=f'client_reject_{project_id}_{submission_id}'),
    ]])

    message_text = f"📢 *محتوای جدید آماده:* ادیتور محتوای پروژه (*P{project_id} - {project_data['name']}*) را ارسال کرد.\n\n" \
                   f"لطفاً محتوا را بررسی کنید و:\n" \
                   f"1. *بازخورد:* برای ثبت بازخورد، مستقیماً به همین پیام ریپلای کرده و *تمام* نکات را در *یک پیام* بنویسید.\n" \
                   f"2. *تصمیم‌گیری:* دکمه‌های زیر را برای اتمام کار یا بازگشت به ادیتور فشار دهید."

    # ارسال فایل و پیام همراه با دکمه‌ها برای کارفرما
    try:
        if media_type == 'photo':
            client_msg = await context.bot.send_photo(
                chat_id=client_chat_id,
                photo=file_id,
                caption=caption,
                reply_markup=review_keyboard,
                parse_mode='Markdown')
        elif media_type == 'video':
            client_msg = await context.bot.send_video(
                chat_id=client_chat_id,
                video=file_id,
                caption=caption,
                reply_markup=review_keyboard,
                parse_mode='Markdown')
        elif media_type == 'document':
            client_msg = await context.bot.send_document(
                chat_id=client_chat_id,
                document=file_id,
                caption=caption,
                reply_markup=review_keyboard,
                parse_mode='Markdown')

        # ذخیره message_id محتوای ارسال شده برای کارفرما برای پیگیری ریپلای‌ها
        new_submission['media_message_id'] = client_msg.message_id
        save_project_data() # ⬅️ ذخیره message_id در دیتابیس

        await update.message.reply_text(
            f"✅ محتوا با موفقیت برای کارفرما (*{project_data['client_chat_id']}*) ارسال شد و منتظر بازخورد ایشان است."
        )

    except BadRequest as e:
        await update.message.reply_text(
            f"❌ اخطار: پیام برای کارفرما ارسال نشد. (احتمالاً ربات را بلاک کرده است.) جزئیات خطا: {e}"
        )

    # 2. اطلاع‌رسانی به مدیر
    await context.bot.send_message(
        chat_id=MANAGER_CHAT_ID,
        text=
        f"🔔 *سابمیت جدید:* ادیتور محتوای پروژه *P{project_id}* را ارسال کرد. منتظر بازخورد کارفرما باشید."
    )


async def send_to_manager_for_review(context: ContextTypes.DEFAULT_TYPE,
                                     project_id: str, submission: dict,
                                     project_name: str, action_type: str):
    """پیام‌های تصمیم‌گیری (تایید یا رد بازخورد) را برای مدیر ارسال می‌کند."""

    feedback_text = "\n".join(
        [f"- {fb}" for fb in submission['feedback']])

    if action_type == 'feedback_submitted':
        manager_message = f"💬 *بازخورد ثبت شد - نیاز به تصمیم‌گیری:* کارفرما برای سابمیت *{submission['submission_id'][:8]}* از پروژه *P{project_id} - {project_name}* بازخورد ثبت کرد.\n\n" \
                          f"--- بازخورد کارفرما ---\n{feedback_text}\n-----------------------\n\n" \
                          f"لطفاً تصمیم بگیرید: آیا ادیتور باید بازبینی کند یا بازخورد کارفرما رد شود؟"

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "↩️ بازگشت به ادیتور برای بازبینی",
                callback_data=
                f'manager_send_back_{project_id}_{submission["submission_id"]}'
            ),
        ], [
            InlineKeyboardButton(
                "❌ رد بازخورد (محتوا تأیید شود)",
                callback_data=
                f'manager_reject_feedback_{project_id}_{submission["submission_id"]}'
            )
        ]])

    elif action_type == 'client_approved':
        manager_message = f"✅ *تأیید نهایی کارفرما:* کارفرما سابمیت *{submission['submission_id'][:8]}* از پروژه *P{project_id} - {project_name}* را تأیید کرد.\n\n" \
                          f"لطفاً وضعیت پروژه را به 'تکمیل شده' تغییر دهید."
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🏁 تکمیل پروژه P{project_id}",
                callback_data=f'manager_complete_project_{project_id}'),
        ]])

    elif action_type == 'client_rejected_no_feedback':
        manager_message = f"❌ *درخواست بازبینی مستقیم:* کارفرما سابمیت *{submission['submission_id'][:8]}* از پروژه *P{project_id} - {project_name}* را مستقیماً (بدون پیام بازخورد) برای بازبینی رد کرد.\n\n" \
                          f"لطفاً دکمه زیر را فشار دهید تا به ادیتور اطلاع داده شود."
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "↩️ بازگشت به ادیتور برای بازبینی",
                callback_data=
                f'manager_send_back_{project_id}_{submission["submission_id"]}'
            ),
        ]])
    else:
        return

    await context.bot.send_message(chat_id=MANAGER_CHAT_ID,
                                   text=manager_message,
                                   reply_markup=keyboard,
                                   parse_mode='Markdown')


async def list_all(update: Update, context):
    """نمایش لیست کامل پروژه‌ها (فقط برای مدیر)."""
    if update.callback_query:
        message = update.callback_query.message
        chat_id = message.chat.id
    else:
        message = update.message
        chat_id = message.chat.id

    if not is_manager(chat_id):
        await message.reply_text(
            "⛔️ دسترسی محدود: این دستور فقط برای مدیر تیم است.")
        return

    if not PROJECT_DATA:
        await message.reply_text("🤷‍♂️ هیچ پروژه‌ای ثبت نشده است.")
        return

    # فیلتر کردن پروژه‌های تکمیل شده برای نمایش کمتر
    active_projects = {
        k: v
        for k, v in PROJECT_DATA.items()
        if v['status'] != 'Completed'
    }

    if not active_projects:
        await message.reply_text("✅ تمام پروژه‌ها تکمیل شده‌اند.")
        return

    project_list = "*لیست پروژه‌های فعال:*\n\n"
    for p_id, data in active_projects.items():
        project_list += (
            f"🔸 *P{p_id}:* {data['name']}\n"
            f"  *وضعیت:* {data['status']}\n")

    keyboard = []
    for p_id, data in active_projects.items():
        keyboard.append([
            InlineKeyboardButton(
                f"🛠️ مدیریت P{p_id}: {data['name']}",
                callback_data=f'manage_project_{p_id}'),
        ])

    keyboard.append([
        InlineKeyboardButton("🗑️ حذف پروژه تکمیل شده",
                             callback_data='manage_completed')
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        try:
            await message.edit_text(project_list,
                                    reply_markup=reply_markup,
                                    parse_mode='Markdown')
        except BadRequest:
            await message.reply_text(project_list,
                                     reply_markup=reply_markup,
                                     parse_mode='Markdown')
    else:
        await message.reply_text(project_list,
                                 reply_markup=reply_markup,
                                 parse_mode='Markdown')


async def show_project_details(update: Update, context, project_id):
    """نمایش جزئیات یک پروژه خاص و دکمه‌های مدیریت."""
    query = update.callback_query
    project_data, error = get_project_and_validate(project_id)
    if error:
        await query.edit_message_text(error)
        return

    details = f"📋 *جزئیات پروژه P{project_id}:* {project_data['name']}\n" \
              f"-----------------------------\n" \
              f"  *وضعیت:* `{project_data['status']}`\n" \
              f"  *شناسه کارفرما:* `{project_data['client_chat_id']}`\n" \
              f"  *شناسه ادیتور:* `{project_data['editor_chat_id']}`\n"

    # افزودن اطلاعات سابمیشن‌های اخیر
    if project_data['submissions']:
        last_sub = project_data['submissions'][-1]
        details += (f"-----------------------------\n"
                    f"  *آخرین سابمیشن (ID: {last_sub['submission_id'][:8]}):*\n"
                    f"    *وضعیت:* `{last_sub['status']}`\n"
                    f"    *بازخوردها:* {len(last_sub['feedback'])} مورد\n")

    keyboard = [[
        InlineKeyboardButton(
            "تغییر شناسه کارفرما",
            callback_data=f'change_role_P{project_id}_client'),
        InlineKeyboardButton(
            "تغییر شناسه ادیتور",
            callback_data=f'change_role_P{project_id}_editor'),
    ], [
        InlineKeyboardButton("🗑️ حذف پروژه",
                             callback_data=f'delete_project_{project_id}'),
        InlineKeyboardButton("↩️ بازگشت به لیست",
                             callback_data='list_all')
    ]]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(details,
                                  reply_markup=reply_markup,
                                  parse_mode='Markdown')


async def delete_completed(update: Update, context):
    """حذف پروژه‌های تکمیل شده (فقط مدیر)."""
    query = update.callback_query
    if not is_manager(query.message.chat.id):
        await query.answer("⛔️ دسترسی محدود.", show_alert=True)
        return

    completed_projects = [
        k for k, v in PROJECT_DATA.items() if v['status'] == 'Completed'
    ]

    if not completed_projects:
        await query.answer("🤷‍♂️ هیچ پروژه تکمیل شده‌ای برای حذف وجود ندارد.",
                           show_alert=True)
        await list_all(update, context)
        return

    for p_id in completed_projects:
        del PROJECT_DATA[p_id]

    save_project_data() # ⬅️ ذخیره دائمی در دیتابیس

    await query.answer(f"✅ {len(completed_projects)} پروژه تکمیل شده حذف شد.",
                       show_alert=True)
    await list_all(update, context)


async def delete_project(update: Update, context, project_id):
    """حذف یک پروژه خاص و تایید آن (فقط مدیر)."""
    query = update.callback_query
    if not is_manager(query.message.chat.id):
        await query.answer("⛔️ دسترسی محدود.", show_alert=True)
        return

    project_data, error = get_project_and_validate(project_id)
    if error:
        await query.edit_message_text(error)
        return

    del PROJECT_DATA[project_id]
    save_project_data() # ⬅️ ذخیره دائمی در دیتابیس

    await query.edit_message_text(
        f"✅ پروژه *P{project_id} - {project_data['name']}* با موفقیت حذف شد.",
        parse_mode='Markdown')
    await list_all(update, context)


async def change_role_prompt(update: Update, context, project_id, role_type):
    """شروع فرآیند تغییر شناسه ادیتور یا کارفرما."""
    query = update.callback_query
    if not is_manager(query.message.chat.id):
        await query.answer("⛔️ دسترسی محدود.", show_alert=True)
        return

    role_name = "کارفرما" if role_type == 'client' else "ادیتور"
    context.user_data[
        'state'] = f'awaiting_new_role_P{project_id}_{role_type}'

    await query.edit_message_text(
        f"💡 لطفاً *شناسه عددی (Chat ID)* جدید برای *{role_name}* پروژه *P{project_id}* را وارد کنید."
    )


async def handle_callback(update: Update, context):
    """مدیریت تمام فشارهای دکمه‌های Inline."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_chat_id = str(query.message.chat.id)

    # 1. مدیریت‌های مدیر
    if is_manager(user_chat_id):
        if data == 'menu_dashboard' or data == 'menu_new_project':
            await new_project(query, context)
            return

        elif data == 'list_all':
            await list_all(query, context)
            return

        elif data.startswith('manage_project_'):
            project_id = data.split('_')[2]
            await show_project_details(query, context, project_id)
            return

        elif data.startswith('delete_project_'):
            project_id = data.split('_')[2]
            await delete_project(query, context, project_id)
            return

        elif data == 'manage_completed':
            await delete_completed(query, context)
            return

        elif data.startswith('change_role_'):
            parts = data.split('_')
            project_id = parts[2][1:]
            role_type = parts[3]
            await change_role_prompt(query, context, project_id, role_type)
            return

        elif data.startswith('manager_send_back_'):
            # بازگشت به ادیتور برای بازبینی پس از بازخورد کارفرما یا رد مستقیم
            _, _, project_id, sub_id = data.split('_')
            project_data, error = get_project_and_validate(project_id)
            if error:
                await query.edit_message_text(error)
                return

            submission = next(
                (sub for sub in project_data['submissions']
                 if sub['submission_id'] == sub_id), None)
            if not submission:
                await query.edit_message_text("❌ سابمیشن یافت نشد.")
                return

            # به‌روزرسانی وضعیت سابمیشن و پروژه
            submission['status'] = 'ReturnedForRevision'
            project_data[
                'status'] = 'ReturnedForRevision'  # پروژه برای بازبینی آماده است

            # ارسال بازخورد و فایل به ادیتور
            feedback_text = "\n".join(
                [f"- {fb}" for fb in submission['feedback']])
            if not feedback_text:
                feedback_text = "بازخورد متنی از کارفرما ثبت نشده است. لطفاً آخرین فایل را بازبینی کنید."

            message_to_editor = f"↩️ *نیاز به بازبینی:* مدیر سابمیت *{sub_id[:8]}* از پروژه *P{project_id} - {project_data['name']}* را برای بازبینی بازگرداند.\n\n" \
                                f"--- بازخورد (در صورت وجود) ---\n{feedback_text}\n---------------------------\n" \
                                f"لطفاً محتوای بازبینی شده را با کد *P{project_id}* دوباره ارسال کنید."

            # ارسال پیام به ادیتور
            try:
                await context.bot.send_message(
                    chat_id=project_data['editor_chat_id'],
                    text=message_to_editor,
                    parse_mode='Markdown')
            except BadRequest:
                await query.edit_message_text(
                    f"❌ اخطار: پیام بازگشت به ادیتور ارسال نشد. (ربات را بلاک کرده است.)"
                )

            # به‌روزرسانی پیام مدیر و حذف دکمه‌ها
            await query.edit_message_text(
                f"✅ سابمیت *{sub_id[:8]}* با موفقیت به ادیتور بازگردانده شد و وضعیت پروژه به 'بازبینی' تغییر کرد."
            )
            save_project_data() # ⬅️ ذخیره دائمی در دیتابیس
            return

        elif data.startswith('manager_reject_feedback_'):
            # رد بازخورد کارفرما و تکمیل پروژه
            _, _, project_id, sub_id = data.split('_')
            project_data, error = get_project_and_validate(project_id)
            if error:
                await query.edit_message_text(error)
                return

            # به‌روزرسانی وضعیت سابمیشن و پروژه
            submission = next(
                (sub for sub in project_data['submissions']
                 if sub['submission_id'] == sub_id), None)
            if submission:
                submission['status'] = 'ManagerApproved'
                submission['feedback'] = [] # بازخورد حذف می‌شود

            project_data['status'] = 'Completed'

            # اطلاع‌رسانی به کارفرما و ادیتور
            try:
                await context.bot.send_message(
                    chat_id=project_data['client_chat_id'],
                    text=
                    f"❌ *رد بازخورد/تأیید نهایی:* مدیر بازخورد شما را رد و سابمیت *{sub_id[:8]}* را تأیید نهایی کرد. پروژه *P{project_id}* تکمیل شد."
                )
                await context.bot.send_message(
                    chat_id=project_data['editor_chat_id'],
                    text=
                    f"✅ *تأیید مدیر:* سابمیت *{sub_id[:8]}* توسط مدیر تأیید شد و پروژه *P{project_id}* تکمیل گردید."
                )
            except BadRequest as e:
                logger.warning(
                    f"Error sending final message to client/editor: {e}")

            await query.edit_message_text(
                f"✅ بازخورد کارفرما برای سابمیت *{sub_id[:8]}* رد و پروژه *P{project_id}* تکمیل شد."
            )
            save_project_data() # ⬅️ ذخیره دائمی در دیتابیس
            return

        elif data.startswith('manager_complete_project_'):
            # تکمیل پروژه پس از تایید نهایی کارفرما
            project_id = data.split('_')[3]
            project_data, error = get_project_and_validate(project_id)
            if error:
                await query.edit_message_text(error)
                return

            project_data['status'] = 'Completed'

            # پیدا کردن آخرین سابمیشن و تکمیل آن
            if project_data['submissions']:
                last_sub = project_data['submissions'][-1]
                last_sub['status'] = 'Completed'

            await query.edit_message_text(
                f"✅ پروژه *P{project_id} - {project_data['name']}* رسماً تکمیل شد."
            )
            save_project_data() # ⬅️ ذخیره دائمی در دیتابیس

            # اطلاع‌رسانی به ادیتور
            try:
                await context.bot.send_message(
                    chat_id=project_data['editor_chat_id'],
                    text=
                    f"🏁 *پایان کار:* پروژه *P{project_id} - {project_data['name']}* رسماً تکمیل شد. خسته نباشید."
                )
            except BadRequest as e:
                logger.warning(f"Error sending completion message to editor: {e}")

            return

    # 2. مدیریت‌های کارفرما (Client)
    elif any(data.get('client_chat_id') == user_chat_id
             for data in PROJECT_DATA.values()):
        if data.startswith('client_approve_'):
            # تأیید نهایی محتوا توسط کارفرما
            _, _, project_id, sub_id = data.split('_')
            project_data, error = get_project_and_validate(project_id)
            if error:
                await query.edit_message_text(error)
                return

            submission = next(
                (sub for sub in project_data['submissions']
                 if sub['submission_id'] == sub_id), None)
            if not submission:
                await query.edit_message_text("❌ سابمیشن یافت نشد.")
                return

            # به‌روزرسانی وضعیت سابمیشن
            submission['status'] = 'ClientApproved'

            # حذف دکمه‌ها از پیام
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_caption(
                caption=
                f"{query.message.caption_html}\n\n✅ *تأیید نهایی شد. در انتظار تکمیل پروژه توسط مدیر.*",
                parse_mode='HTML')

            # ارسال به مدیر برای تکمیل
            await send_to_manager_for_review(context, project_id, submission,
                                             project_data['name'],
                                             'client_approved')
            save_project_data() # ⬅️ ذخیره دائمی در دیتابیس
            return

        elif data.startswith('client_reject_'):
            # رد محتوا توسط کارفرما (بدون بازخورد متنی)
            _, _, project_id, sub_id = data.split('_')
            project_data, error = get_project_and_validate(project_id)
            if error:
                await query.edit_message_text(error)
                return

            submission = next(
                (sub for sub in project_data['submissions']
                 if sub['submission_id'] == sub_id), None)
            if not submission:
                await query.edit_message_text("❌ سابمیشن یافت نشد.")
                return

            # به‌روزرسانی وضعیت سابمیشن
            submission['status'] = 'ClientRejectedNoFeedback'

            # حذف دکمه‌ها و درخواست ریپلای
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_caption(
                caption=
                f"{query.message.caption_html}\n\n❌ *رد شد. منتظر تصمیم‌گیری مدیر.*",
                parse_mode='HTML')

            # ارسال به مدیر برای تصمیم‌گیری
            await send_to_manager_for_review(context, project_id, submission,
                                             project_data['name'],
                                             'client_rejected_no_feedback')
            save_project_data() # ⬅️ ذخیره دائمی در دیتابیس
            return

    # 3. مدیریت‌های ادیتور (Editor)
    elif any(data.get('editor_chat_id') == user_chat_id
             for data in PROJECT_DATA.values()):
        if data == 'editor_my_projects':
            # نمایش لیست پروژه‌های ادیتور
            editor_projects = [
                f"🔸 *P{k}:* {v['name']} (وضعیت: {v['status']})"
                for k, v in PROJECT_DATA.items()
                if v['editor_chat_id'] == user_chat_id
            ]
            message_text = "📝 *پروژه‌های شما:*\n\n" + "\n".join(
                editor_projects)
            await query.edit_message_text(message_text, parse_mode='Markdown')
            return

        elif data == 'editor_send_guide':
            await query.edit_message_text(
                "📢 *راهنمای ارسال محتوا:*\n\n"
                "1. فایل (عکس، ویدیو یا سند) خود را آماده کنید.\n"
                "2. در کپشن آن، *حتماً* کد پروژه را به صورت `P[ID]` (مثلاً `P1`) ذکر کنید.\n"
                "3. فایل را ارسال کنید. ربات به طور خودکار آن را برای کارفرما می‌فرستد."
            )
            return

    # 4. سایر دکمه‌ها (ناشناس یا منوهای نامربوط)
    else:
        await query.edit_message_text(
            "🤷‍♂️ این گزینه برای نقش شما یا وضعیت فعلی معتبر نیست.")


# --------------------------------------------------------------------------------------------------
# ۶.
# اجرای نهایی ربات و ثبت Handlers (ساختار Webhook)
# --------------------------------------------------------------------------------------------------

def build_application():
    """Application را برای Webhook می‌سازد و Handlers را ثبت می‌کند."""
    
    initialize_db() # ⬅️ اطمینان از وجود جدول دیتابیس
    load_project_data() # ⬅️ اکنون از دیتابیس خارجی بارگذاری می‌کند
    
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Command Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("new_project", new_project))
    application.add_handler(CommandHandler("dashboard", list_all))
    application.add_handler(CommandHandler("list_all", list_all))

    # Message Handlers
    # filters.ATTACHMENT شامل عکس، ویدیو، سند و فایل‌های دیگر است.
    application.add_handler(
        MessageHandler(filters.ATTACHMENT, handle_media))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Callback Handler
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    return application

# ⬅️ هسته اصلی Flask و Webhook
# Gunicorn این نمونه 'app' را اجرا می کند.
app = Flask(__name__)
# Application ربات در خارج از تابع build_application ساخته می‌شود.
TG_APPLICATION = build_application()

# ⬅️ آدرس پینگ/Keep Alive (مسیر ریشه /)
@app.route('/', methods=['GET'])
def home():
    """پاسخ به پینگ UptimeRobot."""
    return "Hello. I am alive!"

# ⬅️ آدرس Webhook اصلی (با استفاده از توکن به عنوان مسیر)
@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
async def handle_webhook():
    """دریافت به‌روزرسانی (Update) از تلگرام و ارسال به Application."""
    
    # اطمینان از مقداردهی اولیه Application (ضروری در محیط Webhook/Gunicorn)
    await TG_APPLICATION.initialize()
    
    if request.method == "POST":
        try:
            # دریافت داده خام JSON از تلگرام
            update_data = request.get_json(force=True)
            if update_data:
                # تبدیل به آبجکت Update تلگرام و پردازش
                update = Update.de_json(update_data, TG_APPLICATION.bot)
                await TG_APPLICATION.process_update(update)
                return jsonify({"status": "ok"}), 200
            else:
                return jsonify({"status": "no update data"}), 200
        except Exception as e:
            logger.error(f"❌ خطای پردازش Webhook: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500
    
    return jsonify({"status": "method not allowed"}), 405