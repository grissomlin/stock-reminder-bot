# Dockerfile (最終最簡版 - 無 TA-Lib C 擴展)

# 1. 採用 Python 3.10 穩定版
FROM python:3.10-slim

# 設定工作目錄
WORKDIR /usr/src/app

# 2. 安裝 Numba/NumPy 所需的系統依賴 (主要為 libffi-dev)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libffi-dev \
        # Numba 編譯可能需要的基礎工具
        build-essential \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 3. 複製依賴文件並安裝 Python 套件
COPY requirements.txt .
# 這裡將安裝所有依賴，包括 Numba。Numba 的安裝會自動編譯，但比 TA-Lib 穩定得多。
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 4. 複製所有專案文件
COPY . .

# 5. 啟動指令
CMD ["python", "bot.py"]