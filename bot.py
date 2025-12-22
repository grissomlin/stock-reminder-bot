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

# --- 2. 環境變數全清單診斷器 ---
def diagnose_env():
    print("\n" + "🚀" + "="*50)
    print("🔍 [Railway 全環境變數清單掃描]")
    
    # 定義需要遮蔽的敏感關鍵字
    sensitive_keywords = ['TOKEN', 'KEY', 'CREDENTIALS', 'PASSWORD', 'SECRET', 'AUTH', 'PWD']
    
    # 取得並排序所有環境變數名稱
    env_keys = sorted(os.environ.keys())
    
    for key in env_keys:
        value = os.environ.get(key)
        
        # 檢查是否為敏感資訊
        is_sensitive = any(keyword in key.upper() for keyword in sensitive_keywords)
        
        if is_sensitive:
            # 敏感資訊：只顯示頭尾與長度
            if value and len(value) > 8:
                display_value = f"{value[:4]}***{value[-4:]} (長度: {len(value)})"
            else:
                display_value = "********"
        else:
            # 一般資訊：直接顯示
            display_value = value
            
        print(f"🔹 {key}: {display_value}")

    print("\n🎯 [核心變數專項檢查]")
    # 核心檢查邏輯
    target_id = os.environ.get("TELEGRAM_CHAT_ID")
    if target_id:
        clean_id = target_id.strip().replace('"', '').replace("'", "")
        print(f"✅ TELEGRAM_CHAT_ID: [{clean_id}] (格式: {'純數字' if clean_id.replace('-','').isdigit() else '非純數字，請檢查！'})")
    else:
        print("❌ TELEGRAM_CHAT_ID: 缺失！")

    print("🚀" + "="*50 + "\n")

# 在程式最前端執行診斷
diagnose_env()

# --- 3. 基礎參數設定 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SPREADSHEET_NAME = "雲端提醒"
TAIPEI_TZ = timezone('Asia/Taipei')

# --- 輔助函式：安全獲取 Chat ID (支援多種 Key 備援) ---
def safe_get_chat_id():
    # 同時嘗試多種可能的名字
    val = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("CHAT_ID")
    if not val: return None
    try:
        return int(str(val).strip().replace('"', '').replace("'", ""))
    except: return None

# 全域變數
APPLICATION = None
USER_CHAT_ID = safe_get_chat_id()
ANALYZE_FUNC = None

# --- 4. 核心模組動態加載 ---
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
    if not USER_CHAT_ID:
        USER_CHAT_ID = safe_get_chat_id()

    if not USER_CHAT_ID:
        logger.warning("‼️ 找不到目標 Chat ID，取消排程。")
        return
        
    logger.info(f"⏰ 啟動分析任務 (目標 ID: {USER_CHAT_ID})")
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
    USER_CHAT_ID = current_id
    await update.message.reply_text(f"綁定成功！\n此對話 ID 為: `{current_id}`")

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
        logger.error("❌ 找不到 TOKEN，啟動 Flask 模式")
        app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
        return

    APPLICATION = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    setup_scheduling(APPLICATION.job_queue)
    APPLICATION.add_handler(CommandHandler("start", start_command))

    logger.info(f"📢 Bot 啟動成功！")
    APPLICATION.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
