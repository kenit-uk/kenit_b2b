"""현장조사: 설치업자 방 카드 → 액세서리 → 실외기 → 제출, 회사 검토 → 추가 사진 요청 → 작업 범위 v1"""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app):
    pass
from testdb import db

codes = {k: v for k, v in db.execute("select key, value from settings")}


def mk(code, name, email):
    c = TestClient(app); c.get("/lang/ko")
    r = c.post("/signup", data=dict(invite_code=code, name=name, email=email, password="pass1234", password2="pass1234"))
    assert r.status_code == 200, r.text[:200]
    return c


admin = mk(codes["invite_code_staff"], "관리자", "a@ex.com")
dave = mk(codes["invite_code_installer"], "Dave C.", "d@ex.com")
sam = mk(codes["invite_code_installer"], "Sam R.", "s@ex.com")      # 배정 안 된 설치업자
uid = lambda e: db.execute("select id from users where email = ?", (e,)).fetchone()[0]
r = admin.post("/houses/new", data=dict(address="22 Mill Lane, Ealing", postcode="W5 2BB"), follow_redirects=True)
hid = int(re.search(r"/houses/(\d+)", str(r.url)).group(1))
admin.post(f"/houses/{hid}/members", data=dict(user_id=uid("d@ex.com")))
B = f"/houses/{hid}/survey"

def go(c, url, **kw):
    r = c.post(url, follow_redirects=False, **kw)
    assert r.status_code in (200, 303), (url, r.status_code, r.text[:300])
    return r

# 시작
assert "현장조사 시작" in dave.get(f"/houses/{hid}").text
assert sam.get(B).status_code == 404 and sam.post(B + "/start").status_code == 404
r = go(dave, B + "/start"); room1 = int(re.search(r"/rooms/(\d+)$", r.headers["location"]).group(1))
page = dave.get(f"{B}/rooms/{room1}").text
assert "방 1개 중 1번째" in page and 'value="방 1"' in page and 'name="indoor_type"' in page

# 3a: 방 카드 저장 + 사진
png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
r = go(dave, f"{B}/rooms/{room1}", data=dict(name="거실", indoor_type="wall", kw="3.5", memo="소파 위, 뒤쪽이 외벽", action="next"),
       files=[("files", ("wall.png", png, "image/png"))])
assert r.headers["location"] == f"{B}/rooms/{room1}/acc"
assert db.execute("select name, indoor_type, kw from survey_rooms where id=?", (room1,)).fetchone() == ("거실", "wall", "3.5")
mid = db.execute("select id from media where owner_type='room' and owner_id=?", (room1,)).fetchone()[0]
assert dave.get(f"/media/{mid}").status_code == 200 and admin.get(f"/media/{mid}").status_code == 200
assert sam.get(f"/media/{mid}").status_code == 404

# 3b: 벽걸이 기본값(그릴·플레넘·점검구 해당 없음)이 미리 선택돼 있음
page = dave.get(f"{B}/rooms/{room1}/acc").text
assert re.search(r'name="grille" value="na" checked', page) and re.search(r'name="hatch" value="na" checked', page)
r = go(dave, f"{B}/rooms/{room1}/acc", data=dict(controls=["app", "lcd"], lcd_colour="white", grille="na", plenum="na", hatch="na", action="apply_all"))
d = json.loads(db.execute("select defaults from surveys").fetchone()[0])
assert d["controls"] == ["app", "lcd"] and d["by_type"] == {"wall": {"grille": "na", "plenum": "na", "hatch": "na"}}, d
assert json.loads(db.execute("select acc from survey_rooms where id=?", (room1,)).fetchone()[0]) == {}

# 방 추가: 덕트형은 그릴·플레넘·점검구를 꼭 물어봄, 컨트롤은 모든 방 기본값을 따름
r = go(dave, B + "/rooms/new", data=dict(name="침실 1")); room2 = int(re.search(r"/rooms/(\d+)$", r.headers["location"]).group(1))
go(dave, f"{B}/rooms/{room2}", data=dict(name="침실 1", indoor_type="ducted", kw="unknown", action="next"))
page = dave.get(f"{B}/rooms/{room2}/acc").text
assert 'name="grille" value="na" checked' not in page and "꼭 확인" in page
assert re.search(r'name="controls" value="lcd" checked', page)                     # 기본값(모든 방에 적용) 반영
r = go(dave, f"{B}/rooms/{room2}/acc", data=dict(controls=["app", "lcd"], lcd_colour="black", grille="incl", plenum="incl", hatch="need", extra="콘센트 추가", action="next"))
assert r.headers["location"] == f"{B}/outdoor"                                       # 마지막 방 → 실외기
own = json.loads(db.execute("select acc from survey_rooms where id=?", (room2,)).fetchone()[0])
assert own == {"lcd_colour": "black", "grille": "incl", "plenum": "incl", "hatch": "need", "extra": "콘센트 추가"}, own   # 다른 값만 저장
# 같은 방에 실내기 한 대 더 → 같은 이름, 그리고 빼기
r = go(dave, B + "/rooms/new", data=dict(copy_of=str(room2))); room3 = int(re.search(r"/rooms/(\d+)$", r.headers["location"]).group(1))
assert db.execute("select name from survey_rooms where id=?", (room3,)).fetchone()[0] == "침실 1"
go(dave, f"{B}/rooms/{room3}/remove")
assert "방 2개" in dave.get(B).text

# 3c: 빠진 항목이 있으면 제출 안 됨
odu = db.execute("select id from survey_odus").fetchone()[0]
r = dave.post(B + "/outdoor", data={f"odu_{odu}_mount": "bracket", "system": "multi", "action": "submit"})
assert r.status_code == 400 and "빠진 항목이 있습니다" in r.text and "전기 작업 필요 여부를 고르세요" in r.text
assert db.execute("select status from surveys").fetchone()[0] == "draft"
# 사진 올리기 직전 자동 저장
assert dave.post(B + "/outdoor", data={"system": "multi", "action": "autosave"}).status_code == 204
go(dave, f"{B}/photos/odu/{odu}", files=[("files", ("side.png", png, "image/png"))])
go(dave, f"{B}/photos/board/{db.execute('select id from surveys').fetchone()[0]}", files=[("files", ("board.png", png, "image/png"))])
assert sam.post(f"{B}/photos/odu/{odu}", files=[("files", ("x.png", png, "image/png"))]).status_code == 404
admin.get(f"/houses/{hid}")
r = go(dave, B + "/outdoor", data={f"odu_{odu}_mount": "bracket", f"odu_{odu}_note": "측면 벽", "system": "multi", "pipe_ok": "yes",
                                   "power_near": "no", "electric_work": "need", "trunking": "incl", "trunking_colour": "white",
                                   "access": "ladder", "memo": "분전반은 차고", "action": "submit"})
assert db.execute("select status from surveys").fetchone()[0] == "submitted"
assert "회사 검토 대기" in dave.get(f"/houses/{hid}").text
assert 'class="new-badge"' in admin.get("/houses").text                              # 회사에 새 소식

# 회사 검토
page = admin.get(B).text
for s_ in ["거실", "침실 1", "용량 미정", "이상 없음", "멀티 스플릿", "벽 브래킷", "분전반은 차고", "이 조사로 작업 범위 v1 만들기", "콘센트 추가"]:
    assert s_ in page, s_
assert dave.post(f"{B}/rooms/{room2}/photo-request", data=dict(text="천장 점검구 위치")).status_code == 403
go(admin, f"{B}/rooms/{room2}/photo-request", data=dict(text="천장 점검구 위치"))
assert "천장 점검구 위치" in dave.get(B).text and "추가 사진을 요청했습니다 (1곳)" in dave.get(f"/houses/{hid}").text

# 작업 범위 v1 만들기
assert dave.post(f"/houses/{hid}/scope/from-survey").status_code == 403
r = go(admin, f"/houses/{hid}/scope/from-survey"); assert r.headers["location"] == f"/houses/{hid}/scope/1"
data = json.loads(db.execute("select data from scope_versions where house_id=? and version=1", (hid,)).fetchone()[0])
assert [x["name"] for x in data["rooms"]] == ["거실", "침실 1"] and data["rooms"][0]["acc"]["controls"] == ["app", "lcd"]
assert data["rooms"][1]["acc"]["hatch"] == "need" and data["common"]["electric_work"] == "need" and data["odus"][0]["mount"] == "bracket"
assert db.execute("select status from surveys").fetchone()[0] == "reviewed"
assert db.execute("select stage from houses where id=?", (hid,)).fetchone()[0] == 2     # 견적·범위 협의 단계로
page = dave.get(f"/houses/{hid}/scope/1").text
assert "작업 범위" in page and "가격이 들어가지 않습니다" in page and "£" not in page
assert sam.get(f"/houses/{hid}/scope/1").status_code == 404
# 검토가 끝난 조사는 설치업자가 못 고침, 회사는 고칠 수 있음
assert dave.post(f"{B}/rooms/{room1}", data=dict(kw="5.0")).status_code == 403
go(admin, f"{B}/rooms/{room1}", data=dict(name="거실", indoor_type="wall", kw="5.0", action="stay"))
# 두 번째 버전
go(admin, f"/houses/{hid}/scope/from-survey")
assert db.execute("select max(version) from scope_versions where house_id=?", (hid,)).fetchone()[0] == 2

# 영어
dave.get("/lang/en")
page = dave.get(f"{B}/rooms/{room1}").text
assert "Approx. capacity" in page and "Wall mounted" in page and "Room 1 of 2" in page
dave.get("/lang/ko")

# 관리자 설정: 현장조사 항목 (액세서리·부가 사항)
assert dave.get("/settings/survey").status_code == 403
page = admin.get("/settings/survey").text
assert 'name="c0_ko"' in page and 'name="inew_ko"' in page
form = {"c0_ko": "앱 (Wi-Fi)", "c0_active": "1", "c1_ko": "리모컨", "c1_active": "1", "c2_ko": "벽부착 LCD", "c2_active": "1",
        "cnew_ko": "스마트 온도조절기", "cnew_en": "Smart thermostat",
        "l0_ko": "화이트", "l0_active": "1", "l1_ko": "블랙", "l1_active": "1",
        "i0_ko": "그릴 (Grille)", "i1_ko": "플레넘 (Plenum)", "i1_active": "1", "i2_ko": "점검구 (Access hatch)", "i2_active": "1",   # 그릴 숨김
        "inew_ko": "배수 펌프", "inew_en": "Condensate pump", "inew_answers": "need", "inew_def_wall": "na",
        "enew_ko": "콘센트 추가"}
admin.post("/settings/survey", data=form)
import json as _json
saved = _json.loads(db.execute("select value from settings where key='survey_options'").fetchone()[0])
pump = saved["items"][-1]
assert pump["ko"] == "배수 펌프" and pump["answers"] == "need" and pump["defaults"]["wall"] == "na" and pump["defaults"]["ducted"] is None
assert [c["ko"] for c in saved["controls"]][-1] == "스마트 온도조절기" and not saved["items"][0]["active"]
# 새 현장조사에 바로 반영: 그릴은 안 보이고, 배수 펌프·온도조절기·빠른 선택이 보임
r = admin.post("/houses/new", data=dict(address="9 Options Road"), follow_redirects=True)
h2 = int(re.search(r"/houses/(\d+)", str(r.url)).group(1))
admin.post(f"/houses/{h2}/members", data=dict(user_id=uid("d@ex.com")))
dave.post(f"/houses/{h2}/survey/start")
rid = db.execute("select r.id from survey_rooms r join surveys s on s.id=r.survey_id where s.house_id=?", (h2,)).fetchone()[0]
dave.post(f"/houses/{h2}/survey/rooms/{rid}", data=dict(name="거실", indoor_type="ducted", kw="3.5", action="stay"))
page = dave.get(f"/houses/{h2}/survey/rooms/{rid}/acc").text
assert "배수 펌프" in page and "스마트 온도조절기" in page and "+ 콘센트 추가" in page and "그릴 (Grille)" not in page
key = pump["key"]
dave.post(f"/houses/{h2}/survey/rooms/{rid}/acc", data={"plenum": "incl", "hatch": "need", key: "need", "action": "stay"})
acc = _json.loads(db.execute("select acc from survey_rooms where id=?", (rid,)).fetchone()[0])
assert acc.get(key) == "need"
assert "배수 펌프" in admin.get(f"/houses/{h2}/survey").text                                   # 회사 검토에도 보임
print("SURVEY OK")
