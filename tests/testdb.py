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
