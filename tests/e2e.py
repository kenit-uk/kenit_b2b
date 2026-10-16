"""전체 흐름: 가입, 집 등록, 기록·첨부, 권한, 상태, 수정 이력, 검색, 사용자 관리, 영어 화면"""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app):
    pass
from testdb import db

codes = dict(db.execute("select key, value from settings"))
sc, ic = codes["invite_code_staff"], codes["invite_code_installer"]

staff = TestClient(app); inst = TestClient(app); anon = TestClient(app)
assert "Log in" in anon.get("/login").text      # 브라우저 언어 없음 → 영어 기본
assert "로그인" in TestClient(app, headers={"Accept-Language": "ko-KR"}).get("/login").text
for c in (staff, inst, anon):
    c.get("/lang/ko")


def ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:800])
    return r


# 비로그인 차단, 잘못된 초대코드
assert anon.get("/houses", follow_redirects=False).headers["location"].startswith("/login")
r = anon.post("/signup", data=dict(invite_code="nope", name="x", email="x@x.com", password="12345678", password2="12345678"))
assert r.status_code == 400 and "초대코드" in r.text

ok(staff.post("/signup", data=dict(invite_code=sc, name="김직원", email="s@ex.com", password="pass1234", password2="pass1234")))
ok(inst.post("/signup", data=dict(invite_code=ic, name="Tom Installer", email="i@ex.com", password="pass1234", password2="pass1234")))

# 설치업자는 집 등록 불가
assert inst.post("/houses/new", data=dict(address="x")).status_code == 403
r = ok(staff.post("/houses/new", data=dict(address="12 Baker Street, London", postcode="nw1 6xe", landlord_name="John Smith", landlord_phone="+447700900000")))
hid = int(re.search(r"/houses/(\d+)", str(r.url)).group(1))
ok(staff.post("/houses/new", data=dict(address="5 Abbey Road", postcode="NW8 9AY")))

# 설치업자 현장정보 + 사진
png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
r = ok(inst.post(f"/houses/{hid}/entries/survey", data=dict(property_type="Terraced", floor_area="85", rooms="3", floors="2", body="실외기 뒷마당"),
                 files=[("files", ("living.png", png, "image/png")), ("files", ("bed.jpg", b"jpgdata", "image/jpeg"))]))
assert "living.png" in r.text and "85 ㎡" in r.text
r = ok(inst.post(f"/houses/{hid}/entries/survey", data=dict(body="x"), files=[("files", ("a.svg", b"<svg/>", "image/svg+xml"))]))
assert "올릴 수 없습니다" in r.text                                   # SVG 거부
assert inst.post(f"/houses/{hid}/entries/quote", data=dict(body="x")).status_code == 403
assert inst.post(f"/houses/{hid}/entries/conversation", data=dict(body="x")).status_code == 403

ok(staff.post(f"/houses/{hid}/entries/quote", data=dict(amount="3,450", units="2대", body="Daikin 2대"), files=[("files", ("quote.pdf", b"%PDF-1.4", "application/pdf"))]))
ok(staff.post(f"/houses/{hid}/entries/conversation", data=dict(channel="전화", occurred_at="2026-10-10T14:30", body="다음주 화요일 설치 희망")))

# 집주인 대화는 설치업자에게 안 보임
r = ok(inst.get(f"/houses/{hid}"))
assert "Daikin" in r.text and "화요일 설치" not in r.text and "견적 금액" in r.text
r = ok(staff.get(f"/houses/{hid}")); assert "화요일 설치" in r.text and "2026-10-10 (토) 14:30" in r.text

# 상태 변경
assert inst.post(f"/houses/{hid}/status", data=dict(status="검토중")).status_code == 403
ok(staff.post(f"/houses/{hid}/status", data=dict(status="견적완료", note="견적 발송")))
r = ok(staff.get(f"/houses/{hid}?tab=status")); assert "검토전 → 견적완료" in r.text and "견적 발송" in r.text

# 기록 수정 (설치업자 본인), 첨부 삭제, 이력
eid = db.execute("select id from entries where type='survey'").fetchone()[0]
att = db.execute("select id from attachments where entry_id=? order by id", (eid,)).fetchall()
assert staff.get(f"/entries/{eid}/edit").status_code == 403      # 남이 쓴 현장정보는 수정 불가
ok(inst.post(f"/entries/{eid}/edit", data=dict(property_type="Terraced", floor_area="90", rooms="3", floors="2", body="실외기 뒷마당, 전기 증설 필요", remove_files=str(att[1][0]))))
r = ok(inst.get(f"/houses/{hid}")); assert "90 ㎡" in r.text and "내용수정 1회" in r.text and "bed.jpg" not in r.text
r = ok(inst.get(f"/entries/{eid}/edit")); assert "85 ㎡" in r.text and "첨부 삭제" in r.text
r = ok(staff.get(f"/houses/{hid}?tab=history")); assert "기록 수정" in r.text and "상태 변경" in r.text

# 파일 접근
ok(inst.get(f"/files/{att[0][0]}"))
assert inst.get(f"/files/{att[1][0]}").status_code == 404   # 삭제표시 → 설치업자 불가
ok(staff.get(f"/files/{att[1][0]}"))
assert anon.get(f"/files/{att[0][0]}", follow_redirects=False).status_code == 303

# 집 정보 수정 이력
ok(staff.post(f"/houses/{hid}/edit", data=dict(address="12 Baker Street, London", postcode="NW1 6XE", landlord_name="John Smith", landlord_phone="+447700900001")))
r = ok(staff.get(f"/houses/{hid}?tab=history")); assert "집주인 연락처" in r.text and "+447700900000" in r.text


# 목록 검색
def count(c, qs):
    return c.get("/houses" + qs).text.count('class="card house-row')


assert count(staff, "") == 2
assert "전체 2건" in staff.get("/houses").text and "검색 결과 1건" in staff.get("/houses?q=baker").text
assert count(staff, "?q=baker") == 1
assert count(staff, "?q=NW16XE") == 1          # 우편번호 공백 무시
assert count(staff, "?q=900001") == 1          # 관리자: 연락처 검색
assert count(inst, "?q=900001") == 0           # 설치업자: 연락처 검색 불가
assert count(staff, "?status=견적완료") == 1
assert count(staff, "?status=견적완료&status=검토전") == 2
r = ok(staff.get("/houses?status=견적완료"))
assert '<input type="hidden" name="status" value="견적완료">' in r.text and 'type="checkbox" name="status"' not in r.text
assert count(staff, "?status=견적완료&q=London") == 1   # 칩 상태 + 키워드 검색 유지
assert count(staff, "?status=검토전&q=Baker") == 0
iid = db.execute("select id from users where role='installer'").fetchone()[0]
for s in ("activity", "created", "oldest", "address"):
    ok(staff.get(f"/houses?sort={s}"))
# 현장 안 검색
r = ok(staff.get(f"/houses/{hid}?q=daikin")); assert "Daikin" in r.text and "화요일" not in r.text
r = ok(staff.get(f"/houses/{hid}?q=living.png")); assert "living.png" in r.text

# 사용자 관리
ok(staff.get("/users")); assert inst.get("/users").status_code == 403
ok(staff.post(f"/users/{iid}/toggle"))
assert inst.get("/houses", follow_redirects=False).status_code == 303
ok(staff.post("/invite-codes/installer/regenerate"))
assert dict(db.execute("select key, value from settings"))["invite_code_installer"] != ic

# ---- 영어 화면 ----
staff.post(f"/users/{iid}/toggle")  # 재활성화
inst.post("/login", data=dict(email="i@ex.com", password="pass1234"))
ok(inst.get("/lang/en?next=/houses"))
assert db.execute("select lang from users where id=?", (iid,)).fetchone()[0] == "en"
r = ok(inst.get(f"/houses/{hid}"))
for s_ in ["Site survey", "Quote", "Quoted", "Floor area", "90 ㎡", "Terraced", "Save Site survey", "1 edit(s)", "History"]:
    assert s_ in r.text, s_
r = ok(inst.get(f"/houses/{hid}?tab=history"))
for s_ in ["Status changed", "Not reviewed → Quoted", "Record edited", "File removed", "Changed: Landlord phone", "Before edit"]:
    assert s_ in r.text, s_
r = ok(inst.get(f"/houses/{hid}?q=없는기록")); assert "No records match &#39;없는기록&#39;" in r.text   # 검색어는 번역 안 함
r = ok(inst.get("/houses?status=견적완료"))
assert "1 result(s)" in r.text and "Quoted" in r.text and "More filters" not in r.text
r = ok(inst.post(f"/houses/{hid}/entries/survey", data=dict(body="x"), files=[("files", ("a.svg", b"<svg/>", "image/svg+xml"))]))
assert "&#39;a.svg&#39; can&#39;t be uploaded" in r.text
r = ok(inst.post(f"/houses/{hid}/entries/survey", data=dict(rooms="4"))); assert "Site survey saved." in r.text
assert "You don&#39;t have permission" in inst.get("/users").text
# 로그아웃 후에도 언어 유지, 재로그인 시 계정 언어 적용
inst.post("/logout"); assert "Log in" in inst.get("/login").text
k = TestClient(app, headers={"Accept-Language": "ko"}); k.post("/login", data=dict(email="i@ex.com", password="pass1234"))
assert "Jobs" in k.get("/houses").text
# 관리자 화면 영어 + 날짜 형식
staff.get("/lang/en")
r = ok(staff.get(f"/houses/{hid}")); assert "Landlord contact" in r.text and "Phone call" in r.text
assert re.search(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d+ \w{3} \d{4}, \d\d:\d\d", r.text)
r = ok(staff.post(f"/houses/{hid}/status", data=dict(status="설치중"))); assert "Status changed to &#39;Installing&#39;." in r.text
inv = ok(staff.get("/invites")).text
assert "For Installer" in inv and "For Admin" in inv and "Staff" not in inv
print("ALL OK")
