# -*- coding: utf-8 -*-
import os, sys, time, random, json, subprocess, logging, asyncio, difflib, threading
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

# --- 1. 設置日誌記錄 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 2. 環境變數深度診斷器 ---
def diagnose_env():
    print("\n" + "🚀" + "="*50)
    print("🔍 [Railway 環境變數深度偵錯]")
    
    all_keys = list(os.environ.keys())
    target_key = "TELEGRAM_CHAT_ID"
    sensitive_keywords = ['TOKEN', 'KEY', 'CREDENTIALS', 'PASSWORD', 'SECRET', 'AUTH', 'PWD']
    
    val = os.environ.get(target_key)
    if val:
        clean_id = str(val).strip().replace('"', '').replace("'", "")
        print(f"✅ 找到精確匹配: {target_key} = [{clean_id}]")
    else:
        print(f"❌ 找不到精確名稱: '{target_key}'")
        matches = difflib.get_close_matches(target_key, all_keys, n=3, cutoff=0.6)
        if matches:
            print(f"💡 發現疑似變數 (請檢查名稱): {matches}")

    print("\n📋 系統環境變數縮影:")
    for key in sorted(all_keys):
        is_sensitive = any(kw in key.upper() for kw in sensitive_keywords)
        v = os.environ.get(key)
        display_v = f"{v[:4]}***{v[-4:]}" if is_sensitive and v and len(v)>8 else v
        print(f"🔹 {key}: {display_v}")
    print("🚀" + "="*50 + "\n")

# --- 3. 基礎參數與輔助函式 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SPREADSHEET_NAME = "雲端提醒"
TAIPEI_TZ = timezone('Asia/Taipei')

def safe_get_chat_id():
    # 同時嘗試多種可能的名字
    val = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("CHAT_ID")
    if not val: return None
    try:
        # 清理字串，只保留數字與負號
        clean_val = "".join(c for c in str(val).strip() if c.isdigit() or c == '-')
        return int(clean_val)
    except:
        return None

# 全域變數初始化
USER_CHAT_ID = safe_get_chat_id()
ANALYZE_FUNC = None
ta_helpers = None

# --- 4. 核心模組動態加載 ---
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

# --- 5. Google Sheets 與資料處理 ---
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
        df = pd.DataFrame(data[1:], columns=data[0])
        df['代號'] = df['代號'].str.strip()
        df = df[df['代號'].astype(bool)].copy()
        provider_col = '提供者'
        if provider_col not in df.columns: df[provider_col] = ''
        if ta_helpers:
            df['連結'] = df.apply(lambda row: ta_helpers.get_static_link(row['代號'], row[provider_col]), axis=1)
        return df
    except Exception as e:
        logger.error(f"讀取試算表失敗: {e}")
        return pd.DataFrame()

# --- 6. Telegram 排程任務 ---
async def periodic_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    # 執行時重新獲取 ID，確保變數更新能即時生效
    target_id = safe_get_chat_id()
    if not target_id:
        logger.warning("‼️ 找不到目標 Chat ID，取消任務。")
        return
        
    logger.info(f"⏰ 啟動分析任務 (目標 ID: {target_id})")
    stock_df = fetch_stock_data_for_reminder()
    if stock_df.empty: return

    gc = get_google_sheets_client()
    if ANALYZE_FUNC:
        alerts = ANALYZE_FUNC(gc, SPREADSHEET_NAME, stock_df['代號'].tolist(), stock_df)
        if alerts:
            header = f"🔔 *技術指標警報 ({datetime.now(TAIPEI_TZ).strftime('%H:%M:%S')})*"
            await context.bot.send_message(chat_id=target_id, text=header, parse_mode='Markdown')
            for msg in alerts:
                try:
                    await context.bot.send_message(chat_id=target_id, text=msg, parse_mode='Markdown', disable_web_page_preview=True)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"發送失敗: {e}")

# --- 7. 指令處理 ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    current_id = update.effective_chat.id
    await update.message.reply_text(f"👋 您好！綁定成功。\n您的 Chat ID 是: `{current_id}`\n\n請確保此 ID 已填入 Railway 的 TELEGRAM_CHAT_ID 變數中。")

# --- 8. 排程設定 ---
def setup_scheduling(job_queue: JobQueue):
    # 亞洲盤
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0,30', 'hour': '8-13', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Asia')
    # 全球盤
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0', 'hour': '17,23', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Global')
    # 美股收盤
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0', 'hour': '5', 'day_of_week': 'sat', 'timezone': TAIPEI_TZ}, name='US_Close')

# --- 9. Web 服務 (解決 502 Bad Gateway) ---
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health_check():
    return jsonify({
        "status": "ok", 
        "chat_id_configured": safe_get_chat_id(),
        "server_time": datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M:%S'),
        "bot_token_loaded": bool(TELEGRAM_BOT_TOKEN)
    }), 200

def run_flask():
    # 優先讀取 Railway 分配的 PORT
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"🌐 Flask 伺服器啟動於 Port: {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# --- 10. 主程式入口 ---
def main():
    # 診斷環境變數
    diagnose_env()

    # 在後台線程啟動 Flask (這能解決 Railway 的 Public Networking 檢查)
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ 找不到 TELEGRAM_BOT_TOKEN，停止 Bot 啟動")
        while True: time.sleep(100) # 保持進程不退出
        return

    # 進入 Bot 運行循環 (含防衝突機制)
    while True:
        try:
            logger.info("⏳ 正在啟動 Bot (包含 10 秒衝突避讓延遲)...")
            time.sleep(10)
            
            application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
            setup_scheduling(application.job_queue)
            application.add_handler(CommandHandler("start", start_command))
            
            logger.info("📢 Telegram Bot 已上線")
            application.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)
            
        except Exception as e:
            if "Conflict" in str(e):
                logger.warning("⚠️ 偵測到 Bot 實例衝突，20 秒後自動重試...")
                time.sleep(20)
            else:
                logger.error(f"💥 Bot 運行崩潰: {e}")
                time.sleep(30)

if __name__ == '__main__':
    main()
