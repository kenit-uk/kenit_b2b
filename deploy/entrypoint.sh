#!/bin/sh
set -e
mkdir -p "$DATA_DIR" "$UPLOAD_DIR"
export S3_REGION="${S3_REGION:-auto}"
APP="uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*"

if [ -z "$S3_BUCKET" ]; then
  echo "!!! 경고 / WARNING: S3_BUCKET 이 없어 백업이 꺼져 있습니다. 서버 디스크가 사라지면 데이터도 사라집니다."
  echo "!!! WARNING: S3_BUCKET not set - backups are OFF. Data is lost if the server disk is lost."
  exec $APP
fi

# 서버 디스크에 DB가 없으면(새 서버, 디스크 유실 등) 백업에서 복구한다.
litestream restore -config /etc/litestream.yml -if-db-not-exists -if-replica-exists "$DATA_DIR/app.db"
# 앱을 실행하면서 DB 변경분을 계속 백업한다.
exec litestream replicate -config /etc/litestream.yml -exec "$APP"
