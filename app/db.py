"""DB 연결. DATABASE_URL 이 있으면 PostgreSQL(Neon), 없으면 로컬 SQLite 파일을 쓴다.

두 DB에서 같은 SQL이 돌도록 ? 자리표시자로 작성하고, 차이 나는 부분만 이 파일에서 맞춘다.
"""
import json
import re
import secrets
import sqlite3
from datetime import datetime, timezone

from fastapi import Request

from .config import DATABASE_URL, DB_PATH

IS_PG = bool(DATABASE_URL)
if IS_PG:
    import psycopg
    from psycopg.rows import dict_row

    IntegrityError = (sqlite3.IntegrityError, psycopg.IntegrityError)
else:
    IntegrityError = (sqlite3.IntegrityError,)

# {pk}: 자동 증가 기본키, {nocase}: 대소문자 무시 비교 (이메일은 항상 소문자로 저장하므로 PG에서는 생략)
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            {pk},
  email         TEXT UNIQUE {nocase},  -- 휴대폰으로만 가입한 사람은 비어 있음
  name          TEXT NOT NULL,
  phone         TEXT,
  phone_e164    TEXT,  -- 문자 로그인용 인증된 번호 (+447700900123)
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('staff', 'manager', 'installer')),
  active        INTEGER NOT NULL DEFAULT 1,
  lang          TEXT,
  seen_baseline INTEGER NOT NULL DEFAULT 0,  -- 가입 시점의 마지막 변경이력 번호(그 이전 내용은 새 소식으로 치지 않음)
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phone_codes (
  id          {pk},
  phone       TEXT NOT NULL,
  purpose     TEXT NOT NULL,  -- login: 로그인, verify: 내 정보에서 번호 등록
  user_id     INTEGER,
  code_hash   TEXT NOT NULL,
  attempts    INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  used_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_phone_codes_phone ON phone_codes(phone);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS houses (
  id              {pk},
  address         TEXT NOT NULL,
  postcode        TEXT,
  landlord_name   TEXT,
  landlord_phone  TEXT,
  landlord_email  TEXT,
  memo            TEXT,
  status          TEXT NOT NULL,  -- 이전 버전 상태값 (지금은 stage 사용)
  ref_no          TEXT,           -- 관리번호 WS-100001
  stage           INTEGER NOT NULL DEFAULT 1,
  on_hold         INTEGER NOT NULL DEFAULT 0,
  created_by      INTEGER NOT NULL REFERENCES users(id),
  created_at      TEXT NOT NULL,
  updated_by      INTEGER REFERENCES users(id),
  updated_at      TEXT NOT NULL
);

-- 설치업자 현장정보(survey), 관리자 견적(quote), 집주인 대화(conversation)
CREATE TABLE IF NOT EXISTS entries (
  id          {pk},
  house_id    INTEGER NOT NULL REFERENCES houses(id),
  type        TEXT NOT NULL CHECK (type IN ('survey', 'quote', 'conversation')),
  author_id   INTEGER NOT NULL REFERENCES users(id),
  fields      TEXT NOT NULL DEFAULT '{{}}',
  body        TEXT,
  created_at  TEXT NOT NULL,
  updated_by  INTEGER REFERENCES users(id),
  updated_at  TEXT NOT NULL,
  edit_count  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_entries_house ON entries(house_id);

CREATE TABLE IF NOT EXISTS attachments (
  id            {pk},
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
  id         {pk},
  house_id   INTEGER NOT NULL REFERENCES houses(id),
  entry_id   INTEGER REFERENCES entries(id),
  actor_id   INTEGER NOT NULL REFERENCES users(id),
  action     TEXT NOT NULL,
  detail     TEXT,
  snapshot   TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_house ON audit_log(house_id);

-- 프로젝트에 배정된 설치업자 (설치업자는 배정된 프로젝트만 본다)
CREATE TABLE IF NOT EXISTS house_members (
  house_id  INTEGER NOT NULL REFERENCES houses(id),
  user_id   INTEGER NOT NULL REFERENCES users(id),
  added_by  INTEGER REFERENCES users(id),
  added_at  TEXT NOT NULL,
  PRIMARY KEY (house_id, user_id)
);

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


class Conn:
    """sqlite3 / psycopg 연결을 같은 방식으로 쓰기 위한 얇은 래퍼.

    - execute(sql, params): ? 자리표시자 사용
    - insert(sql, params): 새 행의 id 반환
    - with conn: 블록 단위 트랜잭션(성공 시 commit, 예외 시 rollback). 연결은 닫지 않는다.
    """

    def __init__(self, raw, pg: bool):
        self.raw, self.pg = raw, pg
        self.like = "ILIKE" if pg else "LIKE"  # 대소문자 무시 검색

    def execute(self, sql: str, params=()):
        if self.pg:
            return self.raw.execute(sql.replace("?", "%s"), params)
        return self.raw.execute(sql, params)

    def insert(self, sql: str, params=()) -> int:
        if self.pg:
            return self.execute(sql + " RETURNING id", params).fetchone()["id"]
        return self.execute(sql, params).lastrowid

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.raw.commit()
        else:
            self.raw.rollback()
        return False

    def close(self):
        self.raw.close()


def connect() -> Conn:
    if IS_PG:
        # Neon 연결 풀(pgbouncer)과 함께 쓰므로 서버 측 prepared statement는 끈다
        raw = psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None, connect_timeout=10)
        return Conn(raw, pg=True)
    # 요청마다 연결을 새로 열고 닫으므로 스레드 간 공유가 없다(동기 의존성 → async 라우트 전달 허용)
    raw = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    return Conn(raw, pg=False)


_initialized = False


def ensure_db() -> dict:
    """프로세스당 한 번 스키마를 준비한다(서버리스에서는 시작 이벤트가 보장되지 않아 첫 요청 때 실행)."""
    global _initialized
    if _initialized:
        return {}
    created = init_db()
    _initialized = True
    for role, code in created.items():
        print(f"[초기 설정 / Setup] {role} 초대코드 / invite code: {code}")
    return created


def get_db(request: Request):
    ensure_db()
    conn = connect()
    request.state.db = conn  # 화면 공통 정보(N 개수 등) 조회에 같은 연결을 재사용
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> dict:
    """스키마 생성 후, 초대코드가 없으면 만들어서 반환한다."""
    conn = connect()
    created = {}
    try:
        if conn.pg:
            with conn:
                # 여러 서버리스 인스턴스가 동시에 시작해도 한 번만 만들도록 잠금
                conn.execute("SELECT pg_advisory_xact_lock(4242)")
                conn.raw.execute(SCHEMA.format(pk="INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", nocase=""))
                conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS lang TEXT")
                conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS seen_baseline INTEGER NOT NULL DEFAULT 0")
                # 매니저 역할 추가 (이전 DB는 관리자/설치업자만 허용)
                conn.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check")
                conn.execute("ALTER TABLE users ADD CONSTRAINT users_role_check "
                             "CHECK (role IN ('staff', 'manager', 'installer'))")
                # 문자 로그인: 이메일 없이 휴대폰으로만 가입 가능, 인증된 번호 칸
                conn.execute("ALTER TABLE users ALTER COLUMN email DROP NOT NULL")
                conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_e164 TEXT")
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone ON users(phone_e164)")
                for col, typ in HOUSE_NEW_COLS:
                    conn.execute(f"ALTER TABLE houses ADD COLUMN IF NOT EXISTS {col} {typ}")
        else:
            conn.raw.execute("PRAGMA journal_mode = WAL")
            conn.raw.executescript(SCHEMA.format(pk="INTEGER PRIMARY KEY AUTOINCREMENT", nocase="COLLATE NOCASE"))
            # 이전 버전 DB 호환
            user_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
            if "lang" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN lang TEXT")
            if "seen_baseline" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN seen_baseline INTEGER NOT NULL DEFAULT 0")
            if "phone_e164" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN phone_e164 TEXT")
            house_cols = {r["name"] for r in conn.execute("PRAGMA table_info(houses)")}
            for col, typ in HOUSE_NEW_COLS:
                if col not in house_cols:
                    conn.execute(f"ALTER TABLE houses ADD COLUMN {col} {typ}")
            conn.raw.commit()
            _sqlite_upgrade_users(conn)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone ON users(phone_e164)")
            conn.raw.commit()
        _backfill_login_phones(conn)
        _migrate_projects(conn)
        with conn:
            for role in ("staff", "manager", "installer"):
                code = secrets.token_urlsafe(9)
                cur = conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                                   (f"invite_code_{role}", code))
                if cur.rowcount == 1:
                    created[role] = code
    finally:
        conn.close()
    return created


HOUSE_NEW_COLS = [("ref_no", "TEXT"), ("stage", "INTEGER NOT NULL DEFAULT 1"), ("on_hold", "INTEGER NOT NULL DEFAULT 0")]


def ref_no_for(house_id: int) -> str:
    return f"WS-{100000 + house_id}"


def _migrate_projects(conn: Conn) -> None:
    """한 번만: 이전 상태값 → 9단계·보류, 관리번호 부여, 기존 설치업자를 기존 프로젝트에 배정(보이던 것이 그대로 보이도록)."""
    from .constants import STATUS_TO_STAGE

    with conn:
        for r in conn.execute("SELECT id FROM houses WHERE ref_no IS NULL").fetchall():
            conn.execute("UPDATE houses SET ref_no = ? WHERE id = ?", (ref_no_for(r["id"]), r["id"]))
        if conn.execute("SELECT 1 FROM settings WHERE key = 'projects_migrated'").fetchone():
            return
        for r in conn.execute("SELECT id, status FROM houses").fetchall():
            conn.execute("UPDATE houses SET stage = ?, on_hold = ? WHERE id = ?",
                         (STATUS_TO_STAGE.get(r["status"], 1), 1 if r["status"] == "보류" else 0, r["id"]))
        ts = now()
        for h in conn.execute("SELECT id FROM houses").fetchall():
            for u in conn.execute("SELECT id FROM users WHERE role = 'installer'").fetchall():
                conn.execute("INSERT INTO house_members (house_id, user_id, added_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                             (h["id"], u["id"], ts))
        conn.execute("INSERT INTO settings (key, value) VALUES ('projects_migrated', '1') ON CONFLICT(key) DO NOTHING")


def _backfill_login_phones(conn: Conn) -> None:
    """문자 로그인 도입 전에 가입한 사람: 가입 때 적은 연락처를 로그인 번호로 한 번만 옮긴다."""
    from .sms import normalize_phone

    if conn.execute("SELECT 1 FROM settings WHERE key = 'phone_backfill_done'").fetchone():
        return
    with conn:
        taken = {r["phone_e164"] for r in conn.execute("SELECT phone_e164 FROM users WHERE phone_e164 IS NOT NULL")}
        for r in conn.execute("SELECT id, phone FROM users WHERE phone_e164 IS NULL AND phone IS NOT NULL").fetchall():
            e164 = normalize_phone(r["phone"])
            if e164 and e164 not in taken:
                conn.execute("UPDATE users SET phone_e164 = ? WHERE id = ?", (e164, r["id"]))
                taken.add(e164)
        conn.execute("INSERT INTO settings (key, value) VALUES ('phone_backfill_done', '1') ON CONFLICT(key) DO NOTHING")


def _sqlite_upgrade_users(conn: Conn) -> None:
    """이전 SQLite DB의 users 테이블을 지금 구조로 다시 만든다(매니저 역할 허용, 이메일 선택 입력).
    SQLite는 제약 조건만 바꿀 수 없어서 새 테이블에 옮겨 담는다. 데이터와 id는 그대로 유지된다."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()["sql"]
    email_required = re.search(r"email\s+TEXT\s+NOT\s+NULL", sql)
    if "'manager'" in sql and not email_required:
        return
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)")]
    users_def = re.search(r"CREATE TABLE IF NOT EXISTS users \(.*?\n\);", SCHEMA, re.S).group(0)
    new_sql = users_def.replace("CREATE TABLE IF NOT EXISTS users", "CREATE TABLE users_new", 1).format(
        pk="INTEGER PRIMARY KEY AUTOINCREMENT", nocase="COLLATE NOCASE")
    new_cols = re.findall(r"^\s+(\w+)\s+[A-Z]", new_sql, re.M)
    cols = [c for c in cols if c in new_cols]
    conn.raw.execute("PRAGMA foreign_keys = OFF")
    try:
        with conn:
            conn.raw.execute("BEGIN")
            conn.execute(new_sql)
            conn.execute(f"INSERT INTO users_new ({', '.join(cols)}) SELECT {', '.join(cols)} FROM users")
            conn.execute("DROP TABLE users")
            conn.execute("ALTER TABLE users_new RENAME TO users")
    finally:
        conn.raw.execute("PRAGMA foreign_keys = ON")


def get_setting(conn: Conn, key: str):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: Conn, key: str, value: str) -> None:
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
