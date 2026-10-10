import json
import secrets
import sqlite3
from datetime import datetime, timezone

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name          TEXT NOT NULL,
  phone         TEXT,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('staff', 'installer')),
  active        INTEGER NOT NULL DEFAULT 1,
  lang          TEXT,
  seen_baseline INTEGER NOT NULL DEFAULT 0,  -- 가입 시점의 마지막 변경이력 번호(그 이전 내용은 새 소식으로 치지 않음)
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS houses (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  address         TEXT NOT NULL,
  postcode        TEXT,
  landlord_name   TEXT,
  landlord_phone  TEXT,
  landlord_email  TEXT,
  memo            TEXT,
  status          TEXT NOT NULL,
  created_by      INTEGER NOT NULL REFERENCES users(id),
  created_at      TEXT NOT NULL,
  updated_by      INTEGER REFERENCES users(id),
  updated_at      TEXT NOT NULL
);

-- 설치업자 현장정보(survey), 직원 견적(quote), 집주인 대화(conversation)
CREATE TABLE IF NOT EXISTS entries (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  house_id    INTEGER NOT NULL REFERENCES houses(id),
  type        TEXT NOT NULL CHECK (type IN ('survey', 'quote', 'conversation')),
  author_id   INTEGER NOT NULL REFERENCES users(id),
  fields      TEXT NOT NULL DEFAULT '{}',
  body        TEXT,
  created_at  TEXT NOT NULL,
  updated_by  INTEGER REFERENCES users(id),
  updated_at  TEXT NOT NULL,
  edit_count  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_entries_house ON entries(house_id);

CREATE TABLE IF NOT EXISTS attachments (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id      INTEGER NOT NULL REFERENCES entries(id),
  house_id      INTEGER NOT NULL REFERENCES houses(id),
  stored_name   TEXT NOT NULL,
  original_name TEXT NOT NULL,
  mime          TEXT NOT NULL,
  size          INTEGER NOT NULL,
  uploaded_by   INTEGER NOT NULL REFERENCES users(id),
  created_at    TEXT NOT NULL,
  deleted_by    INTEGER REFERENCES users(id),
  deleted_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_attachments_entry ON attachments(entry_id);

-- 모든 생성/수정/상태변경/첨부삭제 기록. 수정 전 내용은 snapshot(JSON)에 남는다.
CREATE TABLE IF NOT EXISTS audit_log (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  house_id   INTEGER NOT NULL REFERENCES houses(id),
  entry_id   INTEGER REFERENCES entries(id),
  actor_id   INTEGER NOT NULL REFERENCES users(id),
  action     TEXT NOT NULL,
  detail     TEXT,
  snapshot   TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_house ON audit_log(house_id);

-- 사용자별로 각 현장의 변경이력을 어디까지 확인했는지 (N 표시용)
CREATE TABLE IF NOT EXISTS house_views (
  user_id      INTEGER NOT NULL REFERENCES users(id),
  house_id     INTEGER NOT NULL REFERENCES houses(id),
  last_seen_id INTEGER NOT NULL,
  PRIMARY KEY (user_id, house_id)
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    # 요청마다 연결을 새로 열고 닫으므로 스레드 간 공유가 없다(동기 의존성 → async 라우트 전달 허용)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> dict:
    """스키마 생성 후, 초대코드가 없으면 만들어서 반환한다."""
    conn = connect()
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    # 이전 버전 DB 호환: 언어 설정 컬럼 추가
    user_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "lang" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN lang TEXT")
    if "seen_baseline" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN seen_baseline INTEGER NOT NULL DEFAULT 0")
    created = {}
    with conn:
        for role in ("staff", "installer"):
            key = f"invite_code_{role}"
            if not conn.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone():
                code = secrets.token_urlsafe(9)
                conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, code))
                created[role] = code
    conn.close()
    return created


def get_setting(conn: sqlite3.Connection, key: str):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def audit(conn, *, house_id, actor_id, action, entry_id=None, detail=None, snapshot=None) -> None:
    conn.execute(
        """INSERT INTO audit_log (house_id, entry_id, actor_id, action, detail, snapshot, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            house_id,
            entry_id,
            actor_id,
            action,
            detail,
            json.dumps(snapshot, ensure_ascii=False) if snapshot is not None else None,
            now(),
        ),
    )
