"""이름 바꾸기, 설치업자 자격증(F-Gas 등) 올리기·승인.

설치업자가 내 정보에서 자격증을 올리면 회사의 '지금 처리할 것'에 뜨고, 회사가 사용자 관리에서 승인·반려한다.
F-Gas 개인 자격증이 승인되고 만료 전이어야 냉매 작업(시공)에 배정할 수 있다 (visits.booking_warnings).
"""
import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException

from . import constants as C
from .auth import Forbidden, current_user, office_user
from .db import get_db, now
from .main import LONDON, UserError, flash, form_str, is_office, redirect, remove_files, save_uploads
from .storage import storage

router = APIRouter()


def fgas_approved(u) -> bool:
    """회사가 F-Gas를 승인했고 만료되지 않았는가."""
    return bool(u["fgas_approved_at"]) and bool(u["fgas_expiry"]) and u["fgas_expiry"] >= datetime.now(LONDON).date().isoformat()


def can_manage(actor, target) -> bool:
    return actor["id"] != target["id"] and target["role"] in C.MANAGEABLE.get(actor["role"], ())


def user_licences(conn, user_id: int) -> list:
    return conn.execute("""SELECT l.*, u.name AS checker_name FROM licences l LEFT JOIN users u ON u.id = l.checked_by
                           WHERE l.user_id = ? AND l.removed_at IS NULL ORDER BY l.id DESC""", (user_id,)).fetchall()


# ---------- 이름 ----------

def apply_rename(conn, user_id: int, old: str, new: str) -> None:
    """이름을 바꾸면 작업 기록에 글자로 남은 옛 이름(설치업자 배정, 방문 예약·변경)도 새 이름으로 바꾼다.
    다른 화면(작성자, 참여자, 일정 등)은 사용자 표에서 이름을 바로 읽으므로 따로 할 일이 없다."""
    if not old or old == new:
        return
    linked = """(SELECT house_id FROM house_members WHERE user_id = ? UNION SELECT house_id FROM visits WHERE installer_id = ?)"""
    conn.execute(f"""UPDATE audit_log SET detail = ? WHERE action IN ('member_add', 'member_remove') AND detail = ?
                     AND house_id IN {linked}""", (new, old, user_id, user_id))
    rows = conn.execute(f"""SELECT id, detail FROM audit_log WHERE action IN ('visit_create', 'visit_update')
                            AND detail LIKE ? AND house_id IN {linked}""", (f"%{old}%", user_id, user_id)).fetchall()
    for r in rows:
        try:
            d = json.loads(r["detail"])
        except ValueError:
            continue
        if d.get("installer_id") == user_id or ("installer_id" not in d and d.get("installer") == old):
            d["installer"] = new
            conn.execute("UPDATE audit_log SET detail = ? WHERE id = ?", (json.dumps(d, ensure_ascii=False), r["id"]))

@router.post("/me/name")
async def my_name(request: Request, user=Depends(current_user), conn=Depends(get_db)):
    name = form_str(await request.form(), "name", 60)
    if not name:
        flash(request, "이름을 입력하세요.", "error")
        return redirect("/me")
    with conn:
        conn.execute("UPDATE users SET name = ? WHERE id = ?", (name, user["id"]))
        apply_rename(conn, user["id"], user["name"], name)
    flash(request, "이름을 바꿨습니다.")
    return redirect("/me")


@router.post("/users/{user_id}/name")
async def user_name(request: Request, user_id: int, user=Depends(office_user), conn=Depends(get_db)):
    target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        raise HTTPException(404)
    if not can_manage(user, target):
        raise Forbidden()
    name = form_str(await request.form(), "name", 60)
    if not name:
        flash(request, "이름을 입력하세요.", "error")
        return redirect("/users")
    with conn:
        conn.execute("UPDATE users SET name = ? WHERE id = ?", (name, user_id))
        apply_rename(conn, user_id, target["name"], name)
    flash(request, "{name}(으)로 이름을 바꿨습니다.", name=name)
    return redirect("/users")


# ---------- 자격증 ----------

@router.post("/me/licences")
async def licence_upload(request: Request, user=Depends(current_user), conn=Depends(get_db)):
    form = await request.form()
    kind = form_str(form, "kind", 20)
    number = form_str(form, "number", 60)
    expiry = form_str(form, "expiry", 10)
    if kind not in C.LICENCE_KINDS:
        raise HTTPException(400)
    try:
        if expiry:
            date.fromisoformat(expiry)
    except ValueError:
        flash(request, "날짜 형식이 올바르지 않습니다.", "error")
        return redirect("/me#licences")
    if kind == "fgas" and not (number and expiry):
        flash(request, "F-Gas 자격증은 번호와 만료일이 꼭 필요합니다.", "error")
        return redirect("/me#licences")
    try:
        saved = await save_uploads(form.getlist("files")[:1], "licences")
    except UserError as e:
        flash(request, e.key, "error", **e.args_)
        return redirect("/me#licences")
    if not saved:
        flash(request, "자격증 사진이나 PDF를 올려 주세요.", "error")
        return redirect("/me#licences")
    f = saved[0]
    try:
        with conn:
            conn.insert("""INSERT INTO licences (user_id, kind, number, expiry, stored_name, original_name, mime, size, uploaded_by, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (user["id"], kind, number or None, expiry or None, f["stored_name"], f["original_name"], f["mime"], f["size"],
                         user["id"], now()))
    except Exception:
        remove_files(saved)
        raise
    flash(request, "자격증을 올렸습니다. 회사가 확인해 승인하면 시공에 배정될 수 있습니다.")
    return redirect("/me#licences")


@router.get("/licences/{licence_id}")
def licence_file(licence_id: int, download: int = 0, user=Depends(current_user), conn=Depends(get_db)):
    lic = conn.execute("SELECT * FROM licences WHERE id = ?", (licence_id,)).fetchone()
    if not lic or not lic["stored_name"] or (lic["user_id"] != user["id"] and not is_office(user)):
        raise HTTPException(404)
    return storage.response(lic["stored_name"], lic["mime"], lic["original_name"], bool(download))


@router.post("/licences/{licence_id}/remove")
def licence_remove(request: Request, licence_id: int, user=Depends(current_user), conn=Depends(get_db)):
    lic = conn.execute("SELECT * FROM licences WHERE id = ? AND removed_at IS NULL", (licence_id,)).fetchone()
    if not lic or (lic["user_id"] != user["id"] and not is_office(user)):
        raise HTTPException(404)
    if lic["status"] == "approved" and not is_office(user):
        flash(request, "승인된 자격증은 회사만 지울 수 있습니다. 새 자격증을 올리면 회사가 다시 확인합니다.", "error")
        return redirect("/me#licences")
    with conn:
        conn.execute("UPDATE licences SET removed_at = ? WHERE id = ?", (now(), licence_id))
    flash(request, "자격증을 지웠습니다.")
    return redirect("/users#installers" if is_office(user) else "/me#licences")


@router.post("/licences/{licence_id}/review")
async def licence_review(request: Request, licence_id: int, user=Depends(office_user), conn=Depends(get_db)):
    lic = conn.execute("SELECT * FROM licences WHERE id = ? AND removed_at IS NULL", (licence_id,)).fetchone()
    if not lic:
        raise HTTPException(404)
    form = await request.form()
    decision = form_str(form, "decision", 10)
    if decision not in ("approved", "rejected"):
        raise HTTPException(400)
    note = form_str(form, "note", 300)
    if decision == "rejected" and not note:
        flash(request, "반려 사유를 적어 주세요. 설치업자에게 보입니다.", "error")
        return redirect(f"/users#lic-{lic['user_id']}")
    ts = now()
    with conn:
        conn.execute("UPDATE licences SET status = ?, checked_by = ?, checked_at = ?, check_note = ? WHERE id = ?",
                     (decision, user["id"], ts, note or None, licence_id))
        if decision == "approved" and lic["kind"] == "fgas":      # 승인한 F-Gas가 배정 기준이 된다
            conn.execute("""UPDATE users SET fgas_no = ?, fgas_expiry = ?, fgas_approved_at = ?, fgas_approved_by = ? WHERE id = ?""",
                         (lic["number"], lic["expiry"], ts, user["id"], lic["user_id"]))
        if decision == "approved" and lic["kind"] == "company":
            conn.execute("UPDATE users SET fgas_company = ? WHERE id = ?",
                         (" · ".join(x for x in (lic["number"], lic["expiry"]) if x) or None, lic["user_id"]))
    flash(request, "자격증을 승인했습니다." if decision == "approved" else "자격증을 반려했습니다.")
    back = form_str(form, "back", 200)
    return redirect(back if back.startswith("/") and not back.startswith("//") else f"/users#lic-{lic['user_id']}")
