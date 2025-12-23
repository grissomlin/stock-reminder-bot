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

# === 1. 技術指標計算 (Numba 加速) ===

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

# === 2. 欄位映射 ===
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
    # --- 新增：建立代號與名稱的對照字典 ---
    # 這裡假設 stock_df 裡面已經有 '名稱' 欄位（bot.py 抓回來的）
    name_map = {}
    if '代號' in stock_df.columns and '名稱' in stock_df.columns:
        name_map = dict(zip(stock_df['代號'], stock_df['名稱']))

    try:
        sh = gc.open(spreadsheet_name)
        ws = sh.worksheet("工作表1")
        all_rows = ws.get_all_values()

        code_to_row = {}
        for idx, row in enumerate(all_rows[1:], start=2):
            if not row or not row[0]: continue
            raw_val = row[0]
            actual_code = raw_val.split('"')[-2] if '"' in raw_val else raw_val
            code_to_row[actual_code.strip()] = idx

        successful_data = {}
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(download_one_stock, code): code for code in stock_codes}
            for f in as_completed(futures):
                ticker, status, data = f.result()
                if status == "ok":
                    successful_data[ticker] = data

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
            
            update_cells.append({'range': f"{COLUMN_MAP['latest_close']}{row_idx}", 'values': [[round(float(c[-1]), 2)]]})
            
            link = ""
            if code in stock_df['代號'].values:
                link = stock_df[stock_df['代號'] == code]['連結'].values[0]
            
            if link:
                correct_formula = f'=HYPERLINK("{link}", "{code}")'
                update_cells.append({'range': f"A{row_idx}", 'values': [[correct_formula]]})
            
            # --- 修改處：在產生警報訊息前，先將名稱與代號組合 ---
            current_stock_name = name_map.get(code, "")
            display_name = f"{code} {current_stock_name}".strip()
            
            msg_list = []
            temp_alerts = [] # 用來暫存這次循環產生的警報
            
            for s_name, is_act, s_txt in [('KD', is_kd, kd_sig), ('MACD', is_macd, macd_sig)]:
                # 注意：這裡傳入的 code 改為 display_name
                ta_helpers.process_single_signal(
                    s_name, is_act, s_txt, display_name, row_map_old, COLUMN_MAP, 
                    taipei_now, temp_alerts, msg_list, update_cells, row_idx, link
                )
            
            # 將處理完帶有名稱的訊息加入最終列表
            alerts.extend(temp_alerts)

            s5 = ta_helpers.calculate_slope(ma5)
            update_cells.append({'range': f"{COLUMN_MAP['MA5_SLOPE']}{row_idx}", 'values': [[round(float(s5), 4)]]})
            
            if msg_list:
                update_cells.append({'range': f"{COLUMN_MAP['alert_time']}{row_idx}", 'values': [[taipei_now.strftime('%H:%M:%S')]]})
                update_cells.append({'range': f"{COLUMN_MAP['Alert_Detail']}{row_idx}", 'values': [[' | '.join(msg_list)]]})

        if update_cells:
            final_batch = []
            for item in update_cells:
                if isinstance(item, dict) and 'range' in item:
                    final_batch.append(item)
                elif isinstance(item, tuple) and len(item) == 2:
                    pos, val = item
                    cell_range = f"{pos[0]}{pos[1]}" if isinstance(pos, tuple) else str(pos)
                    final_batch.append({'range': cell_range, 'values': [[val]]})
            
            if final_batch:
                ws.batch_update(final_batch, value_input_option='USER_ENTERED')
                logger.info(f"✅ 更新完成，總處理 {len(successful_data)} 筆。")

    except Exception as e:
        logger.error(f"❌ 分析流程發生重大錯誤: {e}", exc_info=True)
    
    return alerts
