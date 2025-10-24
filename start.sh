#!/usr/bin/env bash

# اجرای ربات در پس‌زمینه (Polling)
python app.py &

# اجرای سرور Flask با Gunicorn برای پاسخ به پینگ Render
gunicorn --worker-class gevent --workers 1 --bind 0.0.0.0:$PORT app:app