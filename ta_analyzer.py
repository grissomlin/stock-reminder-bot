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

# === 1. 技術指標計算 ===

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
    s = pd.Series(np.array(arr).flatten())
    if len(s) < period: return np.full(len(s), np.nan)
    return s.rolling(period).mean().values

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
        if not df.empty and len(df) >= 20:
            return clean_ticker, "ok", df
    except Exception as e:
        logger.warning(f"⚠️ {clean_ticker} 下載失敗: {e}")
    return clean_ticker, "error", None

# --- 4. 主分析函式 ---
def analyze_and_update_sheets(gc, spreadsheet_name, stock_codes, stock_df):
    alerts = []
    taipei_now = datetime.now(TAIPEI_TZ)
    current_date_obj = taipei_now.date()
    
    # 調試：顯示當前日期
    logger.info(f"📅 當前台北日期: {current_date_obj.strftime('%Y-%m-%d')}")

    try:
        sh = gc.open(spreadsheet_name)
        ws = sh.worksheet("工作表1")
        all_rows = ws.get_all_values()
        
        # 獲取表頭（第一行）
        headers = all_rows[0]
        
        # 建立中文欄位名稱到索引的映射
        header_to_index = {}
        for idx, header in enumerate(headers):
            header_to_index[header.strip()] = idx
            logger.debug(f"表頭索引 {idx}: {header.strip()}")
        
        # 調試：顯示重要的欄位索引
        important_fields = ['KD_通知開關', 'MACD_通知開關', 'KD_去重日期', 'MACD_去重日期']
        for field in important_fields:
            if field in header_to_index:
                logger.info(f"✅ 找到欄位: {field} -> 索引 {header_to_index[field]}")
            else:
                logger.warning(f"⚠️ 未找到欄位: {field}")

        # 建立股票代碼到行索引的映射
        code_to_row = {}
        for idx, row in enumerate(all_rows[1:], start=2):
            if not row or not row[0]: continue
            code = row[0].split('"')[-2] if '"' in row[0] else row[0]
            code_to_row[code.strip()] = idx

        successful_data = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(download_one_stock, c): c for c in stock_codes}
            for f in as_completed(futures):
                ticker, status, data = f.result()
                if status == "ok": successful_data[ticker] = data

        update_cells_raw = []
        for code, df in successful_data.items():
            row_idx = code_to_row.get(code)
            if not row_idx: continue

            try:
                # --- 核心強化：處理多維度數據提取 ---
                def get_clean_values(col_name):
                    col_data = df[col_name]
                    # 如果該欄位有多個 sub-column，取第一欄 (iloc[:, 0])
                    if len(col_data.shape) > 1:
                        return col_data.iloc[:, 0].values.flatten().astype(float)
                    return col_data.values.flatten().astype(float)

                c = get_clean_values('Close')
                h = get_clean_values('High')
                l = get_clean_values('Low')
                
                clean_index = df.index[-len(c):]
                series_low = pd.Series(l, index=clean_index)
                series_high = pd.Series(h, index=clean_index)
            except Exception as e:
                logger.error(f"❌ {code} 數據深度清洗失敗: {e}")
                continue

            # 讀取舊資料列（使用中文欄位名稱）
            old_row = all_rows[row_idx - 1]
            
            # 使用中文欄位名稱讀取數據
            row_data = {
                'KD_SWITCH': old_row[header_to_index.get('KD_通知開關', 10)] if len(old_row) > 10 else 'ON',
                'MACD_SWITCH': old_row[header_to_index.get('MACD_通知開關', 13)] if len(old_row) > 13 else 'ON',
                'MA5_MA10_SWITCH': old_row[header_to_index.get('MA5/10_通知開關', 16)] if len(old_row) > 16 else 'ON',
                'MA5_MA20_SWITCH': old_row[header_to_index.get('MA5/20_通知開關', 19)] if len(old_row) > 19 else 'ON',
                'MA10_MA20_SWITCH': old_row[header_to_index.get('MA10/20_通知開關', 22)] if len(old_row) > 22 else 'ON',
                'BIAS_SWITCH': old_row[header_to_index.get('乖離率_通知開關', 25)] if len(old_row) > 25 else 'ON',
                
                'KD_ALERT_DATE': old_row[header_to_index.get('KD_去重日期', 11)] if len(old_row) > 11 else '',
                'MACD_ALERT_DATE': old_row[header_to_index.get('MACD_去重日期', 14)] if len(old_row) > 14 else '',
                'MA5_MA10_ALERT_DATE': old_row[header_to_index.get('MA5/10_去重日期', 17)] if len(old_row) > 17 else '',
                'MA5_MA20_ALERT_DATE': old_row[header_to_index.get('MA5/20_去重日期', 20)] if len(old_row) > 20 else '',
                'MA10_MA20_ALERT_DATE': old_row[header_to_index.get('MA10/20_去重日期', 23)] if len(old_row) > 23 else '',
                'BIAS_ALERT_DATE': old_row[header_to_index.get('乖離率_去重日期', 26)] if len(old_row) > 26 else '',
                
                'LOW_DAYS': old_row[header_to_index.get('低點間隔天數', 5)] if len(old_row) > 5 else '999',
                'HIGH_DAYS': old_row[header_to_index.get('月高點間隔天數', 6)] if len(old_row) > 6 else '999',
                'MA_TANGLE': old_row[header_to_index.get('均線糾纏狀態', 7)] if len(old_row) > 7 else '不明',
                'SLOPE_DESC': old_row[header_to_index.get('趨勢斜率描述', 8)] if len(old_row) > 8 else '不明',
                'BIAS_Val': old_row[header_to_index.get('10日乖離率 (%)', 4)] if len(old_row) > 4 else '0.00%',
                'MA5_SLOPE': old_row[header_to_index.get('MA5 斜率數值', 27)] if len(old_row) > 27 else 'N/A',
                'MA10_SLOPE': old_row[header_to_index.get('MA10 斜率數值', 28)] if len(old_row) > 28 else 'N/A',
                'MA20_SLOPE': old_row[header_to_index.get('MA20 斜率數值', 29)] if len(old_row) > 29 else 'N/A',
            }
            
            # 添加調試日誌
            logger.info(f"📊 {code} - KD開關: {row_data['KD_SWITCH']}, KD上次日期: '{row_data['KD_ALERT_DATE']}'")
            logger.info(f"📊 {code} - MACD開關: {row_data['MACD_SWITCH']}, MACD上次日期: '{row_data['MACD_ALERT_DATE']}'")

            # 指標計算
            ma5, ma10, ma20 = sma(c, 5), sma(c, 10), sma(c, 20)
            slowk = sma(stoch(h, l, c), 3)
            slowd = sma(slowk, 3)
            macd_l, sig_l, _ = macd(c)

            # 訊號
            kd_sig, is_kd = ta_helpers.check_cross_signal(slowk[-1], slowd[-1], slowk[-2], slowd[-2], "KD")
            macd_sig, is_macd = ta_helpers.check_cross_signal(macd_l[-1], sig_l[-1], macd_l[-2], sig_l[-2], "MACD")

            # 斜率與輔助數值
            s5, s10, s20 = round(ta_helpers.calculate_slope(ma5), 4), round(ta_helpers.calculate_slope(ma10), 4), round(ta_helpers.calculate_slope(ma20), 4)
            tangle = ta_helpers.check_ma_tangle(ma5, ma10, ma20)
            slope_desc = ta_helpers.get_slope_description(s5, s10, s20)
            bias = f"{round(((c[-1] / ma20[-1]) - 1) * 100, 2)}%" if not np.isnan(ma20[-1]) else "N/A"

            row_data.update({
                'MA_TANGLE': tangle, 'SLOPE_DESC': slope_desc, 'BIAS_Val': bias,
                'MA5_SLOPE': str(s5), 'MA10_SLOPE': str(s10), 'MA20_SLOPE': str(s20),
                'LOW_DAYS': str(ta_helpers.find_extreme_time_diff(series_low, float(l[-1]), 'LOW')),
                'HIGH_DAYS': str(ta_helpers.find_extreme_time_diff(series_high, float(h[-1]), 'HIGH'))
            })

            provider = old_row[header_to_index.get('提供者', 2)] if len(old_row) > 2 else ""
            link = ta_helpers.get_static_link(code, provider)

            # 生成警報
            ta_helpers.process_single_signal('KD', is_kd, kd_sig, code, row_data, COLUMN_MAP, current_date_obj, alerts, [], update_cells_raw, row_idx, link)
            ta_helpers.process_single_signal('MACD', is_macd, macd_sig, code, row_data, COLUMN_MAP, current_date_obj, alerts, [], update_cells_raw, row_idx, link)

            # 輔助數據更新
            for k, v in [('latest_close', round(float(c[-1]), 2)), ('MA5_SLOPE', s5), ('MA10_SLOPE', s10), ('MA20_SLOPE', s20), ('BIAS_Val', bias), ('MA_TANGLE', tangle), ('SLOPE_DESC', slope_desc)]:
                update_cells_raw.append({'range': f"{COLUMN_MAP[k]}{row_idx}", 'values': [[v]]})

        # --- 格式統一轉換 ---
        final_updates = []
        for item in update_cells_raw:
            if isinstance(item, dict):
                final_updates.append(item)
            elif isinstance(item, tuple):
                (col_letter, row_num), val = item
                final_updates.append({'range': f"{col_letter}{row_num}", 'values': [[val]]})

        if final_updates:
            ws.batch_update(final_updates, value_input_option='USER_ENTERED')
            logger.info(f"✅ 分析任務圓滿完成，更新了 {len(final_updates)} 筆資料。")

    except Exception as e:
        logger.error(f"❌ 分析失敗: {e}", exc_info=True)
    
    return alerts
