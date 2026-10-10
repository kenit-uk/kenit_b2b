FROM python:3.12-slim

# Litestream: SQLite 변경분을 S3 호환 저장소(R2)에 실시간 복제하는 백업 도구
ARG LITESTREAM_VERSION=0.3.13
ARG TARGETARCH
ADD https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-v${LITESTREAM_VERSION}-linux-${TARGETARCH}.tar.gz /tmp/litestream.tar.gz
RUN tar -xzf /tmp/litestream.tar.gz -C /usr/local/bin && rm /tmp/litestream.tar.gz

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY deploy/litestream.yml /etc/litestream.yml
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV DATA_DIR=/data/db \
    UPLOAD_DIR=/data/uploads \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["/entrypoint.sh"]
