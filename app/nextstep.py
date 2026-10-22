"""프로젝트마다 '지금 누가 무엇을 해야 하는지' 한 가지를 정한다.

단계 번호만 보면 막히는 곳(누가 다음 단계로 옮겨야 하는지 모름)이 생기므로, 실제 상태(방문·현장조사·작업 범위·자료·보고)를
순서대로 보고 첫 번째로 비어 있는 일을 고른다. 프로젝트 화면과 확인할 일 목록에서 쓴다.

결과: {"key": 번역 키, "args": {...}, "actions": [{"label", "href"} 또는 {"label", "post", "fields"}], "waiting": bool}
waiting=True 는 '상대방 차례'(기다리면 됨)라는 뜻.
"""
from datetime import datetime

from . import constants as C

LONDON_TODAY = None   # 테스트에서 바꿀 수 있게


def _today():
    from .main import LONDON
    return (LONDON_TODAY or datetime.now(LONDON).date()).isoformat()


def _visits(conn, house_id):
    return conn.execute("""SELECT v.*, u.name AS installer_name FROM visits v LEFT JOIN users u ON u.id = v.installer_id
                           WHERE v.house_id = ? AND v.status != 'cancelled' ORDER BY v.id""", (house_id,)).fetchall()


def _last(visits, kind):
    rows = [v for v in visits if v["kind"] == kind and v["status"] != "declined"]
    return rows[-1] if rows else None


def step(key, *actions, waiting=False, **args):
    return {"key": key, "args": args, "actions": list(actions), "waiting": waiting}


def link(label, href, primary=True, **args):
    return {"label": label, "href": href, "primary": primary, "args": args}


def post(label, url, primary=True, **fields):
    return {"label": label, "post": url, "fields": fields, "primary": primary, "args": {}}


def office_step(conn, house) -> dict:
    h = house["id"]
    cal = f"/houses/{h}#book"
    if house["on_hold"]:
        return step("보류 중인 프로젝트입니다. 다시 진행하려면 보류를 해제하세요.", post("보류 해제", f"/houses/{h}/hold"))
    if house["stage"] >= C.DONE_STAGE:
        return step("완료된 프로젝트입니다.", waiting=True)
    if not conn.execute("SELECT 1 FROM house_members WHERE house_id = ?", (h,)).fetchone():
        return step("설치업자를 배정하세요.", link("설치업자 배정", f"/houses/{h}#members"))
    visits = _visits(conn, h)
    survey = conn.execute("SELECT * FROM surveys WHERE house_id = ?", (h,)).fetchone()
    scope = conn.execute("SELECT * FROM scope_versions WHERE house_id = ? ORDER BY version DESC LIMIT 1", (h,)).fetchone()
    locked = conn.execute("SELECT 1 FROM scope_versions WHERE house_id = ? AND locked_at IS NOT NULL", (h,)).fetchone()

    if not scope:
        sv = _last(visits, "survey")
        if not survey or survey["status"] == "draft":
            if not sv:
                return step("현장조사 방문을 예약하세요.", link("+ 방문 예약", cal))
            if sv["status"] == "requested":
                return step("{name}님이 현장조사 가능한 날짜를 고르는 중입니다.", waiting=True, name=sv["installer_name"])
            if sv["status"] == "options":
                return step("{name}님이 날짜 후보를 보냈습니다. 고객과 통화한 뒤 하루를 확정하세요.",
                            link("날짜 확정하기", f"/houses/{h}#confirm-{sv['id']}"), name=sv["installer_name"])
            if sv["status"] == "proposed":
                return step("{name}님의 가능 여부 답을 기다리는 중입니다.", waiting=True, name=sv["installer_name"])
            if survey and survey["status"] == "draft":
                return step("{name}님이 현장조사를 작성하고 있습니다. 제출되면 확인할 일에 올라옵니다.",
                            link("작성 중인 내용 보기", f"/houses/{h}/survey", primary=False), waiting=True, name=sv["installer_name"])
            return step("{date} 현장조사 예정입니다. 설치업자가 현장에서 작성합니다.", waiting=True, date=sv["date"] or "")
        if survey["status"] == "submitted":
            return step("현장조사가 제출됐습니다. 검토하세요.", link("현장조사 검토", f"/houses/{h}/survey"))
        return step("현장조사로 작업 범위 v1을 만드세요.", link("현장조사 열기", f"/houses/{h}/survey"))
    if not locked:
        return step("작업 범위 v{n}을 확인하고 확정하세요. 확정해야 시공 일정을 잡을 수 있습니다.",
                    link("작업 범위 v{n} 열기", f"/houses/{h}/scope/{scope['version']}", n=scope["version"]), n=scope["version"])

    pending_vo = conn.execute("SELECT number FROM variations WHERE house_id = ? AND status = 'pending' ORDER BY number", (h,)).fetchone()
    if pending_vo:
        return step("변경 요청 VO-{n}을 고객과 확인한 뒤 결정하세요.",
                    link("변경 요청 보기", f"/houses/{h}/variations#vo-{pending_vo['number']}"), n=pending_vo["number"])
    unreviewed = conn.execute("""SELECT d.id, v.kind, v.seq FROM day_reports d JOIN visits v ON v.id = d.visit_id
                                 WHERE d.house_id = ? AND d.reviewed_at IS NULL ORDER BY d.id""", (h,)).fetchone()
    if unreviewed:
        return step("{kind} 작업 끝 보고를 확인하세요.", link("보고 확인", f"/approvals?item=report-{unreviewed['id']}"),
                    kind=C.visit_label(unreviewed))

    from .documents import install_pack_ready
    from .payments import gate_block
    if house["stage"] < C.HANDOVER_STAGE:
        installs = [v for v in visits if v["kind"] == "install" and v["status"] != "declined"]
        open_v = next((v for v in installs if v["status"] in ("requested", "options", "proposed", "confirmed")), None)
        if not installs:                                    # 아직 시공을 한 번도 안 잡음
            if not install_pack_ready(conn, h):
                return step("시공 전 필수 자료(건물 도면, 제품 카탈로그·사양서, 최종 LATS)를 올리세요.", link("자료함", f"/houses/{h}/docs"))
            msg = gate_block(conn, h, "deposit_before_install")
            if msg:
                return step(msg, link("결제 표시하기", f"/houses/{h}#payments"))
            return step("1차 시공 일정을 잡으세요.", link("+ 방문 예약", cal))
        if open_v:
            v, label = open_v, C.visit_label(open_v)
            if v["status"] == "requested":
                return step("{name}님이 {kind} 가능한 날짜를 고르는 중입니다.", waiting=True, name=v["installer_name"], kind=label)
            if v["status"] == "options":
                return step("{name}님이 날짜 후보를 보냈습니다. 고객과 통화한 뒤 하루를 확정하세요.",
                            link("날짜 확정하기", f"/houses/{h}#confirm-{v['id']}"), name=v["installer_name"])
            if v["status"] == "proposed":
                return step("{name}님의 가능 여부 답을 기다리는 중입니다.", waiting=True, name=v["installer_name"])
            if v["date"] and v["date"] < _today():
                return step("{date} {kind}의 작업 끝 보고가 아직 없습니다. {name}님에게 확인하세요.",
                            link("현장 기록", f"/visits/{v['id']}", primary=False), date=v["date"], kind=label, name=v["installer_name"])
            return step("{date} {kind} 예정입니다 ({name}).", link("현장 기록", f"/visits/{v['id']}", primary=False),
                        waiting=True, date=v["date"], kind=label, name=v["installer_name"])
        # 잡아 둔 시공이 다 끝남: 더 할지(n차 추가), 끝낼지 회사가 정한다
        done_n = sum(1 for v in installs if v["status"] == "done")
        nxt = link("+ 다음 시공 예약", cal, primary=False)
        if gate_block(conn, h, "interim_before_next_install"):
            nxt = link("중도금 받음 표시 (다음 시공용)", f"/houses/{h}#payments", primary=False)
        return step("{n}차 시공까지 끝났습니다. 시공이 더 필요하면 다음 시공을 잡고, 다 끝났으면 시공 완료를 확인하세요.",
                    post("시공 완료 확인", f"/houses/{h}/install-done"), nxt, n=done_n)
    from .payments import gate_block
    msg = gate_block(conn, h, "balance_before_complete")
    if msg:
        return step(msg, link("결제 표시하기", f"/houses/{h}#payments"))
    return step("고객에게 사용법·서류를 넘겨 주고 인수인계가 끝나면 완료하세요.", post("인수인계 완료", f"/houses/{h}/handover-done"))


def installer_step(conn, house, user) -> dict:
    h = house["id"]
    if house["on_hold"]:
        return step("회사가 이 프로젝트를 보류했습니다. 회사에서 연락이 올 때까지 작업하지 마세요.", waiting=True)
    if house["stage"] >= C.DONE_STAGE:
        return step("완료된 프로젝트입니다.", waiting=True)
    t = _today()
    mine = [v for v in _visits(conn, h) if v["installer_id"] == user["id"]]
    for v in mine:
        if v["status"] == "requested":
            return step("{kind} 가능한 날짜를 골라 주세요.", link("날짜 고르기", f"/visits/{v['id']}/dates"), kind=C.visit_label(v))
        if v["status"] == "proposed":
            return step("회사가 {date} {kind}이 가능한지 묻습니다.",
                        post("예, 가능해요", f"/visits/{v['id']}/reply", answer="yes", back=f"/houses/{h}"),
                        post("아니요", f"/visits/{v['id']}/reply", primary=False, answer="no", back=f"/houses/{h}"),
                        date=v["date"], kind=C.visit_label(v))
    for v in mine:
        if v["status"] == "confirmed" and v["kind"] != "survey" and v["date"] and v["date"] < t:
            return step("{date} {kind}의 작업 끝 보고를 아직 보내지 않았습니다.", link("오늘 작업 끝 보내기", f"/visits/{v['id']}/finish"),
                        date=v["date"], kind=C.visit_label(v))
    survey = conn.execute("SELECT * FROM surveys WHERE house_id = ?", (h,)).fetchone()
    if survey and survey["status"] == "draft":
        req = conn.execute("SELECT 1 FROM survey_rooms WHERE survey_id = ? AND photo_request IS NOT NULL AND removed_at IS NULL",
                           (survey["id"],)).fetchone()
        return step("회사가 추가 사진을 요청했습니다. 올린 뒤 다시 보내 주세요." if req else "현장조사를 마치고 회사로 보내 주세요.",
                    link("현장조사 계속하기", f"/houses/{h}/survey"))
    sv = _last(mine, "survey")
    if not survey and sv and sv["status"] == "confirmed":
        if sv["date"] and sv["date"] <= t:
            return step("현장에 도착하면 현장조사를 시작하세요.", link("현장조사 시작", f"/houses/{h}/survey"))
        return step("{date} 현장조사 예정입니다.", waiting=True, date=sv["date"])
    scope = conn.execute("""SELECT s.version FROM scope_versions s WHERE s.house_id = ? ORDER BY version DESC LIMIT 1""", (h,)).fetchone()
    if scope and not conn.execute("""SELECT 1 FROM scope_acks a JOIN scope_versions s ON s.id = a.scope_id
                                     WHERE s.house_id = ? AND s.version = ? AND a.user_id = ?""", (h, scope["version"], user["id"])).fetchone():
        return step("작업 범위 v{n}이 나왔습니다. 확인해 주세요.", link("작업 범위 v{n} 보기", f"/houses/{h}/scope/{scope['version']}", n=scope["version"]),
                    n=scope["version"])
    for v in mine:
        if v["status"] == "confirmed" and v["date"] == t and v["kind"] != "survey":
            return step("오늘 {kind} 일정입니다.", link("현장 기록 열기", f"/visits/{v['id']}"), kind=C.visit_label(v))
    upcoming = [v for v in mine if v["status"] == "confirmed" and v["date"] and v["date"] > t]
    if upcoming:
        v = upcoming[0]
        return step("다음 일정: {date} {kind}", waiting=True, date=v["date"], kind=C.visit_label(v))
    if survey and survey["status"] == "submitted":
        return step("현장조사를 보냈습니다. 회사가 검토하고 있습니다.", waiting=True)
    return step("지금 할 일이 없습니다. 회사가 다음 일정을 잡으면 오늘 화면에 나옵니다.", waiting=True)


def next_step(conn, house, user) -> dict:
    from .main import is_office
    return office_step(conn, house) if is_office(user) else installer_step(conn, house, user)
