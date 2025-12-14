FROM python:3.10

COPY /app /app
COPY /config /config

RUN wget https://github.com/TA-Lib/ta-lib/releases/download/v0.4.0/ta-lib-0.4.0-src.tar.gz
RUN tar -xvf ta-lib-0.4.0-src.tar.gz
WORKDIR /ta-lib
RUN ./configure --prefix=/usr --build=`/bin/arch`-unknown-linux-gnu
RUN make
RUN make install
RUN pip install --no-cache-dir TA-Lib

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade -r requirements.txt

WORKDIR /

ENTRYPOINT ["python" , "app/main.py"]
