import os
import time
import random
import logging
import gspread
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# 導入必要的函式庫
import yfinance as yf
from numba import njit 
import ta_helpers  # 確保您有此輔助模組

logger = logging.getLogger(__name__)

# === 指標實作 (保持不變) ===

def sma(arr, period):
    return pd.Series(arr).rolling(period).mean().values

def macd(close, fast=12, slow=26, signal=9):
    close = pd.Series(close)
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line.values, signal_line.values, hist.values

@njit
def stoch(high, low, close, k_period=9):
    n = len(close)
    k = np.full(n, np.nan)
    for i in range(k_period - 1, n):
        ll = np.min(low[i - k_period + 1:i + 1])
        hh = np.max(high[i - k_period + 1:i + 1])
        if hh - ll != 0:
            k[i] = 100 * (close[i] - ll) / (hh - ll)
    return k

# === 參數設定 ===
CACHE_DIR = "cache_data"
os.makedirs(CACHE_DIR, exist_ok=True)
MIN_DATA_POINTS = 50
DOWNLOAD_RETRIES = 2
MIN_SLEEP_SEC = 0.5
MAX_SLEEP_SEC = 1.5
MAX_WORKERS = 5

COLUMN_MAP = {
    'latest_close': 'D', 'BIAS_Val': 'E', 'LOW_DAYS': 'F', 'HIGH_DAYS': 'G',
    'MA_TANGLE': 'H', 'SLOPE_DESC': 'I', 'KD_Signal': 'J', 'KD_SWITCH': 'K',
    'KD_ALERT_DATE': 'L', 'MACD_Signal': 'M', 'MACD_SWITCH': 'N', 'MACD_ALERT_DATE': 'O',
    'MA5_MA10_Sig': 'P', 'MA5_MA10_SWITCH': 'Q', 'MA5_MA10_ALERT_DATE': 'R',
    'MA5_MA20_Sig': 'S', 'MA5_MA20_SWITCH': 'T', 'MA5_MA20_ALERT_DATE': 'U',
    'MA10_MA20_Sig': 'V', 'MA10_MA20_SWITCH': 'W', 'MA10_MA20_ALERT_DATE': 'X',
    'BIAS_Sig': 'Y', 'BIAS_SWITCH': 'Z', 'BIAS_ALERT_DATE': 'AA',
    'MA5_SLOPE': 'AB', 'MA10_SLOPE': 'AC', 'MA20_SLOPE': 'AD',
    'Alert_Detail': 'AE', 'alert_time': 'AF',
}

def excel_col_to_index(col_letter: str) -> int:
    index = 0
    power = 0
    for letter in reversed(col_letter):
        index += (ord(letter) - ord('A') + 1) * (26 ** power)
        power += 1
    return index - 1

def download_one_stock(ticker: str, cache_dir: str = CACHE_DIR) -> tuple:
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    last_err = None
    
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            time.sleep(random.uniform(MIN_SLEEP_SEC, MAX_SLEEP_SEC))
            ticker_obj = yf.Ticker(ticker)
            data = ticker_obj.history(start=start_date, end=end_date, interval="1d", auto_adjust=True)
            if data.empty or len(data) < MIN_DATA_POINTS:
                return ticker, "too_short", pd.DataFrame()
            return ticker, "ok", data
        except Exception as e:
            last_err = e
    return ticker, f"error:{last_err}", pd.DataFrame()

def analyze_and_update_sheets(gc: gspread.Client, spreadsheet_name: str, stock_codes: list, stock_df: pd.DataFrame) -> list:
    alerts = []
    try:
        spreadsheet = gc.open(spreadsheet_name)
        worksheet = spreadsheet.worksheet("工作表1")
        all_values = worksheet.get_all_values()
        
        # --- 修正後的 A 欄代號提取邏輯 ---
        code_to_row = {}
        for i, row in enumerate(all_values[1:]): # 跳過標題列
            if row and row[0]:
                raw_a = row[0]
                if '=HYPERLINK' in raw_a.upper():
                    try:
                        # 格式通常為 =HYPERLINK("url", "code")，取最後一個引號組
                        actual_code = raw_a.split('"')[-2]
                        code_to_row[actual_code] = i + 2
                    except:
                        code_to_row[raw_a] = i + 2
                else:
                    code_to_row[raw_a] = i + 2

        # 多執行緒下載
        downloaded_data = {}
        successful_tickers = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(download_one_stock, code): code for code in stock_codes}
            for future in as_completed(futures):
                ticker, status, data_df = future.result()
                if status == "ok":
                    downloaded_data[ticker] = data_df
                    successful_tickers.append(ticker)

        update_cells = []
        current_date = datetime.now().date()
        
        for code in successful_tickers:
            data = downloaded_data[code]
            row_num = code_to_row.get(code)
            if not row_num: continue
            
            # 取得該列舊資料以比對訊號
            current_sheet_row = all_values[row_num - 1]
            row_data_old = {k: current_sheet_row[excel_col_to_index(v)].strip().upper() 
                            for k, v in COLUMN_MAP.items() if excel_col_to_index(v) < len(current_sheet_row)}
            
            # 獲取正確的連結
            row_info = stock_df[stock_df['代號'] == code]
            if not row_info.empty:
                link = row_info.iloc[0].get('連結', '')
                # --- 🚀 關鍵修正：確保寫入乾淨的單一超連結公式 ---
                if link:
                    hyperlink_formula = f'=HYPERLINK("{link}", "{code}")'
                    update_cells.append((('A', row_num), hyperlink_formula))

            # 指標計算
            close_vals = data['Close'].values
            high_vals = data['High'].values
            low_vals = data['Low'].values
            
            k_fast = stoch(high_vals, low_vals, close_vals)
            k_clean = k_fast[~np.isnan(k_fast)]
            slowk = sma(k_clean, 3)
            slowd = sma(slowk, 3)
            
            macd_l, sig_l, _ = macd(close_vals)
            ma5 = sma(close_vals, 5)
            ma10 = sma(close_vals, 10)
            ma20 = sma(close_vals, 20)
            
            # 訊號判斷
            kd_sig, is_kd = ta_helpers.check_cross_signal(slowk[-1], slowd[-1], slowk[-2], slowd[-2], "KD")
            macd_sig, is_macd = ta_helpers.check_cross_signal(macd_l[-1], sig_l[-1], macd_l[-2], sig_l[-2], "MACD")
            
            # 乖離率與斜率
            latest_c = close_vals[-1]
            bias = ((latest_c - ma10[-1]) / ma10[-1]) * 100 if ma10[-1] else 0
            s5 = ta_helpers.calculate_slope(ma5)
            s10 = ta_helpers.calculate_slope(ma10)
            s20 = ta_helpers.calculate_slope(ma20)
            
            alert_msg_summary = []
            # 處理訊號與發送通知
            for s_name, is_a, s_txt in [('KD', is_kd, kd_sig), ('MACD', is_macd, macd_sig)]:
                ta_helpers.process_single_signal(s_name, is_a, s_txt, code, row_data_old, COLUMN_MAP, current_date, alerts, alert_msg_summary, update_cells, row_num, link if 'link' in locals() else "")

            # 填充更新清單
            update_cells.append(((COLUMN_MAP['latest_close'], row_num), round(float(latest_c), 2)))
            update_cells.append(((COLUMN_MAP['MA5_SLOPE'], row_num), round(float(s5), 4)))
            update_cells.append(((COLUMN_MAP['MA_TANGLE'], row_num), ta_helpers.check_ma_tangle(ma5, ma10, ma20)))
            
            if alert_msg_summary:
                update_cells.append(((COLUMN_MAP['alert_time'], row_num), datetime.now().strftime('%H:%M:%S')))
                update_cells.append(((COLUMN_MAP['Alert_Detail'], row_num), ' | '.join(alert_msg_summary)))

        # 執行批量更新
        if update_cells:
            batch = [{'range': f"{c}{r}", 'values': [[v]]} for (c, r), v in update_cells]
            worksheet.batch_update(batch)
            logger.info(f"✅ 已完成 {len(successful_tickers)} 檔分析。A 欄超連結已優化。")
            
    except Exception as e:
        logger.error(f"分析流程錯誤: {e}", exc_info=True)
    return alerts

if __name__ == '__main__':
    print("請通過 bot.py 運行此分析程式。")
