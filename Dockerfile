# Dockerfile (最終穩定版 - 只安裝輪子)
FROM python:3.11-slim
WORKDIR /usr/src/app

# 1. 安裝系統依賴
RUN apt-get update && \
    apt-get install -y --no-install-recommends libffi-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 2. 升級 pip 並安裝 numpy
# 這裡將 numpy 版本鎖定在 1.26.4，以配合 cp311-linux 輪子的測試環境
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir "numpy==1.26.4"

# 3. 複製依賴文件並安裝 Python 套件 (安裝 TA-Lib 輪子)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 4. 複製所有專案文件到容器內
COPY . .
CMD ["python", "bot.py"]
