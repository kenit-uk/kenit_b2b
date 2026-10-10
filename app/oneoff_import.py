"""1회용: 로컬 SQLite 데이터를 운영 DB로 옮긴다. 옮긴 뒤 이 파일과 IMPORT_TOKEN 환경변수를 지운다.

IMPORT_TOKEN 환경변수가 있을 때만 켜지고, 요청 헤더의 토큰이 맞고 운영 DB에 사용자가 한 명도 없을 때만 동작한다.
"""
import hmac
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .db import get_db

router = APIRouter()

# 옮길 테이블과 열(순서 = 외래키 의존 순서). 첨부는 사진 저장소 연결 후 따로 옮긴다.
TABLES = {
    "users": ["id", "email", "name", "phone", "password_hash", "role", "active", "lang", "seen_baseline", "created_at"],
    "houses": ["id", "address", "postcode", "landlord_name", "landlord_phone", "landlord_email", "memo", "status",
               "created_by", "created_at", "updated_by", "updated_at"],
    "entries": ["id", "house_id", "type", "author_id", "fields", "body", "created_at", "updated_by", "updated_at",
                "edit_count"],
    "audit_log": ["id", "house_id", "entry_id", "actor_id", "action", "detail", "snapshot", "created_at"],
    "house_views": ["user_id", "house_id", "last_seen_id"],
}


@router.post("/admin/one-time-import")
async def one_time_import(request: Request, conn=Depends(get_db)):
    token = os.environ.get("IMPORT_TOKEN", "")
    if not token or not hmac.compare_digest(token, request.headers.get("x-import-token", "")):
        return JSONResponse({"error": "forbidden"}, 403)
    if conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]:
        return JSONResponse({"error": "target database already has users"}, 409)
    data = await request.json()
    counts = {}
    with conn:
        for table, cols in TABLES.items():
            rows = data.get(table, [])
            for row in rows:
                conn.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [row.get(c) for c in cols],
                )
            counts[table] = len(rows)
            # 이후 새로 만드는 행이 옮긴 id와 겹치지 않도록 자동 번호를 맞춘다
            if conn.pg and "id" in cols and rows:
                conn.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT MAX(id) FROM {table}))")
    return counts
