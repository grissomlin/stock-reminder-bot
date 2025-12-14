# Dockerfile (最終穩定版 - 採用 Python 3.10 解決編譯兼容性)

# 1. 🚨 採用 Python 3.10 穩定版
FROM python:3.10-slim

# 設定工作目錄
WORKDIR /usr/src/app

# 2. 安裝所有必要的系統依賴 (包括 C 編譯器、wget 和 libffi)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wget \
        build-essential \
        libffi-dev \
        # 確保動態鏈接器配置更新
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

# 4. 返回應用程式工作目錄並安裝 Python 依賴
WORKDIR /usr/src/app
COPY requirements.txt .

# 5. 安裝所有 Python 依賴 (使用 TA-Lib 的 Python 綁定)
# 在 Python 3.10 環境下，TA-Lib 0.4.28 可以順利安裝
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir "numpy<2.0" && \
    pip install --no-cache-dir -r requirements.txt

# 6. 複製所有專案文件 (Bot.py 等)
COPY . .

# 7. 啟動指令
CMD ["python", "bot.py"]
