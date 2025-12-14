# Dockerfile (最終穩定版 - 採用 Python 3.10 + 成功編譯 TA-Lib)

# 1. 採用 Python 3.10 環境 (經證實可避開 TA-Lib C 擴展衝突)
FROM python:3.10-slim

# 設定工作目錄為應用程式的根目錄
WORKDIR /usr/src/app

# 2. 安裝所有必要的系統依賴 (包括 C 編譯器、wget 和 libffi)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wget \
        build-essential \
        libffi-dev \
        && ldconfig \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 3. 下載、編譯並安裝 TA-Lib C 函式庫
WORKDIR /tmp/ta-lib
RUN wget https://github.com/TA-Lib/ta-lib/releases/download/v0.4.0/ta-lib-0.4.0-src.tar.gz && \
    tar -xvf ta-lib-0.4.0-src.tar.gz && \
    cd ta-lib && \
    ./configure --prefix=/usr && \
    make && \
    make install && \
    cd .. && \
    rm -rf ta-lib ta-lib-0.4.0-src.tar.gz

# 4. 返回應用程式工作目錄並複製依賴文件
WORKDIR /usr/src/app
COPY requirements.txt .

# 5. 安裝 Python 依賴
# 鎖定 NumPy 版本，然後安裝 TA-Lib Python 綁定和其餘 requirements.txt
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir "numpy==1.26.4" && \
    pip install --no-cache-dir "TA-Lib==0.4.28" && \
    pip install --no-cache-dir -r requirements.txt

# 6. 🚨 複製所有應用程式碼 (Bot.py, ta_analyzer.py 等)
COPY . .

# 7. 🚨 定義執行命令 (啟動您的 Bot 程式)
CMD ["python", "bot.py"]
