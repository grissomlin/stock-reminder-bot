# Dockerfile (Debian Slim 穩定版 - 推薦用於 TA-Lib)
FROM python:3.11-slim

# 設定工作目錄
WORKDIR /usr/src/app

# 安裝系統依賴 (Debian 使用 apt-get)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wget \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# 下載並編譯 TA-Lib C 庫
RUN wget -qO- http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz | tar xz && \
    cd ta-lib && \
    ./configure --prefix=/usr && \
    make && \
    make install && \
    cd .. && \
    rm -rf ta-lib

# 先安裝 numpy (TA-Lib 的核心依賴)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir numpy==1.26.4

# 複製依賴文件並安裝 Python 套件
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 複製所有專案文件到容器內
COPY . .

# 暴露端口
EXPOSE 8080

# 啟動指令
CMD ["python", "bot.py"]
