import logging
import os
import re
import json
from uuid import uuid4

# ⬅️ تغییرات لازم برای Webhook و Render
# حذف بخش Flask/Thread قبلی و استفاده از ساختار Webhook
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.error import BadRequest
import telegram

# --------------------------------------------------------------------------------------------------
# ۱. تنظیمات و متغیرهای کلیدی
# --------------------------------------------------------------------------------------------------
# توکن ربات تلگرام
TELEGRAM_BOT_TOKEN = os.environ.get("BOT_TOKEN")
# شناسه عددی چت مدیر
MANAGER_CHAT_ID = os.environ.get("MANAGER_ID")
# نام فایل ذخیره سازی
DATA_FILE = 'project_data.json'

# پایگاه داده پروژه
PROJECT_DATA = {}

# تنظیمات Logging
logging.basicConfig(
    format=
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s - %(funcName)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------------------
# ۱.۵. توابع مدیریت داده (ذخیره سازی دائمی)
# --------------------------------------------------------------------------------------------------


def load_project_data():
    """بارگذاری داده‌های پروژه از فایل JSON."""
    global PROJECT_DATA
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                PROJECT_DATA = json.load(f)
            logger.info(
                f"✅ داده‌های پروژه از '{DATA_FILE}' با موفقیت بارگذاری شدند. ({len(PROJECT_DATA)} پروژه)"
            )
        except json.JSONDecodeError as e:
            logger.error(
                f"❌ خطای دیکد JSON هنگام بارگذاری داده‌ها: {e}. با داده خالی ادامه می‌یابد."
            )
            PROJECT_DATA = {}
    else:
        logger.info(
            f"⚠️ فایل '{DATA_FILE}' یافت نشد. با داده خالی شروع می‌شود.")
        PROJECT_DATA = {}


def save_project_data():
    """ذخیره‌سازی داده‌های پروژه در فایل JSON."""
    global PROJECT_DATA
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(PROJECT_DATA, f, indent=4, ensure_ascii=False)
        logger.info(f"💾 داده‌های پروژه با موفقیت در '{DATA_FILE}' ذخیره شدند.")
    except Exception as e:
        logger.error(f"❌ خطای ذخیره‌سازی داده‌ها: {e}")


# --------------------------------------------------------------------------------------------------
# ۱.۶. توابع کمکی (برای دسترسی و اعتبارسنجی)
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
# ۲. توابع Handlers (مدیریت جریان کار)
# --------------------------------------------------------------------------------------------------


async def smart_guidance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پاسخ هوشمند به پیام‌های خارج از دستور با نمایش دکمه‌های راهنما بر اساس نقش پویا."""

    if update.callback_query:
        return

    user_chat_id = str(update.effective_chat.id)

    if context.user_data.get('state') and is_manager(user_chat_id):
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
        await message.edit_text("💡 لطفاً نام کامل پروژه جدید را وارد کنید:")
    else:
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
            await update.message.reply_text(
                "👤 لطفاً *شناسه عددی (Chat ID)* تلگرام کارفرما را وارد کنید:")
            return

        elif state == 'awaiting_client_chat_id':
            try:
                client_chat_id = str(int(update.message.text))
                context.user_data['temp_client_chat_id'] = client_chat_id
                context.user_data['state'] = 'awaiting_editor_chat_id'
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

            save_project_data()

            try:
                await context.bot.send_message(
                    chat_id=editor_chat_id,
                    text=
                    f"🔔 *پروژه جدید:* مدیر پروژه '{project_name}' (*P{project_id}*) را برای شما ثبت کرد. لطفاً اولین محتوای ادیت شده را با ذکر کد *P{project_id}* در کپشن ارسال کنید."
                )
            except BadRequest:
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

            save_project_data()
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
            # ⬅️ محدودیت ریپلای: اگر وضعیت 'ClientReviewed' باشد، یعنی قبلاً ریپلای کرده است.
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
                save_project_data()

                try:
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
            "⚠️ *کد پروژه یافت نشد.* لطفاً در کپشن فایل، حتماً کد پروژه را به فرمت *P[ID]* (مثال: `P12`) ذکر کنید."
        )
        return

    project_id = match.group(1)

    if project_id not in PROJECT_DATA:
        await update.message.reply_text(f"❌ پروژه *P{project_id}* یافت نشد.")
        return

    project_data = PROJECT_DATA[project_id]

    if project_data.get('editor_chat_id') != user_chat_id:
        await update.message.reply_text(
            "⛔️ شما ادیتور تعیین شده برای این پروژه نیستید.")
        return

    client_chat_id = project_data['client_chat_id']
    project_name = project_data['name']

    # ⬅️ ۱. استخراج file_id و media_type
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        media_type = 'photo'
    elif update.message.video:
        file_id = update.message.video.file_id
        media_type = 'video'
    elif update.message.document:  # پشتیبانی از فایل سند
        file_id = update.message.document.file_id
        media_type = 'document'
    else:
        file_id = None
        media_type = 'unknown'

    # 2. کپی کردن محتوا برای کارفرما
    try:
        submission_id = str(uuid4())

        client_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "بازخوردی ندارم، تایید نهایی ✅",
                callback_data=f'client_approve_{project_id}_{submission_id}')
        ]])

        sent_message = await update.message.copy(chat_id=client_chat_id,
                                                 caption=caption,
                                                 reply_markup=client_keyboard)

        # 3. ذخیره اطلاعات
        new_submission = {
            "submission_id": submission_id,
            "media_message_id": sent_message.message_id,
            "file_id": file_id,
            "media_type": media_type,
            "caption": caption,
            "feedback": [],
            "status": "AwaitingFeedback"
        }
        project_data['submissions'].append(new_submission)

        save_project_data()

        await context.bot.send_message(
            chat_id=client_chat_id,
            text=
            f"✨ *محتوای جدید برای پروژه '{project_name}'* (P{project_id}) رسید.\n"
            f"1️⃣ *برای تایید:* دکمه زیر محتوا را بزنید.\n"
            f"2️⃣ *برای درخواست تغییر:* *مستقیماً روی محتوا ریپلای کنید* و نظر خود را بنویسید (فقط یک بار مجاز است)."
        )

        await update.message.reply_text(
            f"✅ محتوای ادیت شده با موفقیت برای کارفرما ارسال شد. (Submission ID: {submission_id})"
        )

    except BadRequest as e:
        await update.message.reply_text(
            f"❌ اخطار: پیام به کارفرما ارسال نشد. (آیدی اشتباه یا ربات بلاک شده است.)"
        )
        project_data['status'] = 'Error_Client_Unreachable_Edit'


# --------------------------------------------------------------------------------------------------
# ۳. توابع گزارش‌گیری و داشبورد
# --------------------------------------------------------------------------------------------------


async def get_status_text(project_id, data, user_chat_id):
    """تولید پیام وضعیت پروژه."""

    is_manager_user = is_manager(user_chat_id)

    submission_counts = {
        'AwaitingFeedback': 0,
        'ClientReviewed': 0,
        'ClientApproved': 0,
        'RejectedByClient_AwaitingEditor': 0,
        'ManagerApproved': 0
    }

    for sub in data.get('submissions', []):
        if sub['status'] in submission_counts:
            submission_counts[sub['status']] += 1

    total_submissions = len(data.get('submissions', []))
    status_msg = f"پروژه در حال اجراست."

    if is_manager_user:
        editor_info = f"✂️ ادیتور: *{data.get('editor_chat_id')}*"
        client_info = f"👤 کارفرما: *{data.get('client_chat_id')}*"
    else:
        editor_info = "✂️ ادیتور: 🔒 مخفی"
        client_info = "👤 کارفرما: 🔒 مخفی"

    return (
        f"📋 *جزئیات پروژه P{project_id}: {data['name']}*\n"
        f"وضعیت کلی: *{status_msg}*\n"
        f"----------------------------------------\n"
        f"{editor_info}\n"
        f"{client_info}\n"
        f"*آمار محتواهای ارسالی ({total_submissions} محتوا):*\n"
        f" - 🟡 در انتظار بازخورد کارفرما: *{submission_counts['AwaitingFeedback']}*\n"
        f" - 📝 در انتظار تصمیم مدیر (بازخورد کارفرما): *{submission_counts['ClientReviewed']}*\n"
        f" - 🟠 در انتظار تایید نهایی مدیر (تایید کارفرما): *{submission_counts['ClientApproved']}*\n"
        f" - ↩️ برگشت خورده به ادیتور: *{submission_counts['RejectedByClient_AwaitingEditor']}*\n"
        f" - ✅ نهایی شده: *{submission_counts['ManagerApproved']}*\n")


async def check_project_status(update: Update, context):
    """[وظیفه مدیر/ادیتور]: بررسی وضعیت یک پروژه با ID."""
    message = update.message if update.message else update.callback_query.message
    user_chat_id = str(message.chat.id)
    if not update.message: return

    is_authorized = is_manager(user_chat_id) or any(
        data.get('editor_chat_id') == user_chat_id
        for data in PROJECT_DATA.values())
    if not is_authorized:
        await message.reply_text(
            "⛔️ دسترسی محدود: فقط مدیر یا ادیتور مربوط به پروژه می‌تواند وضعیت را چک کند."
        )
        return

    if not context.args or not context.args[0].startswith('P'):
        await message.reply_text("⚠️ فرمت دستور نادرست است. مثال: `/check P1`")
        return

    project_id = context.args[0][1:]

    project_data, error = get_project_and_validate(project_id)
    if error:
        await message.reply_text(error)
        return

    if not is_manager(user_chat_id) and project_data.get(
            'editor_chat_id') != user_chat_id:
        await message.reply_text("⛔️ شما به این پروژه دسترسی ندارید.")
        return

    status_text = await get_status_text(project_id, project_data, user_chat_id)
    await message.reply_text(status_text, parse_mode='Markdown')


async def dashboard(update: Update, context):
    """نمایش داشبورد مدیریتی و وضعیت پروژه‌ها."""
    message = update.message if update.message else update.callback_query.message
    if not is_manager(message.chat.id):
        await message.reply_text("⛔️ دسترسی محدود.")
        return

    total_projects = len(PROJECT_DATA)

    waiting_manager_approval_count = 0
    for data in PROJECT_DATA.values():
        for sub in data.get('submissions', []):
            if sub['status'] in ['ClientApproved', 'ClientReviewed']:
                waiting_manager_approval_count += 1

    dashboard_text = (
        "📊 *داشبورد مدیریتی تیم محتوا*\n\n"
        f"*تعداد کل پروژه‌ها:* {total_projects}\n"
        f"*↩️ محتوای منتظر تأیید نهایی شما:* {waiting_manager_approval_count}\n"
    )

    if waiting_manager_approval_count > 0:
        dashboard_text += "\n*فوری (نیاز به اقدام مدیر):*\n"
        for pid, data in PROJECT_DATA.items():
            for sub in data.get('submissions', []):
                if sub['status'] == 'ClientApproved':
                    dashboard_text += f" - P{pid} ({data['name']}): تایید کارفرما، منتظر تایید نهایی شما.\n"
                elif sub['status'] == 'ClientReviewed':
                    dashboard_text += f" - P{pid} ({data['name']}): بازخورد کارفرما، منتظر تصمیم شما.\n"

    keyboard = [[
        InlineKeyboardButton("📄 *نمایش لیست کامل پروژه‌ها*",
                             callback_data='list_all')
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await message.edit_text(dashboard_text,
                                reply_markup=reply_markup,
                                parse_mode='Markdown')
    else:
        await message.reply_text(dashboard_text,
                                 reply_markup=reply_markup,
                                 parse_mode='Markdown')


# --------------------------------------------------------------------------------------------------
# ۴. توابع ارسال مدیا و نوتیفیکیشن
# --------------------------------------------------------------------------------------------------


async def send_to_manager_for_review(context, project_id, submission,
                                     project_name, action_type):
    """تابع کمکی برای ارسال محتوا و گزارش بازخورد به مدیر جهت تصمیم‌گیری."""

    submission_id = submission['submission_id']
    raw_feedback_report = submission.get('feedback', [])

    if action_type == 'approve_without_feedback':
        raw_feedback_text = "کارفرما هیچ بازخورد متنی ثبت نکرد و مستقیماً محتوا را تایید نهایی کرد."
        manager_prompt = "🔥 *تایید نهایی مدیر لازم است (تایید کارفرما)*"
    elif action_type == 'feedback_submitted':
        raw_feedback_text = "\n".join(
            [f"  - {fb}" for fb in raw_feedback_report])
        manager_prompt = "🔥 *تصمیم مدیریتی لازم است (بازخورد کارفرما)*"
        if not raw_feedback_report:
            raw_feedback_text = "کارفرما ریپلای کرد اما متن بازخورد خالی بود. نیاز به تصمیم‌گیری مدیر."

    # 2. کپی محتوای اصلی برای مدیر (از file_id ذخیره‌شده)
    if submission['file_id']:
        manager_caption = f"{manager_prompt}\n\n" \
                          f"*پروژه:* P{project_id} - {project_name}\n" \
                          f"*ID محتوا:* {submission_id}\n" \
                          f"*بازخوردهای کارفرما:*\n" \
                          f"```\n{raw_feedback_text}```\n" \
                          f"----------------------------------------\n" \
                          f"*تصمیم نهایی با شماست:*"

        try:
            if submission['media_type'] == 'photo':
                await context.bot.send_photo(MANAGER_CHAT_ID,
                                             submission['file_id'],
                                             caption=manager_caption,
                                             parse_mode='Markdown')
            elif submission['media_type'] == 'video':
                await context.bot.send_video(MANAGER_CHAT_ID,
                                             submission['file_id'],
                                             caption=manager_caption,
                                             parse_mode='Markdown')
            elif submission['media_type'] == 'document':  # ارسال فایل عمومی
                await context.bot.send_document(MANAGER_CHAT_ID,
                                                submission['file_id'],
                                                caption=manager_caption,
                                                parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error copying media to manager: {e}")
            await context.bot.send_message(
                MANAGER_CHAT_ID,
                f"❌ *خطای ارسال محتوا مدیا* (P{project_id} - {submission_id}): فایل در تلگرام یافت نشد.\n\n"
                f"{manager_caption}",
                parse_mode='Markdown')

        # 3. ارسال دکمه‌های تصمیم‌گیری به مدیر

        if action_type == 'feedback_submitted':
            manager_keyboard = InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "تایید بازخورد (بازگشت به ادیتور) 🔄",
                        callback_data=
                        f'manager_review_accept_{project_id}_{submission_id}')
                ],
                 [
                     InlineKeyboardButton(
                         "رد بازخورد (تایید نهایی) ✅",
                         callback_data=
                         f'manager_review_reject_{project_id}_{submission_id}')
                 ]])
            await context.bot.send_message(
                MANAGER_CHAT_ID,
                f"👆 محتوای *P{project_id} ({submission_id})* نیاز به تصمیم‌گیری دارد.",
                reply_markup=manager_keyboard,
                parse_mode='Markdown')

        elif action_type == 'approve_without_feedback':
            manager_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "تایید نهایی مدیر ✅",
                    callback_data=
                    f'manager_final_approve_{project_id}_{submission_id}')
            ]])
            await context.bot.send_message(
                MANAGER_CHAT_ID,
                f"👆 محتوای *P{project_id} ({submission_id})* توسط کارفرما تایید شده. لطفا تایید نهایی کنید.",
                reply_markup=manager_keyboard,
                parse_mode='Markdown')


async def send_media_to_editor(context, editor_chat_id, project_id, submission,
                               message_prefix):
    """تابع کمکی برای کپی کردن محتوای اصلی به ادیتور همراه با پیام."""

    submission_id = submission['submission_id']

    if submission['file_id']:
        editor_caption = f"{message_prefix}\n\n*پروژه:* P{project_id}\n*ID محتوا:* {submission_id}\n"

        try:
            if submission['media_type'] == 'photo':
                await context.bot.send_photo(editor_chat_id,
                                             submission['file_id'],
                                             caption=editor_caption,
                                             parse_mode='Markdown')
            elif submission['media_type'] == 'video':
                await context.bot.send_video(editor_chat_id,
                                             submission['file_id'],
                                             caption=editor_caption,
                                             parse_mode='Markdown')
            elif submission['media_type'] == 'document':  # ارسال فایل عمومی
                await context.bot.send_document(editor_chat_id,
                                                submission['file_id'],
                                                caption=editor_caption,
                                                parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error copying media to editor: {e}")
            await context.bot.send_message(
                editor_chat_id,
                f"❌ *خطای ارسال محتوا* (P{project_id}): فایل محتوا در تلگرام یافت نشد."
            )


# --------------------------------------------------------------------------------------------------
# ۵. توابع Callback Handler (مدیریت کلیک دکمه‌ها)
# --------------------------------------------------------------------------------------------------


async def handle_callback(update: Update, context):
    """مدیریت کلیک روی دکمه های شیشه ای (Inline Buttons)."""
    query = update.callback_query
    await query.answer()

    data = query.data.split('_')
    action = data[0]

    # --- منطق‌های عمومی (منو، داشبورد، وضعیت) ---
    if action in ['menu', 'editor', 'list', 'status']:
        if action == 'menu':
            if data[1] == 'dashboard': return await dashboard(query, context)
            elif data[1] == 'new' and data[2] == 'project':
                return await new_project(query, context)
        elif action == 'editor':
            editor_id = str(query.message.chat.id)
            if data[1] == 'my':
                editor_projects = [(pid, data['name'])
                                   for pid, data in PROJECT_DATA.items()
                                   if data.get('editor_chat_id') == editor_id]
                if not editor_projects:
                    return await query.edit_message_text(
                        "شما پروژه فعالی ندارید.")
                project_list_text = "📋 *پروژه‌های شما:*\n\n"
                keyboard = [[
                    InlineKeyboardButton(f"⚙️ P{pid}: {name}",
                                         callback_data=f'status_{pid}')
                ] for pid, name in editor_projects]
                return await query.edit_message_text(
                    project_list_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown')

            elif data[1] == 'send' and data[2] == 'guide':
                guide_text = (
                    "📢 *راهنمای ارسال محتوای ادیت شده*\n\n"
                    "1️⃣ *فایل را آماده کنید:* فایل نهایی (عکس، ویدیو یا سند) را برای ارسال انتخاب کنید.\n"
                    "2️⃣ *کپشن را تنظیم کنید:* در قسمت کپشن فایل، *حتماً* کد پروژه را به فرمت *P[ID]* وارد کنید.\n"
                    "   مثال: `P12`\n\n"
                    "   *💡 اگر پروژه شما P5 است، فقط کافی است در کپشن بنویسید P5 یا محتوای کپشن خود را با P5 شروع کنید.*\n\n"
                    "3️⃣ *ارسال کنید:* ربات به صورت خودکار فایل را به کارفرمای مربوطه ارسال می‌کند.\n\n"
                    "4️⃣ *بازگشت:*")
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("بازگشت به منو",
                                         callback_data='menu_dashboard')
                ]])
                return await query.edit_message_text(guide_text,
                                                     reply_markup=keyboard,
                                                     parse_mode='Markdown')

        elif action == 'list' and data[1] == 'all':
            if not is_manager(query.message.chat.id): return
            manager_projects = [(pid, data['name'])
                                for pid, data in PROJECT_DATA.items()]
            project_list_text = "📋 *لیست کامل پروژه‌ها (مدیر):*\n\n"

            keyboard = []
            for pid, name in manager_projects:
                status_button = InlineKeyboardButton(
                    f"⚙️ P{pid}: {name}", callback_data=f'status_{pid}')
                manage_buttons = [
                    InlineKeyboardButton(
                        "🔄 ادیتور",
                        callback_data=f'manage_start_P{pid}_editor'),
                    InlineKeyboardButton(
                        "🔄 کارفرما",
                        callback_data=f'manage_start_P{pid}_client'),
                    InlineKeyboardButton(
                        "🗑️ حذف",
                        callback_data=f'manage_confirm_delete_P{pid}')
                ]
                keyboard.append([status_button])
                keyboard.append(manage_buttons)

            return await query.edit_message_text(
                project_list_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown')
        elif action == 'status':
            project_id = data[1]
            if project_id in PROJECT_DATA:
                project_data = PROJECT_DATA[project_id]
                status_text = await get_status_text(project_id, project_data,
                                                    str(query.message.chat.id))

                if is_manager(query.message.chat.id):
                    back_keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("بازگشت به لیست پروژه‌ها",
                                             callback_data='list_all')
                    ]])
                    return await query.edit_message_text(
                        status_text,
                        reply_markup=back_keyboard,
                        parse_mode='Markdown')

                return await query.edit_message_text(status_text,
                                                     parse_mode='Markdown')
            else:
                return await query.edit_message_text("❌ پروژه یافت نشد.")

    # --- منطق تغییر نقش و حذف (فقط برای مدیر) ---
    elif action == 'manage' and is_manager(query.message.chat.id):
        if data[1] == 'start':
            project_code = data[2]
            role_type = data[3]
            project_id = project_code[1:]

            role_name = "ادیتور" if role_type == 'editor' else "کارفرما"

            context.user_data[
                'state'] = f'awaiting_new_role_{project_code}_{role_type}'

            await query.edit_message_text(
                f"🔑 *تغییر {role_name} پروژه P{project_id}:*\n"
                f"لطفاً *شناسه عددی (Chat ID)* جدید {role_name} را در پیام بعدی ارسال کنید."
            )
            return
        elif data[1] == 'confirm' and data[2] == 'delete':
            project_code = data[3]
            project_id = project_code[1:]

            if project_id in PROJECT_DATA:
                project_name = PROJECT_DATA[project_id]['name']

                confirm_keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        f"⚠️ *تایید حذف نهایی P{project_id}*",
                        callback_data=f'manage_execute_delete_P{project_id}')
                ], [
                    InlineKeyboardButton("❌ انصراف", callback_data='list_all')
                ]])
                await query.edit_message_text(
                    f"⚠️ *اخطار حذف!* آیا مطمئن هستید که می‌خواهید پروژه *'{project_name}' (P{project_id})* را برای همیشه حذف کنید؟",
                    reply_markup=confirm_keyboard,
                    parse_mode='Markdown')
            else:
                await query.edit_message_text(
                    f"❌ پروژه P{project_id} یافت نشد.")
            return
        elif data[1] == 'execute' and data[2] == 'delete':
            project_code = data[3]
            project_id = project_code[1:]

            if project_id in PROJECT_DATA:
                project_name = PROJECT_DATA[project_id]['name']
                del PROJECT_DATA[project_id]

                save_project_data()

                await query.edit_message_text(
                    f"🗑️ پروژه *'{project_name}' (P{project_id})* با موفقیت *حذف نهایی* شد."
                )
                await dashboard(query, context)
            else:
                await query.edit_message_text(
                    f"❌ پروژه P{project_id} یافت نشد.")
            return

    # --- منطق کارفرما (بازخورد و تایید) ---

    if action == 'client' and data[1] == 'faq':
        return await query.edit_message_text(
            "❓ *سوالات متداول کارفرما:*\n"
            "1️⃣ *برای تایید سریع:* دکمه *'بازخوردی ندارم، تایید نهایی ✅'* را بزنید.\n"
            "2️⃣ *برای درخواست تغییر:* *مستقیماً روی محتوای ارسالی ریپلای کنید* و نظر خود را بنویسید (فقط یک بار مجاز است)."
        )

    if action == 'client' and data[1] == 'approve':
        project_id = data[2]
        submission_id = data[3]

        project_data = PROJECT_DATA.get(project_id)
        if not project_data or str(
                query.message.chat.id) != project_data['client_chat_id']:
            return

        target_submission = next((sub for sub in project_data['submissions']
                                  if sub['submission_id'] == submission_id),
                                 None)

        if not target_submission or target_submission[
                'status'] != 'AwaitingFeedback':
            await query.edit_message_text(
                "⚠️ این محتوا قبلاً بررسی شده یا وضعیت نامعتبری دارد.")
            return

        target_submission['status'] = 'ClientApproved'
        save_project_data()

        await query.edit_message_text(
            f"✅ *تایید شد!* این محتوا برای تایید نهایی مدیر ارسال شد.")

        await send_to_manager_for_review(context, project_id,
                                         target_submission,
                                         project_data['name'],
                                         'approve_without_feedback')
        return

    # --- منطق‌های تصمیم‌گیری مدیر ---

    # 1. تایید بازخورد کارفرما (بازگشت به ادیتور) 🔄
    elif action == 'manager' and data[1] == 'review' and data[2] == 'accept':
        project_id = data[3]
        submission_id = data[4]

        if not is_manager(
                query.message.chat.id) or project_id not in PROJECT_DATA:
            return

        project_data = PROJECT_DATA[project_id]
        target_submission = next((sub for sub in project_data['submissions']
                                  if sub['submission_id'] == submission_id
                                  and sub['status'] == 'ClientReviewed'), None)

        if not target_submission:
            return await query.edit_message_text("⚠️ وضعیت محتوا نامعتبر است.")

        target_submission['status'] = 'RejectedByClient_AwaitingEditor'
        save_project_data()
        await query.edit_message_text(
            f"🔄 *بازگشت به ادیتور:* بازخورد کارفرما برای محتوای *P{project_id}* توسط مدیر تایید شد."
        )

        feedback_list = "\n".join(
            [f"  - {fb}" for fb in target_submission['feedback']])
        editor_message_prefix = f"❌ *نیاز به بازبینی:* محتوای شما نیاز به اصلاح دارد.\n\n*بازخوردهای کارفرما:*\n{feedback_list}\n\n*لطفاً پس از اصلاح، فایل جدید را مجدداً با کد پروژه ارسال کنید.*"
        await send_media_to_editor(context, project_data['editor_chat_id'],
                                   project_id, target_submission,
                                   editor_message_prefix)
        await context.bot.send_message(
            project_data['client_chat_id'],
            f"🔄 *اطلاعیه:* بازخورد شما برای محتوای (ID: {submission_id}) توسط مدیر تایید شد و برای اصلاح به ادیتور بازگشت.",
            parse_mode='Markdown')
        target_submission['feedback'] = []

    # 2. رد بازخورد کارفرما (تایید نهایی محتوا) ✅
    elif action == 'manager' and data[1] == 'review' and data[2] == 'reject':
        project_id = data[3]
        submission_id = data[4]

        if not is_manager(
                query.message.chat.id) or project_id not in PROJECT_DATA:
            return

        project_data = PROJECT_DATA[project_id]
        target_submission = next((sub for sub in project_data['submissions']
                                  if sub['submission_id'] == submission_id
                                  and sub['status'] == 'ClientReviewed'), None)

        if not target_submission:
            return await query.edit_message_text("⚠️ وضعیت محتوا نامعتبر است.")

        target_submission['status'] = 'ManagerApproved'
        save_project_data()
        await query.edit_message_text(
            f"✅ محتوای *P{project_id}* توسط مدیر نهایی شد (بازخورد کارفرما رد شد)."
        )

        editor_message_prefix = f"✅ *تایید نهایی:* محتوای شما نهایی و تایید شد (علی‌رغم بازخورد کارفرما، مدیر آن را نهایی کرد)."
        await send_media_to_editor(context, project_data['editor_chat_id'],
                                   project_id, target_submission,
                                   editor_message_prefix)

        notification_text = f"✅ *تصمیم نهایی مدیر:* محتوای شما (ID: {submission_id}) از پروژه *P{project_id} - {project_data['name']}* نهایی و تایید شد."
        try:
            await context.bot.send_message(project_data['client_chat_id'],
                                           f"🔔 اطلاعیه: {notification_text}",
                                           parse_mode='Markdown')
        except:
            pass

    # --- تایید نهایی مدیر (حالت تایید سریع کارفرما) ---
    elif action == 'manager' and data[1] == 'final' and data[2] == 'approve':
        project_id = data[3]
        submission_id = data[4]

        if not is_manager(
                query.message.chat.id) or project_id not in PROJECT_DATA:
            return

        project_data = PROJECT_DATA[project_id]
        target_submission = next((sub for sub in project_data['submissions']
                                  if sub['submission_id'] == submission_id
                                  and sub['status'] == 'ClientApproved'), None)

        if not target_submission:
            return await query.edit_message_text(
                "⚠️ وضعیت محتوا نامعتبری دارد یا قبلاً نهایی شده است.")

        target_submission['status'] = 'ManagerApproved'
        save_project_data()
        await query.edit_message_text(
            f"✅ محتوای *P{project_id}* توسط مدیر نهایی شد.")

        editor_message_prefix = f"🎉 *تایید نهایی:* محتوای شما توسط مدیر نهایی و تایید شد."
        await send_media_to_editor(context, project_data['editor_chat_id'],
                                   project_id, target_submission,
                                   editor_message_prefix)

        notification_text = f"🎉 محتوای شما (ID: {submission_id}) از پروژه *P{project_id} - {project_data['name']}* توسط مدیر نهایی و تایید شد."
        try:
            await context.bot.send_message(project_data['client_chat_id'],
                                           f"🔔 اطلاعیه: {notification_text}",
                                           parse_mode='Markdown')
        except:
            pass


# --------------------------------------------------------------------------------------------------
# ۶. اجرای نهایی ربات و ثبت Handlers (ساختار Webhook)
# --------------------------------------------------------------------------------------------------

def build_application():
    """Application را برای Webhook می‌سازد و Handlers را ثبت می‌کند."""
    load_project_data()

    if not TELEGRAM_BOT_TOKEN or not MANAGER_CHAT_ID:
        raise ValueError(
            "❌ خطای پیکربندی: مقادیر BOT_TOKEN و MANAGER_ID باید تنظیم شوند."
        )

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("new_project", new_project))
    application.add_handler(CommandHandler("dashboard", dashboard))
    application.add_handler(CommandHandler("check", check_project_status))

    # Message Handlers
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
    
    # اطمینان از مقداردهی اولیه Application (رفع خطای Event Loop در محیط Webhook)
    # این خط در هر درخواست اجرا می‌شود، که در محیط Webhook/Gunicorn ضروری است.
    await TG_APPLICATION.initialize()
    
    if request.method == "POST":
        # دریافت داده JSON از درخواست تلگرام
        update = Update.de_json(request.get_json(force=True), TG_APPLICATION.bot)
        
        # پردازش آپدیت به صورت ناهمگام (Async)
        await TG_APPLICATION.process_update(update)
        
    return jsonify({"status": "ok"})

# نکته: نیازی به 'if __name__ == "__main__":' نیست، زیرا Gunicorn ربات را اجرا می‌کند.