# bot.py (環境變數優化版)

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

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
# 🚨 關鍵修改：從環境變數獲取 CHAT_ID
ENV_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

SPREADSHEET_NAME = "雲端提醒"
LOCAL_SERVICE_ACCOUNT_FILE = "service_account_key.json"
GOOGLE_CREDENTIALS_ENV = "GOOGLE_CREDENTIALS"

# 全域時區
TAIPEI_TZ = timezone('Asia/Taipei')

# 全域變數
APPLICATION = None
USER_CHAT_ID = int(ENV_CHAT_ID) if ENV_CHAT_ID and ENV_CHAT_ID.isdigit() else None
ANALYZE_FUNC = None

# --- 核心模組加載 ---
try:
    module_name = "ta_analyzer"
    module_path = os.path.join(current_dir, f"{module_name}.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    ta_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ta_module)
    ANALYZE_FUNC = ta_module.analyze_and_update_sheets
    logger.info("✅ ta_analyzer 模組已載入。")

    module_name_helpers = "ta_helpers"
    module_path_helpers = os.path.join(current_dir, f"{module_name_helpers}.py")
    spec_helpers = importlib.util.spec_from_file_location(module_name_helpers, module_path_helpers)
    ta_helpers_module = importlib.util.module_from_spec(spec_helpers)
    spec_helpers.loader.exec_module(ta_helpers_module)
    ta_helpers = ta_helpers_module
    logger.info("✅ ta_helpers 模組已載入。")
except Exception as e:
    logger.error(f"核心模組載入失敗: {e}")
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
    # 如果環境變數沒設定，則臨時使用當前對話 ID
    if not USER_CHAT_ID:
        USER_CHAT_ID = update.message.chat_id
        await update.message.reply_text(f'提醒已啟動！暫時使用此 ID：{USER_CHAT_ID}\n💡 建議將此 ID 加入 Railway 環境變數 TELEGRAM_CHAT_ID 以持久保存。')
    else:
        await update.message.reply_text(f'提醒機器人運作中！當前設定 ID：{USER_CHAT_ID}')

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f'請發送 /start 檢查設定狀態。')

async def periodic_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    global USER_CHAT_ID
    if not USER_CHAT_ID:
        logger.warning("未設定 USER_CHAT_ID，取消排程任務。")
        return
        
    stock_df = fetch_stock_data_for_reminder()
    if stock_df.empty: return
    gc = get_google_sheets_client()
    if not gc: return
    
    logger.info(f"開始執行定時分析任務 (目標 ID: {USER_CHAT_ID})...")
    alerts = ANALYZE_FUNC(gc, SPREADSHEET_NAME, stock_df['代號'].tolist(), stock_df)
    
    if alerts:
        header = f"🔔 **🚨 技術指標警報 ({datetime.now().strftime('%H:%M:%S')})**："
        await context.bot.send_message(chat_id=USER_CHAT_ID, text=header, parse_mode='Markdown')
        for alert_message in alerts:
            try:
                await context.bot.send_message(chat_id=USER_CHAT_ID, text=alert_message, parse_mode='Markdown', disable_web_page_preview=True)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"發送訊息失敗: {e}")

# --- 排程設定 ---
def setup_scheduling(job_queue: JobQueue):
    # 排程設定保持不變
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0,30', 'hour': '8-13', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Asia')
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0', 'hour': '17', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Europe')
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0', 'hour': '23', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Late')
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0', 'hour': '4', 'day_of_week': 'sat', 'timezone': TAIPEI_TZ}, name='US_Close')
    logger.info("✅ 排程設定完成。")

# --- 初始化 ---
def initialize_bot_and_scheduler():
    global APPLICATION
    if not TELEGRAM_BOT_TOKEN: return False
    APPLICATION = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
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
def health_check(): return jsonify({"status": "ok", "chat_id": USER_CHAT_ID}), 200

if __name__ == '__main__':
    if TELEGRAM_BOT_TOKEN:
        if not initialize_bot_and_scheduler(): sys.exit(1)
        logger.info(f"Bot 啟動中... 目標 Chat ID: {USER_CHAT_ID}")
        try:
            APPLICATION.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)
        except AttributeError as ae:
            if "_pending_futures" in str(ae): logger.info("Bot 已安全停止。")
            else: logger.error(f"屬性錯誤: {ae}")
        except Exception as e:
            logger.error(f"運行出錯: {e}")
    else:
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port)
