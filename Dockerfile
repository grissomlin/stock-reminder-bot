# Dockerfile (優化版 - Alpine with TA-Lib)
FROM python:3.11-alpine

# 設定工作目錄
WORKDIR /usr/src/app

# === TA-Lib 系統依賴安裝 (使用 Alpine 的 apk) ===
RUN apk update && \
    # 安裝編譯工具和運行時依賴
    apk add --no-cache --virtual .build-deps \
        build-base \
        wget \
        curl \
        openssl-dev \
        linux-headers && \
    # 安裝運行時必需的系統庫
    apk add --no-cache \
        libstdc++ \
        ca-certificates && \
    # 下載並編譯 TA-Lib C 庫
    wget http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz && \
    tar -xzf ta-lib-0.4.0-src.tar.gz && \
    cd ta-lib && \
    ./configure --prefix=/usr && \
    make && \
    make install && \
    cd .. && \
    rm -rf ta-lib ta-lib-0.4.0-src.tar.gz && \
    # 只刪除編譯工具,保留運行時依賴
    apk del .build-deps

# 升級 pip 並安裝 numpy (TA-Lib 依賴)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir numpy

# 複製依賴文件並安裝 Python 套件
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 複製所有專案文件到容器內
COPY . .

# 暴露端口 (Railway 需要)
EXPOSE 8080

# 定義容器啟動時執行的指令
CMD ["python", "bot.py"]