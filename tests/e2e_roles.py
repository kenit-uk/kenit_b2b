"""관리자 / 매니저 / 설치업자 권한, 초대, 역할 변경"""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app):
    pass
from testdb import db

codes = {k: v for k, v in db.execute("select key, value from settings") if k.startswith("invite_code_")}
assert set(codes) == {"invite_code_staff", "invite_code_manager", "invite_code_installer"}, codes


def mk(code, name, email):
    c = TestClient(app); c.get("/lang/ko")
    r = c.post("/signup", data=dict(invite_code=code, name=name, email=email, password="pass1234", password2="pass1234"))
    assert r.status_code == 200, r.text[:200]
    return c


admin = mk(codes["invite_code_staff"], "관리자A", "admin@ex.com")
mgr = mk(codes["invite_code_manager"], "매니저M", "mgr@ex.com")
inst = mk(codes["invite_code_installer"], "설치업자I", "inst@ex.com")
role = lambda email: db.execute("select role from users where email = ?", (email,)).fetchone()[0]
uid = lambda email: db.execute("select id from users where email = ?", (email,)).fetchone()[0]
st = lambda c, url, **kw: c.get(url, follow_redirects=False, **kw).status_code
assert role("mgr@ex.com") == "manager"

# 매니저: 현장 업무
page = mgr.get("/houses").text
assert "+ 새 프로젝트" in page and 'href="/users/installers"' in page and 'href="/users/staff"' in page and 'href="/invites"' in page and 'href="/settings/survey"' in page and "role-manager" in page   # 매니저도 질문지 관리
r = mgr.post("/houses/new", data=dict(address="7 Manager Road", landlord_phone="+447700111222"), follow_redirects=True)
hid = int(re.search(r"/houses/(\d+)", str(r.url)).group(1))
assert mgr.post(f"/houses/{hid}/members", data=dict(user_id=uid("inst@ex.com"))).status_code in (200, 303)   # 매니저도 배정 가능
assert mgr.post(f"/houses/{hid}/edit", data=dict(address="7 Manager Road", landlord_phone="+447700111222", memo="key safe")).status_code in (200, 303)
assert mgr.post(f"/houses/{hid}/stage", data=dict(stage="4")).status_code in (404, 405)                 # 단계는 자동
mgr.post(f"/houses/{hid}/entries/quote", data=dict(amount="1000", body="매니저 견적"))
mgr.post(f"/houses/{hid}/entries/conversation", data=dict(body="매니저 통화"))
admin.post(f"/houses/{hid}/entries/quote", data=dict(amount="2000", body="관리자 견적"))
assert "7 Manager Road" in mgr.get("/houses?q=111222").text

# 매니저: 사용자 관리·초대 (관리자 계정은 건드릴 수 없음)
page = mgr.get("/users/staff").text + mgr.get("/users/installers").text
assert st(mgr, "/users/staff") == 200 and st(mgr, "/users/installers") == 200 and 'name="role"' not in page
assert f'/users/{uid("admin@ex.com")}/toggle' not in page and f'/users/{uid("inst@ex.com")}/toggle' in page
assert st(mgr, "/settings/survey") == 200 and st(inst, "/settings/survey") == 403             # 질문지: 회사 직원만
assert mgr.post(f"/users/{uid('admin@ex.com')}/toggle").status_code == 403
assert db.execute("select active from users where email='admin@ex.com'").fetchone()[0] == 1
assert mgr.post(f"/users/{uid('inst@ex.com')}/role", data=dict(role="staff")).status_code == 403
assert mgr.post("/invite-codes/manager/regenerate").status_code == 403
assert role("inst@ex.com") == "installer"
inv = mgr.get("/invites").text
assert codes["invite_code_manager"] in inv and codes["invite_code_installer"] in inv and codes["invite_code_staff"] not in inv
assert "/regenerate" not in inv
assert st(inst, "/invites") == 403 and st(inst, "/users") == 403
assert f'value="{codes["invite_code_installer"]}"' in TestClient(app).get(f"/signup?code={codes['invite_code_installer']}").text
m2 = mk(codes["invite_code_manager"], "매니저2", "mgr2@ex.com")
assert mgr.post(f"/users/{uid('mgr2@ex.com')}/toggle").status_code in (200, 303)
assert db.execute("select active from users where email='mgr2@ex.com'").fetchone()[0] == 0
assert st(m2, "/houses") == 303
mgr.post(f"/users/{uid('mgr2@ex.com')}/toggle")
assert db.execute("select active from users where email='mgr2@ex.com'").fetchone()[0] == 1

# 매니저: 본인 견적만 수정
q_mgr = db.execute("select id from entries where body = '매니저 견적'").fetchone()[0]
q_adm = db.execute("select id from entries where body = '관리자 견적'").fetchone()[0]
assert st(mgr, f"/entries/{q_mgr}/edit") == 200 and st(mgr, f"/entries/{q_adm}/edit") == 403
assert st(admin, f"/entries/{q_mgr}/edit") == 200

# 설치업자: 견적·집주인 대화 안 보임, 집 등록·단계 변경·배정 불가
page = inst.get(f"/houses/{hid}").text
assert "매니저 견적" not in page and "매니저 통화" not in page and "7 Manager Road" in page
assert inst.post("/houses/new", data=dict(address="x")).status_code == 403
assert inst.post(f"/houses/{hid}/install-done").status_code == 403 and inst.post(f"/houses/{hid}/handover-done").status_code == 403
assert inst.post(f"/houses/{hid}/members", data=dict(user_id=uid("inst@ex.com"))).status_code == 403
# N: 매니저 대화 → 관리자 N, 설치업자는 안 뜸
admin.get(f"/houses/{hid}"); inst.get(f"/houses/{hid}")
mgr.post(f"/houses/{hid}/entries/conversation", data=dict(body="두번째 통화"))
assert 'class="new-badge"' in admin.get("/houses").text and 'class="new-badge"' not in inst.get("/houses").text

# 관리자: 역할 변경
assert 'name="role"' in admin.get("/users").text
inv = admin.get("/invites").text
assert all(v in inv for v in codes.values()) and "매니저용" in inv and inv.count("/regenerate") == 3
r = admin.post(f"/users/{uid('inst@ex.com')}/role", data=dict(role="manager"), follow_redirects=True)
assert "설치업자I님의 역할을 매니저(으)로 바꿨습니다." in r.text and role("inst@ex.com") == "manager"
assert "매니저 통화" in inst.get(f"/houses/{hid}").text and "매니저 견적" in inst.get(f"/houses/{hid}").text
r = admin.post(f"/users/{uid('admin@ex.com')}/role", data=dict(role="installer"), follow_redirects=True)
assert "본인 역할은 바꿀 수 없습니다" in r.text and role("admin@ex.com") == "staff"
assert admin.post(f"/users/{uid('mgr@ex.com')}/role", data=dict(role="owner")).status_code == 400
admin.post(f"/users/{uid('inst@ex.com')}/role", data=dict(role="installer"))
assert "매니저 통화" not in inst.get(f"/houses/{hid}").text
admin.post("/invite-codes/manager/regenerate")
assert dict(db.execute("select key, value from settings"))["invite_code_manager"] != codes["invite_code_manager"]
mgr.get("/lang/en"); assert "Manager" in mgr.get("/houses").text
admin.get("/lang/en"); assert "For Manager" in admin.get("/invites").text and "Copy invite link" in admin.get("/invites").text
# 관리자: 사용자 삭제 (매니저는 불가, 본인 불가). 목록에서 빠지고 로그인 불가, 같은 이메일로 다시 초대 가능
admin.get("/lang/ko")
victim = mk(codes["invite_code_installer"], "지울사람", "gone@ex.com")
gid = uid("gone@ex.com")
assert mgr.post(f"/users/{gid}/delete").status_code == 403
r = admin.post(f"/users/{uid('admin@ex.com')}/delete", follow_redirects=True); assert "본인 계정은 삭제할 수 없습니다" in r.text
admin.post(f"/users/{gid}/delete")
row = db.execute("select active, email, deleted_at from users where id=?", (gid,)).fetchone()
assert row[0] == 0 and row[1] is None and row[2]
assert "지울사람" not in admin.get("/users/installers").text and victim.get("/houses", follow_redirects=False).status_code == 303
assert admin.post(f"/users/{gid}/toggle").status_code == 404                                   # 되살릴 수 없음
again = mk(codes["invite_code_installer"], "다시가입", "gone@ex.com")
assert db.execute("select count(*) from users where email='gone@ex.com'").fetchone()[0] == 1
# 가이드: 역할에 맞는 내용
admin.get("/lang/ko"); inst.get("/lang/ko")
g = admin.get("/guide").text; assert "프로젝트 6단계" in g and "시공 준비" in g and 'href="/guide"' in g
g = inst.get("/guide").text; assert "하루 사용법" in g and "프로젝트 6단계" not in g and "상태값 정리" in g and "날짜 후보 받음" in g and "출결" not in g
assert "상태값 정리" in admin.get("/guide").text and "노쇼" in admin.get("/guide").text
print("ROLES OK")
