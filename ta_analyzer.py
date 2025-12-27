# -*- coding: utf-8 -*-
import os, time, logging, json
from datetime import datetime
from pytz import timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import numpy as np
import yfinance as yf
import ta_helpers 

logger = logging.getLogger(__name__)
TAIPEI_TZ = timezone('Asia/Taipei')

# === 1. 技術指標計算 (移除 Numba 以確保穩定) ===

def stoch(high, low, close, k_period=9):
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
    'Alert_Detail': 'AE', 'alert_time': 'AF', 
    'MA5_SLOPE': 'AB',    'MA10_SLOPE': 'AC',    'MA20_SLOPE': 'AD',
    'MA_TANGLE': 'Z',     'SLOPE_DESC': 'AA',    'BIAS_Val': 'Y'
}

def excel_col_to_index(col_letter):
    index = 0
    for i, letter in enumerate(reversed(col_letter.upper())):
        index += (ord(letter) - ord('A') + 1) * (26 ** i)
    return index - 1

# --- 3. 下載器 ---
def download_one_stock(ticker):
    clean_ticker = ticker.split('"')[-2] if '"' in ticker else ticker
    clean_ticker = clean_ticker.strip()
    if clean_ticker.isdigit() and len(clean_ticker) <= 4: clean_ticker += ".TW"
    try:
        df = yf.download(clean_ticker, period="6mo", interval="1d", progress=False, auto_adjust=True)
        if not df.empty and len(df) >= 30:
            return clean_ticker, "ok", df
    except Exception as e:
        logger.warning(f"⚠️ {clean_ticker} 下載失敗: {e}")
    return clean_ticker, "error", None

# --- 4. 主分析函式 ---
def analyze_and_update_sheets(gc, spreadsheet_name, stock_codes, stock_df):
    alerts = []
    taipei_now = datetime.now(TAIPEI_TZ)
    current_date_obj = taipei_now.date()
    full_time_str = taipei_now.strftime('%Y-%m-%d %H:%M:%S')

    try:
        sh = gc.open(spreadsheet_name)
        ws = sh.worksheet("工作表1")
        all_rows = ws.get_all_values()

        code_to_row = {}
        for idx, row in enumerate(all_rows[1:], start=2):
            if not row or not row[0]: continue
            code = row[0].split('"')[-2] if '"' in row[0] else row[0]
            code_to_row[code.strip()] = idx

        successful_data = {}
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(download_one_stock, c): c for c in stock_codes}
            for f in as_completed(futures):
                ticker, status, data = f.result()
                if status == "ok": successful_data[ticker] = data

        update_cells = []
        for code, df in successful_data.items():
            row_idx = code_to_row.get(code)
            if not row_idx: continue

            # 取得舊資料列以讀取開關狀態
            old_row = all_rows[row_idx - 1]
            row_data = {}
            for key, col in COLUMN_MAP.items():
                col_idx = excel_col_to_index(col)
                row_data[key] = old_row[col_idx] if col_idx < len(old_row) else ""
            
            # 特別補充開關欄位 (假設開關在 K, N 等位置，請根據試算表實際位置調整)
            row_data['KD_SWITCH'] = old_row[excel_col_to_index('K')] if len(old_row) > 10 else 'ON'
            row_data['MACD_SWITCH'] = old_row[excel_col_to_index('N')] if len(old_row) > 13 else 'ON'

            # 計算指標
            c = df['Close'].values
            ma5, ma10, ma20 = sma(c, 5), sma(c, 10), sma(c, 20)
            
            # 計算 KD & MACD
            slowk = sma(stoch(df['High'].values, df['Low'].values, c), 3)
            slowd = sma(slowk, 3)
            macd_l, sig_l, _ = macd(c)

            # 訊號判斷
            kd_sig, is_kd = ta_helpers.check_cross_signal(slowk[-1], slowd[-1], slowk[-2], slowd[-2], "KD")
            macd_sig, is_macd = ta_helpers.check_cross_signal(macd_l[-1], sig_l[-1], macd_l[-2], sig_l[-2], "MACD")

            # 計算輔助數值 (斜率、乖離率)
            s5 = round(ta_helpers.calculate_slope(ma5), 4)
            s10 = round(ta_helpers.calculate_slope(ma10), 4)
            s20 = round(ta_helpers.calculate_slope(ma20), 4)
            tangle = ta_helpers.check_ma_tangle(ma5, ma10, ma20)
            slope_desc = ta_helpers.get_slope_description(s5, s10, s20)
            bias = f"{round(((c[-1] / ma20[-1]) - 1) * 100, 2)}%"

            # 更新 row_data 供 helper 使用最新數值
            row_data.update({
                'MA_TANGLE': tangle, 'SLOPE_DESC': slope_desc, 'BIAS_Val': bias,
                'MA5_SLOPE': s5, 'MA10_SLOPE': s10, 'MA20_SLOPE': s20
            })

            # 獲取連結
            provider = old_row[excel_col_to_index('B')] if len(old_row) > 1 else ""
            link = ta_helpers.get_static_link(code, provider)

            # 呼叫 Helper 處理警報 (這會生成詳細的 Telegram 訊息並加入 alerts)
            summary_msgs = []
            
            # 處理 KD
            ta_helpers.process_single_signal(
                'KD', is_kd, kd_sig, code, row_data, COLUMN_MAP, 
                current_date_obj, alerts, summary_msgs, update_cells, row_idx, link
            )
            
            # 處理 MACD
            ta_helpers.process_single_signal(
                'MACD', is_macd, macd_sig, code, row_data, COLUMN_MAP, 
                current_date_obj, alerts, summary_msgs, update_cells, row_idx, link
            )

            # 準備寫入試算表的更新數據
            update_cells.append({'range': f"{COLUMN_MAP['latest_close']}{row_idx}", 'values': [[round(float(c[-1]), 2)]]})
            update_cells.append({'range': f"{COLUMN_MAP['MA5_SLOPE']}{row_idx}", 'values': [[s5]]})
            update_cells.append({'range': f"{COLUMN_MAP['MA10_SLOPE']}{row_idx}", 'values': [[s10]]})
            update_cells.append({'range': f"{COLUMN_MAP['MA20_SLOPE']}{row_idx}", 'values': [[s20]]})
            update_cells.append({'range': f"{COLUMN_MAP['BIAS_Val']}{row_idx}", 'values': [[bias]]})
            update_cells.append({'range': f"{COLUMN_MAP['MA_TANGLE']}{row_idx}", 'values': [[tangle]]})
            update_cells.append({'range': f"{COLUMN_MAP['SLOPE_DESC']}{row_idx}", 'values': [[slope_desc]]})

        # 批次執行 Sheets 更新
        if update_cells:
            # 轉換格式以符合 gspread batch_update
            ws.batch_update(update_cells, value_input_option='USER_ENTERED')

    except Exception as e:
        logger.error(f"❌ 分析失敗: {e}", exc_info=True)
    
    return alerts
