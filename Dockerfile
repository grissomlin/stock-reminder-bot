# Dockerfile (最終穩定版 - 使用預編譯 TA-Lib 輪子)

# 使用 Debian slim
FROM python:3.11-slim

# 設定工作目錄
WORKDIR /usr/src/app

# 1. 🚨 僅安裝基礎系統依賴，移除所有 C 編譯工具
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        # 安裝 libffi-dev，確保 Python 擴展可以正常工作
        libffi-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 2. 升級 pip 並安裝基礎套件
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# 3. 複製依賴文件並安裝 Python 套件 (包括 TA-Lib 輪子)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 4. 複製所有專案文件到容器內
COPY . .

# 啟動指令
CMD ["python", "bot.py"]
