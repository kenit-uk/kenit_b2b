"""테스트용 DB 접근: SQLite/PostgreSQL 어느 쪽이든 sqlite3 처럼 튜플로 읽고, 쓰기는 바로 반영(autocommit)한다."""
from app.db import connect

_c = connect()
if _c.pg:
    _c.raw.autocommit = True
else:
    _c.raw.isolation_level = None


class _Res:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class DB:
    def execute(self, sql, params=()):
        cur = _c.execute(sql, params)
        try:
            rows = cur.fetchall()
        except Exception:  # UPDATE 등 결과가 없는 문장
            rows = []
        return _Res([tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in rows])


db = DB()


def set_stage(house_id: int, stage: int, actor_email: str) -> None:
    """테스트용: 화면에서는 단계를 직접 바꿀 수 없으므로(진행에 따라 자동) 기록과 함께 DB에서 바꾼다."""
    from app.db import audit, connect, now
    c = connect()
    try:
        with c:
            old = c.execute("SELECT stage FROM houses WHERE id = ?", (house_id,)).fetchone()["stage"]
            actor = c.execute("SELECT id FROM users WHERE email = ?", (actor_email,)).fetchone()["id"]
            c.execute("UPDATE houses SET stage = ?, updated_by = ?, updated_at = ? WHERE id = ?", (stage, actor, now(), house_id))
            audit(c, house_id=house_id, actor_id=actor, action="stage", detail=f"{old} → {stage}")
    finally:
        c.close()


def approve_fgas(email: str, expiry: str = "2099-12-31") -> None:
    """테스트용: 회사가 F-Gas 자격증을 승인한 상태로 만든다 (1·2차 시공 배정 조건)."""
    from app.db import connect, now
    c = connect()
    try:
        with c:
            c.execute("UPDATE users SET fgas_no = COALESCE(fgas_no, 'F-GAS TEST'), fgas_expiry = ?, fgas_approved_at = ? WHERE email = ?",
                      (expiry, now(), email))
    finally:
        c.close()
