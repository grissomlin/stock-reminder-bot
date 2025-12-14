# bot.py — FINAL STABLE VERSION (PTB v20+)

import os
import sys
import json
import logging
import asyncio
from datetime import datetime
import importlib.util
from pytz import timezone

# =======================
# Logging
# =======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# =======================
# Telegram (PTB v20+)
# =======================
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =======================
# Google Sheets
# =======================
import gspread
import pandas as pd

# =======================
# Paths & Env
# =======================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

SPREADSHEET_NAME = "雲端提醒"
LOCAL_SERVICE_ACCOUNT_FILE = "service_account_key.json"
GOOGLE_CREDENTIALS_ENV = "GOOGLE_CREDENTIALS"

CHAT_ID_SHEET = "工作表2"
CHAT_ID_CELL = "A2"
CHAT_ID_NOTE_CELL = "A1"

TAIPEI_TZ = timezone("Asia/Taipei")

APPLICATION = None
USER_CHAT_ID = None
ANALYZE_FUNC = None

# =======================
# Load ta_analyzer / helpers
# =======================
try:
    spec = importlib.util.spec_from_file_location(
        "ta_analyzer", os.path.join(BASE_DIR, "ta_analyzer.py")
    )
    ta_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ta_module)
    ANALYZE_FUNC = ta_module.analyze_and_update_sheets

    spec_h = importlib.util.spec_from_file_location(
        "ta_helpers", os.path.join(BASE_DIR, "ta_helpers.py")
    )
    ta_helpers = importlib.util.module_from_spec(spec_h)
    spec_h.loader.exec_module(ta_helpers)

    logger.info("✅ ta_analyzer / ta_helpers 載入成功")

except Exception as e:
    logger.error(f"❌ 模組載入失敗: {e}")

    def ANALYZE_FUNC(*args, **kwargs):
        return []

    class DummyHelpers:
        def get_static_link(self, *a, **k):
            return ""
    ta_helpers = DummyHelpers()

# =======================
# Google Sheets helpers
# =======================
def get_google_sheets_client():
    if os.environ.get(GOOGLE_CREDENTIALS_ENV):
        creds = json.loads(os.environ[GOOGLE_CREDENTIALS_ENV])
        return gspread.service_account_from_dict(creds)

    if os.path.exists(LOCAL_SERVICE_ACCOUNT_FILE):
        return gspread.service_account(filename=LOCAL_SERVICE_ACCOUNT_FILE)

    logger.error("❌ 找不到 Google Sheets 憑證")
    return None


def save_chat_id_to_sheets(chat_id: int):
    try:
        gc = get_google_sheets_client()
        ws = gc.open(SPREADSHEET_NAME).worksheet(CHAT_ID_SHEET)
        ws.update_acell(CHAT_ID_NOTE_CELL, "Telegram Bot Chat ID")
        ws.update_acell(CHAT_ID_CELL, str(chat_id))
        return True
    except Exception as e:
        logger.error(f"儲存 Chat ID 失敗: {e}")
        return False


def get_chat_id_from_sheets():
    try:
        gc = get_google_sheets_client()
        ws = gc.open(SPREADSHEET_NAME).worksheet(CHAT_ID_SHEET)
        v = ws.acell(CHAT_ID_CELL).value
        return int(v) if v and v.isdigit() else None
    except Exception:
        return None


def fetch_stock_data_for_reminder():
    try:
        gc = get_google_sheets_client()
        ws = gc.open(SPREADSHEET_NAME).worksheet("工作表1")
        data = ws.get_all_values()
        if len(data) < 2:
            return pd.DataFrame()

        df = pd.DataFrame(data[1:], columns=data[0])
        df = df[df["代號"].astype(str).str.strip().astype(bool)]
        df["代號"] = df["代號"].str.strip()

        if "提供者" not in df.columns:
            df["提供者"] = ""

        df["連結"] = df.apply(
            lambda r: ta_helpers.get_static_link(r["代號"], r["提供者"]),
            axis=1
        )
        return df

    except Exception as e:
        logger.error(f"讀取股票資料失敗: {e}")
        return pd.DataFrame()

# =======================
# Telegram handlers
# =======================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global USER_CHAT_ID
    USER_CHAT_ID = update.message.chat_id
    save_chat_id_to_sheets(USER_CHAT_ID)

    await update.message.reply_text(
        f"✅ 提醒機器人已啟動\nChat ID：{USER_CHAT_ID}"
    )


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("請輸入 /start 初始化")


async def periodic_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    global USER_CHAT_ID

    if not USER_CHAT_ID:
        USER_CHAT_ID = get_chat_id_from_sheets()
    if not USER_CHAT_ID:
        return

    df = fetch_stock_data_for_reminder()
    if df.empty:
        return

    gc = get_google_sheets_client()
    alerts = ANALYZE_FUNC(
        gc,
        SPREADSHEET_NAME,
        df["代號"].tolist(),
        df
    )

    if alerts:
        await context.bot.send_message(
            chat_id=USER_CHAT_ID,
            text=f"🔔 技術警報 ({datetime.now().strftime('%H:%M:%S')})",
        )
        for msg in alerts:
            await context.bot.send_message(chat_id=USER_CHAT_ID, text=msg)
            await asyncio.sleep(0.5)

# =======================
# Scheduler
# =======================
def setup_scheduling(job_queue):
    job_queue.run_cron(
        periodic_reminder_job,
        minute="0,30",
        hour="8-13",
        day_of_week="mon-fri",
        name="Asia Market"
    )

    job_queue.run_cron(
        periodic_reminder_job,
        hour=17,
        minute=0,
        day_of_week="mon-fri",
        name="Europe"
    )

    job_queue.run_cron(
        periodic_reminder_job,
        hour=23,
        minute=0,
        day_of_week="mon-fri",
        name="Night"
    )

    job_queue.run_cron(
        periodic_reminder_job,
        hour=4,
        minute=0,
        day_of_week="sat",
        name="US Close"
    )

    logger.info("✅ Cron 排程完成")

# =======================
# Init
# =======================
def initialize_bot_and_scheduler():
    global APPLICATION

    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN 未設定")
        return False

    APPLICATION = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    setup_scheduling(APPLICATION.job_queue)

    APPLICATION.add_handler(CommandHandler("start", start_command))
    APPLICATION.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    logger.info("✅ Bot 初始化完成")
    return True

# =======================
# Flask (Railway health)
# =======================
from flask import Flask, jsonify
app = Flask(__name__)

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

# =======================
# Main
# =======================
if __name__ == "__main__":

    if initialize_bot_and_scheduler():
        logger.info("🚀 Bot polling 啟動")
        APPLICATION.run_polling(allowed_updates=Update.ALL_TYPES)
    else:
        port = int(os.environ.get("PORT", 5000))
        app.run(host="0.0.0.0", port=port)
