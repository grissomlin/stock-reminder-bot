# Dockerfile (最終穩定版 - 只安裝輪子)

# 使用 Debian slim
FROM python:3.11-slim

# 設定工作目錄
WORKDIR /usr/src/app

# 1. 🚨 僅安裝必要的系統依賴
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        # 僅安裝 libffi-dev，用於許多 Python 擴展
        libffi-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 2. 升級 pip 並安裝基礎套件 (包含 numpy)
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    # 確保安裝一個與 TA-Lib 兼容的 numpy 版本
    pip install --no-cache-dir "numpy==1.26.4"

# 3. 複製依賴文件並安裝 Python 套件 (從 URL 安裝 TA-Lib 輪子)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 4. 複製所有專案文件到容器內
COPY . .

# 啟動指令
CMD ["python", "bot.py"]
