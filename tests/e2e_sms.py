"""문자 인증 로그인: 번호 등록, 코드 발송·확인, 제한, 내 정보 번호 등록"""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from fastapi.testclient import TestClient
from app.main import app
from app import sms

with TestClient(app):
    pass
from testdb import db

assert sms.SMS_ENABLED and sms.sender.name == "console"
sent = sms.sender.sent
codes = dict(db.execute("select key, value from settings"))


def ko():  # 한국어 화면 클라이언트
    return TestClient(app, headers={"Accept-Language": "ko"})


def last_code(phone):
    for to, text in reversed(sent):
        if to == phone:
            return re.search(r"\b(\d{6})\b", text).group(1)
    raise AssertionError("no sms to " + phone)


# 번호 형식
assert sms.normalize_phone("07700 900555") == "+447700900555"
assert sms.normalize_phone("+44 7700 900555") == "+447700900555"
assert sms.normalize_phone("447700900555") == "+447700900555"
assert sms.normalize_phone("(07700) 900-555") == "+447700900555"
assert sms.normalize_phone("+82 10 1234 5678") == "+821012345678"
assert sms.normalize_phone("12345") is None and sms.normalize_phone("0770090055") is None
assert sms.normalize_phone("010-9977-3841") is None                 # 한국 번호를 영국 번호로 잘못 바꾸지 않음
assert sms.normalize_phone("+82 10 9977 3841") == "+821099773841"
assert sms.normalize_phone("01632 960123") is None and sms.normalize_phone("+44 1632 960123") is None   # 영국 일반전화
assert sms.normalize_phone("7700900555") == "+447700900555"
assert sms.format_phone("+447700900555") == "+44 7700 900555"

admin = ko(); admin.get("/lang/ko")
admin.post("/signup", data=dict(invite_code=codes["invite_code_staff"], name="관리자", email="a@ex.com", password="pass1234", password2="pass1234"))
mgr = ko(); mgr.get("/lang/ko")
mgr.post("/signup", data=dict(invite_code=codes["invite_code_manager"], name="매니저", email="m@ex.com", password="pass1234", password2="pass1234"))

# 관리자가 휴대폰 번호로 설치업자 등록 + 안내 문자
n0 = len(sent)
r = admin.post("/invites/phone", data=dict(name="Dave C.", phone="07700 900555", role="installer", notify="1"), follow_redirects=True)
assert "Dave C.님을 설치업자(으)로 등록했습니다" in r.text
row = db.execute("select email, phone_e164, role, password_hash from users where name = 'Dave C.'").fetchone()
assert row == (None, "+447700900555", "installer", "!"), row
assert len(sent) == n0 + 1 and "/login" in sent[-1][1]
r = admin.post("/invites/phone", data=dict(name="Dup", phone="+447700900555", role="installer"), follow_redirects=True)
assert "이미 등록된 번호입니다" in r.text
assert mgr.post("/invites/phone", data=dict(name="X", phone="07700 900111", role="staff")).status_code == 403   # 매니저는 관리자 등록 불가
assert mgr.post("/invites/phone", data=dict(name="Mgr2", phone="07700 900112", role="manager"), follow_redirects=True).status_code == 200
assert "SMS" in admin.get("/users").text and "+44 7700 900555" in admin.get("/users").text

# 문자 로그인
dave = ko(); dave.get("/lang/ko")
assert "문자로 코드 받기" in dave.get("/login").text
r = dave.post("/login/phone", data=dict(phone="+44 7700 900555"))
assert r.status_code == 200 and "•" in r.text and "0555" in r.text and 'autocomplete="one-time-code"' in r.text
code = last_code("+447700900555")
assert "로그인 코드" in sent[-1][1] and "@testserver #" + code in sent[-1][1]   # 안드로이드 자동 입력 형식
stored = db.execute("select code_hash from phone_codes order by id desc").fetchone()[0]
assert code not in stored and len(stored) == 64                                    # 코드는 해시로만 저장
wrong = "000000" if code != "000000" else "111111"
r = dave.post("/login/phone/verify", data=dict(code=wrong)); assert r.status_code == 400 and "코드가 맞지 않습니다" in r.text
r = dave.post("/login/phone/verify", data=dict(code=code), follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/houses"
assert dave.get("/houses", follow_redirects=False).status_code == 200
r = ko().post("/login/phone/verify", data=dict(code=code), follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/login"              # 세션 없이 코드만으로는 불가
d2 = ko(); d2.post("/login/phone", data=dict(phone="07700900555"))   # 같은 코드 재사용 불가 (60초 안이라 새 코드 안 보냄)
assert "코드가 만료" in d2.post("/login/phone/verify", data=dict(code=code)).text

# 1분 안에 다시 요청 → 문자 안 보냄
n0 = len(sent)
r = d2.post("/login/phone/resend"); assert "1분 뒤에" in r.text and len(sent) == n0

# 1시간에 5번 제한
db.execute("update phone_codes set created_at = '2000-01-01T00:00:00+00:00'")   # 이전 기록 정리
from datetime import datetime, timedelta, timezone
for i in range(5):   # 지난 1시간 안(1분보다 오래 전)에 5번 보낸 기록
    sent_at = (datetime.now(timezone.utc) - timedelta(minutes=10 + i)).isoformat(timespec="seconds")
    db.execute("insert into phone_codes (phone, purpose, code_hash, attempts, created_at, expires_at) values (?, 'login', 'x', 0, ?, ?)",
               ("+447700900555", sent_at, "2000-01-01T00:00:00+00:00"))
n0 = len(sent)
r = ko().post("/login/phone", data=dict(phone="07700900555"))
assert "너무 많이" in r.text and len(sent) == n0
db.execute("delete from phone_codes")

# 5번 틀리면 끝
c3 = ko(); c3.post("/login/phone", data=dict(phone="07700900555"))
code = last_code("+447700900555")
for i in range(5):
    r = c3.post("/login/phone/verify", data=dict(code=f"{(int(code) + 1 + i) % 10**6:06d}"))
assert "여러 번 틀렸습니다" in r.text
assert "여러 번 틀렸습니다" in c3.post("/login/phone/verify", data=dict(code=code)).text

# 만료
db.execute("delete from phone_codes")
c4 = ko(); c4.post("/login/phone", data=dict(phone="07700900555"))
code = last_code("+447700900555")
db.execute("update phone_codes set expires_at = '2000-01-01T00:00:00+00:00'")
assert "만료" in c4.post("/login/phone/verify", data=dict(code=code)).text

# 등록 안 된 번호 → 같은 화면, 문자는 안 보냄
n0 = len(sent)
r = ko().post("/login/phone", data=dict(phone="07700 999999"))
assert r.status_code == 200 and "6자리" in r.text and len(sent) == n0
assert ko().post("/login/phone", data=dict(phone="123")).status_code == 400

# 비활성화된 사람 → 문자 안 보냄
db.execute("delete from phone_codes")
dave_id = db.execute("select id from users where name = 'Dave C.'").fetchone()[0]
admin.post(f"/users/{dave_id}/toggle")
assert dave.get("/houses", follow_redirects=False).status_code == 303
n0 = len(sent); ko().post("/login/phone", data=dict(phone="07700900555")); assert len(sent) == n0
admin.post(f"/users/{dave_id}/toggle")

# 내 정보에서 번호 등록 (관리자)
r = admin.post("/me/phone", data=dict(phone="07700 900555")); assert "이미 다른 계정" in r.text     # Dave 번호
r = admin.post("/me/phone", data=dict(phone="07700 900777")); assert 'action="/me/phone/verify"' in r.text
code = last_code("+447700900777"); assert "확인 코드" in sent[-1][1]
r = admin.post("/me/phone/verify", data=dict(code=code), follow_redirects=True)
assert "휴대폰 번호를 등록했습니다" in r.text and "+44 7700 900777" in r.text
assert db.execute("select phone_e164 from users where email='a@ex.com'").fetchone()[0] == "+447700900777"
# 다른 사람에게 발급된 확인 코드는 못 씀 (매니저가 받은 코드를 관리자가 입력)
mgr.post("/me/phone", data=dict(phone="07700 900778")); code = last_code("+447700900778")
admin.post("/me/phone", data=dict(phone="07700 900778"))          # 1분 안이라 새 코드는 안 나감
r = admin.post("/me/phone/verify", data=dict(code=code)); assert "만료" in r.text
assert db.execute("select phone_e164 from users where email='a@ex.com'").fetchone()[0] == "+447700900777"
db.execute("delete from phone_codes")   # 같은 번호로 1분 안에 또 보내지 않는 제한을 넘기기 위해
a2 = ko(); a2.post("/login/phone", data=dict(phone="07700 900777"))
assert a2.post("/login/phone/verify", data=dict(code=last_code("+447700900777")), follow_redirects=False).status_code == 303
assert "관리자" in a2.get("/me").text

# 이메일 로그인도 계속 됨, 휴대폰 전용 계정은 비밀번호 로그인 불가
assert ko().post("/login", data=dict(email="a@ex.com", password="pass1234"), follow_redirects=False).status_code == 303
assert ko().post("/login", data=dict(email="", password="!")).status_code in (400, 422)

# 영어 문자 본문
en = TestClient(app, headers={"Accept-Language": "en"}); en.get("/lang/en"); db.execute("delete from phone_codes")
en.post("/login/phone", data=dict(phone="07700900555"))
assert "sign-in code" in sent[-1][1] and "Enter code" in en.post("/login/phone/resend").text
print("SMS OK")
