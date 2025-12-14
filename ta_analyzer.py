# ta_analyzer.py (最終增強版 - 結構優化與純 Python 指標實作 + Sheets 健壯性修正)

import os
import time
import random
import logging
import gspread
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# 🚨 關鍵變更：導入純 Python 指標所需的函式庫
import yfinance as yf
from numba import njit # 使用 Numba 提升 KD 指標的迴圈效能
import ta_helpers  # 導入輔助模組

logger = logging.getLogger(__name__)

# === 純 Python / NumPy / Pandas 指標實作 (替代 TA-Lib) ===

def sma(arr, period):
    """計算簡單移動平均線 (Simple Moving Average)。"""
    return pd.Series(arr).rolling(period).mean().values

def macd(close, fast=12, slow=26, signal=9):
    """計算 MACD 指標 (完全等價於 TA-Lib 預設參數)。"""
    close = pd.Series(close)
    # 使用 EWM 計算 EMA
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    # 返回 NumPy 陣列，以保持與 TA-Lib 一致的格式
    return macd_line.values, signal_line.values, hist.values

@njit
def stoch(high, low, close, k_period=9, d_period=3):
    """
    計算 KD (Stochastic Oscillator) 指標。
    使用 Numba (njit) 提升迴圈效能。
    """
    n = len(close)
    k = np.full(n, np.nan)
    d = np.full(n, np.nan)

    # 1. 計算 %K (FastK)
    for i in range(k_period - 1, n):
        ll = np.min(low[i - k_period + 1:i + 1]) # K 週期內的最低價
        hh = np.max(high[i - k_period + 1:i + 1]) # K 週期內的最高價
        if hh - ll != 0:
            k[i] = 100 * (close[i] - ll) / (hh - ll)
    
    # Numba 複雜，這裡我們只返回 K 值
    return k

# === 參數設定 (與原文件保持一致) ===
CACHE_DIR = "cache_data"
os.makedirs(CACHE_DIR, exist_ok=True)
MIN_DATA_POINTS = 50
DOWNLOAD_RETRIES = 2
MIN_SLEEP_SEC = 0.5
MAX_SLEEP_SEC = 1.5
MAX_WORKERS = 5

# 擴展欄位映射
COLUMN_MAP = {
    # --- 核心數據 (D-I) ---
    'latest_close': 'D',
    'BIAS_Val': 'E',
    'LOW_DAYS': 'F',
    'HIGH_DAYS': 'G',
    'MA_TANGLE': 'H',
    'SLOPE_DESC': 'I',
    
    # --- 指標區塊 (J-AA) ---
    'KD_Signal': 'J',
    'KD_SWITCH': 'K',
    'KD_ALERT_DATE': 'L',
    'MACD_Signal': 'M',
    'MACD_SWITCH': 'N',
    'MACD_ALERT_DATE': 'O',
    'MA5_MA10_Sig': 'P',
    'MA5_MA10_SWITCH': 'Q',
    'MA5_MA10_ALERT_DATE': 'R',
    'MA5_MA20_Sig': 'S',
    'MA5_MA20_SWITCH': 'T',
    'MA5_MA20_ALERT_DATE': 'U',
    'MA10_MA20_Sig': 'V',
    'MA10_MA20_SWITCH': 'W',
    'MA10_MA20_ALERT_DATE': 'X',
    
    'BIAS_Sig': 'Y',        # 乖離率超買/超賣訊號 (結果)
    'BIAS_SWITCH': 'Z',     # 乖離率開關
    'BIAS_ALERT_DATE': 'AA',# 乖離率去重日期
    
    # --- 均線斜率數值 (AB-AD) ---
    'MA5_SLOPE': 'AB',
    'MA10_SLOPE': 'AC',
    'MA20_SLOPE': 'AD',
    # --- 總結警報資訊 (AE-AF) ---
    'Alert_Detail': 'AE',
    'alert_time': 'AF',
}

# === 輔助函式區域 (修復欄位字母轉換, 與原文件一致) ===
def excel_col_to_index(col_letter: str) -> int:
    """
    將 Excel/Sheets 欄位字母 (如 'A', 'Z', 'AA', 'AF') 轉換為 0-based 索引。
    """
    index = 0
    power = 0
    for letter in reversed(col_letter):
        index += (ord(letter) - ord('A') + 1) * (26 ** power)
        power += 1
    return index - 1

# --- 輔助函式區域 (下載, 與原文件一致) ---
def download_one_stock(ticker: str,
                       period: str = "90d",
                       cache_dir: str = CACHE_DIR) -> tuple[str, str, pd.DataFrame]:
    """ 下載單一股票的日K (1d)。"""
    cache_file = os.path.join(cache_dir, f"{ticker}_history.csv")
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    last_err = None
    
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            time.sleep(random.uniform(MIN_SLEEP_SEC, MAX_SLEEP_SEC))
            ticker_obj = yf.Ticker(ticker)
            data = ticker_obj.history(
                start=start_date,
                end=end_date,
                interval="1d",
                auto_adjust=True
            )
            if not data.index.name:
                data.index.name = 'Date'
            if data.empty or len(data) < MIN_DATA_POINTS:
                return ticker, "too_short", pd.DataFrame()
            data.to_csv(cache_file, index=True)
            return ticker, "ok", data
        except Exception as e:
            last_err = e
            logger.warning(f"⚠️ {ticker} 下載失敗（第 {attempt} 次）：{e}")
    return ticker, f"error:{last_err}", pd.DataFrame()

# --- 核心邏輯：分析與 Sheets 更新 ---
def analyze_and_update_sheets(gc: gspread.Client, spreadsheet_name: str, stock_codes: list, stock_df: pd.DataFrame) -> list:
    """
    對每個股票代號進行技術分析，使用 ta_helpers 進行獨立警報判斷，更新 Google Sheets，並返回警報清單。
    """
    alerts = []
    
    try:
        # 1. Sheets 初始化與數據讀取
        spreadsheet = gc.open(spreadsheet_name)
        worksheet = spreadsheet.worksheet("工作表1")
        all_values = worksheet.get_all_values()
        
        # 創建代號到 Row 索引的映射 (1-based row number)
        code_to_row = {row[0]: i + 2 for i, row in enumerate(all_values[1:]) if row and row[0]}
        
        # 2. 多執行緒下載日 K 數據
        downloaded_data = {}
        successful_tickers = []
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(download_one_stock, code): code for code in stock_codes}
            for future in as_completed(futures):
                ticker, status, data_df = future.result()
                if status == "ok":
                    downloaded_data[ticker] = data_df
                    successful_tickers.append(ticker)
                else:
                    logger.error(f"❌ {ticker} 下載/快取失敗，狀態：{status}")
        logger.info(f"下載完成。成功獲取 {len(successful_tickers)} 份日 K 數據。")
        
        # 3. 遍歷成功下載的數據並分析
        update_cells = []
        current_date = datetime.now().date()
        
        for code in successful_tickers:
            data = downloaded_data[code]  # 日 K 數據
            row_num = code_to_row[code]
            
            if len(data) < MIN_DATA_POINTS:
                logger.warning(f"數據 {code} 筆數不足，無法計算指標。")
                continue
            
            current_sheet_row = all_values[row_num - 1]
            
            # 構造包含所有舊值 (SWITCH/DATE) 的字典 (使用 excel_col_to_index)
            row_data_old = {}
            for map_key, col_letter in COLUMN_MAP.items():
                col_index = excel_col_to_index(col_letter)
                try:
                    # 🚨 修正點 1: 讀取 Sheets 數值時，先去除前後空格並轉為大寫，確保健壯性
                    raw_value = current_sheet_row[col_index].strip().upper()
                    row_data_old[map_key] = raw_value
                except IndexError:
                    row_data_old[map_key] = ''
            
            # 獲取額外信息
            original_row = stock_df[stock_df['代號'] == code].iloc[0]
            link = original_row.get('連結', '')
            
            try:
                # --- 純 Python 計算技術指標 (取代 TA-Lib) ---
                close_values = data['Close'].values
                high_values = data['High'].values
                low_values = data['Low'].values
                
                # 1. 計算 KD 基礎 K 值
                k_fast = stoch(high_values, low_values, close_values)
                
                # 2. 計算 KD 慢速線 (SlowK, SlowD) - 保持與 TA-Lib STOCH(9, 3, 3) 相同
                slowk = sma(k_fast[~np.isnan(k_fast)], 3)
                slowd = sma(slowk[~np.isnan(slowk)], 3)
                
                # 3. 計算 MACD
                macd_line, signal_line, hist = macd(close_values)
                
                # 4. 計算 SMA
                ma5 = sma(close_values, 5)
                ma10 = sma(close_values, 10)
                ma20 = sma(close_values, 20)
                
                latest_close = data['Close'].iloc[-1]
                
                # --- 獲取最新兩筆數據 ---
                valid_slowk = slowk[~np.isnan(slowk)]
                valid_slowd = slowd[~np.isnan(slowd)]
                
                # 檢查是否有足夠的歷史數據
                if len(valid_slowk) < 2 or len(valid_slowd) < 2:
                    logger.warning(f"數據 {code} KD 值不足兩筆，跳過 KD 交叉判斷。")
                    k_val, d_val, prev_k_val, prev_d_val = np.nan, np.nan, np.nan, np.nan
                else:
                    k_val, d_val, prev_k_val, prev_d_val = (
                        valid_slowk[-1], valid_slowd[-1], valid_slowk[-2], valid_slowd[-2]
                    )
                
                macd_val, signal_val, prev_macd_val, prev_signal_val = macd_line[-1], signal_line[-1], macd_line[-2], signal_line[-2]
                
                ma5_val, ma10_val, ma20_val = ma5[-1], ma10[-1], ma20[-1]
                prev_ma5_val, prev_ma10_val, prev_ma20_val = ma5[-2], ma10[-2], ma20[-2]
                
                # --- 交叉訊號判斷 (使用 ta_helpers) ---
                kd_sig, is_kd_alert = ta_helpers.check_cross_signal(k_val, d_val, prev_k_val, prev_d_val, "KD")
                macd_sig, is_macd_alert = ta_helpers.check_cross_signal(macd_val, signal_val, prev_macd_val, prev_signal_val, "MACD")
                
                ma5_10_sig, is_ma5_10_alert = ta_helpers.check_cross_signal(ma5_val, ma10_val, prev_ma5_val, prev_ma10_val, "MA5/10")
                ma5_20_sig, is_ma5_20_alert = ta_helpers.check_cross_signal(ma5_val, ma20_val, prev_ma5_val, prev_ma20_val, "MA5/20")
                ma10_20_sig, is_ma10_20_alert = ta_helpers.check_cross_signal(ma10_val, ma20_val, prev_ma10_val, prev_ma20_val, "MA10/20")
                
                # 乖離率計算
                ma10_val_safe = ma10[-1] if ma10[-1] and not np.isnan(ma10[-1]) else latest_close
                bias10_val = ((latest_close - ma10_val_safe) / ma10_val_safe) * 100 if ma10_val_safe else 0
                bias_sig_val = f"{bias10_val:.2f}%"
                
                bias_tolerance = 10.0
                is_bias_alert = False
                bias_alert_msg = "無訊號"
                if bias10_val > bias_tolerance:
                    is_bias_alert = True
                    bias_alert_msg = "乖離率 超買"
                elif bias10_val < -bias_tolerance:
                    is_bias_alert = True
                    bias_alert_msg = "乖離率 超賣"
                
                # --- 均線斜率與糾纏計算 (使用 ta_helpers) ---
                s5 = ta_helpers.calculate_slope(ma5)
                s10 = ta_helpers.calculate_slope(ma10)
                s20 = ta_helpers.calculate_slope(ma20)
                
                slope_desc = ta_helpers.get_slope_description(s5, s10, s20)
                tangle_state = ta_helpers.check_ma_tangle(ma5, ma10, ma20)
                
                # --- 極端點位計算 (使用 ta_helpers) ---
                current_low = data['Low'].iloc[-1]
                low_days_diff = ta_helpers.find_extreme_time_diff(data['Low'], current_low, 'LOW')
                
                ticker_obj = yf.Ticker(code)
                # 這裡使用 yfinance 抓取月線數據進行 HIGH_DAYS 判斷
                monthly_data = ticker_obj.history(period='2y', interval='1mo', auto_adjust=True)
                if monthly_data.empty or len(monthly_data) < 2:
                    high_days_diff = 999
                else:
                    current_high = monthly_data['High'].iloc[-1]
                    high_days_diff = ta_helpers.find_extreme_time_diff(monthly_data['High'], current_high, 'HIGH')
                
                # --- 新核心：單一訊號處理 (去重與開關) ---
                alert_msg_summary = []  # 用於 Sheets AE 欄總結
                
                # 1. KD 訊號
                ta_helpers.process_single_signal('KD', is_kd_alert, kd_sig, code, row_data_old, COLUMN_MAP, current_date, alerts, alert_msg_summary, update_cells, row_num, link)
                # 2. MACD 訊號
                ta_helpers.process_single_signal('MACD', is_macd_alert, macd_sig, code, row_data_old, COLUMN_MAP, current_date, alerts, alert_msg_summary, update_cells, row_num, link)
                # 3. MA5/10 訊號
                ta_helpers.process_single_signal('MA5_MA10', is_ma5_10_alert, ma5_10_sig, code, row_data_old, COLUMN_MAP, current_date, alerts, alert_msg_summary, update_cells, row_num, link)
                # 4. MA5/20 訊號
                ta_helpers.process_single_signal('MA5_MA20', is_ma5_20_alert, ma5_20_sig, code, row_data_old, COLUMN_MAP, current_date, alerts, alert_msg_summary, update_cells, row_num, link)
                # 5. MA10/20 訊號
                ta_helpers.process_single_signal('MA10_MA20', is_ma10_20_alert, ma10_20_sig, code, row_data_old, COLUMN_MAP, current_date, alerts, alert_msg_summary, update_cells, row_num, link)
                # 6. 乖離率訊號
                ta_helpers.process_single_signal('BIAS', is_bias_alert, bias_alert_msg, code, row_data_old, COLUMN_MAP, current_date, alerts, alert_msg_summary, update_cells, row_num, link)
                
                # --- 寫入核心數據 (D 欄到 I 欄) ---
                update_cells.append(((COLUMN_MAP['latest_close'], row_num), f"{latest_close:.2f}"))
                update_cells.append(((COLUMN_MAP['BIAS_Val'], row_num), bias_sig_val))
                update_cells.append(((COLUMN_MAP['LOW_DAYS'], row_num), str(low_days_diff)))
                update_cells.append(((COLUMN_MAP['HIGH_DAYS'], row_num), str(high_days_diff)))
                update_cells.append(((COLUMN_MAP['MA_TANGLE'], row_num), tangle_state))
                update_cells.append(((COLUMN_MAP['SLOPE_DESC'], row_num), slope_desc))
                # --- 寫入指標結果 (J 欄到 Y 欄) ---
                update_cells.append(((COLUMN_MAP['KD_Signal'], row_num), kd_sig))
                update_cells.append(((COLUMN_MAP['MACD_Signal'], row_num), macd_sig))
                update_cells.append(((COLUMN_MAP['MA5_MA10_Sig'], row_num), ma5_10_sig))
                update_cells.append(((COLUMN_MAP['MA5_MA20_Sig'], row_num), ma5_20_sig))
                update_cells.append(((COLUMN_MAP['MA10_MA20_Sig'], row_num), ma10_20_sig))
                update_cells.append(((COLUMN_MAP['BIAS_Sig'], row_num), bias_alert_msg))  # 乖離率訊號結果
                
                # --- 寫入斜率數值 (AB 欄到 AD 欄) ---
                update_cells.append(((COLUMN_MAP['MA5_SLOPE'], row_num), f"{s5:.4f}"))
                update_cells.append(((COLUMN_MAP['MA10_SLOPE'], row_num), f"{s10:.4f}"))
                update_cells.append(((COLUMN_MAP['MA20_SLOPE'], row_num), f"{s20:.4f}"))
                
                # --- 寫入總警報資訊 (AE 欄和 AF 欄) ---
                if alert_msg_summary:
                    current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    full_alert_detail = f"{' | '.join(alert_msg_summary)}"
                    update_cells.append(((COLUMN_MAP['alert_time'], row_num), current_time_str))  # AF
                    update_cells.append(((COLUMN_MAP['Alert_Detail'], row_num), full_alert_detail))  # AE
                
                # 寫入開關預設值 (確保新股或空欄位預設為 'ON')
                for sig_name in ['KD', 'MACD', 'MA5_MA10', 'MA5_MA20', 'MA10_MA20', 'BIAS']:
                    switch_key = f'{sig_name}_SWITCH'
                    # 🚨 修正點 2: 檢查 Sheets 讀取到的舊值是否為 ON 或 OFF
                    # 由於 raw_value 已經被 .strip().upper() 處理，這裡檢查 'ON' 和 'OFF' 即可
                    if row_data_old.get(switch_key) not in ('ON', 'OFF'):
                        # 如果是空值，或者值不是 ON/OFF，都將其預設為 ON
                        update_cells.append(((COLUMN_MAP[switch_key], row_num), 'ON'))
                
            except Exception as e:
                logger.error(f"分析 {code} 時發生錯誤: {e}")
                
        # 4. 執行批量寫入
        if update_cells:
            gspread_updates = [ (f"{col}{row}", val) for (col, row), val in update_cells ]
            updates = [ {'range': range_name, 'values': [[value]]} for range_name, value in gspread_updates ]
            worksheet.batch_update(updates)
            logger.info(f"成功批量更新 {len(update_cells)} 個儲存格到 Google Sheets。")
            
    except Exception as e:
        logger.error(f"技術分析主流程發生致命錯誤: {e}")
    return alerts

if __name__ == '__main__':
    print("這是技術分析模組，請通過 bot.py 運行。")