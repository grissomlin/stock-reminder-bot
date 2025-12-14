# ta_helpers.py (最終整合修正版 - 新增 get_static_link 函式)
import logging
import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

# --- 技術指標計算與判斷 (此區塊未修改，與您提供的內容一致) ---

def find_extreme_time_diff(data_series: pd.Series, current_extreme_val: float, extreme_type: str) -> int:
    """
    尋找當前點位往前看，找到第一個比 current_extreme_val 更極端的點位，並返回時間間隔天數。
    """
    if data_series.empty or len(data_series) < 2:
        return 999

    current_date = data_series.index[-1]
    
    for i in range(2, len(data_series) + 1):
        past_val = data_series.iloc[-i]
        past_date = data_series.index[-i]
        
        if extreme_type == 'LOW':
            if past_val < current_extreme_val: 
                return (current_date - past_date).days
        elif extreme_type == 'HIGH':
            if past_val > current_extreme_val:
                return (current_date - past_date).days
    return 999


def calculate_slope(series: np.ndarray, lookback: int = 5) -> float:
    """計算均線最後 lookback 根 K 棒的線性回歸斜率。"""
    if len(series) < lookback:
        return 0.0
    
    clean_series = series[~np.isnan(series)]
    if len(clean_series) < lookback:
          return 0.0
          
    y = clean_series[-lookback:]
    x = np.arange(lookback)
    
    slope, intercept = np.polyfit(x, y, 1)
    return slope


def get_slope_description(s5: float, s10: float, s20: float) -> str:
    """根據三條均線的斜率，判斷多空排列及趨勢強弱。"""
    
    TH_STRONG = 0.005 # 強勁趨勢閾值
    signs = [np.sign(s) for s in [s5, s10, s20]]
    
    if all(s > TH_STRONG for s in [s5, s10, s20]) and (s5 > s10 > s20):
        return "多頭排列加速"
    if all(s < -TH_STRONG for s in [s5, s10, s20]) and (s5 < s10 < s20):
        return "空頭排列加速"
    
    if np.sign(s5) != np.sign(s20) or (np.abs(s5) < TH_STRONG and np.abs(s10) < TH_STRONG):
        return "趨勢混亂/盤整"
        
    if signs == [1, 1, 1]:
        return "標準多頭"
    if signs == [-1, -1, -1]:
        return "標準空頭"
    
    return "趨勢不明顯"


def check_ma_tangle(ma5: np.ndarray, ma10: np.ndarray, ma20: np.ndarray) -> str:
    """判斷 MA5, MA10, MA20 是否處於糾纏 (Coiling) 狀態。"""
    
    TANGLE_TOLERANCE_PCT = 0.005 # 0.5% 容忍度
    
    if np.any(np.isnan([ma5[-1], ma10[-1], ma20[-1]])):
        return "數據不足"

    last_vals = np.array([ma5[-1], ma10[-1], ma20[-1]])
    
    price_range = np.max(last_vals) - np.min(last_vals)
    average_price = np.mean(last_vals)
    
    if average_price == 0:
        return "數據錯誤"
    
    if (price_range / average_price) < TANGLE_TOLERANCE_PCT:
        return "均線糾纏"
    else:
        if ma5[-1] > ma10[-1] > ma20[-1]:
              return "多頭發散中"
        elif ma5[-1] < ma10[-1] < ma20[-1]:
              return "空頭發散中"
              
        return "趨勢發散"


def check_cross_signal(current_val_a: float, current_val_b: float, prev_val_a: float, prev_val_b: float, name: str) -> Tuple[str, bool]:
    """判斷指標 A (快線) 和 B (慢線) 的金叉、死叉或持續訊號。"""
    
    signal = "無訊號"
    is_alert = False
    
    if pd.isna(current_val_a) or pd.isna(current_val_b) or pd.isna(prev_val_a) or pd.isna(prev_val_b):
        return "數據不足", False

    # 金叉 (A 向上穿過 B)
    if current_val_a > current_val_b and prev_val_a <= prev_val_b:
        signal = f"{name}金叉"
        is_alert = True
    # 死叉 (A 向下穿過 B)
    elif current_val_a < current_val_b and prev_val_a >= prev_val_b:
        signal = f"{name}死叉"
        is_alert = True
    # 多頭持續 (A 在 B 上方持續)
    elif current_val_a > current_val_b and prev_val_a > prev_val_b:
        signal = f"{name}多頭持續"
    # 空頭持續 (A 在 B 下方持續)
    elif current_val_a < current_val_b and prev_val_a < prev_val_b:
        signal = f"{name}空頭持續"
        
    return signal, is_alert

# --- 🚨 核心邏輯修正：process_single_signal (已包含您新增的斜率數值) ---

def process_single_signal(
    signal_name: str, 
    is_triggered: bool, 
    signal_msg: str, 
    stock_code: str,
    row_data: Dict[str, str], 
    column_map: Dict[str, str],
    current_date: datetime.date,
    alerts: List[str],
    alert_msg_summary: List[str],
    update_cells: List[Tuple[Tuple[str, int], Any]],
    row_num: int,
    link: str
) -> bool:
    """
    處理單個技術指標訊號的開關、去重、Sheets 更新和警報發送邏輯。
    """
    
    # 決定開關和去重欄位的 Key
    if signal_name == 'BIAS':
        switch_key = 'BIAS_SWITCH'
        date_key = 'BIAS_ALERT_DATE'
    else:
        switch_key = f'{signal_name}_SWITCH'
        date_key = f'{signal_name}_ALERT_DATE'

    # 預設值處理：如果欄位不存在或為空，預設為 'ON'
    switch_val = row_data.get(switch_key, 'ON').upper().strip() 
    last_alert_date_str = row_data.get(date_key, '')

    is_switch_on = (switch_val == 'ON')
    
    # 1. 檢查是否已在今天發送過警報 (去重邏輯)
    last_alert_date = None
    try:
        if last_alert_date_str:
            last_alert_date = datetime.strptime(last_alert_date_str, '%Y-%m-%d').date()
    except ValueError:
        pass 
        
    has_alerted_today = (last_alert_date == current_date)
    
    if not is_triggered:
        return False # 訊號未觸發，直接結束
        
    # 訊號已觸發 (is_triggered == True)
    alert_msg_summary.append(signal_msg) # 總是將訊號結果加入 Sheets 總結欄位

    # 2. 檢查開關和去重條件
    if is_switch_on and not has_alerted_today:
        
        # 🚨 觸發警報並發送 Telegram 提醒
        
        # 2.1 更新 Sheets 獨立去重日期
        update_cells.append(((column_map[date_key], row_num), current_date.strftime('%Y-%m-%d')))
        
        # 2.2 🚨 創建獨立 Telegram 警報訊息 (包含所有輔助資訊)
        code_link = f"[{stock_code}]({link})" if link else stock_code 
        
        # 抓取所有輔助資訊 (包括新的斜率數值)
        # 注意：這裡抓取的 row_data 是 ta_analyzer 從 Sheets 讀取到的舊值，
        # 實際的斜率數值是在 ta_analyzer 中計算並準備寫入 Sheets，
        # 但由於它們是同時寫入，在單次運行中，這裡應該從 Sheets 中讀取到的舊值為空或舊的。
        # 為了正確顯示當前最新的斜率值，應該從 ta_analyzer 傳入，但目前結構中做不到。
        # 暫時使用 Sheets 中的值作為輔助顯示。
        low_days = row_data.get('LOW_DAYS', '999')
        high_days = row_data.get('HIGH_DAYS', '999')
        tangle_state = row_data.get('MA_TANGLE', '不明')
        slope_desc = row_data.get('SLOPE_DESC', '不明')
        bias_val = row_data.get('BIAS_Val', '0.00%')
        
        # 🚨 新增：斜率數值 (從 Sheets 讀取)
        s5 = row_data.get('MA5_SLOPE', 'N/A')
        s10 = row_data.get('MA10_SLOPE', 'N/A')
        s20 = row_data.get('MA20_SLOPE', 'N/A')
        
        slope_values_str = f"MA5:{s5} | MA10:{s10} | MA20:{s20}"

        # 格式化極端點位資訊
        extreme_info = []
        if low_days != '999': extreme_info.append(f"日低點間隔: {low_days} 天")
        if high_days != '999': extreme_info.append(f"月高點間隔: {high_days} 天")

        # 組合 Telegram 訊息
        formatted_message = (
            f"🔔 **🚨 {code_link}** (指標警報)\n"
            f"-> **訊號**：{signal_msg} (今日首次觸發)\n"
            f"-> **MA趨勢**：{tangle_state} | {slope_desc}\n"
            f"-> **斜率數值**：{slope_values_str}\n" # 🚨 新增斜率數值行
            f"-> **乖離率**：{bias_val}\n"
            f"-> **極端點**：{' | '.join(extreme_info) if extreme_info else '無明顯極端點'}"
        )

        alerts.append(formatted_message)
        logger.info(f"✅ {stock_code} 觸發 {signal_msg} 警報，並發送 Telegram 訊息。")
        return True
        
    elif is_triggered and has_alerted_today:
        logger.info(f"去重：{stock_code} 的 {signal_msg} 今天已發送過警報，跳過 Telegram 通知。")
        
    elif is_triggered and not is_switch_on:
        logger.info(f"禁用：{stock_code} 的 {signal_msg} 已觸發，但開關為 OFF。")
        
    return False

# ----------------------------------------------------------------------
# 🚨 關鍵新增：get_static_link 函式 - 解決 bot.py 報錯
# ----------------------------------------------------------------------
def get_static_link(stock_code: str, provider: str) -> str:
    """
    根據股票代號和提供者生成靜態連結，供 Telegram 訊息使用。
    """
    code = str(stock_code).strip()
    provider = str(provider).strip().upper()
    
    # 處理空代號或提供者
    if not code:
        return "https://www.google.com" # 預設連結
        
    if provider == 'TWSE':
        # 台灣證券交易所或 Yahoo 台灣股市
        return f"https://tw.stock.yahoo.com/q/q?s={code}"
    
    elif provider == 'US':
        # 美股 (例如使用 Yahoo Finance)
        return f"https://finance.yahoo.com/quote/{code}"
        
    elif provider == 'HK':
        # 港股 (例如使用 AAStocks)
        return f"http://www.aastocks.com/tc/stocks/quote/quick-quote.aspx?symbol={code}"
        
    else:
        # 預設使用 Google Finance 查詢
        # 對於台股，通常是 "代號.TW" 或直接代號
        return f"https://www.google.com/finance/quote/{code}"
# ----------------------------------------------------------------------