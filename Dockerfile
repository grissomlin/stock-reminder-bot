# Dockerfile (最終穩定版 - 採用 Python 3.10 + NumPy 1.22.4 編譯)

# 1. 鎖定使用 Python 3.10
FROM python:3.10-slim

# 設定工作目錄
WORKDIR /usr/src/app

# 2. 安裝系統依賴 (TA-Lib C 庫所需)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wget \
        build-essential \
        libffi-dev \
        && ldconfig \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 3. 下載、編譯並安裝 TA-Lib C 函式庫 (系統庫)
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

# 5. 🚨 關鍵步驟：鎖定 NumPy 1.22.4，並執行所有依賴安裝
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    # 鎖定一個與舊版 TA-Lib C 擴展兼容的 NumPy 版本
    pip install --no-cache-dir "numpy==1.22.4" && \
    # 這裡會安裝 requirements.txt 中 TA-Lib 的原始碼，但搭配兼容的 NumPy 版本
    pip install --no-cache-dir -r requirements.txt

# 6. 複製所有專案文件
COPY . .

# 7. 啟動指令
CMD ["python", "bot.py"]
