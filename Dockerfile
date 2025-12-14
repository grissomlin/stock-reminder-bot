# Dockerfile (最終穩定版 - Python 3.11 環境 + 成功編譯 TA-Lib)

# 1. 鎖定使用您的專案目標版本 Python 3.11
FROM python:3.11-slim

# 設定工作目錄為應用程式的根目錄
WORKDIR /usr/src/app

# 2. 安裝所有必要的系統依賴 (確保 C 編譯器、wget 和 libffi 存在)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wget \
        build-essential \
        libffi-dev \
        # 確保動態鏈接器配置更新，解決運行時的 GLIBC 衝突
        && ldconfig \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 3. 下載、編譯並安裝 TA-Lib C 函式庫
# 使用 /tmp/ta-lib 作為編譯臨時目錄
WORKDIR /tmp/ta-lib
RUN wget https://github.com/TA-Lib/ta-lib/releases/download/v0.4.0/ta-lib-0.4.0-src.tar.gz && \
    tar -xvf ta-lib-0.4.0-src.tar.gz && \
    cd ta-lib && \
    ./configure --prefix=/usr && \
    make && \
    make install && \
    cd .. && \
    rm -rf ta-lib ta-lib-0.4.0-src.tar.gz

# 4. 返回應用程式工作目錄並安裝 Python 依賴
WORKDIR /usr/src/app
COPY requirements.txt .

# 🚨 關鍵步驟：分兩階段安裝 Python 依賴
# 鎖定 NumPy 版本以避免與 TA-Lib Python 綁定發生 C 標頭檔衝突
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir "numpy==1.26.4" && \
    # 安裝 TA-Lib Python 綁定，它會使用剛剛安裝的 C 庫
    pip install --no-cache-dir "TA-Lib==0.4.28" && \
    # 安裝 requirements.txt 中剩餘的依賴
    pip install --no-cache-dir -r requirements.txt

# 5. 複製所有應用程式碼
COPY . .

# 6. 定義執行命令 (啟動您的 Bot 程式)
CMD ["python", "bot.py"]
