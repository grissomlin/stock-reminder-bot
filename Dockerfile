# Use a standard Python base image (avoid alpine for complex C extensions)
FROM python:3.10-slim

# Install system dependencies needed for building TA-Lib
RUN apt-get update && apt-get install -y \
    wget \
    gcc \
    make \
    # Clean up apt lists to keep image size down
    && rm -rf /var/lib/apt/lists/*

# Download, compile, and install the TA-Lib C library
WORKDIR /tmp/ta-lib
# Use wget to fetch the source directly from GitHub releases
RUN wget https://github.com/TA-Lib/ta-lib/releases/download/v0.4.0/ta-lib-0.4.0-src.tar.gz
RUN tar -xvf ta-lib-0.4.0-src.tar.gz
WORKDIR /tmp/ta-lib/ta-lib
RUN ./configure --prefix=/usr
RUN make
RUN make install

# Install the Python wrapper for TA-Lib using pip
WORKDIR /app
COPY requirements.txt .
# Use --no-cache-dir to prevent storing cache data in the image
# It might also be necessary to manage numpy versions explicitly due to compatibility issues
RUN pip install --no-cache-dir TA-Lib

# Copy the rest of your application code
# COPY . /app

# Define the command to run your application (example)
# CMD ["python", "your_app_script.py"]
