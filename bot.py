# bot.py (最終穩定版 - PTB v20+ 官方 JobQueue 寫法，已修正 run_cron + 時區)

import os
import sys
import json
import logging
import asyncio
from datetime import datetime
import importlib.util
from pytz import timezone

# --- 設置日誌記錄 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 導入 PTB 必要類別 ---
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    JobQueue
)
import gspread
import pandas as pd

# --- 設定路徑和變數 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_BOT_TOKEN = os.environ.get(TELEGRAM_BOT_TOKEN_ENV)
SPREADSHEET_NAME = "雲端提醒"
LOCAL_SERVICE_ACCOUNT_FILE = "service_account_key.json"
GOOGLE_CREDENTIALS_ENV = "GOOGLE_CREDENTIALS"
CHAT_ID_SHEET = '工作表2'
CHAT_ID_CELL = 'A2'
CHAT_ID_NOTE_CELL = 'A1'

# 全域時區
TAIPEI_TZ = timezone('Asia/Taipei')

# 全域變數
APPLICATION = None
USER_CHAT_ID = None
ANALYZE_FUNC = None

# --- 核心模組加載 ---
try:
    module_name = "ta_analyzer"
    module_path = os.path.join(current_dir, f"{module_name}.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    ta_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ta_module)
    ANALYZE_FUNC = ta_module.analyze_and_update_sheets
    logger.info("✅ ta_analyzer 模組已通過絕對路徑加載成功。")

    module_name_helpers = "ta_helpers"
    module_path_helpers = os.path.join(current_dir, f"{module_name_helpers}.py")
    spec_helpers = importlib.util.spec_from_file_location(module_name_helpers, module_path_helpers)
    ta_helpers = importlib.util.module_from_spec(spec_helpers)
    spec_helpers.loader.exec_module(ta_helpers)
    logger.info("✅ ta_helpers 模組已加載成功。")

except Exception as e:
    logger.error(f"FATAL ERROR: 無法使用 importlib 加載核心模組。錯誤詳情: {e}")

    def ANALYZE_FUNC(*args, **kwargs):
        logger.error("FATAL ERROR: 技術分析模組加載失敗，無法執行任務。")
        return []

    class DummyHelpers:
        def get_static_link(*args, **kwargs):
            logger.error("FATAL ERROR: ta_helpers 模組加載失敗，連結功能無法使用。")
            return "連結失敗"
    ta_helpers = DummyHelpers()

# --- Google Sheets 基礎處理函數 ---
def get_google_sheets_client():
    if os.environ.get(GOOGLE_CREDENTIALS_ENV):
        logger.info("從環境變數讀取 Google 憑證 (部署模式)...")
        try:
            credentials_json = json.loads(os.environ.get(GOOGLE_CREDENTIALS_ENV))
            return gspread.service_account_from_dict(credentials_json)
        except json.JSONDecodeError:
            logger.error("GOOGLE_CREDENTIALS 環境變數格式錯誤。")
            return None
    elif os.path.exists(LOCAL_SERVICE_ACCOUNT_FILE):
        logger.info("從本地金鑰檔案讀取 Google 憑證 (本地模式)...")
        return gspread.service_account(filename=LOCAL_SERVICE_ACCOUNT_FILE)
    else:
        logger.error(f"找不到 Google Sheets 憑證！請檢查 {GOOGLE_CREDENTIALS_ENV} 和 {LOCAL_SERVICE_ACCOUNT_FILE}。")
        return None

def save_chat_id_to_sheets(chat_id: int):
    try:
        gc = get_google_sheets_client()
        if not gc:
            logger.error("無法連線 Google Sheets，Chat ID 無法持久儲存。")
            return False
        spreadsheet = gc.open(SPREADSHEET_NAME)
        try:
            worksheet = spreadsheet.worksheet(CHAT_ID_SHEET)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=CHAT_ID_SHEET, rows="100", cols="20")
            logger.info(f"創建了新的工作表: {CHAT_ID_SHEET}")
        worksheet.update_acell(CHAT_ID_NOTE_CELL, "Telegram Bot - 提醒目標 Chat ID (勿刪)")
        worksheet.update_acell(CHAT_ID_CELL, str(chat_id))
        logger.info(f"Chat ID {chat_id} 成功儲存到 Google Sheets 的 {CHAT_ID_SHEET}!{CHAT_ID_CELL}。")
        return True
    except Exception as e:
        logger.error(f"儲存 Chat ID 到試算表時發生錯誤: {e}")
        return False

def get_chat_id_from_sheets():
    try:
        gc = get_google_sheets_client()
        if not gc:
            return None
        spreadsheet = gc.open(SPREADSHEET_NAME)
        worksheet = spreadsheet.worksheet(CHAT_ID_SHEET)
        chat_id_str = worksheet.acell(CHAT_ID_CELL).value
        if chat_id_str and chat_id_str.isdigit():
            return int(chat_id_str)
        return None
    except Exception as e:
        logger.warning(f"從試算表讀取 Chat ID 時發生錯誤: {e}")
        return None

def fetch_stock_data_for_reminder():
    try:
        gc = get_google_sheets_client()
        if not gc:
            return pd.DataFrame()
        spreadsheet = gc.open(SPREADSHEET_NAME)
        worksheet1 = spreadsheet.worksheet("工作表1")
        data1 = worksheet1.get_all_values()
        if not data1 or len(data1) < 2 or '代號' not in data1[0]:
            logger.warning("工作表1是空的或沒有代號欄位。")
            return pd.DataFrame()
        df = pd.DataFrame(data1[1:], columns=data1[0])
        df = df[df['代號'].astype(str).str.strip().astype(bool)].copy()
        df['代號'] = df['代號'].astype(str).str.strip()
        provider_column_name = '提供者'
        if provider_column_name not in df.columns:
            logger.error(f"工作表1中找不到欄位 '{provider_column_name}'，連結功能將受限。")
            df[provider_column_name] = ''
        df['連結'] = df.apply(
            lambda row: ta_helpers.get_static_link(row['代號'], row[provider_column_name]),
            axis=1
        )
        logger.info(f"成功讀取 {len(df)} 個股票代號並生成連結。")
        return df
    except Exception as e:
        logger.error(f"讀取試算表資料時發生錯誤: {e}")
        return pd.DataFrame()

# --- Telegram Bot 命令 ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global USER_CHAT_ID
    new_chat_id = update.message.chat_id
    USER_CHAT_ID = new_chat_id
    save_chat_id_to_sheets(new_chat_id)
    stock_df = fetch_stock_data_for_reminder()
    code_preview = f"{'、'.join(stock_df['代號'].tolist()[:3])}..." if not stock_df.empty else "目前試算表無代號"
    await update.message.reply_text(
        f'提醒機器人已啟動！您的 Chat ID 已儲存：{USER_CHAT_ID}\n'
        f'我已將此 ID 儲存至 Google Sheets ({CHAT_ID_SHEET}!{CHAT_ID_CELL})，**下次重啟後無需再次輸入 /start**。\n\n'
        f'(測試讀取: {code_preview})'
    )
    logger.info(f"Chat ID 儲存成功: {USER_CHAT_ID}")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f'收到訊息: "{update.message.text}"。請發送 /start 來設定提醒目標。')

async def periodic_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    global USER_CHAT_ID
    if not USER_CHAT_ID:
        USER_CHAT_ID = get_chat_id_from_sheets()
    if not USER_CHAT_ID:
        logger.warning("沒有可用的 USER_CHAT_ID，無法發送提醒。請先發送 /start。")
        return
    stock_df = fetch_stock_data_for_reminder()
    if stock_df.empty:
        logger.info("試算表沒有代號需要處理。")
        return
    stock_codes = stock_df['代號'].tolist()
    gc = get_google_sheets_client()
    if not gc:
        logger.error("無法連線 Google Sheets，無法進行技術分析。")
        return
    logger.info(f"開始對 {len(stock_codes)} 個代號進行技術分析...")
    alerts = ANALYZE_FUNC(gc, SPREADSHEET_NAME, stock_codes, stock_df)
    if alerts:
        reminder_header = f"🔔 **🚨 技術指標警報 ({datetime.now().strftime('%H:%M:%S')})**："
        await context.bot.send_message(chat_id=USER_CHAT_ID, text=reminder_header, parse_mode='Markdown')
        for alert_message in alerts:
            try:
                await context.bot.send_message(chat_id=USER_CHAT_ID, text=alert_message, parse_mode='Markdown')
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"發送單一訊息失敗: {e}")
        logger.info(f"成功發送 {len(alerts)} 個警報。")
    else:
        logger.info("本次無警報觸發。")

# --- 排程設定 (PTB v20+ 官方寫法) ---
def setup_scheduling(job_queue: JobQueue):
    """
    設定多個市場的 Cron 排程（使用 run_custom 實現 cron）。
    """
    # 1. 亞洲盤交易時間 (週一到週五，08:00-13:00，每 30 分鐘：00 和 30)
    job_queue.run_custom(
        periodic_reminder_job,
        job_kwargs={
            'trigger': 'cron',
            'minute': '0,30',
            'hour': '8-13',
            'day_of_week': 'mon-fri',
            'timezone': TAIPEI_TZ
        },
        name='Asia Market Scan (08:30-13:30)'
    )

    # 2. 歐洲盤 (週一到週五，17:00)
    job_queue.run_custom(
        periodic_reminder_job,
        job_kwargs={
            'trigger': 'cron',
            'minute': '0',
            'hour': '17',
            'day_of_week': 'mon-fri',
            'timezone': TAIPEI_TZ
        },
        name='Europe Open Scan (17:00)'
    )

    # 3. 晚盤 (週一到週五，23:00)
    job_queue.run_custom(
        periodic_reminder_job,
        job_kwargs={
            'trigger': 'cron',
            'minute': '0',
            'hour': '23',
            'day_of_week': 'mon-fri',
            'timezone': TAIPEI_TZ
        },
        name='Late Scan (23:00)'
    )

    # 4. 美股收盤後 (週六 04:00)
    job_queue.run_custom(
        periodic_reminder_job,
        job_kwargs={
            'trigger': 'cron',
            'minute': '0',
            'hour': '4',
            'day_of_week': 'sat',
            'timezone': TAIPEI_TZ
        },
        name='US Close Scan (Sat 04:00)'
    )

    logger.info("✅ 已使用 run_custom + cron 設定所有排程（台灣時間）。")

# --- 初始化 Bot 和 JobQueue ---
def initialize_bot_and_scheduler(run_web_server=False):
    global APPLICATION
    if not TELEGRAM_BOT_TOKEN:
        logger.error(f"無法啟動：{TELEGRAM_BOT_TOKEN_ENV} 環境變數未設定。")
        if not run_web_server:
            print("\n🚨 本地運行失敗提示：請設定 TELEGRAM_BOT_TOKEN 環境變數。\n")
        return False

    APPLICATION = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    job_queue = APPLICATION.job_queue
    job_queue.scheduler.configure(
        timezone=TAIPEI_TZ,
        job_defaults={'coalesce': True, 'max_instances': 3, 'misfire_grace_time': 100}
    )

    setup_scheduling(job_queue)

    async def post_init(app: Application):
        logger.info("Bot 初始化完成，排程器已啟動。")

    APPLICATION.post_init = post_init
    APPLICATION.add_handler(CommandHandler("start", start_command))
    APPLICATION.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    logger.info("Bot 和 JobQueue 初始化成功。")
    return True

# --- Flask Health Check (部署用) ---
from flask import Flask, jsonify
app = Flask(__name__)

@app.route('/health')
def health_check():
    return jsonify({"status": "ok" if APPLICATION else "error", "message": "Bot is running."}), (200 if APPLICATION else 500)

if __name__ == '__main__':
    if TELEGRAM_BOT_TOKEN:
        if not initialize_bot_and_scheduler(run_web_server=False):
            sys.exit(1)
        logger.info("以 Polling 模式啟動 Bot...")
        try:
            APPLICATION.run_polling(allowed_updates=Update.ALL_TYPES)
        except KeyboardInterrupt:
            logger.info("程式已手動終止。")
    else:
        initialize_bot_and_scheduler(run_web_server=True)
        logger.warning("Bot 初始化失敗，僅啟動 Flask 健康檢查。")
        port = int(os.environ.get('PORT', 5000))
        logger.info(f"以 Web 模式啟動 Flask，監聽端口: {port}")
        app.run(host='0.0.0.0', port=port)

