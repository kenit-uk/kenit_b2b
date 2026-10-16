"""작업 범위(Scope) 버전 (시안 p.9 설치업자 변경 확인, p.23 회사 버전 표).

- 범위가 바뀌면 새 버전을 만든다(이전 버전은 '대체됨'). 가격만 바뀌면 버전을 만들지 않고 견적 기록만 남긴다.
- 가격은 넣지 않는다.
- 회사가 '확정(잠금)'하면 그 뒤로는 변경 요청(VO)으로만 바꾼다.
- 설치업자는 이전 버전 대비 바뀐 것만 보고 '확인했어요'(기록 남음) 또는 '의견 남기기'(회사로 메시지).
"""
import json

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException

from . import constants as C
from .auth import current_user, office_user
from .db import audit, get_db, now
from .main import flash, form_str, get_house_or_404, is_office, redirect, render

router = APIRouter()

ROOM_FIELDS = ("indoor_type", "kw", "model", "location", "memo")
ACC_FIELDS = ("controls", "lcd_colour", "grille", "plenum", "hatch", "extra")
ODU_FIELDS = ("mount", "model", "note")
COMMON_FIELDS = ("system", "pipe_ok", "power_near", "electric_work", "trunking", "trunking_colour", "trunking_other", "access")


def _norm(v):
    return v if v not in ("", None, []) else None


def diff_scopes(old: dict, new: dict) -> dict:
    """이전 버전 대비 바뀐 것. 방은 이름으로 짝을 짓는다(같은 이름이 여러 개면 순서대로)."""
    rooms, used = [], set()
    old_rooms = old.get("rooms", [])
    for nr in new.get("rooms", []):
        idx = next((i for i, o in enumerate(old_rooms) if i not in used and o.get("name") == nr.get("name")), None)
        if idx is None:
            rooms.append({"status": "added", "room": nr, "changes": []})
            continue
        used.add(idx)
        orr = old_rooms[idx]
        ch = [(f, orr.get(f), nr.get(f)) for f in ROOM_FIELDS if _norm(orr.get(f)) != _norm(nr.get(f))]
        oa, na = orr.get("acc") or {}, nr.get("acc") or {}
        ch += [(f, oa.get(f), na.get(f)) for f in ACC_FIELDS if _norm(oa.get(f)) != _norm(na.get(f))]
        rooms.append({"status": "changed" if ch else "same", "room": nr, "changes": ch})
    removed = [o for i, o in enumerate(old_rooms) if i not in used]
    odus = []
    oo, no = old.get("odus", []), new.get("odus", [])
    for i in range(max(len(oo), len(no))):
        a, b = (oo[i] if i < len(oo) else None), (no[i] if i < len(no) else None)
        if a is None:
            odus.append({"status": "added", "odu": b, "changes": [], "n": i + 1})
        elif b is None:
            odus.append({"status": "removed", "odu": a, "changes": [], "n": i + 1})
        else:
            ch = [(f, a.get(f), b.get(f)) for f in ODU_FIELDS if _norm(a.get(f)) != _norm(b.get(f))]
            odus.append({"status": "changed" if ch else "same", "odu": b, "changes": ch, "n": i + 1})
    oc, nc = old.get("common", {}), new.get("common", {})
    common = [(f, oc.get(f), nc.get(f)) for f in COMMON_FIELDS if _norm(oc.get(f)) != _norm(nc.get(f))]
    count = sum(1 for r in rooms if r["status"] != "same") + len(removed) \
        + sum(1 for o in odus if o["status"] != "same") + (1 if common else 0)
    return {"rooms": rooms, "removed": removed, "odus": odus, "common": common, "count": count}


def get_versions(conn, house_id: int):
    return conn.execute(
        """SELECT s.*, u.name AS creator_name, lu.name AS locker_name FROM scope_versions s
           JOIN users u ON u.id = s.created_by LEFT JOIN users lu ON lu.id = s.locked_by
           WHERE s.house_id = ? ORDER BY s.version""", (house_id,)).fetchall()


def latest_scope(conn, house_id: int):
    return conn.execute("SELECT * FROM scope_versions WHERE house_id = ? ORDER BY version DESC LIMIT 1", (house_id,)).fetchone()


def unacked_scopes(conn, user) -> list:
    """설치업자가 아직 확인하지 않은 최신 작업 범위 (배정된 프로젝트만). 오늘 할 일·프로젝트 화면에서 씀."""
    return conn.execute(
        """SELECT s.id, s.version, s.house_id, h.address, h.postcode FROM scope_versions s JOIN houses h ON h.id = s.house_id
           JOIN house_members m ON m.house_id = s.house_id AND m.user_id = ?
           WHERE s.version = (SELECT MAX(version) FROM scope_versions x WHERE x.house_id = s.house_id)
             AND NOT EXISTS (SELECT 1 FROM scope_acks a WHERE a.scope_id = s.id AND a.user_id = ?)
           ORDER BY s.created_at DESC""", (user["id"], user["id"])).fetchall()


def loads(s):
    return json.loads(s) if s else {}


@router.get("/houses/{house_id}/scope")
def scope_latest(request: Request, house_id: int, user=Depends(current_user), conn=Depends(get_db)):
    get_house_or_404(conn, house_id, user)
    last = latest_scope(conn, house_id)
    if not last:
        flash(request, "아직 작업 범위가 없습니다.", "error")
        return redirect(f"/houses/{house_id}")
    return redirect(f"/houses/{house_id}/scope/{last['version']}")


@router.get("/houses/{house_id}/scope/{version}")
def scope_page(request: Request, house_id: int, version: int, compare: int = 0,
               user=Depends(current_user), conn=Depends(get_db)):
    house = get_house_or_404(conn, house_id, user)
    versions = get_versions(conn, house_id)
    row = next((v for v in versions if v["version"] == version), None)
    if not row:
        raise HTTPException(404)
    base_v = compare or (version - 1)
    base = next((v for v in versions if v["version"] == base_v), None)
    data = loads(row["data"])
    diff = diff_scopes(loads(base["data"]), data) if base else None
    acked = conn.execute("SELECT at FROM scope_acks WHERE scope_id = ? AND user_id = ?", (row["id"], user["id"])).fetchone()
    acks = conn.execute(
        """SELECT u.id, u.name, a.at FROM house_members m JOIN users u ON u.id = m.user_id
           LEFT JOIN scope_acks a ON a.scope_id = ? AND a.user_id = u.id WHERE m.house_id = ? ORDER BY u.name""",
        (row["id"], house_id)).fetchall()
    comments = conn.execute(
        """SELECT m.*, u.name AS author_name FROM messages m JOIN users u ON u.id = m.user_id
           WHERE m.house_id = ? AND m.kind = 'scope_comment' AND m.scope_id = ? ORDER BY m.id""",
        (house_id, row["id"])).fetchall() if is_office(user) else conn.execute(
        """SELECT m.*, u.name AS author_name FROM messages m JOIN users u ON u.id = m.user_id
           WHERE m.kind = 'scope_comment' AND m.scope_id = ? AND m.user_id = ? ORDER BY m.id""",
        (row["id"], user["id"])).fetchall()
    return render(request, "scope.html", user=user, house=house, scope=row, data=data, versions=versions, C=C,
                  base=base, diff=diff, acked=acked, acks=acks, comments=comments,
                  is_latest=row["version"] == versions[-1]["version"], locked=any(v["locked_at"] for v in versions))


@router.post("/houses/{house_id}/scope/{version}/ack")
def scope_ack(request: Request, house_id: int, version: int, user=Depends(current_user), conn=Depends(get_db)):
    get_house_or_404(conn, house_id, user)
    row = conn.execute("SELECT id FROM scope_versions WHERE house_id = ? AND version = ?", (house_id, version)).fetchone()
    if not row:
        raise HTTPException(404)
    if not conn.execute("SELECT 1 FROM scope_acks WHERE scope_id = ? AND user_id = ?", (row["id"], user["id"])).fetchone():
        with conn:
            conn.execute("INSERT INTO scope_acks (scope_id, user_id, at) VALUES (?, ?, ?)", (row["id"], user["id"], now()))
            audit(conn, house_id=house_id, actor_id=user["id"], action="scope_ack", detail=str(version))
    flash(request, "작업 범위 v{n}을 확인했다고 기록했습니다.", n=version)
    return redirect(f"/houses/{house_id}/scope/{version}")


@router.post("/houses/{house_id}/scope/{version}/comment")
async def scope_comment(request: Request, house_id: int, version: int, user=Depends(current_user), conn=Depends(get_db)):
    """설치업자의 기술 의견 (예: 배관 길이가 너무 길다) → 회사 승인 대기로 간다."""
    get_house_or_404(conn, house_id, user)
    row = conn.execute("SELECT id FROM scope_versions WHERE house_id = ? AND version = ?", (house_id, version)).fetchone()
    if not row:
        raise HTTPException(404)
    body = form_str(await request.form(), "body", 4000)
    if not body:
        flash(request, "의견을 입력하세요.", "error")
        return redirect(f"/houses/{house_id}/scope/{version}#comment")
    with conn:
        conn.insert("""INSERT INTO messages (house_id, kind, scope_id, user_id, body, created_at)
                       VALUES (?, 'scope_comment', ?, ?, ?, ?)""", (house_id, row["id"], user["id"], body, now()))
        audit(conn, house_id=house_id, actor_id=user["id"], action="scope_comment", detail=str(version))
    flash(request, "의견을 회사에 보냈습니다.")
    return redirect(f"/houses/{house_id}/scope/{version}")


# ---------- 회사: 편집·확정 ----------

def is_locked(conn, house_id: int) -> bool:
    return bool(conn.execute("SELECT 1 FROM scope_versions WHERE house_id = ? AND locked_at IS NOT NULL", (house_id,)).fetchone())


@router.get("/houses/{house_id}/scope-edit")
def scope_edit_page(request: Request, house_id: int, user=Depends(office_user), conn=Depends(get_db)):
    house = get_house_or_404(conn, house_id, user)
    if is_locked(conn, house_id):
        flash(request, "확정된 작업 범위는 변경 요청(VO)으로만 바꿀 수 있습니다.", "error")
        return redirect(f"/houses/{house_id}/scope")
    last = latest_scope(conn, house_id)
    data = loads(last["data"]) if last else {"rooms": [], "odus": [{}], "common": {}}
    return render(request, "scope_edit.html", user=user, house=house, data=data, last=last, C=C)


def _parse_scope_form(form) -> dict:
    rooms = []
    i = 0
    while f"r{i}_name" in form:
        name = form_str(form, f"r{i}_name", 80)
        if name and form.get(f"r{i}_remove") != "1":
            pick = lambda k, choices: (lambda v: v if v in choices else None)(form_str(form, f"r{i}_{k}", 20))
            controls = [c for c in form.getlist(f"r{i}_controls") if c in C.CONTROLS]
            rooms.append({
                "name": name, "indoor_type": pick("indoor_type", C.INDOOR_TYPES), "kw": pick("kw", C.KW_OPTIONS),
                "model": form_str(form, f"r{i}_model", 80), "location": form_str(form, f"r{i}_location", 200),
                "memo": form_str(form, f"r{i}_memo", 2000),
                "acc": {"controls": controls, "lcd_colour": pick("lcd_colour", C.LCD_COLOURS) if "lcd" in controls else None,
                        "grille": pick("grille", C.INCLUDE_CHOICES), "plenum": pick("plenum", C.INCLUDE_CHOICES),
                        "hatch": pick("hatch", C.HATCH_CHOICES), "extra": form_str(form, f"r{i}_extra", 500) or None},
            })
        i += 1
    odus = []
    i = 0
    while f"o{i}_mount" in form or f"o{i}_model" in form:
        if form.get(f"o{i}_remove") != "1":
            m = form_str(form, f"o{i}_mount", 20)
            odu = {"mount": m if m in C.ODU_MOUNTS else None, "model": form_str(form, f"o{i}_model", 80),
                   "note": form_str(form, f"o{i}_note", 300)}
            if any(odu.values()):
                odus.append(odu)
        i += 1
    choices = {"system": C.SYSTEMS, "pipe_ok": C.YES_NO, "power_near": C.YES_NO, "electric_work": C.NEEDED,
               "trunking": C.TRUNKING, "trunking_colour": C.TRUNKING_COLOURS, "access": C.ACCESS}
    common = {k: (lambda v: v if v in ch else None)(form_str(form, k, 20)) for k, ch in choices.items()}
    common["trunking_other"] = form_str(form, "trunking_other", 80) if common["trunking_colour"] == "other" else ""
    return {"rooms": rooms, "odus": odus, "common": common}


@router.post("/houses/{house_id}/scope-edit")
async def scope_save(request: Request, house_id: int, user=Depends(office_user), conn=Depends(get_db)):
    house = get_house_or_404(conn, house_id, user)
    if is_locked(conn, house_id):
        raise HTTPException(400, "확정된 작업 범위는 변경 요청(VO)으로만 바꿀 수 있습니다.")
    form = await request.form()
    data = _parse_scope_form(form)
    summary = form_str(form, "summary", 500)
    last = latest_scope(conn, house_id)
    error = None
    if not data["rooms"]:
        error = "방을 하나 이상 넣으세요."
    elif not summary:
        error = "변경 내용을 적어 주세요. 설치업자가 무엇이 바뀌었는지 봅니다."
    elif last and diff_scopes(loads(last["data"]), data)["count"] == 0:
        error = "바뀐 내용이 없습니다. 가격만 바뀐 경우에는 새 버전을 만들지 않고 견적 기록만 남기세요."
    if error:
        return render(request, "scope_edit.html", user=user, house=house, data=data, last=last, C=C, summary=summary,
                      error=error, status_code=400)
    version = (last["version"] if last else 0) + 1
    with conn:
        conn.insert("""INSERT INTO scope_versions (house_id, version, data, note, created_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""", (house_id, version, json.dumps(data), summary, user["id"], now()))
        audit(conn, house_id=house_id, actor_id=user["id"], action="scope_create", detail=str(version))
    flash(request, "작업 범위 v{n}을 만들었습니다. 배정된 설치업자에게 변경 알림이 갑니다.", n=version)
    return redirect(f"/houses/{house_id}/scope/{version}")


@router.post("/houses/{house_id}/scope/{version}/lock")
def scope_lock(request: Request, house_id: int, version: int, user=Depends(office_user), conn=Depends(get_db)):
    """확정(잠금). v1에서는 고객 서명 대신 회사가 표시한다. 4단계 '최종 확정'으로 옮긴다."""
    house = get_house_or_404(conn, house_id, user)
    last = latest_scope(conn, house_id)
    if not last or last["version"] != version:
        raise HTTPException(400, "최신 버전만 확정할 수 있습니다.")
    if is_locked(conn, house_id):
        return redirect(f"/houses/{house_id}/scope/{version}")
    ts = now()
    with conn:
        conn.execute("UPDATE scope_versions SET locked_at = ?, locked_by = ? WHERE id = ?", (ts, user["id"], last["id"]))
        audit(conn, house_id=house_id, actor_id=user["id"], action="scope_lock", detail=str(version))
        if house["stage"] < 4:
            conn.execute("UPDATE houses SET stage = 4, updated_by = ?, updated_at = ? WHERE id = ?", (user["id"], ts, house_id))
            audit(conn, house_id=house_id, actor_id=user["id"], action="stage", detail=f"{house['stage']} → 4")
    flash(request, "작업 범위 v{n}을 확정했습니다. 이후 변경은 변경 요청(VO)으로만 할 수 있습니다.", n=version)
    return redirect(f"/houses/{house_id}/scope/{version}")
