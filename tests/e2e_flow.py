"""한 프로젝트를 1단계부터 9단계까지: 단계가 저절로 맞게 움직이는지, '다음 할 일'이 막히지 않는지,
막다른 길(엉뚱한 날 현장 도착, 보고 두 번, 검토 끝난 조사에 사진 요청 등)이 없는지."""
import os, re, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(__file__))
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app):
    pass
from testdb import db, approve_fgas

codes = {k: v for k, v in db.execute("select key, value from settings")}
D = lambda n: (datetime.now(ZoneInfo("Europe/London")).date() + timedelta(days=n)).isoformat()


def mk(code, name, email):
    c = TestClient(app, headers={"Accept-Language": "ko"}); c.get("/lang/ko")
    c.post("/signup", data=dict(invite_code=code, name=name, email=email, phone="", password="pass1234", password2="pass1234"))
    return c


admin = mk(codes["invite_code_staff"], "Admin", "a@ex.com")
dave = mk(codes["invite_code_installer"], "Dave", "d@ex.com")
DAVE = db.execute("select id from users where email='d@ex.com'").fetchone()[0]
approve_fgas("d@ex.com")          # 1·2차 시공 배정 조건 (자격증 승인 흐름은 e2e_visits에서)
stage = lambda: db.execute("select stage from houses where id=?", (hid,)).fetchone()[0]
step = lambda c: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.search(r'id="next-step">(.*?)</section>', c.get(f"/houses/{hid}").text, re.S).group(1)))
last_visit = lambda: db.execute("select id from visits order by id desc").fetchone()[0]
png = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def book(kind, date, mode="confirmed", **kw):
    data = dict(house_id=str(hid), kind=kind, installer_id=str(DAVE), mode=mode, date=date or "", start_time="08:00", end_time="16:00")
    data.update(kw)
    return admin.post("/visits", data=data, follow_redirects=False)


r = admin.post("/houses/new", data=dict(address="1 Flow Road", postcode="TW1 1AA"), follow_redirects=True)
hid = int(re.search(r"/houses/(\d+)", str(r.url)).group(1))

# 1단계: 설치업자 배정 → 현장조사 예약. 날짜를 묻기만 해서는 2단계로 가지 않는다
assert "설치업자를 배정하세요" in step(admin)
admin.post(f"/houses/{hid}/members", data=dict(user_id=str(DAVE)))
assert "현장조사 방문을 예약하세요" in step(admin)
assert book("survey", None, mode="requested").status_code == 303 and stage() == 1
sv = last_visit()
assert "날짜를 고르는 중" in step(admin) and "날짜 고르기" in step(dave)
dave.post(f"/visits/{sv}/dates", data=dict(date=[D(0)]))
assert "하루를 확정하세요" in step(admin)
admin.post(f"/visits/{sv}/confirm", data=dict(date=D(0), start_time="09:00", end_time="10:00"))
assert stage() == 1                                                   # 현장조사 단계 안에서 진행

# 2단계: 설치업자가 현장조사 작성·제출 → 방문도 끝남
assert "현장조사를 시작하세요" in step(dave)
dave.post(f"/houses/{hid}/survey/start")
s = db.execute("select id from surveys where house_id=?", (hid,)).fetchone()[0]
room = db.execute("select id from survey_rooms where survey_id=?", (s,)).fetchone()[0]
odu = db.execute("select id from survey_odus where survey_id=?", (s,)).fetchone()[0]
dave.post(f"/houses/{hid}/survey/rooms/{room}", data=dict(name="거실", indoor_type="wall", kw="3.5", action="stay"))
dave.post(f"/houses/{hid}/survey/rooms/{room}/acc", data=dict(grille="na", plenum="na", hatch="na"))
dave.post(f"/houses/{hid}/survey/outdoor", data={f"odu_{odu}_mount": "ground", "system": "single", "electric_work": "noneed", "trunking": "excl", "action": "submit"})
assert db.execute("select status from visits where id=?", (sv,)).fetchone()[0] == "done"          # 도착·취소 버튼이 남지 않음
assert "검토하세요" in step(admin) and "회사가 검토하고 있습니다" in step(dave)

# 회사가 검토 → 추가 사진 요청하면 설치업자가 다시 고칠 수 있게 열림 → 사진 올리면 요청이 지워짐
admin.post(f"/houses/{hid}/survey/reviewed")
admin.post(f"/houses/{hid}/survey/rooms/{room}/photo-request", data=dict(text="창문 쪽 벽 전체"))
assert db.execute("select status from surveys where id=?", (s,)).fetchone()[0] == "draft"
assert "추가 사진을 요청했습니다" in step(dave)
r = dave.post(f"/houses/{hid}/survey/rooms/{room}", data=dict(name="거실", action="stay"), files=[("files", ("w.png", png, "image/png"))])
assert r.status_code == 200 and db.execute("select photo_request from survey_rooms where id=?", (room,)).fetchone()[0] is None
dave.post(f"/houses/{hid}/survey/outdoor", data={f"odu_{odu}_mount": "ground", "system": "single", "electric_work": "noneed", "trunking": "excl", "action": "submit"})
# 회사가 직접 고칠 때 '저장'은 상태를 바꾸지 않는다
admin.post(f"/houses/{hid}/survey/reviewed")
admin.post(f"/houses/{hid}/survey/outdoor", data={f"odu_{odu}_mount": "ground", "system": "single", "electric_work": "noneed", "trunking": "excl", "action": "submit"})
assert db.execute("select status from surveys where id=?", (s,)).fetchone()[0] == "reviewed"
assert "조사 내용 보기" in dave.get(f"/houses/{hid}").text and "보기만 할 수 있고" in dave.get(f"/houses/{hid}/survey/rooms/{room}").text

# 3단계: 작업 범위 v1 → 4단계: 확정
assert "작업 범위 v1을 만드세요" in step(admin)
admin.post(f"/houses/{hid}/scope/from-survey")
assert stage() == 2 and "확정하세요" in step(admin) and "확인해 주세요" in step(dave)
dave.post(f"/houses/{hid}/scope/1/ack")
r = book("install", D(3)); assert r.status_code == 409 and "작업 범위가 아직 확정되지 않았습니다" in r.text
admin.post(f"/houses/{hid}/scope/1/lock")
assert stage() == 3 and "필수 자료" in step(admin)

# 5단계: 자료 → 첫 시공 날짜를 잡으면 5단계
for cat in ("drawing", "catalogue", "lats"):
    admin.post(f"/houses/{hid}/docs", data=dict(category=cat, title=cat), files=[("files", ("f.pdf", b"%PDF-1.4", "application/pdf"))])
assert "1차 시공 일정을 잡으세요" in step(admin)
# 결제 관문: 처음엔 꺼짐. 관리자가 켜면 계약금을 받아야 1차 시공 예약 가능
assert dave.get("/payments").status_code == 403 and 'id="payments"' not in dave.get(f"/houses/{hid}").text   # 설치업자에게 결제 안 보임
admin.post("/payments/gates", data={"deposit_before_install": "1", "interim_before_next_install": "1", "balance_before_complete": "1"})
r = book("install", D(3)); assert r.status_code == 409 and "계약금을 받기 전" in r.text
assert "계약금을 받기 전" in step(admin)
pay = lambda **kw: admin.post(f"/houses/{hid}/payments", data=kw, follow_redirects=True)
assert "총 계약 금액을 적어" in pay(deposit_amount="1200", deposit_status="received").text          # 총액 먼저
assert "더한 금액이 총 계약 금액보다 큽니다" in pay(total="1000", deposit_amount="800", interim_amount="400").text
pay(total="£4,000", deposit_amount="1,200", interim_amount="2000", deposit_status="received")
assert db.execute("select status, amount from payments where house_id=? and kind='deposit'", (hid,)).fetchone() == ("received", "1200")
assert db.execute("select amount from payments where house_id=? and kind='balance'", (hid,)).fetchone()[0] == "800"   # 잔금 자동 = 4000 − 1200 − 2000
assert "£2,800" in admin.get("/payments").text                                      # 남은 금액
assert "계약금" in admin.get(f"/houses/{hid}?tab=history").text
assert book("install", D(3)).status_code == 303 and stage() == 3
ff = last_visit()
assert db.execute("select seq from visits where id=?", (ff,)).fetchone()[0] == 1 and "1차 시공" in admin.get(f"/houses/{hid}").text

# 방문일이 아닌 날에는 현장 도착 불가 (단계가 앞서 나가지 않음)
r = dave.post(f"/visits/{ff}/arrive", follow_redirects=True)
assert "당일에만" in r.text and stage() == 3 and not db.execute("select 1 from checkins where visit_id=?", (ff,)).fetchone()
assert "방문일(" in dave.get(f"/visits/{ff}").text

# 6단계: 당일 도착 → 작업 끝 (두 번 보내지 않음) → 보고가 현장 기록에 보임
db.execute("update visits set date=? where id=?", (D(0), ff))
dave.post(f"/visits/{ff}/arrive")
assert stage() == 4
dave.post(f"/visits/{ff}/finish", data=dict(work=["pipework"], problem="no", note="배관 끝"))
r = dave.post(f"/visits/{ff}/finish", data=dict(work=["pipework"], problem="no"), follow_redirects=True)
assert "이미 작업 끝 보고를 보냈습니다" in r.text
assert db.execute("select count(*) from day_reports where visit_id=?", (ff,)).fetchone()[0] == 1
assert "배관 끝" in admin.get(f"/visits/{ff}").text and "회사 확인 전" in admin.get(f"/visits/{ff}").text
assert "작업 끝 보고를 확인하세요" in step(admin)
rep = db.execute("select id from day_reports where visit_id=?", (ff,)).fetchone()[0]
page = admin.get(f"/houses/{hid}").text
assert 'id="pending"' in page and f"/approvals/report/{rep}/review" in page                # 프로젝트 화면에서 바로 확인
assert "확인할 일 1" in admin.get("/houses").text                                           # 목록에도 개수
r = admin.post(f"/approvals/report/{rep}/review", data=dict(back=f"/houses/{hid}#pending"), follow_redirects=False)
assert r.headers["location"] == f"/houses/{hid}#pending" and 'id="pending"' not in admin.get(f"/houses/{hid}").text
# 1차가 끝나면: 다음 시공(n차)을 더 잡거나 시공 완료 확인. 2차부터는 중도금 관문
assert "1차 시공까지 끝났습니다" in step(admin) and "시공 완료 확인" in step(admin)
r = book("install", D(-1), override_reason="테스트"); assert r.status_code == 409 and "중도금을 받기 전" in r.text
r = pay(total="4000", deposit_amount="1200", interim_amount="", deposit_status="received", interim_status="received")
assert "금액이 없어서" in r.text and db.execute("select status from payments where house_id=? and kind='interim'", (hid,)).fetchone()[0] == "not_due"
pay(total="4000", deposit_amount="1200", interim_amount="2000", deposit_status="received", interim_status="received")
assert db.execute("select status from payments where house_id=? and kind='interim'", (hid,)).fetchone()[0] == "received"
# 2차 시공 — 지나간 날 보고를 안 보내면 오늘 화면에 남는다
assert book("install", D(-1), override_reason="테스트").status_code == 303
sf = last_visit()
assert db.execute("select seq from visits where id=?", (sf,)).fetchone()[0] == 2
assert admin.post(f"/houses/{hid}/install-done", follow_redirects=True).text.count("끝나지 않은 시공 방문") == 1   # 열린 시공이 있으면 안 됨
assert "보고 안 보냄" in dave.get("/houses").text and "작업 끝 보고를 아직 보내지 않았습니다" in step(dave)
dave.post(f"/visits/{sf}/finish", data=dict(work=["indoor", "commissioning"], problem="no"))
rep = db.execute("select id from day_reports where visit_id=?", (sf,)).fetchone()[0]
admin.post(f"/approvals/report/{rep}/review")
assert stage() == 4 and "2차 시공까지 끝났습니다" in step(admin)             # 몇 차까지 할지는 회사가 정한다
admin.post(f"/houses/{hid}/install-done")
assert stage() == 5                                                   # 시공 완료 확인 → 인수인계
# 잘못 눌렀으면 되돌리기 (사유 필수, 기록에 남음, 설치업자는 불가)
assert 'id="stage-back"' in admin.get(f"/houses/{hid}").text and 'id="stage-back"' not in dave.get(f"/houses/{hid}").text
r = admin.post(f"/houses/{hid}/stage-back", follow_redirects=True); assert "사유를 적어" in r.text and stage() == 5
assert dave.post(f"/houses/{hid}/stage-back", data=dict(note="x")).status_code == 403
admin.post(f"/houses/{hid}/stage-back", data=dict(note="잘못 눌렀음"))
assert stage() == 4 and "잘못 눌렀음" in admin.get(f"/houses/{hid}?tab=status").text
admin.post(f"/houses/{hid}/install-done")
assert stage() == 5

# 8단계(완료): '인수인계 완료' 버튼 (단계를 직접 고르지 않음)
assert admin.post(f"/houses/{hid}/stage", data=dict(stage="8")).status_code in (404, 405)
r = admin.post(f"/houses/{hid}/handover-done", follow_redirects=True); assert "잔금을 받기 전" in r.text and stage() == 5   # 잔금 관문
assert "잔금을 받기 전" in step(admin) and "완료 막힘" in admin.get("/payments").text
pay(total="4000", deposit_amount="1200", interim_amount="2000", deposit_status="received", interim_status="received", balance_status="received")
assert "인수인계 완료" in step(admin)
admin.post(f"/houses/{hid}/handover-done")
assert stage() == 6
admin.post(f"/houses/{hid}/stage-back", data=dict(note="서류 다시 확인"))
assert stage() == 5
admin.post(f"/houses/{hid}/handover-done")
assert stage() == 6
assert "완료된 프로젝트입니다" in step(admin)

# 프로젝트 화면에서 방문 예약 → 그 프로젝트로 돌아감. 겹치면 경고 화면에서 바꾸거나 그대로 예약
assert 'id="book"' in admin.get(f"/houses/{hid}").text
pb = lambda **kw: admin.post("/visits", data=dict(house_id=str(hid), kind="other", installer_id=str(DAVE), mode="confirmed",
                                                 date=D(4), start_time="09:00", end_time="10:00", **{"from": "project"}, **kw), follow_redirects=False)
r = pb(); assert r.status_code == 303 and r.headers["location"] == f"/houses/{hid}#visits"
ov = last_visit()
r = pb(); assert r.status_code == 409 and "이미 이 시간에 예약" in r.text and "그대로 예약" in r.text and f'value="{hid}"' in r.text
r = pb(override_reason="고객 요청", force="1"); assert r.status_code == 303 and last_visit() != ov
# 일정 화면 빈 칸 → 그 자리에서 바로 예약 (아래 '방문 예약하기' 칸은 없음), 일정 화면으로 돌아옴
cal = admin.get("/calendar").text
assert 'id="new-visit"' in cal and 'id="book"' not in cal
r = admin.post("/visits", data={"house_id": str(hid), "kind": "other", "installer_id": str(DAVE), "mode": "confirmed",
                                "date": D(6), "start_time": "13:00", "end_time": "14:00", "from": "calendar"}, follow_redirects=False)
assert r.status_code == 303 and r.headers["location"].startswith("/calendar")
r = admin.post("/visits", data={"house_id": str(hid), "kind": "other", "installer_id": str(DAVE), "mode": "confirmed",
                                "date": D(6), "start_time": "13:00", "end_time": "14:00", "from": "calendar"})
assert r.status_code == 409 and 'name="from" value="calendar"' in r.text        # 겹침 경고 → 고친 뒤 일정 화면으로
# 일정 화면: 방문을 누르면 그 자리에서 바꾸는 창
cal = admin.get(f"/calendar?week={D(4)}").text
assert f'id="visit-{ov}"' in cal and f"/visits/{ov}/reschedule" in cal
r = admin.post(f"/visits/{ov}/reschedule", data=dict(date=D(5), start_time="09:00", end_time="10:00", installer_id=str(DAVE),
                                                     back=f"/calendar?week={D(4)}"), follow_redirects=False)
assert r.headers["location"] == f"/calendar?week={D(4)}" and db.execute("select date from visits where id=?", (ov,)).fetchone()[0] == D(5)

# 출결: 현장 좌표(우편번호 TW1 1AA)와 도착 위치 비교, 노쇼·도착 기록 없음
db.execute("update houses set lat = 51.4470, lng = -0.3290, geo_postcode = 'TW1 1AA' where id = ?", (hid,))
assert book("other", D(0), start_time="00:00", end_time="00:30", override_reason="출결 테스트").status_code == 303
far_v = last_visit()
dave.post(f"/visits/{far_v}/arrive", data=dict(lat="51.5074", lng="-0.1278", accuracy="10"))   # 런던 시내 (약 15km)
page = admin.get(f"/users/{DAVE}/attendance").text
assert "떨어짐" in page and "km" in page and "지도" in page
assert "현장과 떨어짐" in admin.get(f"/visits/{far_v}").text and "출결" not in dave.get(f"/visits/{far_v}").text   # 회사만
db.execute("insert into visits (house_id, kind, installer_id, date, start_time, end_time, status, created_by, created_at, updated_at) "
           "values (?, 'other', ?, ?, '08:00', '09:00', 'confirmed', 1, '2026-01-01', '2026-01-01')", (hid, DAVE, D(-3)))
assert "노쇼" in admin.get(f"/users/{DAVE}/attendance").text and "노쇼 1" in admin.get("/users/installers").text
assert dave.get(f"/users/{DAVE}/attendance").status_code == 403

# 이름을 바꾸면 작업 기록에 남은 옛 이름(배정·방문 예약)도 새 이름으로
dave.post("/me/name", data=dict(name="Dave Cole"))
log = admin.get(f"/houses/{hid}?tab=history").text
details = " ".join(r[0] or "" for r in db.execute("select detail from audit_log where house_id=?", (hid,)).fetchall())
assert "Dave Cole" in log and '"installer": "Dave"' not in details and '"installer": "Dave Cole"' in details

# 홈 단계별 현황 필터: 진행 중(기본) / 보류 / 완료
home = admin.get("/approvals").text
assert 'class="znum selected" href="?view=active#projects"' in home
assert "1 Flow Road" in admin.get("/approvals?view=done").text and "1 Flow Road" not in admin.get("/approvals?view=active").text.split('id="projects"')[1]

# 보류: 예약하면 경고, 설치업자에게도 보임
admin.post(f"/houses/{hid}/hold")
r = book("other", D(4)); assert r.status_code == 409 and "보류 중인 프로젝트입니다" in r.text
assert "보류했습니다" in step(dave) and "보류 해제" in step(admin)

# 기록은 사람이 읽는 문장으로 (JSON·영어 키 없음), 되돌아갈 주소는 사이트 안으로만
log = admin.get(f"/houses/{hid}?tab=history").text
assert '{"kind"' not in log and "approved" not in log and "· report" not in log
dave.post(f"/houses/{hid}/variations", data=dict(kind="add_pipe", body="배관 2m"))
x = db.execute("select id from variations order by id desc").fetchone()[0]
r = admin.post(f"/variations/{x}/decide", data=dict(status="approved", back="https://evil.example"), follow_redirects=False)
assert r.headers["location"].startswith("/")
assert "VO-1" in dave.get("/houses").text                              # 결정 결과가 설치업자 오늘 화면에

# 오류 화면: 뒤로 + 역할별 첫 화면
r = dave.get("/houses/999999"); assert r.status_code == 404 and "history.back()" in r.text and 'href="/houses"' in r.text
# 화면 제목(<title>) 안에 스크립트·태그가 잘못 들어가지 않았는지 (템플릿 블록 실수 방지)
import re as _re
for path in ["/approvals", "/houses", f"/houses/{hid}", "/calendar", "/payments", "/users/installers", "/guide", "/me", "/settings/survey"]:
    t = _re.search(r"<title>(.*?)</title>", admin.get(path).text, _re.S).group(1)
    assert "<" not in t, (path, t[:80])
assert 'data-default="schedule"' in admin.get(f"/houses/{hid}").text and 'data-default="log"' in admin.get(f"/houses/{hid}?tab=history").text
print("FLOW OK")
