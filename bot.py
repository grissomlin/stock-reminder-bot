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

# --- 1. 設置日誌記錄 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 2. 環境變數診斷器 (啟動時自動執行) ---
def diagnose_env():
    print("\n" + "🚀" + "="*40)
    print("🔍 [Railway 環境變數診斷啟動]")
    
    # 診斷 TELEGRAM_BOT_TOKEN
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        print(f"✅ BOT_TOKEN: 已偵測 (長度: {len(token)}) -> {token[:5]}***{token[-5:]}")
    else:
        print("❌ BOT_TOKEN: 缺失！(請確認 Railway 變數名稱是否正確)")

    # 診斷 TELEGRAM_CHAT_ID
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if chat_id:
        clean_id = chat_id.strip().replace('"', '').replace("'", "")
        print(f"✅ CHAT_ID: 已偵測 -> [{clean_id}]")
        try:
            int(clean_id)
            print("   -> 格式檢查: 成功 (有效整數)")
        except:
            print("   -> ⚠️ 格式檢查: 失敗 (包含非數字字元，請檢查有無空格)")
    else:
        print("❌ CHAT_ID: 缺失！(這會導致排程無法發送訊息)")

    # 診斷 GOOGLE_CREDENTIALS
    g_creds = os.environ.get("GOOGLE_CREDENTIALS")
    if g_creds:
        print(f"✅ GOOGLE_CREDENTIALS: 已偵測 (長度: {len(g_creds)})")
        try:
            json.loads(g_creds)
            print("   -> 格式檢查: 成功 (有效 JSON)")
        except Exception as e:
            print(f"   -> ⚠️ 格式檢查: 失敗 (JSON 解析錯誤: {str(e)[:50]})")
    else:
        print("❌ GOOGLE_CREDENTIALS: 缺失！")
    
    print("🚀" + "="*40 + "\n")

# 立即執行診斷
diagnose_env()

# --- 3. 基礎參數設定 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ENV_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
SPREADSHEET_NAME = "雲端提醒"
TAIPEI_TZ = timezone('Asia/Taipei')

# --- 輔助函式：安全獲取 Chat ID ---
def safe_get_chat_id(val):
    if not val: return None
    try:
        return int(str(val).strip().replace('"', '').replace("'", ""))
    except: return None

# 全域變數
APPLICATION = None
USER_CHAT_ID = safe_get_chat_id(ENV_CHAT_ID)
ANALYZE_FUNC = None

# --- 4. 核心模組加載 ---
try:
    module_name = "ta_analyzer"
    module_path = os.path.join(current_dir, f"{module_name}.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    ta_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ta_module)
    ANALYZE_FUNC = ta_module.analyze_and_update_sheets
    
    module_name_h = "ta_helpers"
    module_path_h = os.path.join(current_dir, f"{module_name_h}.py")
    spec_h = importlib.util.spec_from_file_location(module_name_h, module_path_h)
    ta_h = importlib.util.module_from_spec(spec_h)
    spec_h.loader.exec_module(ta_h)
    ta_helpers = ta_h
    logger.info("✅ 核心分析模組加載成功")
except Exception as e:
    logger.error(f"❌ 模組載入失敗: {e}")
    def ANALYZE_FUNC(*args, **kwargs): return []

# --- 5. Google Sheets 邏輯 ---
def get_google_sheets_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        try:
            return gspread.service_account_from_dict(json.loads(creds_json))
        except: return None
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
        df = df[df['代號'].str.strip().astype(bool)].copy()
        df['代號'] = df['代號'].str.strip()
        provider_col = '提供者'
        if provider_col not in df.columns: df[provider_col] = ''
        df['連結'] = df.apply(lambda row: ta_helpers.get_static_link(row['代號'], row[provider_col]), axis=1)
        return df
    except Exception as e:
        logger.error(f"讀取試算表失敗: {e}")
        return pd.DataFrame()

# --- 6. Telegram 排程任務 ---
async def periodic_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    global USER_CHAT_ID
    # 執行時再次檢查 ID
    if not USER_CHAT_ID:
        USER_CHAT_ID = safe_get_chat_id(os.environ.get("TELEGRAM_CHAT_ID"))

    if not USER_CHAT_ID:
        logger.warning("‼️ 找不到目標 Chat ID，取消排程。")
        return
        
    logger.info(f"⏰ 啟動分析任務 (ID: {USER_CHAT_ID})")
    stock_df = fetch_stock_data_for_reminder()
    if stock_df.empty: return

    gc = get_google_sheets_client()
    alerts = ANALYZE_FUNC(gc, SPREADSHEET_NAME, stock_df['代號'].tolist(), stock_df)
    
    if alerts:
        header = f"🔔 *技術指標警報 ({datetime.now(TAIPEI_TZ).strftime('%H:%M:%S')})*"
        await context.bot.send_message(chat_id=USER_CHAT_ID, text=header, parse_mode='Markdown')
        for msg in alerts:
            try:
                await context.bot.send_message(chat_id=USER_CHAT_ID, text=msg, parse_mode='Markdown', disable_web_page_preview=True)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"發送失敗: {e}")

# --- 7. 指令處理 ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global USER_CHAT_ID
    current_id = update.effective_chat.id
    if not USER_CHAT_ID:
        USER_CHAT_ID = current_id
        await update.message.reply_text(f"綁定成功！\n此對話 ID 為: `{current_id}`")
    else:
        await update.message.reply_text(f"運行中！目前監聽: `{USER_CHAT_ID}`")

# --- 8. 排程設定 ---
def setup_scheduling(job_queue: JobQueue):
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0,30', 'hour': '8-13', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Asia')
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0', 'hour': '17,23', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Global')
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0', 'hour': '5', 'day_of_week': 'sat', 'timezone': TAIPEI_TZ}, name='US_Close')

# --- 9. Health Check ---
app = Flask(__name__)
@app.route('/health')
def health_check():
    return jsonify({
        "status": "ok", 
        "current_id": USER_CHAT_ID,
        "env_raw": os.environ.get("TELEGRAM_CHAT_ID")
    }), 200

# --- 10. 主程式入口 ---
def main():
    global APPLICATION
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ 找不到 TOKEN，切換為 Flask 模式保持運作")
        app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
        return

    APPLICATION = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    setup_scheduling(APPLICATION.job_queue)
    APPLICATION.add_handler(CommandHandler("start", start_command))

    logger.info(f"📢 Bot 啟動成功！")
    APPLICATION.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
