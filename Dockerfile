# Dockerfile (最終穩定版 - 採用 PyTorch 基礎映像解決 Glibc 衝突)

# 🚨 關鍵變更：使用 PyTorch 官方基於 Debian 的 CUDA-runtime 映像作為基礎
# 選擇一個基於 Python 3.11 環境的穩定版本
FROM pytorch/pytorch:2.2.0-cuda12.1-cudnn8-runtime-ubuntu22.04

# 設定工作目錄
WORKDIR /usr/src/app

# 1. 安裝系統依賴 (這個映像已經很完整，只需安裝 libffi)
# 我們不需要 apt-get update，因為這個映像已經預裝了絕大多數依賴
RUN apt-get update && \
    apt-get install -y --no-install-recommends libffi-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 2. 升級 pip 並安裝基礎套件 (numpy 已經預裝且兼容)
# 我們只需要升級 pip 和安裝 numpy
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir "numpy==1.26.4"

# 3. 複製依賴文件並安裝 Python 套件 (從 URL 安裝 TA-Lib 輪子)
COPY requirements.txt ./
# 🚨 這裡會安裝您在 requirements.txt 中指定的 TA-Lib 輪子
RUN pip install --no-cache-dir -r requirements.txt

# 4. 複製所有專案文件到容器內
COPY . .

# 啟動指令
CMD ["python", "bot.py"]
