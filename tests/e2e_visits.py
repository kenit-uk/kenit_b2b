"""방문·현장·승인 대기: 예약 관문·중복·F-Gas 경고, 날짜 후보, 가능한가요?, 현장 도착·사진·문제·변경 요청·작업 끝, 승인 대기, F-Gas"""
import json, os, re, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(__file__))
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app):
    pass
from testdb import db

codes = {k: v for k, v in db.execute("select key, value from settings")}
TODAY = datetime.now(ZoneInfo("Europe/London")).date()
D = lambda n: (TODAY + timedelta(days=n)).isoformat()


def mk(code, name, email):
    c = TestClient(app); c.get("/lang/ko")
    assert c.post("/signup", data=dict(invite_code=code, name=name, email=email, password="pass1234", password2="pass1234")).status_code == 200
    return c


admin = mk(codes["invite_code_staff"], "Mina", "a@ex.com")
dave = mk(codes["invite_code_installer"], "Dave C.", "d@ex.com")
sam = mk(codes["invite_code_installer"], "Sam R.", "s@ex.com")
uid = lambda e: db.execute("select id from users where email = ?", (e,)).fetchone()[0]
DAVE, SAM = uid("d@ex.com"), uid("s@ex.com")
r = admin.post("/houses/new", data=dict(address="14 Elm Road, Hounslow", postcode="TW3 1AA"), follow_redirects=True)
hid = int(re.search(r"/houses/(\d+)", str(r.url)).group(1))
admin.post(f"/houses/{hid}/members", data=dict(user_id=DAVE))
nvis = lambda: db.execute("select count(*) from visits").fetchone()[0]


def book(c, **kw):
    data = dict(house_id=str(hid), kind="install", installer_id=str(DAVE), mode="confirmed", date=D(0), start_time="08:00", end_time="15:00")
    data.update({k: str(v) for k, v in kw.items()})
    return c.post("/visits", data=data, follow_redirects=False)


# 첫 화면: 역할별
assert dave.get("/", follow_redirects=False).headers["location"] == "/houses"
assert admin.get("/", follow_redirects=False).headers["location"] == "/approvals"
assert dave.get("/calendar").status_code == 403 and dave.get("/approvals").status_code == 403
assert book(dave).status_code == 403

# 1차 시공은 필수 자료 3종 없이는 막힘 (그대로 예약도 불가)
r = book(admin); assert r.status_code == 409 and "시공을 예약할 수 없습니다" in r.text and nvis() == 0
r = book(admin, override_reason="급함"); assert r.status_code == 409 and nvis() == 0
pdf = b"%PDF-1.4"
for cat in ("drawing", "catalogue", "lats"):
    admin.post(f"/houses/{hid}/docs", data=dict(category=cat, title=cat), files=[("files", ("f.pdf", pdf, "application/pdf"))])
r = book(admin); assert r.status_code == 409 and "작업 범위가 아직 확정되지 않았습니다" in r.text and nvis() == 0   # 범위 미확정 경고
admin.post(f"/houses/{hid}/scope-edit", data={"summary": "v1", "r0_name": "거실", "r0_indoor_type": "wall", "r0_kw": "2.5"})
admin.post(f"/houses/{hid}/scope/1/lock")
assert db.execute("select locked_at from scope_versions where house_id=?", (hid,)).fetchone()[0]
# F-Gas 자격증이 승인되지 않은 설치업자는 1·2차 시공에 배정 불가 (그대로 예약도 불가)
r = book(admin); assert r.status_code == 409 and "F-Gas 자격증이 아직 승인되지 않아" in r.text and nvis() == 0
r = book(admin, override_reason="급함"); assert r.status_code == 409 and nvis() == 0
assert book(admin, kind="install", mode="requested", date="").status_code == 409                    # 날짜를 묻는 예약도 막힘
# 설치업자가 내 정보에서 자격증 올림 → 회사 '지금 처리할 것' → 승인하면 배정 가능
assert dave.post("/me/licences", data=dict(kind="fgas", number="F-GAS 1"), files=[("files", ("l.pdf", pdf, "application/pdf"))],
                 follow_redirects=True).text.count("번호와 만료일") == 1                                   # F-Gas는 번호·만료일 필수
dave.post("/me/licences", data=dict(kind="fgas", number="F-GAS 1", expiry=D(400)), files=[("files", ("l.pdf", pdf, "application/pdf"))])
lid = db.execute("select id from licences order by id desc").fetchone()[0]
assert "승인 기다리는 중" in dave.get("/me").text and "자격증 확인" in admin.get("/approvals").text
home = admin.get(f"/approvals?item=licence-{lid}").text                                 # 홈에서 바로 승인·반려
assert f"/licences/{lid}/review" in home and "F-GAS 1" in home
assert dave.get(f"/licences/{lid}").status_code == 200 and sam.get(f"/licences/{lid}").status_code == 404
assert dave.post(f"/licences/{lid}/review", data=dict(decision="approved")).status_code == 403
r = admin.post(f"/licences/{lid}/review", data=dict(decision="rejected"), follow_redirects=True); assert "반려 사유" in r.text   # 반려는 사유 필수
admin.post(f"/licences/{lid}/review", data=dict(decision="approved"))
assert db.execute("select fgas_no, fgas_expiry from users where id=?", (DAVE,)).fetchone() == ("F-GAS 1", D(400))
assert "F-Gas 승인됨" in dave.get("/me").text and "자격증 확인" not in admin.get("/approvals").text
# 이름 바꾸기: 본인, 회사
dave.post("/me/name", data=dict(name="Dave C."))
admin.post(f"/users/{SAM}/name", data=dict(name="Sam R."))
assert db.execute("select name from users where id in (?, ?) order by id", (DAVE, SAM)).fetchall() == [("Dave C.",), ("Sam R.",)]
assert dave.post(f"/users/{SAM}/name", data=dict(name="x")).status_code == 403
assert book(admin).status_code == 303 and nvis() == 1
v1 = db.execute("select id from visits order by id desc").fetchone()[0]

# 중복 배정: 같은 설치업자·같은 날·시간 겹침 → 경고, 사유를 적으면 예약 (기록에 남음). 시간이 안 겹치면 OK
r = book(admin, kind="other", start_time="14:00", end_time="16:00"); assert r.status_code == 409 and "이미 이 시간에 예약" in r.text
assert book(admin, kind="other", start_time="15:30", end_time="17:00").status_code == 303            # 겹치지 않음
assert book(admin, kind="other", start_time="14:00", end_time="16:00", override_reason="고객 요청").status_code == 303
assert db.execute("select override_reason from visits order by id desc").fetchone()[0] == "고객 요청"
assert "고객 요청" in admin.get(f"/houses/{hid}?tab=history").text
cal = admin.get(f"/calendar?week={D(0)}").text
assert "14 Elm Road" in cal and "clash" in cal

# F-Gas 만료 경고 (2차 시공)
admin.post(f"/users/{DAVE}/fgas", data=dict(fgas_no="F123", fgas_expiry=D(-1), fgas_company="REFCOM"))
assert db.execute("select fgas_no, fgas_expiry from users where id=?", (DAVE,)).fetchone() == ("F123", D(-1))
r = book(admin, kind="install", date=D(3)); assert r.status_code == 409 and "F-Gas" in r.text
page = admin.get("/users/installers").text; assert "F-Gas 만료" in page and "REFCOM" in page
assert dave.post(f"/users/{DAVE}/fgas", data=dict(fgas_no="x")).status_code == 403

# 날짜 후보 요청 → 설치업자가 2~3일 고름 → 회사가 확정
assert book(admin, kind="survey", mode="requested", date="").status_code == 303
v2 = db.execute("select id from visits order by id desc").fetchone()[0]
assert db.execute("select status, date from visits where id=?", (v2,)).fetchone() == ("requested", None)
today_page = dave.get("/houses").text
assert "가능한 날짜 고르기" in today_page and "14 Elm Road" in today_page
page = dave.get(f"/visits/{v2}/dates").text
assert "언제 방문할 수 있나요?" in page
assert sam.get(f"/visits/{v2}/dates").status_code == 404                                   # 남의 방문
r = dave.post(f"/visits/{v2}/dates", data=dict(date=[D(5), D(6), D(7), D(8)]), follow_redirects=True); assert "1~3개" in r.text
dave.post(f"/visits/{v2}/dates", data=dict(date=[D(5), D(6)]))
assert db.execute("select status from visits where id=?", (v2,)).fetchone()[0] == "options"
assert [r[0] for r in db.execute("select date from visit_date_options where visit_id=? order by date", (v2,))] == [D(5), D(6)]
assert "방문 날짜 후보" in admin.get("/approvals").text
assert f"/houses/{hid}#confirm-{v2}" in admin.get("/approvals").text                     # 홈 → 프로젝트의 확정 칸으로
page = admin.get(f"/houses/{hid}").text                                                    # 확정은 프로젝트 일정에서
assert f'id="confirm-{v2}"' in page and f'value="{D(5)}"' in page and f'value="{D(6)}"' in page
assert f"/visits/{v2}/confirm" not in admin.get("/calendar").text                         # 일정 화면에서는 확정하지 않음
r = admin.post(f"/visits/{v2}/confirm", data=dict(date=D(6), start_time="10:00", end_time="11:00"), follow_redirects=False)
assert r.headers["location"] == f"/houses/{hid}#visits"
assert db.execute("select status, date from visits where id=?", (v2,)).fetchone() == ("confirmed", D(6))

# 가능한가요? → 아니요 → 불가 답변 → 승인 대기, 같은 날 다시 배정하면 경고. 배정 안 된 설치업자는 자동 배정
assert book(admin, kind="other", installer_id=SAM, mode="proposed", date=D(2)).status_code == 303
v3 = db.execute("select id from visits order by id desc").fetchone()[0]
assert db.execute("select 1 from house_members where house_id=? and user_id=?", (hid, SAM)).fetchone()
assert "가능한가요?" in sam.get(f"/houses/{hid}").text
sam.post(f"/visits/{v3}/reply", data=dict(answer="no"))
assert db.execute("select status from visits where id=?", (v3,)).fetchone()[0] == "declined"
assert "방문 불가 답변" in admin.get("/approvals").text
r = book(admin, kind="other", installer_id=SAM, date=D(2)); assert r.status_code == 409 and "불가" in r.text
assert sam.post(f"/visits/{v3}/reply", data=dict(answer="yes")).status_code == 403                    # 이미 답함
assert dave.get(f"/visits/{v3}").status_code == 404                                                 # 남의 방문

# 오늘 할 일 → 현장 도착 (위치 1회) → 1차 시공이면 6단계
assert "현장 도착" in dave.get("/houses").text
dave.post(f"/visits/{v1}/arrive", data=dict(lat="51.47", lng="-0.36", accuracy="12"))
c = db.execute("select kind, lat, lng from checkins where visit_id=?", (v1,)).fetchone(); assert c == ("arrive", 51.47, -0.36)
assert db.execute("select stage from houses where id=?", (hid,)).fetchone()[0] == 4
dave.post(f"/visits/{v1}/arrive", data=dict(lat="0", lng="0"))
assert db.execute("select count(*) from checkins where visit_id=?", (v1,)).fetchone()[0] == 1            # 한 번만
page = dave.get(f"/visits/{v1}").text
assert "위치 1회 저장됨" in page and "사진 찍기" in page and "오늘 작업 끝" in page
assert sam.post(f"/visits/{v1}/arrive").status_code == 404

# 사진 + 방 태그, 태그 고치기
png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
dave.post(f"/visits/{v1}/photos", data=dict(tag="실외"), files=[("files", ("a.png", png, "image/png")), ("files", ("b.png", png, "image/png"))])
ph = db.execute("select id, tag, visit_id from media where owner_type='visit' order by id").fetchall()
assert [p[1] for p in ph] == ["실외", "실외"] and ph[0][2] == v1
dave.post(f"/visits/{v1}/photos", data={f"tag_{ph[1][0]}": "거실"})
assert db.execute("select tag from media where id=?", (ph[1][0],)).fetchone()[0] == "거실"

# 문제 보고 / 회사에 메시지
dave.post(f"/houses/{hid}/messages", data=dict(kind="problem", visit_id=str(v1), body="벽 속에 장애물"), files=[("files", ("p.png", png, "image/png"))])
assert db.execute("select kind, body from messages").fetchone() == ("problem", "벽 속에 장애물")
appr = admin.get("/approvals").text; assert "문제 보고" in appr and "벽 속에 장애물" in appr
mid = db.execute("select id from messages").fetchone()[0]
page = admin.get(f"/approvals?item=message-{mid}").text; assert "벽 속에 장애물" in page and f"/approvals/message/{mid}/review" in page
admin.post(f"/approvals/message/{mid}/review")
assert db.execute("select reviewed_at from messages").fetchone()[0] is not None

# 변경 요청: 시공 금지 → 회사가 고객 승인 표시 → 시공 가능 (금액 없음)
dave.post(f"/houses/{hid}/variations", data=dict(kind="move_idu", visit_id=str(v1), body="왼쪽으로 1미터"), files=[("files", ("w.png", png, "image/png"))])
page = dave.get(f"/houses/{hid}/variations").text
assert "VO-1" in page and "시공 금지" in page and "£" not in page
xid = db.execute("select id from variations").fetchone()[0]
assert dave.post(f"/variations/{xid}/decide", data=dict(status="approved")).status_code == 403
assert "변경 요청" in admin.get("/approvals").text
admin.post(f"/variations/{xid}/decide", data=dict(status="approved", note="고객 전화 승인"))
assert "시공 가능" in dave.get(f"/houses/{hid}/variations").text and db.execute("select status from variations").fetchone()[0] == "approved"
dave.post(f"/houses/{hid}/variations", data=dict(kind="add_pipe", body="배관 3m"))
assert db.execute("select number from variations order by id desc").fetchone()[0] == 2

# 오늘 작업 끝: 문제 있음이면 사진·메모 필수
r = dave.post(f"/visits/{v1}/finish", data=dict(work=["indoor"], problem="yes", note=""))
assert r.status_code == 400 and "사진과 메모" in r.text
r = dave.post(f"/visits/{v1}/finish", data=dict(work=[], problem="no")); assert r.status_code == 400
dave.post(f"/visits/{v1}/finish", data=dict(work=["indoor", "pipework"], problem="yes", note="침실 2 벽 비어 있음", lat="51.47", lng="-0.36"),
          files=[("files", ("x.png", png, "image/png"))])
rep = db.execute("select work, has_problem, note from day_reports").fetchone()
assert json.loads(rep[0]) == ["indoor", "pipework"] and rep[1] == 1
assert db.execute("select status from visits where id=?", (v1,)).fetchone()[0] == "done"
assert db.execute("select count(*) from checkins where visit_id=? and kind='finish'", (v1,)).fetchone()[0] == 1
rid = db.execute("select id from day_reports").fetchone()[0]
page = admin.get(f"/approvals?item=report-{rid}").text
assert "문제 있음 보고" in page and "침실 2 벽 비어 있음" in page and "실외" in page and "거실" in page
admin.post(f"/approvals/report/{rid}/review")
assert f"report-{rid}" not in admin.get("/approvals").text

# 프로젝트 화면: 일정·변경 요청 카드, 설치업자는 본인 방문만
page = dave.get(f"/houses/{hid}").text
sched = page.split('id="visits"')[1].split('</section>')[0]
assert "일정" in page and "1차 시공" in sched and "불가 답변" not in sched and "Sam R." not in sched
page = admin.get(f"/houses/{hid}").text; assert "Sam R." in page and "불가 답변" in page
# 진행 중 프로젝트 표
assert "단계별 현황" in admin.get("/approvals").text and "WS-" in admin.get("/approvals").text
# 영어
dave.get("/lang/en")
en = dave.get("/houses").text; assert "My projects" in en and "Search" in en and en.index("Search") < en.index("My projects")
dave.get("/lang/ko")
r = dave.get("/houses?q=Elm").text; assert "검색 결과 1건" in r and "오늘 할 일" not in r
print("VISITS OK")
