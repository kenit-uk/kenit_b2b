"""작업 범위 버전·비교·확인·의견·확정, 자료함(올리기·새 버전·NEW·확인함·가격 문서 차단·필수 자료)"""
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
    assert c.post("/signup", data=dict(invite_code=code, name=name, email=email, password="pass1234", password2="pass1234")).status_code == 200
    return c


admin = mk(codes["invite_code_staff"], "Mina", "a@ex.com")
dave = mk(codes["invite_code_installer"], "Dave C.", "d@ex.com")
sam = mk(codes["invite_code_installer"], "Sam R.", "s@ex.com")
uid = lambda e: db.execute("select id from users where email = ?", (e,)).fetchone()[0]
r = admin.post("/houses/new", data=dict(address="7 Park Avenue, Chiswick"), follow_redirects=True)
hid = int(re.search(r"/houses/(\d+)", str(r.url)).group(1))
admin.post(f"/houses/{hid}/members", data=dict(user_id=uid("d@ex.com")))


def go(c, url, code=(200, 303), **kw):
    r = c.post(url, follow_redirects=False, **kw)
    assert r.status_code in code, (url, r.status_code, r.text[:400])
    return r


def scope_form(rooms, summary="", **common):
    data = {"summary": summary, "o0_mount": "bracket", "system": common.get("system", "multi"),
            "trunking": "incl", "trunking_colour": common.get("trunking_colour", "white")}
    for i, rm in enumerate(rooms):
        data.update({f"r{i}_{k}": v for k, v in rm.items()})
    return data


# v1: 회사가 직접 편집으로 만든다 (현장조사 없이도 가능)
assert dave.get(f"/houses/{hid}/scope-edit").status_code == 403
r = admin.post(f"/houses/{hid}/scope-edit", data=scope_form([{"name": "거실", "indoor_type": "wall", "kw": "3.5"}]))
assert r.status_code == 400 and "변경 내용을 적어 주세요" in r.text
go(admin, f"/houses/{hid}/scope-edit", data=scope_form([{"name": "거실", "indoor_type": "wall", "kw": "3.5"},
                                                       {"name": "침실 2", "indoor_type": "wall", "kw": "2.5", "location": "벽 중앙"}], "첫 범위"))
# 같은 내용으로 저장하면 거부 (가격만 바뀐 경우 등)
r = admin.post(f"/houses/{hid}/scope-edit", data=scope_form([{"name": "거실", "indoor_type": "wall", "kw": "3.5"},
                                                            {"name": "침실 2", "indoor_type": "wall", "kw": "2.5", "location": "벽 중앙"}], "가격 수정"))
assert r.status_code == 400 and "바뀐 내용이 없습니다" in r.text
# v2: 침실 2 용량 변경 + 서재 추가 + 트렁킹 색상 변경, 거실은 그대로
go(admin, f"/houses/{hid}/scope-edit", data=scope_form([{"name": "거실", "indoor_type": "wall", "kw": "3.5"},
                                                       {"name": "침실 2", "indoor_type": "wall", "kw": "3.5", "location": "벽 중앙"},
                                                       {"name": "서재", "indoor_type": "wall", "kw": "2.5", "location": "창문 위"}],
                                                      "서재 추가", trunking_colour="black"))
assert db.execute("select max(version) from scope_versions where house_id=?", (hid,)).fetchone()[0] == 2
assert "£" not in json.dumps(json.loads(db.execute("select data from scope_versions where version=2").fetchone()[0]))

# 설치업자: 변경만 보임
page = dave.get(f"/houses/{hid}").text
assert "NEW · v2" in page and "작업 범위가 바뀌었습니다" in page
page = dave.get(f"/houses/{hid}/scope/2").text
assert "v1 → v2" in page
assert re.search(r'diff-changed.*?침실 2.*?<s>2\.5 kW</s> → <b>3\.5 kW</b>', page, re.S)
assert re.search(r'diff-added.*?서재', page, re.S)
assert re.search(r'프로젝트 공통.*?<s>화이트</s> → <b>블랙</b>', page, re.S)
assert re.search(r'same-row.*?거실', page, re.S) and "변경 없음" in page
assert "확인했어요" in page and sam.get(f"/houses/{hid}/scope/2").status_code == 404
# 확인했어요 → 기록, 회사에 보임
go(dave, f"/houses/{hid}/scope/2/ack")
assert "확인함" in dave.get(f"/houses/{hid}/scope/2").text and "NEW · v2" not in dave.get(f"/houses/{hid}").text
page = admin.get(f"/houses/{hid}/scope/2").text
assert re.search(r"Dave C\.</span><span class=\"tag ok\">확인함", page)
assert "작업 범위 확인" in admin.get(f"/houses/{hid}?tab=history").text
# 의견 남기기 → 회사가 봄
assert dave.post(f"/houses/{hid}/scope/2/comment", data=dict(body="")).status_code in (200, 303)
go(dave, f"/houses/{hid}/scope/2/comment", data=dict(body="측면 벽까지 배관이 약 14미터예요."))
assert "측면 벽까지 배관이 약 14미터예요." in admin.get(f"/houses/{hid}/scope/2").text
assert db.execute("select kind from messages").fetchone()[0] == "scope_comment"

# 확정(잠금) → 4단계, 이후 편집·현장조사로 만들기 불가
assert dave.post(f"/houses/{hid}/scope/2/lock").status_code == 403
assert admin.post(f"/houses/{hid}/scope/1/lock").status_code == 400     # 최신만
go(admin, f"/houses/{hid}/scope/2/lock")
assert db.execute("select stage from houses where id=?", (hid,)).fetchone()[0] == 3
r = admin.get(f"/houses/{hid}/scope-edit", follow_redirects=True); assert "변경 요청(VO)으로만" in r.text
assert admin.post(f"/houses/{hid}/scope-edit", data=scope_form([{"name": "x"}], "x")).status_code == 400
assert "🔒" in admin.get(f"/houses/{hid}").text

# ---- 자료함
png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
pdf = b"%PDF-1.4 test"
assert dave.post(f"/houses/{hid}/docs", data=dict(category="drawing", title="x"), files=[("files", ("a.pdf", pdf, "application/pdf"))]).status_code == 403
r = admin.post(f"/houses/{hid}/docs", data=dict(category="lats", title="Quote Q-0412"), files=[("files", ("q.pdf", pdf, "application/pdf"))], follow_redirects=True)
assert "가격 자료" in r.text and db.execute("select count(*) from documents").fetchone()[0] == 0
r = admin.post(f"/houses/{hid}/docs", data=dict(category="drawing", title="도면 — 침실 2"), files=[("files", ("d1.png", png, "image/png"))], follow_redirects=False)
d1 = int(re.search(r"/docs/(\d+)$", r.headers["location"]).group(1))
page = dave.get(f"/houses/{hid}/docs").text
assert "NEW" in page and "도면 — 침실 2" in page and "필수 자료가 아직 다 없습니다" in page
assert sam.get(f"/houses/{hid}/docs").status_code == 404
mid = db.execute("select id from media where owner_type='document' and owner_id=?", (d1,)).fetchone()[0]
assert dave.get(f"/media/{mid}").status_code == 200 and sam.get(f"/media/{mid}").status_code == 404
go(dave, f"/houses/{hid}/docs/{d1}/ack")
assert "확인함" in dave.get(f"/houses/{hid}/docs").text and 'class="tag new">NEW' not in dave.get(f"/houses/{hid}/docs").text
# 새 버전 → 다시 NEW, 이전 버전 확인 기록은 남음
r = admin.post(f"/houses/{hid}/docs", data=dict(group_id=str(d1), note="위치 변경"), files=[("files", ("d2.png", png, "image/png"))], follow_redirects=False)
d2 = int(re.search(r"/docs/(\d+)$", r.headers["location"]).group(1))
assert db.execute("select version, group_id from documents where id=?", (d2,)).fetchone() == (2, d1)
page = dave.get(f"/houses/{hid}/docs").text
assert 'class="tag new">NEW' in page and "이전 버전 1개" in page
page = dave.get(f"/houses/{hid}/docs/{d1}").text; assert "이전 버전입니다" in page and "확인함 ·" in page
page = admin.get(f"/houses/{hid}/docs").text; assert "Dave C. 미확인" in page
# 필수 자료 3종
for cat, name in (("catalogue", "LG 카탈로그"), ("lats", "LATS v3")):
    admin.post(f"/houses/{hid}/docs", data=dict(category=cat, title=name), files=[("files", ("f.pdf", pdf, "application/pdf"))])
assert "필수 자료 준비 완료" in admin.get(f"/houses/{hid}/docs").text
from app.documents import install_pack_ready
from app.db import connect
assert install_pack_ready(connect(), hid)
# 자료 내리기
go(admin, f"/houses/{hid}/docs/{d2}/remove")
assert "도면 — 침실 2" in admin.get(f"/houses/{hid}/docs").text   # v1이 다시 최신으로 보임
# 영어
dave.get("/lang/en")
assert "Got it" in dave.get(f"/houses/{hid}/scope/2").text or "Checked" in dave.get(f"/houses/{hid}/scope/2").text
assert "Documents" in dave.get(f"/houses/{hid}/docs").text
print("SCOPE & DOCS OK")
