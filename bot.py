# -*- coding: utf-8 -*-
import os, sys, time, random, json, subprocess, logging, asyncio
import importlib.util
from datetime import datetime
from pytz import timezone
import pandas as pd
import gspread
from flask import Flask, jsonify

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

# --- 設置日誌記錄 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 路徑設定 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# --- 讀取環境變數 ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ENV_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

SPREADSHEET_NAME = "雲端提醒"
GOOGLE_CREDENTIALS_ENV = "GOOGLE_CREDENTIALS"
TAIPEI_TZ = timezone('Asia/Taipei')

# --- 輔助函式：安全轉換 Chat ID (支援負數群組 ID) ---
def safe_get_chat_id(val):
    if not val:
        return None
    try:
        # 去除可能誤入的引號或空白
        clean_val = str(val).strip().replace('"', '').replace("'", "")
        return int(clean_val)
    except (ValueError, TypeError):
        logger.error(f"❌ 無法解析 TELEGRAM_CHAT_ID: {val}")
        return None

# 全域變數初始化
APPLICATION = None
USER_CHAT_ID = safe_get_chat_id(ENV_CHAT_ID)
ANALYZE_FUNC = None

# --- 核心模組動態加載 ---
try:
    # 加載 ta_analyzer
    module_name = "ta_analyzer"
    module_path = os.path.join(current_dir, f"{module_name}.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    ta_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ta_module)
    ANALYZE_FUNC = ta_module.analyze_and_update_sheets
    
    # 加載 ta_helpers
    module_name_helpers = "ta_helpers"
    module_path_helpers = os.path.join(current_dir, f"{module_name_helpers}.py")
    spec_h = importlib.util.spec_from_file_location(module_name_helpers, module_path_helpers)
    ta_helpers = importlib.util.module_from_spec(spec_h)
    spec_h.loader.exec_module(ta_helpers)
    logger.info("✅ 核心分析模組加載成功")
except Exception as e:
    logger.error(f"❌ 核心模組載入失敗: {e}")
    def ANALYZE_FUNC(*args, **kwargs): return []

# --- Google Sheets 處理 ---
def get_google_sheets_client():
    creds_json = os.environ.get(GOOGLE_CREDENTIALS_ENV)
    if creds_json:
        try:
            return gspread.service_account_from_dict(json.loads(creds_json))
        except Exception as e:
            logger.error(f"Google Credentials 解析失敗: {e}")
    return None

def fetch_stock_data_for_reminder():
    try:
        gc = get_google_sheets_client()
        if not gc: return pd.DataFrame()
        spreadsheet = gc.open(SPREADSHEET_NAME)
        worksheet = spreadsheet.worksheet("工作表1")
        data = worksheet.get_all_values()
        if len(data) < 2: return pd.DataFrame()
        df = pd.DataFrame(data[1:], columns=data[0])
        # 濾除空代號並清理格式
        df = df[df['代號'].str.strip().astype(bool)].copy()
        df['代號'] = df['代號'].str.strip()
        provider_col = '提供者'
        if provider_col not in df.columns: df[provider_col] = ''
        # 調用 ta_helpers 產生連結
        df['連結'] = df.apply(lambda row: ta_helpers.get_static_link(row['代號'], row[provider_col]), axis=1)
        return df
    except Exception as e:
        logger.error(f"讀取試算表失敗: {e}")
        return pd.DataFrame()

# --- Telegram 指令處理 ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global USER_CHAT_ID
    current_id = update.effective_chat.id
    if not USER_CHAT_ID:
        USER_CHAT_ID = current_id
        await update.message.reply_text(f"⚠️ 環境變數未偵測到 ID，暫時綁定此對話：`{USER_CHAT_ID}`\n請記得在 Railway 設定 `TELEGRAM_CHAT_ID`。", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"🚀 機器人運行中！\n當前目標 ID：`{USER_CHAT_ID}`", parse_mode='Markdown')

async def periodic_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    global USER_CHAT_ID
    
    # 💡 保險機制：執行時若 ID 為空，再次嘗試從環境變數讀取
    if not USER_CHAT_ID:
        USER_CHAT_ID = safe_get_chat_id(os.environ.get("TELEGRAM_CHAT_ID"))

    if not USER_CHAT_ID:
        logger.warning("‼️ 仍然找不到 USER_CHAT_ID，取消任務。")
        return
        
    logger.info(f"⏰ 啟動排程分析任務 (ID: {USER_CHAT_ID})")
    stock_df = fetch_stock_data_for_reminder()
    if stock_df.empty:
        logger.info("工作表無資料，跳過分析。")
        return

    gc = get_google_sheets_client()
    alerts = ANALYZE_FUNC(gc, SPREADSHEET_NAME, stock_df['代號'].tolist(), stock_df)
    
    if alerts:
        header = f"🔔 *技術指標警報 ({datetime.now(TAIPEI_TZ).strftime('%H:%M:%S')})*"
        await context.bot.send_message(chat_id=USER_CHAT_ID, text=header, parse_mode='Markdown')
        for msg in alerts:
            try:
                await context.bot.send_message(chat_id=USER_CHAT_ID, text=msg, parse_mode='Markdown', disable_web_page_preview=True)
                await asyncio.sleep(0.5) # 避開 Rate Limit
            except Exception as e:
                logger.error(f"發送警報失敗: {e}")

# --- 排程設定 ---
def setup_scheduling(job_queue: JobQueue):
    # 亞洲/台股盤中 (08:00 - 13:30 每 30 分鐘)
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0,30', 'hour': '8-13', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Asia')
    # 歐股/美股開盤前
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0', 'hour': '17,23', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Global')
    # 美股收盤 (週六凌晨)
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0', 'hour': '5', 'day_of_week': 'sat', 'timezone': TAIPEI_TZ}, name='US_Close')
    logger.info("✅ 所有的 Cron 排程已掛載")

# --- Flask Health Check ---
app = Flask(__name__)
@app.route('/health')
def health_check():
    return jsonify({
        "status": "ok", 
        "chat_id": USER_CHAT_ID,
        "env_raw": os.environ.get("TELEGRAM_CHAT_ID")
    }), 200

# --- 主程式 ---
def main():
    global APPLICATION
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ 找不到 TELEGRAM_BOT_TOKEN，啟動 Flask 模式")
        app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
        return

    APPLICATION = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # 配置 JobQueue
    job_queue = APPLICATION.job_queue
    setup_scheduling(job_queue)
    
    # 指令與訊息處理
    APPLICATION.add_handler(CommandHandler("start", start_command))
    APPLICATION.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text("請使用 /start 檢查狀態")))

    logger.info(f"📢 Bot 啟動成功，目前監聽 ID: {USER_CHAT_ID}")
    APPLICATION.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
