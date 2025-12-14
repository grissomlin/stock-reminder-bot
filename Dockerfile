# Dockerfile (最終穩定版 - 解決 Glibc 衝突)

# 採用輕量級的 Debian Slim
FROM python:3.11-slim

# 設定工作目錄
WORKDIR /usr/src/app

# 1. 🚨 關鍵修正：更新底層 C 庫以增強兼容性，並安裝 libffi
RUN apt-get update && \
    apt-get install -y --no-install-recommends libffi-dev \
    # 執行 ldconfig 確保系統動態鏈接器配置更新
    && ldconfig \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 2. 升級 pip 並安裝基礎套件 (numpy)
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    # 鎖定 numpy 版本，這是 TA-Lib 輪子兼容性的核心
    pip install --no-cache-dir "numpy==1.26.4"

# 3. 複製依賴文件並安裝 Python 套件 (從 URL 安裝 TA-Lib 輪子)
COPY requirements.txt ./
# 這裡將使用 requirements.txt 中的 URL 語法來安裝 TA-Lib 輪子
RUN pip install --no-cache-dir -r requirements.txt

# 4. 複製所有專案文件到容器內
COPY . .

# 啟動指令
CMD ["python", "bot.py"]
