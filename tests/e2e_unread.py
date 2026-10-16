"""확인하지 않은 새 소식(N) 표시"""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app):
    pass
from testdb import db

codes = dict(db.execute("select key, value from settings"))


def client(code, name, email):
    c = TestClient(app); c.get("/lang/ko")
    r = c.post("/signup", data=dict(invite_code=code, name=name, email=email, password="pass1234", password2="pass1234"))
    assert r.status_code == 200, r.text[:300]
    return c


def rows(c):  # 목록에서 {주소: N 여부}
    html = c.get("/houses").text
    return {m.group(2).strip(): bool(m.group(1)) for m in re.finditer(r'<div class="house-addr">(<span class="new-badge"[^>]*>N</span> )?([^<]+)</div>', html)}


def sidebar(c, url="/houses"):
    m = re.search(r'<span class="count"[^>]*>(\d+)</span>', c.get(url).text)
    return int(m.group(1)) if m else 0


staffA = client(codes["invite_code_staff"], "직원A", "a@ex.com")
inst = client(codes["invite_code_installer"], "설치업자", "i@ex.com")
staffB = client(codes["invite_code_staff"], "직원B", "b@ex.com")

# 1) 직원A가 집 등록 → 본인은 N 없음, 다른 사람은 N
r = staffA.post("/houses/new", data=dict(address="1 Test Road")); hid = int(re.search(r"/houses/(\d+)", str(r.url)).group(1))
assert rows(staffA) == {"1 Test Road": False} and sidebar(staffA) == 0
assert rows(staffB) == {"1 Test Road": True} and sidebar(staffB) == 1
assert rows(inst) == {"1 Test Road": True} and sidebar(inst) == 1

# 2) 열람하면 N 사라짐
assert sidebar(inst, f"/houses/{hid}") == 0 and rows(inst) == {"1 Test Road": False}

# 3) 집주인 대화 → 설치업자는 볼 수 없으므로 N 없음
staffA.post(f"/houses/{hid}/entries/conversation", data=dict(body="집주인 통화"))
assert rows(inst) == {"1 Test Road": False} and rows(staffB)["1 Test Road"] is True

# 4) 견적 + 상태 변경 → 상세에서 N, 순서
staffA.post(f"/houses/{hid}/entries/quote", data=dict(amount="2000", body="견적 내용"))
staffA.post(f"/houses/{hid}/status", data=dict(status="견적완료"))
assert rows(inst)["1 Test Road"] is True
page = inst.get(f"/houses/{hid}").text
quote = re.search(r'<article class="card entry entry-quote is-new".*?</article>', page, re.S)
assert quote and 'class="new-badge"' in quote.group(0)
assert re.search(r'<span><span class="new-badge"[^>]*>N</span> 🔁', page)
assert "마지막으로 확인한 뒤 새 소식 2건" in page
order = [m.group(0) for m in re.finditer(r"🔁|entry-quote", page)]
assert order[0] == "🔁", order                         # 상태 변경(가장 최근)이 맨 위
page = inst.get(f"/houses/{hid}").text
assert 'class="new-badge"' not in page and rows(inst)["1 Test Road"] is False

# 5) 본인 작성 → 본인 N 없음, 다른 사람은 N
inst.post(f"/houses/{hid}/entries/survey", data=dict(rooms="3"))
assert rows(inst)["1 Test Road"] is False and rows(staffA)["1 Test Road"] is True

# 6) 기록 수정도 새 소식
eid = db.execute("select id from entries where type='survey'").fetchone()[0]
staffA.get(f"/houses/{hid}")
inst.post(f"/entries/{eid}/edit", data=dict(rooms="4"))
assert rows(staffA)["1 Test Road"] is True
assert staffA.get(f"/houses/{hid}?tab=history").text.count('<tr class="is-new">') == 1

# 7) 나중에 가입한 사람에게 예전 내용은 N 아님
late = client(codes["invite_code_installer"], "늦은가입", "late@ex.com")
assert rows(late) == {"1 Test Road": False} and sidebar(late) == 0
staffA.post(f"/houses/{hid}/entries/quote", data=dict(body="추가 견적"))
assert rows(late)["1 Test Road"] is True

# 8) 영어
inst.get("/lang/en")
assert "New since your last visit" in inst.get("/houses").text
print("UNREAD OK")
