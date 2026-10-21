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
r = ok(staff.post("/houses/new", data=dict(address="5 Abbey Road", postcode="NW8 9AY")))
other = int(re.search(r"/houses/(\d+)", str(r.url)).group(1))
iid = db.execute("select id from users where role='installer'").fetchone()[0]
assert db.execute("select ref_no, stage, on_hold from houses where id=?", (hid,)).fetchone() == (f"WS-{100000 + hid}", 1, 0)

# 배정 전에는 설치업자에게 안 보임
assert inst.get(f"/houses/{hid}").status_code == 404
assert inst.get("/houses").text.count('class="card house-row') == 0
assert inst.post(f"/houses/{hid}/members", data=dict(user_id=iid)).status_code == 403   # 배정은 회사만
r = ok(staff.post(f"/houses/{hid}/members", data=dict(user_id=iid), follow_redirects=True))
assert "Tom Installer님을 배정했습니다" in r.text
ok(inst.get(f"/houses/{hid}"))
assert inst.get(f"/houses/{other}").status_code == 404

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

# 견적(가격)과 집주인 대화는 설치업자에게 안 보임
r = ok(inst.get(f"/houses/{hid}"))
assert "Daikin" not in r.text and "화요일 설치" not in r.text and "견적 금액" not in r.text and "3,450" not in r.text
assert "John Smith" not in r.text and "+447700900000" not in r.text                       # 집주인 정보도 회사 전용
qfile = db.execute("select a.id from attachments a join entries e on e.id=a.entry_id where e.type='quote'").fetchone()[0]
assert inst.get(f"/files/{qfile}").status_code == 404                                       # 견적서 파일도 불가
r = ok(staff.get(f"/houses/{hid}")); assert "화요일 설치" in r.text and "2026-10-10 (토) 14:30" in r.text

# 단계 변경 (회사만)
assert inst.post(f"/houses/{hid}/stage", data=dict(stage="3")).status_code == 403
r = ok(staff.post(f"/houses/{hid}/stage", data=dict(stage="4", note="견적 발송"), follow_redirects=True))
assert "4단계 &#39;최종 확정&#39;(으)로 옮겼습니다." in r.text
assert staff.post(f"/houses/{hid}/stage", data=dict(stage="10")).status_code == 400
r = ok(staff.get(f"/houses/{hid}?tab=status")); assert "1. 현장조사 예약 → 4. 최종 확정" in r.text and "견적 발송" in r.text
r = ok(inst.get(f"/houses/{hid}"))
assert 'class="stagebar' in r.text and "최종 확정" in r.text                                # 설치업자도 진행 단계는 봄
assert 'action="/houses/' + str(hid) + '/stage"' not in r.text                             # 단계 이동 버튼은 없음
# 보류
ok(staff.post(f"/houses/{hid}/hold", data=dict(note="고객 휴가")))
assert db.execute("select on_hold from houses where id=?", (hid,)).fetchone()[0] == 1
assert "고객 휴가" in staff.get(f"/houses/{hid}?tab=status").text
assert inst.post(f"/houses/{hid}/hold").status_code == 403
ok(staff.post(f"/houses/{hid}/hold"))
assert db.execute("select on_hold from houses where id=?", (hid,)).fetchone()[0] == 0

# 기록 수정 (설치업자 본인), 첨부 삭제, 이력
eid = db.execute("select id from entries where type='survey'").fetchone()[0]
att = db.execute("select id from attachments where entry_id=? order by id", (eid,)).fetchall()
assert staff.get(f"/entries/{eid}/edit").status_code == 403      # 남이 쓴 현장정보는 수정 불가
ok(inst.post(f"/entries/{eid}/edit", data=dict(property_type="Terraced", floor_area="90", rooms="3", floors="2", body="실외기 뒷마당, 전기 증설 필요", remove_files=str(att[1][0]))))
r = ok(inst.get(f"/houses/{hid}")); tl = r.text.split('id="timeline"')[1]
assert "90 ㎡" in tl and "내용수정 1회" in tl and "bed.jpg" not in tl          # 삭제한 첨부는 기록 목록에서 숨김 (작업 기록에는 '첨부 삭제'로 남음)
r = ok(inst.get(f"/entries/{eid}/edit")); assert "85 ㎡" in r.text and "첨부 삭제" in r.text
r = ok(staff.get(f"/houses/{hid}?tab=history")); assert "기록 수정" in r.text and "단계 변경" in r.text and "보류 해제" in r.text

# 파일 접근
ok(inst.get(f"/files/{att[0][0]}"))
staff.post(f"/houses/{other}/entries/quote", data=dict(body="x"), files=[("files", ("o.pdf", b"%PDF", "application/pdf"))])
assert inst.get(f"/files/{att[1][0]}").status_code == 404   # 삭제표시 → 설치업자 불가
ok(staff.get(f"/files/{att[1][0]}"))
assert anon.get(f"/files/{att[0][0]}", follow_redirects=False).status_code == 303

# 집 정보 수정 이력
ok(staff.post(f"/houses/{hid}/edit", data=dict(address="12 Baker Street, London", postcode="NW1 6XE", landlord_name="John Smith", landlord_phone="+447700900001")))
r = ok(staff.get(f"/houses/{hid}?tab=history")); assert "집주인 연락처" in r.text and "+447700900000" in r.text


# 목록 검색
def count(c, qs):
    return c.get("/houses" + qs).text.count('class="card house-row')


assert count(staff, "") == 2 and count(inst, "") == 1
assert "전체 2건" in staff.get("/houses").text and "검색 결과 1건" in staff.get("/houses?q=baker").text
assert count(staff, "?q=baker") == 1
assert count(staff, "?q=NW16XE") == 1          # 우편번호 공백 무시
assert count(staff, "?q=900001") == 1          # 관리자: 연락처 검색
assert count(inst, "?q=900001") == 0           # 설치업자: 연락처 검색 불가
assert count(staff, f"?q=WS-{100000 + hid}") == 1   # 관리번호 검색
assert count(staff, "?stage=4") == 1
assert count(staff, "?stage=4&stage=1") == 2
r = ok(staff.get("/houses?stage=4"))
assert '<input type="hidden" name="stage" value="4">' in r.text
assert count(staff, "?stage=4&q=London") == 1   # 칩 단계 + 키워드 검색 유지
assert count(staff, "?stage=1&q=Baker") == 0
staff.post(f"/houses/{other}/hold")
assert count(staff, "?hold=1") == 1 and count(inst, "?hold=1") == 0
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
for s_ in ["Site survey", "Scope agreed", "Stage 4 of 9", "Floor area", "90 ㎡", "Terraced", "1 edit(s)", "History", "People on this project", "Activity log", "Scope versions"]:
    assert s_ in r.text, s_
assert "Save Site survey" not in r.text            # 예전 기록 입력란은 설치업자에게 숨김 (구조화된 현장조사를 씀)
r = ok(inst.get(f"/houses/{hid}?tab=history"))
for s_ in ["Stage changed", "1. Survey booking → 4. Scope agreed", "Record edited", "File removed", "Changed: Landlord phone", "Before edit", "On hold", "Resume"]:
    assert s_ in r.text, s_
r = ok(inst.get(f"/houses/{hid}?q=없는기록")); assert "No records match &#39;없는기록&#39;" in r.text   # 검색어는 번역 안 함
r = ok(inst.get("/houses?stage=4"))
assert "1 result(s)" in r.text and "Scope agreed" in r.text and "My jobs" in r.text
r = ok(inst.post(f"/houses/{hid}/entries/survey", data=dict(body="x"), files=[("files", ("a.svg", b"<svg/>", "image/svg+xml"))]))
assert "&#39;a.svg&#39; can&#39;t be uploaded" in r.text
r = ok(inst.post(f"/houses/{hid}/entries/survey", data=dict(rooms="4"))); assert "Site survey saved." in r.text
assert "You don&#39;t have permission" in inst.get("/users").text
# 로그아웃 후에도 언어 유지, 재로그인 시 계정 언어 적용
inst.post("/logout"); assert "Log in" in inst.get("/login").text
k = TestClient(app, headers={"Accept-Language": "ko"}); k.post("/login", data=dict(email="i@ex.com", password="pass1234"))
assert "My jobs" in k.get("/houses").text
# 관리자 화면 영어 + 날짜 형식
staff.get("/lang/en")
r = ok(staff.get(f"/houses/{hid}")); assert "Landlord contact" in r.text and "Phone call" in r.text
assert re.search(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d+ \w{3} \d{4}, \d\d:\d\d", r.text)
r = ok(staff.post(f"/houses/{hid}/stage", data=dict(stage="6"))); assert "Moved to stage 6: &#39;First fix&#39;." in r.text
# 배정 해제 → 다시 안 보임
r = ok(staff.post(f"/houses/{hid}/members/{iid}/remove", follow_redirects=True)); assert "Their records are kept" in r.text
assert k.get(f"/houses/{hid}").status_code == 404 and k.get(f"/files/{att[0][0]}").status_code == 404   # k = 로그인한 설치업자
inv = ok(staff.get("/invites")).text
assert "For Installer" in inv and "For Admin" in inv and "Staff" not in inv
print("ALL OK")
