# -*- coding: utf-8 -*-
import os, sys, time, json, logging, asyncio, threading
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
    JobQueue
)

# --- 1. 設置日誌記錄 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 2. 核心參數設定 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

def safe_get_chat_id():
    val = os.environ.get("TELEGRAM_CHAT_ID")
    if not val: return None
    try:
        clean_val = "".join(c for c in str(val).strip() if c.isdigit() or c == '-')
        return int(clean_val)
    except:
        return None

SPREADSHEET_NAME = "雲端提醒"
TAIPEI_TZ = timezone('Asia/Taipei')

# 全域變數
ANALYZE_FUNC = None
ta_helpers = None

# --- 3. 核心模組動態加載 ---
try:
    for m in ["ta_analyzer", "ta_helpers"]:
        path = os.path.join(current_dir, f"{m}.py")
        spec = importlib.util.spec_from_file_location(m, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if m == "ta_analyzer":
            ANALYZE_FUNC = mod.analyze_and_update_sheets
        else:
            ta_helpers = mod
    logger.info("✅ 核心分析模組加載成功")
except Exception as e:
    logger.error(f"❌ 模組載入失敗: {e}")

# --- 4. 資料處理函式 ---
def get_google_sheets_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json: return None
    try:
        return gspread.service_account_from_dict(json.loads(creds_json))
    except: return None

def fetch_stock_data_for_reminder():
    try:
        gc = get_google_sheets_client()
        if not gc: return pd.DataFrame()
        spreadsheet = gc.open(SPREADSHEET_NAME)
        worksheet = spreadsheet.worksheet("工作表1")
        data = worksheet.get_all_values()
        if len(data) < 2: return pd.DataFrame()
        
        # 建立 DataFrame
        df = pd.DataFrame(data[1:], columns=data[0])
        
        # --- 修改處：確保 B 欄（名稱）存在 ---
        # 假設 B 欄的標題叫做 '名稱'
        df['代號'] = df['代號'].str.strip()
        if '名稱' not in df.columns:
            # 如果表格沒標題，強行指定第二欄為名稱（視情況調整）
            df.rename(columns={df.columns[1]: '名稱'}, inplace=True)
        
        df = df[df['代號'].astype(bool)].copy()
        provider_col = '提供者'
        if provider_col not in df.columns: df[provider_col] = ''
        
        if ta_helpers:
            df['連結'] = df.apply(lambda row: ta_helpers.get_static_link(row['代號'], row[provider_col]), axis=1)
        return df
    except Exception as e:
        logger.error(f"讀取試算表失敗: {e}")
        return pd.DataFrame()

# --- 5. 核心執行任務 (排程與手動通用) ---
async def run_analysis_and_send(bot):
    target_id = safe_get_chat_id()
    if not target_id:
        logger.warning("‼️ 找不到 TELEGRAM_CHAT_ID，取消任務。")
        return False
        
    logger.info(f"⏰ 啟動分析任務 (目標 ID: {target_id})")
    stock_df = fetch_stock_data_for_reminder()
    if stock_df.empty: return False

    gc = get_google_sheets_client()
    if ANALYZE_FUNC:
        # 注意：這裡將整份 stock_df 傳入 ANALYZE_FUNC
        # ta_analyzer.py 內部的邏輯會決定最終顯示的文字
        alerts = ANALYZE_FUNC(gc, SPREADSHEET_NAME, stock_df['代號'].tolist(), stock_df)
        
        if alerts:
            header = f"🔔 *技術指標警報 ({datetime.now(TAIPEI_TZ).strftime('%H:%M:%S')})*"
            await bot.send_message(chat_id=target_id, text=header, parse_mode='Markdown')
            for msg in alerts:
                try:
                    # 如果 ta_analyzer 回傳的訊息還沒包含名稱，您可以在這裡進行字串處理（如下例示）
                    # 假設 msg 開頭是股票代號，我們可以嘗試匹配名稱
                    await bot.send_message(chat_id=target_id, text=msg, parse_mode='Markdown', disable_web_page_preview=True)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"發送失敗: {e}")
        return True
    return False

# --- 6. Telegram 任務接口 ---
async def periodic_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    await run_analysis_and_send(context.bot)

async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🚀 收到指令，開始即時分析...")
    success = await run_analysis_and_send(context.bot)
    if not success:
        await update.message.reply_text("❌ 分析失敗，請檢查 Log 或設定。")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    current_id = update.effective_chat.id
    await update.message.reply_text(f"👋 綁定成功！\n您的 Chat ID: `{current_id}`\n排程包含：亞盤、13:40 收盤前、全球盤、美股收盤。")

# --- 7. 排程設定 ---
def setup_scheduling(job_queue: JobQueue):
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0,30', 'hour': '8-13', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Asia')
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '40', 'hour': '13', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Asia_Closing')
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0', 'hour': '17,23', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Global')
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0', 'hour': '5', 'day_of_week': 'sat', 'timezone': TAIPEI_TZ}, name='US_Close')

# --- 8. Web 服務與 Health Check ---
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health_check():
    return jsonify({
        "status": "ok", 
        "chat_id": safe_get_chat_id(),
        "bot_ready": bool(TELEGRAM_BOT_TOKEN),
        "server_time": datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M:%S')
    }), 200

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"🌐 網頁伺服器啟動於 Port: {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# --- 9. 主程式入口 ---
def main():
    threading.Thread(target=run_flask, daemon=True).start()

    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ 找不到 TELEGRAM_BOT_TOKEN")
        while True: time.sleep(100)
        return

    while True:
        try:
            logger.info("⏳ 啟動 Telegram Bot (防衝突延遲 10 秒)...")
            time.sleep(10)
            application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
            setup_scheduling(application.job_queue)
            application.add_handler(CommandHandler("start", start_command))
            application.add_handler(CommandHandler("run", run_command))
            logger.info("📢 Bot 已成功連線並運行中")
            application.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)
        except Exception as e:
            if "Conflict" in str(e):
                logger.warning("⚠️ 偵測到實例衝突，正在重試...")
                time.sleep(20)
            else:
                logger.error(f"💥 程式異常: {e}")
                time.sleep(30)

if __name__ == '__main__':
    main()
