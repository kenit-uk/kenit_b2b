"""회사 승인 대기·프로젝트 목록 (시안 p.22), 설치업자 F-Gas 관리 (시안 p.27).

승인 대기 종류: 현장조사 제출, 일일 작업 보고, 문제 보고, 회사에 메시지, 작업 범위 의견, 변경 요청, 방문 날짜 후보, 방문 불가 답변.
v1에서는 고객에게 보내는 기능이 없으므로 회사가 '확인'으로 처리한다(고객 안내 이메일은 v3).
"""
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException

from . import constants as C
from .auth import office_user
from .db import audit, get_db, now
from .main import flash, form_str, redirect, render
from .survey import media_for
from .visits import parse_date, today

router = APIRouter()

REVIEW_TABLES = {"report": "day_reports", "message": "messages", "variation": "variations"}


def pending_items(conn) -> list[dict]:
    items = []
    for s in conn.execute("""SELECT s.id, s.house_id, s.submitted_at AS at, h.address, u.name AS who,
                                    (SELECT COUNT(*) FROM survey_rooms r WHERE r.survey_id = s.id AND r.removed_at IS NULL) AS rooms
                             FROM surveys s JOIN houses h ON h.id = s.house_id LEFT JOIN users u ON u.id = s.submitted_by
                             WHERE s.status = 'submitted'"""):
        items.append({"type": "survey", "id": s["id"], "house_id": s["house_id"], "at": s["at"], "address": s["address"],
                      "who": s["who"], "label": "현장조사 제출", "detail": {"rooms": s["rooms"]},
                      "href": f"/houses/{s['house_id']}/survey"})
    for r in conn.execute("""SELECT d.*, h.address, u.name AS who, v.kind AS visit_kind FROM day_reports d
                             JOIN houses h ON h.id = d.house_id JOIN users u ON u.id = d.user_id JOIN visits v ON v.id = d.visit_id
                             WHERE d.reviewed_at IS NULL"""):
        items.append({"type": "report", "id": r["id"], "house_id": r["house_id"], "at": r["created_at"], "address": r["address"],
                      "who": r["who"], "label": "문제 있음 보고" if r["has_problem"] else "오늘 작업 끝",
                      "urgent": bool(r["has_problem"]), "row": r, "visit_id": r["visit_id"]})
    for m in conn.execute("""SELECT m.*, h.address, u.name AS who FROM messages m JOIN houses h ON h.id = m.house_id
                             JOIN users u ON u.id = m.user_id WHERE m.reviewed_at IS NULL"""):
        items.append({"type": "message", "id": m["id"], "house_id": m["house_id"], "at": m["created_at"], "address": m["address"],
                      "who": m["who"], "label": C.MESSAGE_KINDS[m["kind"]], "urgent": m["kind"] == "problem", "row": m})
    for x in conn.execute("""SELECT x.*, h.address, u.name AS who FROM variations x JOIN houses h ON h.id = x.house_id
                             JOIN users u ON u.id = x.created_by WHERE x.reviewed_at IS NULL"""):
        items.append({"type": "variation", "id": x["id"], "house_id": x["house_id"], "at": x["created_at"], "address": x["address"],
                      "who": x["who"], "label": "변경 요청", "urgent": True, "row": x,
                      "href": f"/houses/{x['house_id']}/variations#vo-{x['number']}"})
    for v in conn.execute("""SELECT v.*, h.address, u.name AS who FROM visits v JOIN houses h ON h.id = v.house_id
                             LEFT JOIN users u ON u.id = v.installer_id WHERE v.status IN ('options', 'declined')"""):
        items.append({"type": "visit", "id": v["id"], "house_id": v["house_id"], "at": v["updated_at"], "address": v["address"],
                      "who": v["who"], "label": "방문 날짜 후보" if v["status"] == "options" else "방문 불가 답변",
                      "urgent": v["status"] == "declined", "row": v, "href": f"/calendar?week={v['date'] or today().isoformat()}"})
    items.sort(key=lambda i: i["at"] or "", reverse=True)
    return items


def pending_count(conn) -> int:
    return len(pending_items(conn))


@router.get("/approvals")
def approvals_page(request: Request, item: str = "", user=Depends(office_user), conn=Depends(get_db)):
    items = pending_items(conn)
    selected = next((i for i in items if f"{i['type']}-{i['id']}" == item), items[0] if items else None)
    extra = {}
    if selected and selected["type"] == "report":
        r = selected["row"]
        extra["report_media"] = media_for(conn, "report", [r["id"]]).get(r["id"], [])
        extra["visit_media"] = conn.execute("SELECT * FROM media WHERE owner_type = 'visit' AND owner_id = ? AND deleted_at IS NULL ORDER BY id",
                                            (r["visit_id"],)).fetchall()
        extra["work"] = json.loads(r["work"] or "[]")
    if selected and selected["type"] in ("message", "variation"):
        owner = "message" if selected["type"] == "message" else "variation"
        extra["item_media"] = media_for(conn, owner, [selected["id"]]).get(selected["id"], [])
    # 진행 중 프로젝트 (보류·완료 제외)
    projects = conn.execute("SELECT h.* FROM houses h WHERE h.on_hold = 0 AND h.stage < 9 ORDER BY h.updated_at DESC").fetchall()
    names, nxt = {}, {}
    for h in projects:
        names[h["id"]] = [r["name"] for r in conn.execute(
            "SELECT u.name FROM house_members m JOIN users u ON u.id = m.user_id WHERE m.house_id = ? ORDER BY u.name", (h["id"],))]
        nxt[h["id"]] = conn.execute(
            """SELECT date, kind, status FROM visits WHERE house_id = ? AND status IN ('confirmed', 'proposed') AND date >= ?
               ORDER BY date, start_time LIMIT 1""", (h["id"], today().isoformat())).fetchone()
    return render(request, "approvals.html", user=user, items=items, selected=selected, projects=projects, names=names, nxt=nxt,
                  C=C, today=today(), **extra)


@router.post("/approvals/{kind}/{item_id}/review")
def approval_review(request: Request, kind: str, item_id: int, user=Depends(office_user), conn=Depends(get_db)):
    table = REVIEW_TABLES.get(kind)
    if not table:
        raise HTTPException(404)
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    with conn:
        conn.execute(f"UPDATE {table} SET reviewed_by = ?, reviewed_at = ? WHERE id = ?", (user["id"], now(), item_id))
        audit(conn, house_id=row["house_id"], actor_id=user["id"], action="review", detail=kind)
    flash(request, "확인했습니다.")
    return redirect("/approvals")


# ---------- F-Gas (p.27) ----------

@router.post("/users/{user_id}/fgas")
async def fgas_save(request: Request, user_id: int, user=Depends(office_user), conn=Depends(get_db)):
    target = conn.execute("SELECT id FROM users WHERE id = ? AND role = 'installer'", (user_id,)).fetchone()
    if not target:
        raise HTTPException(404)
    form = await request.form()
    expiry = form_str(form, "fgas_expiry", 10)
    if expiry and not parse_date(expiry):
        raise HTTPException(400, "날짜 형식이 올바르지 않습니다.")
    with conn:
        conn.execute("UPDATE users SET fgas_no = ?, fgas_expiry = ?, fgas_company = ? WHERE id = ?",
                     (form_str(form, "fgas_no", 60) or None, expiry or None, form_str(form, "fgas_company", 120) or None, user_id))
    flash(request, "F-Gas 정보를 저장했습니다.")
    return redirect("/users#installers")


def fgas_state(expiry: str | None) -> str:
    if not expiry:
        return "unknown"
    if expiry < today().isoformat():
        return "expired"
    if expiry <= (today() + timedelta(days=30)).isoformat():
        return "soon"
    return "ok"
