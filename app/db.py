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
  fgas_no       TEXT,     -- F-Gas 개인 자격 번호
  fgas_expiry   TEXT,     -- 만료·재확인일 YYYY-MM-DD
  fgas_company  TEXT,     -- 업체 인증 (예: REFCOM)
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

-- 로그인 시도 (반복 시도 제한용)
CREATE TABLE IF NOT EXISTS login_attempts (
  id    {pk},
  email TEXT,
  ip    TEXT,
  ok    INTEGER NOT NULL,
  at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_email ON login_attempts(email, at);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip, at);

-- 관리자·매니저가 만든 1회용 비밀번호 재설정 링크 (24시간)
CREATE TABLE IF NOT EXISTS password_resets (
  id         {pk},
  user_id    INTEGER NOT NULL REFERENCES users(id),
  token_hash TEXT NOT NULL UNIQUE,
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at    TEXT
);

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

-- 현장조사: 프로젝트마다 하나 (방 카드 + 실외기·공통)
CREATE TABLE IF NOT EXISTS surveys (
  id           {pk},
  house_id     INTEGER NOT NULL REFERENCES houses(id),
  status       TEXT NOT NULL DEFAULT 'draft',   -- draft / submitted / reviewed
  defaults     TEXT NOT NULL DEFAULT '{{}}',    -- 모든 방에 적용한 액세서리 값 (프로젝트 기본값)
  common       TEXT NOT NULL DEFAULT '{{}}',    -- 실외기·트렁킹·전기 등 프로젝트 공통 답변
  created_by   INTEGER NOT NULL REFERENCES users(id),
  created_at   TEXT NOT NULL,
  updated_by   INTEGER REFERENCES users(id),
  updated_at   TEXT NOT NULL,
  submitted_by INTEGER REFERENCES users(id),
  submitted_at TEXT,
  reviewed_by  INTEGER REFERENCES users(id),
  reviewed_at  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_surveys_house ON surveys(house_id);

CREATE TABLE IF NOT EXISTS survey_rooms (
  id            {pk},
  survey_id     INTEGER NOT NULL REFERENCES surveys(id),
  sort          INTEGER NOT NULL DEFAULT 0,
  name          TEXT NOT NULL,
  indoor_type   TEXT,            -- wall / cassette / ducted / console
  kw            TEXT,            -- 2.5 / 3.5 / 5.0 / 7.0 / unknown (대략적인 제안)
  memo          TEXT,
  acc           TEXT NOT NULL DEFAULT '{{}}',  -- 이 방에서 기본값을 덮어쓴 액세서리 값
  photo_request TEXT,            -- 회사가 요청한 추가 사진
  removed_at    TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS survey_odus (
  id         {pk},
  survey_id  INTEGER NOT NULL REFERENCES surveys(id),
  sort       INTEGER NOT NULL DEFAULT 0,
  mount      TEXT,               -- ground / bracket / roof
  note       TEXT,
  removed_at TEXT,
  created_at TEXT NOT NULL
);

-- 작업 범위(Scope) 버전. 가격은 넣지 않는다.
CREATE TABLE IF NOT EXISTS scope_versions (
  id         {pk},
  house_id   INTEGER NOT NULL REFERENCES houses(id),
  version    INTEGER NOT NULL,
  data       TEXT NOT NULL,      -- JSON: rooms(방별 기기·용량·액세서리), odus(실외기), common(공통 사항)
  note       TEXT,
  survey_id  INTEGER REFERENCES surveys(id),
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  locked_at  TEXT,          -- 확정(잠금). 이후 변경은 변경 요청(VO)으로만
  locked_by  INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scope_version ON scope_versions(house_id, version);

-- 설치업자의 작업 범위 "확인했어요" 기록 (버전별)
CREATE TABLE IF NOT EXISTS scope_acks (
  scope_id INTEGER NOT NULL REFERENCES scope_versions(id),
  user_id  INTEGER NOT NULL REFERENCES users(id),
  at       TEXT NOT NULL,
  PRIMARY KEY (scope_id, user_id)
);

-- 프로젝트 자료 (도면·평면도·LATS·카탈로그·협의 기록). 같은 자료의 새 버전은 group_id가 같다. 가격 문서는 올리지 않는다.
CREATE TABLE IF NOT EXISTS documents (
  id         {pk},
  house_id   INTEGER NOT NULL REFERENCES houses(id),
  group_id   INTEGER,
  version    INTEGER NOT NULL DEFAULT 1,
  category   TEXT NOT NULL,
  title      TEXT NOT NULL,
  note       TEXT,
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  removed_at TEXT,
  removed_by INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_documents_house ON documents(house_id);

CREATE TABLE IF NOT EXISTS doc_acks (
  document_id INTEGER NOT NULL REFERENCES documents(id),
  user_id     INTEGER NOT NULL REFERENCES users(id),
  at          TEXT NOT NULL,
  PRIMARY KEY (document_id, user_id)
);

-- 방문: 현장조사 / 1차 시공 / 2차 시공 / 기타. 한 프로젝트가 여러 날·여러 방문으로 나뉜다.
--  status: requested(설치업자에게 날짜 후보 요청) → options(후보 받음) → confirmed(확정)
--          proposed(회사가 날짜 제안, 설치업자 가능 여부 대기) → confirmed / declined
--          done(작업 끝 보고됨), cancelled
CREATE TABLE IF NOT EXISTS visits (
  id              {pk},
  house_id        INTEGER NOT NULL REFERENCES houses(id),
  kind            TEXT NOT NULL,
  installer_id    INTEGER REFERENCES users(id),
  date            TEXT,            -- YYYY-MM-DD (런던)
  start_time      TEXT,            -- HH:MM
  end_time        TEXT,
  status          TEXT NOT NULL,
  note            TEXT,
  override_reason TEXT,            -- 중복 배정·F-Gas 경고를 무시하고 예약한 이유
  created_by      INTEGER NOT NULL REFERENCES users(id),
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_visits_house ON visits(house_id);
CREATE INDEX IF NOT EXISTS idx_visits_installer_date ON visits(installer_id, date);

CREATE TABLE IF NOT EXISTS visit_date_options (
  id         {pk},
  visit_id   INTEGER NOT NULL REFERENCES visits(id),
  date       TEXT NOT NULL,
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL
);

-- 현장 도착 / 오늘 작업 끝: 누르는 순간의 시각 + 위치 1회만 (백그라운드 추적 없음)
CREATE TABLE IF NOT EXISTS checkins (
  id        {pk},
  visit_id  INTEGER NOT NULL REFERENCES visits(id),
  user_id   INTEGER NOT NULL REFERENCES users(id),
  kind      TEXT NOT NULL,         -- arrive / finish
  at        TEXT NOT NULL,
  lat       REAL,
  lng       REAL,
  accuracy  REAL
);
CREATE INDEX IF NOT EXISTS idx_checkins_visit ON checkins(visit_id);

-- 오늘 작업 끝 보고 → 회사 승인 대기
CREATE TABLE IF NOT EXISTS day_reports (
  id          {pk},
  visit_id    INTEGER NOT NULL REFERENCES visits(id),
  house_id    INTEGER NOT NULL REFERENCES houses(id),
  user_id     INTEGER NOT NULL REFERENCES users(id),
  work        TEXT NOT NULL DEFAULT '[]',   -- 배관·실내기·실외기·전기·시운전 (복수)
  has_problem INTEGER NOT NULL DEFAULT 0,
  note        TEXT,
  created_at  TEXT NOT NULL,
  reviewed_by INTEGER REFERENCES users(id),
  reviewed_at TEXT
);

-- 변경 요청 (Variation, VO-1 …). pending = 시공 금지(고객 승인 대기), approved = 시공 가능, rejected = 원래대로
CREATE TABLE IF NOT EXISTS variations (
  id            {pk},
  house_id      INTEGER NOT NULL REFERENCES houses(id),
  number        INTEGER NOT NULL,
  visit_id      INTEGER REFERENCES visits(id),
  kind          TEXT NOT NULL,
  body          TEXT,
  status        TEXT NOT NULL DEFAULT 'pending',
  created_by    INTEGER NOT NULL REFERENCES users(id),
  created_at    TEXT NOT NULL,
  decided_by    INTEGER REFERENCES users(id),
  decided_at    TEXT,
  decision_note TEXT,
  reviewed_by   INTEGER REFERENCES users(id),
  reviewed_at   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_variation_number ON variations(house_id, number);

-- 설치업자 → 회사: 문제 보고, 회사에 메시지, 작업 범위 의견. 회사가 확인하면 reviewed_at.
CREATE TABLE IF NOT EXISTS messages (
  id          {pk},
  house_id    INTEGER NOT NULL REFERENCES houses(id),
  kind        TEXT NOT NULL,       -- problem / message / scope_comment
  visit_id    INTEGER REFERENCES visits(id),
  scope_id    INTEGER REFERENCES scope_versions(id),
  user_id     INTEGER NOT NULL REFERENCES users(id),
  body        TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  reviewed_by INTEGER REFERENCES users(id),
  reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_house ON messages(house_id);

-- v1 사진·파일 (방 사진, 실외기 사진, 분전반 사진 등). owner_type + owner_id 로 어디에 붙은 파일인지 나타낸다.
CREATE TABLE IF NOT EXISTS media (
  id            {pk},
  house_id      INTEGER NOT NULL REFERENCES houses(id),
  owner_type    TEXT NOT NULL,
  owner_id      INTEGER NOT NULL,
  stored_name   TEXT NOT NULL,
  original_name TEXT NOT NULL,
  mime          TEXT NOT NULL,
  size          INTEGER NOT NULL,
  uploaded_by   INTEGER NOT NULL REFERENCES users(id),
  created_at    TEXT NOT NULL,
  deleted_by    INTEGER REFERENCES users(id),
  deleted_at    TEXT,
  tag           TEXT,     -- 현장 사진의 방 태그
  visit_id      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_media_owner ON media(owner_type, owner_id);

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
            # 서버리스 인스턴스 여러 개가 동시에 시작할 수 있다. 스키마 변경과 데이터 옮기기 전체를
            # 한 번에 하나만 하도록 연결 단위 잠금을 잡는다(연결을 닫으면 자동으로 풀림).
            conn.raw.execute("SELECT pg_advisory_lock(4242)")
            conn.raw.commit()
            with conn:
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
                ensure_columns(conn)
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
            ensure_columns(conn)
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
        if conn.pg:
            try:
                conn.raw.rollback()
                conn.raw.execute("SELECT pg_advisory_unlock(4242)")
            except Exception:
                pass
        conn.close()
    return created


HOUSE_NEW_COLS = [("ref_no", "TEXT"), ("stage", "INTEGER NOT NULL DEFAULT 1"), ("on_hold", "INTEGER NOT NULL DEFAULT 0")]
# 이전 버전 DB에 나중에 추가된 칸 (테이블, 칸, 형식)
NEW_COLS = [
    ("scope_versions", "locked_at", "TEXT"), ("scope_versions", "locked_by", "INTEGER"),
    ("media", "tag", "TEXT"),            # 현장 사진의 방 태그
    ("media", "visit_id", "INTEGER"),
    ("users", "fgas_no", "TEXT"), ("users", "fgas_expiry", "TEXT"), ("users", "fgas_company", "TEXT"),
]


def ensure_columns(conn: "Conn") -> None:
    for table, col, typ in NEW_COLS:
        if conn.pg:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typ}")
        elif col not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


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
