"""단계별 테스트 샘플 프로젝트 만들기 (1~9단계 + 보류 하나씩).

현재 설정된 DB(로컬 data/app.db, 또는 DATABASE_URL)에 실제 화면 경로를 그대로 호출해서 만든다.
이미 있는 관리자 한 명과 설치업자 한 명으로 작업한다 (비밀번호 없이 세션 쿠키를 직접 만든다).

    .venv/bin/python tests/sample_projects.py ADMIN_EMAIL INSTALLER_EMAIL

주소가 "[샘플" 로 시작하는 프로젝트가 이미 있으면 아무것도 하지 않는다.
"""
import base64, io, json, re, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from PIL import Image, ImageDraw

from app.config import SECRET_KEY
from app.db import connect
from app.main import app

ADMIN_EMAIL, INSTALLER_EMAIL = sys.argv[1], sys.argv[2]
TD = datetime.now(ZoneInfo("Europe/London")).date()
D = lambda n: (TD + timedelta(days=n)).isoformat()

with TestClient(app):   # 마이그레이션
    pass
conn = connect()


def one(sql, *args):
    return conn.execute(sql, args).fetchone()


def client(email):
    u = one("SELECT id, role FROM users WHERE email = ? AND active = 1", email)
    if not u:
        sys.exit(f"활성 사용자 없음: {email}")
    data = base64.b64encode(json.dumps({"user_id": u["id"], "lang": "ko"}).encode())
    c = TestClient(app, follow_redirects=True)
    c.cookies.set("aircon_session", TimestampSigner(str(SECRET_KEY)).sign(data).decode())
    return c, u["id"], u["role"]


office, _, role = client(ADMIN_EMAIL)
inst, INST, irole = client(INSTALLER_EMAIL)
assert role in ("staff", "manager") and irole == "installer", "관리자/매니저 이메일, 설치업자 이메일 순서로 주세요"
if one("SELECT id FROM houses WHERE address LIKE ?", "[샘플%"):
    sys.exit("샘플 프로젝트가 이미 있습니다. 그대로 둡니다.")
# 1·2차 시공에 배정하려면 F-Gas 승인이 필요하다 → 샘플용으로 승인된 자격으로 맞춘다
from app.db import now as _now
with conn:
    conn.execute("""UPDATE users SET fgas_no = COALESCE(fgas_no, 'F-GAS SAMPLE'), fgas_expiry = COALESCE(fgas_expiry, ?),
                    fgas_approved_at = COALESCE(fgas_approved_at, ?) WHERE id = ?""", (D(365), _now(), INST))


def ok(r):
    assert r.status_code < 400, (r.request.url, r.status_code, r.text[:300])
    assert "flash-error" not in r.text, (r.request.url, re.findall(r'flash-error">([^<]*)', r.text))
    return r


def photo(seed):
    base = [(196, 186, 170), (178, 190, 196), (205, 200, 190), (170, 180, 168), (190, 176, 168)][seed % 5]
    im = Image.new("RGB", (1200, 900), base)
    dr = ImageDraw.Draw(im)
    for i in range(0, 1600, 60):
        dr.line([(i, 0), (i - 400, 900)], fill=tuple(c - 12 for c in base), width=18)
    dr.rectangle([380, 260, 820, 380], fill=(245, 245, 242), outline=(120, 120, 120), width=6)
    b = io.BytesIO(); im.save(b, "JPEG", quality=85)
    return ("files", (f"sample{seed}.jpg", b.getvalue(), "image/jpeg"))


PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj 3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\ntrailer<</Root 1 0 R>>"
ROOMS = [{"name": "거실", "indoor_type": "wall", "kw": "3.5"}, {"name": "침실 1", "indoor_type": "wall", "kw": "2.5"}]
ACC = {"controls": ["app", "lcd"], "lcd_colour": "white", "grille": "na", "plenum": "na", "hatch": "na"}


def house(stage, addr, pc, landlord):
    r = ok(office.post("/houses/new", data=dict(address=f"[샘플 {stage}] {addr}", postcode=pc, landlord_name=landlord, landlord_phone="+44 7700 111222")))
    h = int(re.search(r"/houses/(\d+)", str(r.url)).group(1))
    ok(office.post(f"/houses/{h}/members", data=dict(user_id=str(INST))))
    return h


def set_stage(h, s):
    """샘플용: 진행을 다 흉내 내지 않은 단계는 기록과 함께 DB에서 맞춘다."""
    from app.db import audit, now
    with conn:
        old = one("SELECT stage FROM houses WHERE id = ?", h)["stage"]
        actor = one("SELECT id FROM users WHERE email = ?", ADMIN_EMAIL)["id"]
        conn.execute("UPDATE houses SET stage = ?, updated_by = ?, updated_at = ? WHERE id = ?", (s, actor, now(), h))
        audit(conn, house_id=h, actor_id=actor, action="stage", detail=f"{old} → {s}")


def book(h, kind, date, start, end, mode="confirmed"):
    ok(office.post("/visits", data=dict(house_id=str(h), kind=kind, installer_id=str(INST), mode=mode,
                                        date=date or "", start_time=start or "", end_time=end or "")))
    v = one("SELECT id FROM visits WHERE house_id = ? AND kind = ? ORDER BY id DESC", h, kind)
    assert v, f"방문 예약 실패: {h} {kind}"
    return v["id"]


def survey(h, submit):
    """설치업자가 방 2개 + 실외기 현장조사."""
    book(h, "survey", D(-40 + len(made)), "10:00", "11:30")
    ok(inst.post(f"/houses/{h}/survey/start"))
    sv = one("SELECT id FROM surveys WHERE house_id = ?", h)["id"]
    ok(inst.post(f"/houses/{h}/survey/rooms/new", data=dict(name="침실 1")))
    rooms = [r["id"] for r in conn.execute("SELECT id FROM survey_rooms WHERE survey_id = ? ORDER BY id", (sv,))]
    for k, (rid, rm) in enumerate(zip(rooms, ROOMS)):
        ok(inst.post(f"/houses/{h}/survey/rooms/{rid}", data=dict(**rm, memo="뒤쪽이 외벽.", action="stay"), files=[photo(k)]))
        ok(inst.post(f"/houses/{h}/survey/rooms/{rid}/acc", data=dict(**ACC, action="next")))
    odu = one("SELECT id FROM survey_odus WHERE survey_id = ?", sv)["id"]
    ok(inst.post(f"/houses/{h}/survey/outdoor", data={f"odu_{odu}_mount": "bracket", f"odu_{odu}_note": "측면 벽", "system": "multi",
                                                       "pipe_ok": "yes", "power_near": "no", "electric_work": "need", "trunking": "incl",
                                                       "trunking_colour": "white", "access": "ladder",
                                                       "action": "submit" if submit else "later"}))


def scope_v1(h, lock):
    """현장조사 검토 → 작업 범위 v1 (→ 확정)."""
    ok(office.post(f"/houses/{h}/survey/reviewed"))
    ok(office.post(f"/houses/{h}/scope/from-survey"))
    if lock:
        ok(office.post(f"/houses/{h}/scope/1/lock"))
        ok(inst.post(f"/houses/{h}/scope/1/ack"))


def required_docs(h):
    for cat, title, name in (("drawing", "건물 도면", "drawing.pdf"), ("catalogue", "제품 카탈로그", "catalogue.pdf"), ("lats", "LATS 보고서", "lats.pdf")):
        ok(office.post(f"/houses/{h}/docs", data=dict(category=cat, title=title), files=[("files", (name, PDF, "application/pdf"))]))


def day(visit, problem=False, photos=2):
    ok(inst.post(f"/visits/{visit}/arrive", data=dict(lat="51.4677", lng="-0.3612", accuracy="9")))
    for k in range(photos):
        ok(inst.post(f"/visits/{visit}/photos", data=dict(tag=ROOMS[k % 2]["name"]), files=[photo(k + 2)]))


def finish(visit, work):
    ok(inst.post(f"/visits/{visit}/finish", data=dict(work=work, problem="no", note="", lat="51.4677", lng="-0.3612")))
    rep = one("SELECT id FROM day_reports WHERE visit_id = ? ORDER BY id DESC", visit)["id"]
    ok(office.post(f"/approvals/report/{rep}/review"))


made = []

# 1. 현장조사 예약 — 설치업자에게 가능한 날짜를 묻는 중
h = house(1, "1 Acacia Avenue, Hounslow", "TW3 1AA", "Alice Brown")
book(h, "survey", None, None, None, mode="requested")
set_stage(h, 1)
made.append(h)

# 2. 현장조사 — 설치업자가 작성 중 (아직 제출 전)
h = house(2, "2 Birch Road, Ealing", "W5 2BB", "Ben Clark")
survey(h, submit=False)
set_stage(h, 2)
made.append(h)

# 3. 견적·범위 협의 — 현장조사 검토 완료, 작업 범위 v1, 견적·집주인 대화 기록
h = house(3, "3 Cedar Close, Chiswick", "W4 3CC", "Chloe Davis")
survey(h, submit=True)
scope_v1(h, lock=False)
ok(office.post(f"/houses/{h}/entries/quote", data=dict(amount="£4,800", units="2", body="벽걸이 2대 (거실 3.5 kW, 침실 2.5 kW). 배관 5m 포함.")))
ok(office.post(f"/houses/{h}/entries/conversation", data=dict(channel="전화", occurred_at=f"{D(-1)}T15:00", body="견적 설명함. 실외기 위치 이웃과 상의 후 회신 예정.")))
set_stage(h, 3)
made.append(h)

# 4. 최종 확정 — 작업 범위 확정(잠금), 설치업자 확인 완료
h = house(4, "4 Dene Gardens, Richmond", "TW10 6AA", "Daniel Evans")
survey(h, submit=True)
scope_v1(h, lock=True)
made.append(h)

# 5. 일정 조율 — 필수 자료 준비, 1차 시공 예약
h = house(5, "5 Elm Grove, Twickenham", "TW1 4AB", "Emma Fox")
survey(h, submit=True)
scope_v1(h, lock=True)
required_docs(h)
book(h, "install", D(5), "08:00", "16:00")
set_stage(h, 5)
made.append(h)

# 6. 1차 시공 — 오늘 오전 현장 도착, 사진
h = house(6, "6 Fern Street, Brentford", "TW8 0AA", "George Hall")
survey(h, submit=True)
scope_v1(h, lock=True)
required_docs(h)
day(book(h, "install", D(0), "08:00", "12:00"))
made.append(h)

# 7. 2차 시공 — 1차 끝남, 오늘 오후 2차 시공 중, 변경 요청 대기
h = house(7, "7 Grove Lane, Kew", "TW9 4DD", "Hannah Irwin")
survey(h, submit=True)
scope_v1(h, lock=True)
required_docs(h)
v = book(h, "install", D(-7), "08:00", "16:00"); day(v); finish(v, ["pipework", "electrics"])
set_stage(h, 7)
day(book(h, "install", D(0), "13:00", "17:00"), photos=1)
ok(inst.post(f"/houses/{h}/variations", data=dict(kind="add_pipe", body="배관 3m 추가 필요 — 실외기까지 거리가 김")))
made.append(h)

# 8. 인수인계 — 1·2차 시공 끝, 회사 확인 완료
h = house(8, "8 Holly Road, Isleworth", "TW7 5EE", "Ian James")
survey(h, submit=True)
scope_v1(h, lock=True)
required_docs(h)
v = book(h, "install", D(-10), "08:00", "16:00"); day(v); finish(v, ["pipework", "electrics"])
v = book(h, "install", D(-3), "08:00", "16:00"); day(v); finish(v, ["indoor", "outdoor", "commissioning"])
set_stage(h, 8)
made.append(h)

# 9. 완료
h = house(9, "9 Ivy Crescent, Hampton", "TW12 2FF", "Julia King")
survey(h, submit=True)
scope_v1(h, lock=True)
required_docs(h)
v = book(h, "install", D(-21), "08:00", "16:00"); day(v); finish(v, ["pipework", "electrics"])
v = book(h, "install", D(-14), "08:00", "16:00"); day(v); finish(v, ["indoor", "outdoor", "commissioning"])
set_stage(h, 9)
made.append(h)

# 보류 — 견적 단계에서 고객 사정으로 멈춤
h = house("보류", "10 Juniper Way, Feltham", "TW13 3GG", "Kevin Lee")
survey(h, submit=True)
scope_v1(h, lock=False)
set_stage(h, 3)
ok(office.post(f"/houses/{h}/hold", data=dict(note="고객 해외 출장 — 11월 중순 재연락")))
made.append(h)

for r in conn.execute(f"SELECT id, ref_no, stage, on_hold, address FROM houses WHERE id IN ({','.join('?' * len(made))}) ORDER BY id", made):
    print(r["ref_no"], r["stage"], "보류" if r["on_hold"] else "", r["address"])
