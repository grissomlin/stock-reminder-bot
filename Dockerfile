# ===============================
# Stable TA-Lib Dockerfile
# ===============================
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# ---------- system deps ----------
RUN apt-get update && apt-get install -y \
    build-essential \
    wget \
    curl \
    ca-certificates \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# ---------- build TA-Lib C lib ----------
WORKDIR /tmp
RUN wget https://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz && \
    tar -xzf ta-lib-0.4.0-src.tar.gz && \
    cd ta-lib && \
    ./configure --prefix=/usr && \
    make && \
    make install

# ---------- python deps ----------
WORKDIR /app
COPY requirements.txt .

# 關鍵：先 numpy → 再其他 → 再 ta-lib
RUN pip install --upgrade pip && \
    pip install --no-cache-dir numpy==1.26.4 && \
    pip install --no-cache-dir -r requirements.txt

# ---------- copy app ----------
COPY . .

# ---------- startup self-check ----------
RUN python - <<'EOF'
import talib, numpy
print("TA-Lib OK:", talib.__version__)
print("NumPy OK:", numpy.__version__)
EOF

# ---------- run ----------
CMD ["python", "bot.py"]
