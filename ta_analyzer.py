# -*- coding: utf-8 -*-
import os, time, random, logging, json
from datetime import datetime, timedelta
from pytz import timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import numpy as np
import yfinance as yf
# from numba import njit  # 暫時註解掉，避免型態衝突
import ta_helpers 

logger = logging.getLogger(__name__)
TAIPEI_TZ = timezone('Asia/Taipei')

# === 1. 技術指標計算 ===

def stoch(high, low, close, k_period=9):
    """
    計算隨機指標 (Stochastic Oscillator)
    """
    # 確保輸入是 1D Numpy Array 且為 float 型態
    h = np.array(high).flatten().astype(float)
    l = np.array(low).flatten().astype(float)
    c = np.array(close).flatten().astype(float)
    
    n = len(c)
    k = np.full(n, np.nan)
    
    for i in range(k_period - 1, n):
        ll = np.min(l[i - k_period + 1 : i + 1])
        hh = np.max(h[i - k_period + 1 : i + 1])
        if hh - ll != 0:
            k[i] = 100 * (c[i] - ll) / (hh - ll)
    return k

def sma(arr, period):
    if len(arr) < period: return np.full(len(arr), np.nan)
    return pd.Series(arr).rolling(period).mean().values

def macd(close, fast=12, slow=26, signal=9):
    # 確保資料為 1D
    c = pd.Series(np.array(close).flatten().astype(float))
    ema_fast = c.ewm(span=fast, adjust=False).mean()
    ema_slow = c.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line.values, signal_line.values, hist.values

# === 2. 欄位映射 ===
COLUMN_MAP = {
    'latest_close': 'D',
    'KD_Signal': 'J',     'KD_ALERT_DATE': 'L',
    'MACD_Signal': 'M',   'MACD_ALERT_DATE': 'O',
    'MA5_MA10_Sig': 'P',  'MA5_MA10_ALERT_DATE': 'R',
    'MA5_MA20_Sig': 'S',  'MA5_MA20_ALERT_DATE': 'U',
    'MA10_MA20_Sig': 'V', 'MA10_MA20_ALERT_DATE': 'X',
    'Alert_Detail': 'AE', 'alert_time': 'AF', 'MA5_SLOPE': 'AB'
}

def excel_col_to_index(col_letter):
    index = 0
    for i, letter in enumerate(reversed(col_letter.upper())):
        index += (ord(letter) - ord('A') + 1) * (26 ** i)
    return index - 1

# --- 3. 下載器 ---
def download_one_stock(ticker):
    # 處理可能的引號問題
    clean_ticker = ticker.split('"')[-2] if '"' in ticker else ticker
    clean_ticker = clean_ticker.strip()
    # 自動補齊台股代碼
    if clean_ticker.isdigit() and len(clean_ticker) <= 4: 
        clean_ticker += ".TW"
    
    try:
        # 下載半年資料確保指標計算正確
        df = yf.download(clean_ticker, period="6mo", interval="1d", progress=False, auto_adjust=True)
        if not df.empty and len(df) >= 20:
            return clean_ticker, "ok", df
    except Exception as e:
        logger.warning(f"⚠️ {clean_ticker} 下載失敗: {e}")
    return clean_ticker, "error", None

# --- 4. 主分析函式 ---
def analyze_and_update_sheets(gc, spreadsheet_name, stock_codes, stock_df):
    alerts = []
    taipei_now = datetime.now(TAIPEI_TZ)
    today_str = taipei_now.strftime('%Y-%m-%d')
    full_time_str = taipei_now.strftime('%Y-%m-%d %H:%M:%S')

    name_map = {}
    if '代號' in stock_df.columns and '名稱' in stock_df.columns:
        name_map = dict(zip(stock_df['代號'].str.strip(), stock_df['名稱']))

    try:
        sh = gc.open(spreadsheet_name)
        ws = sh.worksheet("工作表1")
        all_rows = ws.get_all_values()

        code_to_row = {}
        for idx, row in enumerate(all_rows[1:], start=2):
            if not row or not row[0]: continue
            code = row[0].split('"')[-2] if '"' in row[0] else row[0]
            code_to_row[code.strip()] = idx

        # 執行下載
        successful_data = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(download_one_stock, c): c for c in stock_codes}
            for f in as_completed(futures):
                ticker, status, data = f.result()
                if status == "ok": successful_data[ticker] = data

        update_cells = []
        for code, df in successful_data.items():
            row_idx = code_to_row.get(code)
            if not row_idx: continue

            old_row = all_rows[row_idx - 1]
            def get_old_val(key):
                idx = excel_col_to_index(COLUMN_MAP[key])
                return old_row[idx].strip() if idx < len(old_row) else ""

            # 指標計算
            c, h, l = df['Close'].values, df['High'].values, df['Low'].values
            
            # 計算 KD
            k_raw = stoch(h, l, c)
            slowk = sma(k_raw, 3)
            slowd = sma(slowk, 3)
            
            # 計算 MACD
            macd_l, sig_l, _ = macd(c)

            # 訊號判斷
            kd_sig, is_kd = ta_helpers.check_cross_signal(slowk[-1], slowd[-1], slowk[-2], slowd[-2], "KD")
            macd_sig, is_macd = ta_helpers.check_cross_signal(macd_l[-1], sig_l[-1], macd_l[-2], sig_l[-2], "MACD")

            display_name = f"{code} {name_map.get(code, '')}".strip()
            row_msgs = []

            # 1. KD 去重與更新
            if is_kd:
                last_kd_date = get_old_val('KD_ALERT_DATE')
                if today_str not in last_kd_date:
                    row_msgs.append(f"【KD {kd_sig}】")
                    update_cells.append({'range': f"{COLUMN_MAP['KD_ALERT_DATE']}{row_idx}", 'values': [[full_time_str]]})
                    update_cells.append({'range': f"{COLUMN_MAP['KD_Signal']}{row_idx}", 'values': [[kd_sig]]})

            # 2. MACD 去重與更新
            if is_macd:
                last_macd_date = get_old_val('MACD_ALERT_DATE')
                if today_str not in last_macd_date:
                    row_msgs.append(f"【MACD {macd_sig}】")
                    update_cells.append({'range': f"{COLUMN_MAP['MACD_ALERT_DATE']}{row_idx}", 'values': [[full_time_str]]})
                    update_cells.append({'range': f"{COLUMN_MAP['MACD_Signal']}{row_idx}", 'values': [[macd_sig]]})

            if row_msgs:
                msg_content = f"{display_name} " + " ".join(row_msgs)
                alerts.append(msg_content)
                update_cells.append({'range': f"{COLUMN_MAP['alert_time']}{row_idx}", 'values': [[full_time_str]]})
                update_cells.append({'range': f"{COLUMN_MAP['Alert_Detail']}{row_idx}", 'values': [[msg_content]]})

            # 更新現價
            try:
                current_price = float(c[-1])
                update_cells.append({'range': f"{COLUMN_MAP['latest_close']}{row_idx}", 'values': [[round(current_price, 2)]]})
            except:
                pass

        # 批次更新到 Google Sheets
        if update_cells:
            ws.batch_update(update_cells, value_input_option='USER_ENTERED')

    except Exception as e:
        logger.error(f"❌ 分析失敗: {e}", exc_info=True)
    
    return alerts
