"""빈틈 7가지: 비밀번호 찾기(문자)·재설정 링크, 로그인 반복 시도 제한, 관리자 휴대폰 번호 수정,
설치업자에게 예전 기록 입력란 숨김, 현장조사 '검토만 완료'·다시 열기, 사진 미리보기, 방문 일정 변경"""
import io, os, re, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(__file__))
from fastapi.testclient import TestClient
from app.main import app
from app import sms

with TestClient(app):
    pass
from testdb import db

codes = {k: v for k, v in db.execute("select key, value from settings")}
D = lambda n: (datetime.now(ZoneInfo("Europe/London")).date() + timedelta(days=n)).isoformat()


def mk(code, name, email, phone=""):
    c = TestClient(app, headers={"Accept-Language": "ko"}); c.get("/lang/ko")
    assert c.post("/signup", data=dict(invite_code=code, name=name, email=email, phone=phone, password="pass1234", password2="pass1234")).status_code == 200
    return c


admin = mk(codes["invite_code_staff"], "Admin", "a@ex.com")
mgr = mk(codes["invite_code_manager"], "Mgr", "m@ex.com")
dave = mk(codes["invite_code_installer"], "Dave", "d@ex.com")
uid = lambda e: db.execute("select id from users where email = ?", (e,)).fetchone()[0]
ko = lambda: TestClient(app, headers={"Accept-Language": "ko"})
last_code = lambda phone: re.search(r"\b(\d{6})\b", [t for to, t in sms.sender.sent if to == phone][-1]).group(1)
login = lambda c, email, pw: c.post("/login", data=dict(email=email, password=pw), follow_redirects=False)

# ---- 2. 로그인 반복 시도 제한
c = ko()
for i in range(5):
    assert login(c, "d@ex.com", "wrong").status_code == 400
r = login(c, "d@ex.com", "pass1234"); assert r.status_code == 429 and "15분 뒤에" in r.text            # 맞는 비밀번호도 잠시 막힘
assert login(ko(), "a@ex.com", "pass1234").status_code == 303                                          # 다른 계정은 영향 없음
db.execute("update login_attempts set at = '2000-01-01T00:00:00+00:00'")                              # 15분 지남
assert login(ko(), "d@ex.com", "pass1234").status_code == 303
# 같은 접속 주소에서 20번
for i in range(20):
    login(c, f"nobody{i}@ex.com", "x")
assert login(c, "a@ex.com", "pass1234").status_code == 429
db.execute("delete from login_attempts")

# ---- 3. 관리자·매니저가 휴대폰 번호 바꾸기
r = admin.post(f"/users/{uid('d@ex.com')}/phone", data=dict(phone="07700 900321"), follow_redirects=True)
assert "Dave님의 휴대폰 번호를 바꿨습니다" in r.text
assert db.execute("select phone_e164 from users where email='d@ex.com'").fetchone()[0] == "+447700900321"
assert "번호를 확인하세요" in admin.post(f"/users/{uid('d@ex.com')}/phone", data=dict(phone="12345"), follow_redirects=True).text
assert mgr.post(f"/users/{uid('a@ex.com')}/phone", data=dict(phone="07700 900999")).status_code == 403   # 매니저는 관리자 번호 불가
assert dave.post(f"/users/{uid('m@ex.com')}/phone", data=dict(phone="07700 900999")).status_code == 403
admin.post(f"/users/{uid('m@ex.com')}/phone", data=dict(phone="07700 900777"))
r = admin.post(f"/users/{uid('d@ex.com')}/phone", data=dict(phone="07700 900777"), follow_redirects=True)
assert "이미 다른 계정" in r.text                                                                       # 중복 번호
admin.post(f"/users/{uid('m@ex.com')}/phone", data=dict(phone=""))
assert db.execute("select phone_e164 from users where email='m@ex.com'").fetchone()[0] is None

# ---- 1a. 비밀번호 찾기: 문자 코드
c = ko()
r = c.post("/forgot", data=dict(email="d@ex.com")); assert "코드와 새 비밀번호" in r.text
code = last_code("+447700900321"); assert "재설정 코드" in sms.sender.sent[-1][1]
assert "8자 이상" in c.post("/forgot/verify", data=dict(code=code, password="short", password2="short")).text
assert "맞지 않습니다" in c.post("/forgot/verify", data=dict(code="000000" if code != "000000" else "111111", password="newpass99", password2="newpass99")).text
r = c.post("/forgot/verify", data=dict(code=code, password="newpass99", password2="newpass99"), follow_redirects=True)
assert "비밀번호를 바꿨습니다" in r.text
assert login(ko(), "d@ex.com", "pass1234").status_code == 400 and login(ko(), "d@ex.com", "newpass99").status_code == 303
n = len(sms.sender.sent)
r = ko().post("/forgot", data=dict(email="nobody@ex.com")); assert "코드와 새 비밀번호" in r.text and len(sms.sender.sent) == n   # 같은 화면, 문자 없음
r = ko().post("/forgot", data=dict(email="m@ex.com")); assert len(sms.sender.sent) == n                                            # 휴대폰 없음 → 문자 없음
# ---- 1b. 1회용 재설정 링크
assert dave.post(f"/users/{uid('m@ex.com')}/reset-link").status_code == 403
assert mgr.post(f"/users/{uid('a@ex.com')}/reset-link").status_code == 403
r = mgr.post(f"/users/{uid('d@ex.com')}/reset-link", follow_redirects=True)
link = re.search(r'value="(http[^"]+/reset/[^"]+)"', r.text).group(1); token = link.rsplit("/", 1)[1]
assert "한 번만 보입니다" in r.text and "/reset/" not in mgr.get("/users/staff").text                            # 다시 열면 안 보임
assert token not in str(db.execute("select token_hash from password_resets").fetchall())                  # 토큰은 해시로만 저장
assert ko().get(f"/reset/{token}").status_code == 200
r = ko().post(f"/reset/{token}", data=dict(password="linkpass1", password2="linkpass1"), follow_redirects=True); assert "비밀번호를 정했습니다" in r.text
assert login(ko(), "d@ex.com", "linkpass1").status_code == 303
assert ko().get(f"/reset/{token}").status_code == 410                                                    # 한 번만
mgr.post(f"/users/{uid('d@ex.com')}/reset-link")
t2 = db.execute("select id from password_resets order by id desc").fetchone()[0]
db.execute("update password_resets set expires_at = '2000-01-01T00:00:00+00:00' where id = ?", (t2,))
assert ko().get("/reset/not-a-token").status_code == 410

# ---- 4. 설치업자에게 예전 기록 입력란 숨김 (회사는 그대로)
r = admin.post("/houses/new", data=dict(address="1 Gap Road"), follow_redirects=True)
hid = int(re.search(r"/houses/(\d+)", str(r.url)).group(1))
admin.post(f"/houses/{hid}/members", data=dict(user_id=uid("d@ex.com")))
dave = ko(); login(dave, "d@ex.com", "linkpass1"); dave.get("/lang/ko")
assert 'id="new"' not in dave.get(f"/houses/{hid}").text and 'id="new"' not in admin.get(f"/houses/{hid}").text   # 새 기록 작성 없앰

# ---- 5. 현장조사 '검토만 완료' → 승인 대기에서 빠짐, 다시 열기
dave.post(f"/houses/{hid}/survey/start")
odu = db.execute("select id from survey_odus order by id desc").fetchone()[0]
room = db.execute("select id from survey_rooms order by id desc").fetchone()[0]
# 빈 조사는 검토 완료·작업 범위 만들기 불가, '다음 할 일'이 직접 고치기를 안내
r = admin.post(f"/houses/{hid}/survey/reviewed", follow_redirects=True)
assert "조사 내용이 비어 있습니다" in r.text and db.execute("select status from surveys where house_id=?", (hid,)).fetchone()[0] == "draft"
admin.post(f"/houses/{hid}/scope/from-survey")
assert not db.execute("select 1 from scope_versions where house_id=?", (hid,)).fetchone()
assert "조사 내용 직접 고치기" in admin.get(f"/houses/{hid}/survey").text
dave.post(f"/houses/{hid}/survey/rooms/{room}", data=dict(name="거실", indoor_type="wall", kw="3.5", action="stay"))
dave.post(f"/houses/{hid}/survey/rooms/{room}/acc", data=dict(grille="na", plenum="na", hatch="na"))
dave.post(f"/houses/{hid}/survey/outdoor", data={f"odu_{odu}_mount": "ground", "system": "single", "electric_work": "noneed", "trunking": "excl", "action": "submit"})
assert "현장조사 제출" in admin.get("/approvals").text
assert dave.post(f"/houses/{hid}/survey/reviewed").status_code == 403
admin.post(f"/houses/{hid}/survey/reviewed")
assert db.execute("select status from surveys where house_id=?", (hid,)).fetchone()[0] == "reviewed"
assert "현장조사 제출" not in admin.get("/approvals").text
assert dave.post(f"/houses/{hid}/survey/rooms/{room}", data=dict(kw="5.0")).status_code == 403
admin.post(f"/houses/{hid}/survey/reopen")
assert dave.post(f"/houses/{hid}/survey/rooms/{room}", data=dict(name="거실", kw="5.0", action="stay")).status_code in (200, 303)

# ---- 6. 사진 미리보기: 처음 볼 때 만들고 저장, 작게
from PIL import Image
buf = io.BytesIO(); Image.new("RGB", (2000, 1500), (200, 100, 50)).save(buf, "JPEG", quality=95)
dave.post(f"/houses/{hid}/survey/rooms/{room}", data=dict(name="거실", action="stay"), files=[("files", ("big.jpg", buf.getvalue(), "image/jpeg"))])
mid = db.execute("select id from media where owner_type='room' order by id desc").fetchone()[0]
assert f'/media/{mid}?thumb=1' in dave.get(f"/houses/{hid}/survey/rooms/{room}").text
r = dave.get(f"/media/{mid}?thumb=1"); assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
im = Image.open(io.BytesIO(r.content)); assert max(im.size) == 480, im.size
assert len(r.content) < len(buf.getvalue()) / 5
full = dave.get(f"/media/{mid}"); assert Image.open(io.BytesIO(full.content)).size == (2000, 1500)   # 원본은 그대로
assert dave.get(f"/media/{mid}?thumb=1").content == r.content                                        # 저장해 둔 것 재사용
dave.post(f"/houses/{hid}/survey/rooms/{room}", data=dict(name="거실", action="stay"), files=[("files", ("bad.jpg", b"\xff\xd8\xffnotjpeg", "image/jpeg"))])
bad = db.execute("select id from media where owner_type='room' order by id desc").fetchone()[0]
assert dave.get(f"/media/{bad}?thumb=1").status_code == 200                                          # 깨진 파일 → 원본으로 대체

# ---- 7. 방문 일정 변경 (예약과 같은 경고)
sam = mk(codes["invite_code_installer"], "Sam", "s@ex.com")
admin.post("/visits", data=dict(house_id=str(hid), kind="other", installer_id=str(uid("d@ex.com")), mode="confirmed", date=D(1), start_time="08:00", end_time="10:00"))
admin.post("/visits", data=dict(house_id=str(hid), kind="other", installer_id=str(uid("d@ex.com")), mode="confirmed", date=D(2), start_time="08:00", end_time="10:00"))
v1, v2 = [r[0] for r in db.execute("select id from visits order by id")]
assert "일정 변경" in admin.get(f"/houses/{hid}").text and dave.post(f"/visits/{v2}/reschedule", data=dict(date=D(3))).status_code == 403
r = admin.post(f"/visits/{v2}/reschedule", data=dict(date=D(1), start_time="09:00", end_time="11:00", installer_id=str(uid("d@ex.com"))), follow_redirects=True)
assert "이미 이 시간에 예약" in r.text and db.execute("select date from visits where id=?", (v2,)).fetchone()[0] == D(2)       # 겹침 → 안 바뀜
admin.post(f"/visits/{v2}/reschedule", data=dict(date=D(1), start_time="09:00", end_time="11:00", installer_id=str(uid("d@ex.com")), override_reason="고객 요청"))
assert db.execute("select date, start_time, override_reason from visits where id=?", (v2,)).fetchone() == (D(1), "09:00", "고객 요청")
admin.post(f"/visits/{v2}/reschedule", data=dict(date=D(4), start_time="08:00", end_time="09:00", installer_id=str(uid("s@ex.com")), mode="proposed"))
assert db.execute("select installer_id, status from visits where id=?", (v2,)).fetchone() == (uid("s@ex.com"), "proposed")
assert db.execute("select 1 from house_members where house_id=? and user_id=?", (hid, uid("s@ex.com"))).fetchone()   # 새 설치업자 자동 배정
assert "가능한가요?" in sam.get("/houses").text or "예" in sam.get("/houses").text
admin.post(f"/visits/{v1}/cancel")
r = admin.post(f"/visits/{v1}/reschedule", data=dict(date=D(5)), follow_redirects=True); assert "바꿀 수 없습니다" in r.text
print("GAPS OK")
