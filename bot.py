# bot.py (最終穩定修復版 - 解決關機 AttributeError 並支援 A 欄連結)

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
    ta_helpers_module = importlib.util.module_from_spec(spec_helpers)
    spec_helpers.loader.exec_module(ta_helpers_module)
    ta_helpers = ta_helpers_module # 確保引用正確
    logger.info("✅ ta_helpers 模組已加載成功。")

except Exception as e:
    logger.error(f"FATAL ERROR: 無法加載核心模組。錯誤詳情: {e}")
    def ANALYZE_FUNC(*args, **kwargs): return []

# --- Google Sheets 基礎處理函數 ---
def get_google_sheets_client():
    if os.environ.get(GOOGLE_CREDENTIALS_ENV):
        try:
            credentials_json = json.loads(os.environ.get(GOOGLE_CREDENTIALS_ENV))
            return gspread.service_account_from_dict(credentials_json)
        except: return None
    elif os.path.exists(LOCAL_SERVICE_ACCOUNT_FILE):
        return gspread.service_account(filename=LOCAL_SERVICE_ACCOUNT_FILE)
    return None

def save_chat_id_to_sheets(chat_id: int):
    try:
        gc = get_google_sheets_client()
        if not gc: return False
        spreadsheet = gc.open(SPREADSHEET_NAME)
        try:
            worksheet = spreadsheet.worksheet(CHAT_ID_SHEET)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=CHAT_ID_SHEET, rows="100", cols="20")
        worksheet.update_acell(CHAT_ID_NOTE_CELL, "Telegram Bot - 提醒目標 Chat ID (勿刪)")
        worksheet.update_acell(CHAT_ID_CELL, str(chat_id))
        return True
    except Exception as e:
        logger.error(f"儲存 Chat ID 失敗: {e}")
        return False

def get_chat_id_from_sheets():
    try:
        gc = get_google_sheets_client()
        if not gc: return None
        spreadsheet = gc.open(SPREADSHEET_NAME)
        worksheet = spreadsheet.worksheet(CHAT_ID_SHEET)
        chat_id_str = worksheet.acell(CHAT_ID_CELL).value
        return int(chat_id_str) if chat_id_str and chat_id_str.isdigit() else None
    except: return None

def fetch_stock_data_for_reminder():
    try:
        gc = get_google_sheets_client()
        if not gc: return pd.DataFrame()
        spreadsheet = gc.open(SPREADSHEET_NAME)
        worksheet1 = spreadsheet.worksheet("工作表1")
        data1 = worksheet1.get_all_values()
        if not data1 or len(data1) < 2: return pd.DataFrame()
        df = pd.DataFrame(data1[1:], columns=data1[0])
        df = df[df['代號'].astype(str).str.strip().astype(bool)].copy()
        df['代號'] = df['代號'].astype(str).str.strip()
        provider_col = '提供者'
        if provider_col not in df.columns: df[provider_col] = ''
        df['連結'] = df.apply(lambda row: ta_helpers.get_static_link(row['代號'], row[provider_col]), axis=1)
        return df
    except Exception as e:
        logger.error(f"讀取試算表失敗: {e}")
        return pd.DataFrame()

# --- Telegram Bot 命令 ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global USER_CHAT_ID
    USER_CHAT_ID = update.message.chat_id
    save_chat_id_to_sheets(USER_CHAT_ID)
    stock_df = fetch_stock_data_for_reminder()
    code_preview = f"{'、'.join(stock_df['代號'].tolist()[:3])}..." if not stock_df.empty else "無代號"
    await update.message.reply_text(f'提醒機器人已啟動！ID：{USER_CHAT_ID}\n(測試讀取: {code_preview})')

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f'請發送 /start 來設定提醒目標。')

async def periodic_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    global USER_CHAT_ID
    if not USER_CHAT_ID: USER_CHAT_ID = get_chat_id_from_sheets()
    if not USER_CHAT_ID: return
    stock_df = fetch_stock_data_for_reminder()
    if stock_df.empty: return
    gc = get_google_sheets_client()
    if not gc: return
    
    logger.info(f"開始執行定時分析任務...")
    alerts = ANALYZE_FUNC(gc, SPREADSHEET_NAME, stock_df['代號'].tolist(), stock_df)
    
    if alerts:
        header = f"🔔 **🚨 技術指標警報 ({datetime.now().strftime('%H:%M:%S')})**："
        await context.bot.send_message(chat_id=USER_CHAT_ID, text=header, parse_mode='Markdown')
        for alert_message in alerts:
            try:
                await context.bot.send_message(chat_id=USER_CHAT_ID, text=alert_message, parse_mode='Markdown', disable_web_page_preview=True)
                await asyncio.sleep(0.5)
            except: pass

# --- 排程設定 ---
def setup_scheduling(job_queue: JobQueue):
    # 亞洲盤 (08:00-13:30 每 30 分鐘)
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0,30', 'hour': '8-13', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Asia')
    # 歐洲盤 (17:00)
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0', 'hour': '17', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Europe')
    # 晚盤 (23:00)
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0', 'hour': '23', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Late')
    # 美股收盤 (週六 04:00)
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0', 'hour': '4', 'day_of_week': 'sat', 'timezone': TAIPEI_TZ}, name='US_Close')
    logger.info("✅ 排程設定完成。")

# --- 初始化 ---
def initialize_bot_and_scheduler():
    global APPLICATION
    if not TELEGRAM_BOT_TOKEN: return False
    APPLICATION = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # 優化排程器設定以減少關閉錯誤
    job_queue = APPLICATION.job_queue
    job_queue.scheduler.configure(timezone=TAIPEI_TZ, job_defaults={'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 30})
    
    setup_scheduling(job_queue)
    APPLICATION.add_handler(CommandHandler("start", start_command))
    APPLICATION.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    return True

# --- Flask Health Check ---
from flask import Flask, jsonify
app = Flask(__name__)
@app.route('/health')
def health_check(): return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    if TELEGRAM_BOT_TOKEN:
        if not initialize_bot_and_scheduler(): sys.exit(1)
        logger.info("Bot 啟動中...")
        try:
            # 使用 close_loop=False 並捕捉特定 AttributeError 以優雅停機
            APPLICATION.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)
        except AttributeError as ae:
            if "_pending_futures" in str(ae):
                logger.info("Bot 已安全停止 (忽略已知排程器關閉 Bug)。")
            else: logger.error(f"發生未預期的屬性錯誤: {ae}")
        except Exception as e:
            logger.error(f"Bot 運行出錯: {e}")
        finally:
            logger.info("程序結束。")
    else:
        # Web 模式 (部署平台健康檢查用)
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port)
