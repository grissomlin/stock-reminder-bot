# bot.py (最終運行穩定版 - 修正 NameError)

import os
import sys
import json
import logging
import asyncio
from datetime import datetime
import importlib.util 
from pytz import timezone 

# 🚨 修正：將所有核心 Telegram 類別集中到檔案頂部引入
from telegram import Update
from telegram.ext import (
    Application, 
    CommandHandler, 
    ContextTypes, 
    MessageHandler, 
    filters, 
    JobQueue
)
# 🚨 新增：Sheets 相關的導入
import gspread
import pandas as pd


# --- 設置日誌記錄 (Logging) ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


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

# 🚨 全域時區變數
TAIPEI_TZ = timezone('Asia/Taipei') 

# 全域變數
APPLICATION = None
USER_CHAT_ID = None
ANALYZE_FUNC = None 

# --- 核心模組加載 (保持不變) ---
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
    logger.error(f"FATAL ERROR: 無法使用 importlib 加載核心模組。請檢查檔案名稱和依賴是否正確。錯誤詳情: {e}")
    
    def ANALYZE_FUNC(*args, **kwargs):
        logger.error("FATAL ERROR: 技術分析模組加載失敗，無法執行任務。")
        return []
        
    class DummyHelpers:
        def get_static_link(*args, **kwargs):
            logger.error("FATAL ERROR: ta_helpers 模組加載失敗，連結功能無法使用。")
            return "連結失敗"
    ta_helpers = DummyHelpers()


# --- Google Sheets 基礎處理函數 (保持不變) ---
# 🚨 移除重複的導入語句 (import gspread, import pandas, from telegram...)


def get_google_sheets_client():
    # ... (此函數內容與原文件保持一致) ...
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
    # ... (此函數內容與原文件保持一致) ...
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
    # ... (此函數內容與原文件保持一致) ...
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
        logger.warning(f"從試算表讀取 Chat ID 時發生錯誤 (可能尚未設定或工作表不存在): {e}")
        return None

def fetch_stock_data_for_reminder(): 
    # ... (此函數內容與原文件保持一致) ...
    try:
        gc = get_google_sheets_client()
        if not gc:
            return pd.DataFrame()

        # 1. 讀取工作表1 (代號及其他數據)
        spreadsheet = gc.open(SPREADSHEET_NAME)
        worksheet1 = spreadsheet.worksheet("工作表1") 
        data1 = worksheet1.get_all_values()
        
        if not data1 or len(data1) < 2 or '代號' not in data1[0]:
            logger.warning("工作表1是空的或沒有代號欄位。")
            return pd.DataFrame()
        
        df = pd.DataFrame(data1[1:], columns=data1[0])
        df = df[df['代號'].astype(str).str.strip().astype(bool)].copy()
        df['代號'] = df['代號'].astype(str).str.strip()
        
        # 獲取 C 欄 (提供者) 的內容
        provider_column_name = '提供者' 
        if provider_column_name not in df.columns:
            logger.error(f"工作表1中找不到欄位 '{provider_column_name}'，連結功能將受限。")
            df[provider_column_name] = ''
        
        # 2. 使用 ta_helpers.get_static_link
        df['連結'] = df.apply(
            lambda row: ta_helpers.get_static_link(row['代號'], row[provider_column_name]),
            axis=1
        )
        
        logger.info(f"成功讀取 {len(df)} 個股票代號並使用靜態表/動態規則生成連結。")
        return df

    except Exception as e:
        logger.error(f"讀取試算表資料時發生錯誤: {e}")
        return pd.DataFrame()

# --- Telegram Bot 函數 (保持不變) ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (此函數內容與原文件保持一致) ...
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
    """定時執行，讀取 Sheets 資料，調用技術分析，並發送警報。"""
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

    logger.info(f"開始對 {len(stock_codes)} 個代號進行技術分析並更新 Sheets...")
    
    alerts = ANALYZE_FUNC(gc, SPREADSHEET_NAME, stock_codes, stock_df) 
    
    # 5. 彙整結果並發送通知 (循環獨立發送)
    if alerts:
        reminder_header = f"🔔 **🚨 技術指標警報啟動 ({datetime.now().strftime('%H:%M:%S')})**："
        
        await context.bot.send_message(
            chat_id=USER_CHAT_ID,
            text=reminder_header,
            parse_mode='Markdown'
        )
        
        for alert_message in alerts:
            try:
                await context.bot.send_message(
                    chat_id=USER_CHAT_ID, 
                    text=alert_message, 
                    parse_mode='Markdown'
                )
                await asyncio.sleep(0.5) 
            except Exception as e:
                logger.error(f"發送單一 Telegram 訊息失敗: {e}")
                
        logger.info(f"成功向 {USER_CHAT_ID} 發送 {len(alerts)} 個技術指標獨立警報。")
        
    else:
        # 🚨 移除成功通知
        logger.info("沒有股票符合警報條件。")


def setup_scheduling(job_queue):
    """
    設定多個市場的 Cron 排程。
    """
    # ----------------------------------------------------
    # 🎯 Cron 排程設定 (以台灣時間 Asia/Taipei 為準)
    # ----------------------------------------------------

    # 1. 亞洲盤交易時間 (週一到週五，08:30 到 13:30，每 30 分鐘)
    job_queue.add_job(periodic_reminder_job, 
                      'cron', 
                      minute='30,0', 
                      hour='8-13', 
                      day_of_week='mon-fri',
                      name='Asia Market Scan (08:30-13:30)')

    # 2. 歐洲盤開盤前/中段 (週一到週五，17:00)
    job_queue.add_job(periodic_reminder_job, 
                      'cron', 
                      minute='0', 
                      hour='17', 
                      day_of_week='mon-fri',
                      name='Europe Open Scan (17:00)')

    # 3. 晚盤掃描 (週一到週五，23:00)
    job_queue.add_job(periodic_reminder_job, 
                      'cron', 
                      minute='0', 
                      hour='23', 
                      day_of_week='mon-fri',
                      name='Late Scan (23:00)')

    # 4. 美股收盤數據獲取 (週五收盤後，台灣時間週六凌晨 04:00)
    job_queue.add_job(periodic_reminder_job, 
                      'cron', 
                      minute='0', 
                      hour='4', 
                      day_of_week='sat',
                      name='US Close Scan (Sat 04:00)')

    logger.info("✅ 已設定所有市場的 Cron 排程。")


def initialize_bot_and_scheduler(run_web_server=False):
    global APPLICATION

    if not TELEGRAM_BOT_TOKEN: 
        logger.error(f"無法啟動：{TELEGRAM_BOT_TOKEN_ENV} 環境變數未設定。")
        if not run_web_server:
            print("\n🚨 本地運行失敗提示：請在終端機中設定 TELEGRAM_BOT_TOKEN 環境變數。\n")
        return False

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    # 1. 定義任務預設值
    JOB_DEFAULTS = {'coalesce': True, 'max_instances': 3, 'misfire_grace_time': 100}

    # 🚨 修正步驟 1：手動創建帶有時區設定的 APScheduler
    scheduler = AsyncIOScheduler(timezone=TAIPEI_TZ, job_defaults=JOB_DEFAULTS)
    
    # 🚨 修正步驟 2：手動創建 JobQueue 實例
    job_queue_instance = JobQueue(scheduler=scheduler, application=None)

    # 🚨 修正步驟 3：將 JobQueue 實例傳入 Application.builder()
    # Application.builder() 接受 job_queue 參數，而不是 job_queue 實例本身
    APPLICATION = Application.builder().token(TELEGRAM_BOT_TOKEN).job_queue(job_queue_instance).build()

    # 🚨 修正步驟 4：將 Application 連結回 JobQueue
    job_queue_instance.set_application(APPLICATION)
    
    # 2. 設置 Cron 排程
    setup_scheduling(job_queue_instance) 
    
    async def start_scheduler_after_bot_init(app: Application):
        logger.info("排程器已準備就緒，等待 Application 啟動。")
            
    APPLICATION.post_init = start_scheduler_after_bot_init
    
    APPLICATION.add_handler(CommandHandler("start", start_command))
    APPLICATION.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    
    logger.info("Bot 和 APScheduler 物件建立成功。")
    return True

# --- Flask 服務用於 Railway 健康檢查 (保持不變) ---
from flask import Flask, jsonify
app = Flask(__name__)

@app.route('/health')
def health_check():
    if APPLICATION:
        return jsonify({"status": "ok", "message": "Bot is running and ready."}), 200
    else:
        return jsonify({"status": "error", "message": "Bot not initialized (check logs)."}), 500

if __name__ == '__main__':
    if TELEGRAM_BOT_TOKEN:
        
        # 本地運行模式 (使用 Polling)
        if not initialize_bot_and_scheduler(run_web_server=False):
            exit()
            
        logger.info("以本地 Polling 模式啟動 Bot...")
        
        try:
            logger.info("Bot 已啟動，請在 Telegram 中發送 /start (只需一次)。")
            APPLICATION.run_polling(poll_interval=1, allowed_updates=Update.ALL_TYPES)
            
        except KeyboardInterrupt:
            logger.info("程式已手動終止。")
            
        except Exception as e:
            logger.error(f"本地運行發生致命錯誤: {e}")
            
    else:
        # 部署模式 (如 Railway)
        if not initialize_bot_and_scheduler(run_web_server=True):
            logger.warning("Bot 初始化失敗，Flask 服務將啟動但 Bot 核心未運行。")
        
        # 啟動 Flask 服務以滿足 Railway 對 Web 服務的要求
        port = int(os.environ.get('PORT', 5000))
        logger.info(f"以 Web 模式 (Flask / Health Check) 啟動，監聽端口: {port}")
        app.run(host='0.0.0.0', port=port)
