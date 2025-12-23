# -*- coding: utf-8 -*-
import os, time, random, logging, json, threading
from datetime import datetime, timedelta
from pytz import timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import numpy as np
import gspread
import yfinance as yf
from numba import njit 
import ta_helpers  # 確保 ta_helpers.py 在同目錄下

logger = logging.getLogger(__name__)

# === 1. 技術指標計算 (Numba 加速與 Pandas 優化) ===

def sma(arr, period):
    if len(arr) < period: return np.full(len(arr), np.nan)
    return pd.Series(arr).rolling(period).mean().values

def macd(close, fast=12, slow=26, signal=9):
    close_s = pd.Series(close)
    ema_fast = close_s.ewm(span=fast, adjust=False).mean()
    ema_slow = close_s.ewm(span=slow, adjust=False).mean()
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

# === 2. 常數與欄位映射 ===
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

def excel_col_to_index(col_letter):
    index = 0
    for i, letter in enumerate(reversed(col_letter.upper())):
        index += (ord(letter) - ord('A') + 1) * (26 ** i)
    return index - 1

# --- 3. 單一股票下載器 ---
def download_one_stock(ticker):
    end_date = datetime.now() + timedelta(days=1)
    start_date = end_date - timedelta(days=100)
    
    # 清理公式，提取純代號
    clean_ticker = ticker.split('"')[-2] if '"' in ticker else ticker
    clean_ticker = clean_ticker.strip()
    
    for attempt in range(2):
        try:
            time.sleep(random.uniform(0.6, 1.5))
            t_obj = yf.Ticker(clean_ticker)
            df = t_obj.history(start=start_date.strftime('%Y-%m-%d'), 
                               end=end_date.strftime('%Y-%m-%d'), 
                               interval="1d", auto_adjust=True)
            if not df.empty and len(df) >= 10:
                return clean_ticker, "ok", df
        except Exception as e:
            logger.warning(f"⚠️ 下載 {clean_ticker} 失敗: {e}")
    return clean_ticker, "error", None

# --- 4. 主分析函式 ---
def analyze_and_update_sheets(gc, spreadsheet_name, stock_codes, stock_df):
    alerts = []
    try:
        sh = gc.open(spreadsheet_name)
        ws = sh.worksheet("工作表1")
        all_rows = ws.get_all_values()

        # A. 建立映射
        code_to_row = {}
        for idx, row in enumerate(all_rows[1:], start=2):
            if not row or not row[0]: continue
            raw_val = row[0]
            actual_code = raw_val.split('"')[-2] if '"' in raw_val else raw_val
            code_to_row[actual_code.strip()] = idx

        # B. 下載資料
        successful_data = {}
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(download_one_stock, code): code for code in stock_codes}
            for f in as_completed(futures):
                ticker, status, data = f.result()
                if status == "ok":
                    successful_data[ticker] = data

        # C. 逐一分析
        update_cells = []
        taipei_now = datetime.now(timezone('Asia/Taipei'))
        
        for code, df in successful_data.items():
            row_idx = code_to_row.get(code)
            if not row_idx: continue

            old_row_data = all_rows[row_idx - 1]
            row_map_old = {k: old_row_data[excel_col_to_index(v)].strip().upper() 
                           for k, v in COLUMN_MAP.items() if excel_col_to_index(v) < len(old_row_data)}

            c, h, l = df['Close'].values, df['High'].values, df['Low'].values
            k_raw = stoch(h, l, c)
            k_clean = k_raw[~np.isnan(k_raw)]
            if len(k_clean) < 3: continue
            
            slowk = sma(k_clean, 3)
            slowd = sma(slowk, 3)
            macd_l, sig_l, _ = macd(c)
            ma5 = sma(c, 5)
            
            kd_sig, is_kd = ta_helpers.check_cross_signal(slowk[-1], slowd[-1], slowk[-2], slowd[-2], "KD")
            macd_sig, is_macd = ta_helpers.check_cross_signal(macd_l[-1], sig_l[-1], macd_l[-2], sig_l[-2], "MACD")
            
            # 格式：{'range': 'D10', 'values': [[123.4]]}
            update_cells.append({'range': f"{COLUMN_MAP['latest_close']}{row_idx}", 'values': [[round(float(c[-1]), 2)]]})
            
            msg_list = []
            link = stock_df[stock_df['代號'] == code]['連結'].values[0] if code in stock_df['代號'].values else ""
            
            for s_name, is_act, s_txt in [('KD', is_kd, kd_sig), ('MACD', is_macd, macd_sig)]:
                # 此處呼叫 ta_helpers，它可能會傳入不同的資料格式到 update_cells
                ta_helpers.process_single_signal(
                    s_name, is_act, s_txt, code, row_map_old, COLUMN_MAP, 
                    taipei_now, alerts, msg_list, update_cells, row_idx, link
                )

            s5 = ta_helpers.calculate_slope(ma5)
            update_cells.append({'range': f"{COLUMN_MAP['MA5_SLOPE']}{row_idx}", 'values': [[round(float(s5), 4)]]})
            
            if msg_list:
                update_cells.append({'range': f"{COLUMN_MAP['alert_time']}{row_idx}", 'values': [[taipei_now.strftime('%H:%M:%S')]]})
                update_cells.append({'range': f"{COLUMN_MAP['Alert_Detail']}{row_idx}", 'values': [[' | '.join(msg_list)]]})

        # D. 核心格式修復：將 update_cells 轉換為 gspread 接受的格式
        if update_cells:
            final_batch = []
            for item in update_cells:
                # 如果已經是字典且格式正確
                if isinstance(item, dict) and 'range' in item:
                    final_batch.append(item)
                # 如果是元組 (('D', 10), 123.4) 或 (('D10'), 123.4)
                elif isinstance(item, tuple) and len(item) == 2:
                    pos, val = item
                    if isinstance(pos, tuple): # (('D', 10), 123.4)
                        cell_range = f"{pos[0]}{pos[1]}"
                    else: # ('D10', 123.4)
                        cell_range = str(pos)
                    final_batch.append({'range': cell_range, 'values': [[val]]})
            
            if final_batch:
                ws.batch_update(final_batch)
                logger.info(f"✅ 成功批量更新 {len(successful_data)} 檔資料至 Google Sheets")

    except Exception as e:
        logger.error(f"❌ 分析流程發生重大錯誤: {e}", exc_info=True)
    
    return alerts
