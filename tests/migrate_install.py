"""1차·2차 시공(9단계)으로 쓰던 DB가 '시공 하나 + n차'(6단계)로 옮겨지는지 확인 (8단계 DB도)"""
import os, sys, json
sys.path.insert(0, os.path.dirname(__file__))
from app import db as D

D.init_db()
c = D.connect()
with c:
    # 예전 상태로 되돌린다: 9단계 번호, first_fix/second_fix, 예전 결제 관문 이름
    c.execute("DELETE FROM settings WHERE key IN ('install_unified', 'stage_scheme')")
    D.set_setting(c, "payment_gates", json.dumps({"deposit_before_first_fix": True, "interim_before_second_fix": False}))
    c.execute("INSERT INTO users (email, name, password_hash, role, created_at) VALUES ('a@ex.com', 'A', 'x', 'staff', 'now')")
    uid = c.execute("SELECT id FROM users").fetchone()["id"]
    ids = []
    for addr, stage in (("H7", 7), ("H8", 8), ("H9", 9), ("H6", 6)):
        ids.append(c.insert("INSERT INTO houses (address, status, stage, created_by, created_at, updated_at) VALUES (?, '검토전', ?, ?, 'now', 'now')",
                            (addr, stage, uid)))
    h7 = ids[0]
    for kind, date in (("second_fix", "2026-03-10"), ("first_fix", "2026-03-01"), ("survey", "2026-02-01")):
        c.execute("""INSERT INTO visits (house_id, kind, installer_id, date, status, created_by, created_at, updated_at)
                     VALUES (?, ?, ?, ?, 'done', ?, 'now', 'now')""", (h7, kind, uid, date, uid))
    D.audit(c, house_id=h7, actor_id=uid, action="stage", detail="6 → 7")
    D.audit(c, house_id=h7, actor_id=uid, action="visit_create", detail=json.dumps({"kind": "second_fix", "date": "2026-03-10"}))
c.close()

D._initialized = False
D.init_db()
D.init_db()   # 두 번 해도 그대로
c = D.connect()
st = {r["address"]: r["stage"] for r in c.execute("SELECT address, stage FROM houses")}
assert st == {"H6": 4, "H7": 4, "H8": 5, "H9": 6}, st             # 9단계 → 6단계
vs = [(r["kind"], r["seq"]) for r in c.execute("SELECT kind, seq FROM visits WHERE house_id = ? ORDER BY date", (h7,))]
assert vs == [("survey", None), ("install", 1), ("install", 2)], vs           # 날짜 순으로 1차·2차
logs = [r["detail"] for r in c.execute("SELECT detail FROM audit_log WHERE house_id = ? ORDER BY id", (h7,))]
assert logs[0] == "4 → 4" and '"kind": "install"' in logs[1], logs
g = json.loads(D.get_setting(c, "payment_gates"))
assert g == {"deposit_before_install": True, "interim_before_next_install": False, "balance_before_complete": False}, g
# 8단계(시공 하나)로 쓰던 DB → 6단계
c.close()
c = D.connect()
with c:
    c.execute("DELETE FROM settings WHERE key = 'stage_scheme'")
    for addr, stage in (("E5", 5), ("E7", 7), ("E8", 8)):
        c.execute("INSERT INTO houses (address, status, stage, created_by, created_at, updated_at) VALUES (?, '검토전', ?, ?, 'now', 'now')",
                  (addr, stage, uid))
    c.execute("UPDATE houses SET stage = 99 WHERE address LIKE ?", ("H%",))      # 이미 옮긴 것은 표시만 (아래에서 안 봄)
c.close()
D._initialized = False
D.init_db()
c = D.connect()
st = {r["address"]: r["stage"] for r in c.execute("SELECT address, stage FROM houses WHERE address LIKE ?", ("E%",))}
assert st == {"E5": 3, "E7": 5, "E8": 6}, st                      # 8단계 → 6단계
print("INSTALL MIGRATION OK" + (" (pg)" if c.pg else " (sqlite)"))
