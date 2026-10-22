"""현장조사 (시안 p.6~8 설치업자, p.24 회사 검토) 와 작업 범위 v1 만들기.

설치업자 흐름: 방 카드(사진·실내기 형태·대략 용량·메모) → 액세서리 → … 다음 방 … → 실외기·트렁킹·전기 → 회사로 보내기
회사 흐름: 검토 → 방별 추가 사진 요청 → "이 조사로 작업 범위 만들기"
"""
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from starlette.exceptions import HTTPException

from . import constants as C
from .auth import Forbidden, current_user, office_user
from .db import audit, get_db, now
from .i18n import detect_lang, translate
from starlette.concurrency import run_in_threadpool

from .main import (UserError, collect_direct_uploads, flash, form_str, get_house_or_404, is_office, redirect,
                   remove_files, render, save_uploads, storage)
from .storage import THUMB_MIMES, ensure_thumb

router = APIRouter()

from . import survey_options as SO   # 액세서리 선택지는 관리자 설정에서 온다


# ---------- 조회 ----------

def get_survey(conn, house_id: int):
    return conn.execute("SELECT * FROM surveys WHERE house_id = ?", (house_id,)).fetchone()


def survey_or_404(conn, house_id: int):
    s = get_survey(conn, house_id)
    if not s:
        raise HTTPException(404)
    return s


def list_rooms(conn, survey_id: int):
    return conn.execute("SELECT * FROM survey_rooms WHERE survey_id = ? AND removed_at IS NULL ORDER BY sort, id",
                        (survey_id,)).fetchall()


def list_odus(conn, survey_id: int):
    return conn.execute("SELECT * FROM survey_odus WHERE survey_id = ? AND removed_at IS NULL ORDER BY sort, id",
                        (survey_id,)).fetchall()


def room_or_404(conn, survey_id: int, room_id: int):
    r = conn.execute("SELECT * FROM survey_rooms WHERE id = ? AND survey_id = ? AND removed_at IS NULL",
                     (room_id, survey_id)).fetchone()
    if not r:
        raise HTTPException(404)
    return r


def media_for(conn, owner_type: str, ids) -> dict[int, list]:
    ids = list(ids)
    if not ids:
        return {}
    rows = conn.execute(
        f"""SELECT * FROM media WHERE owner_type = ? AND owner_id IN ({",".join("?" * len(ids))})
            AND deleted_at IS NULL ORDER BY id""", (owner_type, *ids)).fetchall()
    out: dict[int, list] = {}
    for r in rows:
        out.setdefault(r["owner_id"], []).append(r)
    return out


def loads(s) -> dict:
    return json.loads(s) if s else {}


def base_acc(defaults: dict, indoor_type, opts: dict) -> dict:
    """이 방에서 따로 고치지 않았을 때의 값.
    컨트롤·LCD 색상은 '모든 방에 적용' 값을 모든 방이 따르고, 그릴·플레넘·점검구는 실내기 형태마다 다르므로
    같은 형태의 방에서 '모든 방에 적용'한 값만 따른다 (덕트형은 벽걸이 방의 '해당 없음'을 물려받지 않음)."""
    itype = indoor_type or "wall"
    by_type = defaults.get("by_type", {}).get(itype, {})
    return {
        "controls": defaults.get("controls", []),
        "lcd_colour": defaults.get("lcd_colour"),
        **{it["key"]: by_type.get(it["key"], (it.get("defaults") or {}).get(itype)) for it in SO.active_items(opts)},
    }


def resolve_acc(defaults: dict, room, opts: dict) -> dict:
    """방의 액세서리 값 = 이 방에서 고친 값 → 기본값(모든 방에 적용 / 실내기 형태별) 순서."""
    own = loads(room["acc"])
    out = base_acc(defaults, room["indoor_type"], opts)
    known = {it["key"] for it in opts["items"]}            # 숨긴 항목에 예전에 고른 값도 남겨 보여준다
    out.update({k: v for k, v in own.items() if k in (*out, "extra") or k in known})
    out.setdefault("extra", None)
    return out


def survey_is_empty(conn, survey) -> bool:
    """방 형태·용량을 하나도 고르지 않았고 사진도 없는 조사 (검토하거나 작업 범위로 만들 수 없다)."""
    if conn.execute("SELECT 1 FROM survey_rooms WHERE survey_id = ? AND (COALESCE(indoor_type, '') <> '' OR COALESCE(kw, '') <> '')",
                    (survey["id"],)).fetchone():
        return False
    return not conn.execute(
        """SELECT 1 FROM media WHERE deleted_at IS NULL AND (
             (owner_type = 'room' AND owner_id IN (SELECT id FROM survey_rooms WHERE survey_id = ?))
          OR (owner_type = 'odu' AND owner_id IN (SELECT id FROM survey_odus WHERE survey_id = ?))
          OR (owner_type = 'board' AND owner_id = ?))""", (survey["id"], survey["id"], survey["id"])).fetchone()


def room_problems(room, acc: dict, opts: dict) -> list[str]:
    """회사로 보내기 전에 채워야 할 것 (번역 키)."""
    out = []
    if not room["indoor_type"]:
        out.append("실내기 형태를 고르세요.")
    if not room["kw"]:
        out.append("대략 용량을 고르세요.")
    for it in SO.active_items(opts):
        if acc.get(it["key"]) is None:
            out.append("액세서리 항목을 모두 고르세요.")
            break
    return out


def outdoor_problems(odus, common: dict) -> list[str]:
    out = []
    if not odus or any(not o["mount"] for o in odus):
        out.append("실외기 설치 위치를 고르세요.")
    for key, msg in (("system", "추천 시스템을 고르세요."), ("electric_work", "전기 작업 필요 여부를 고르세요."),
                     ("trunking", "트렁킹 포함 여부를 고르세요.")):
        if not common.get(key):
            out.append(msg)
    return out


def can_edit(user, survey) -> bool:
    """검토가 끝난 조사는 회사만 고칠 수 있다."""
    return is_office(user) or survey["status"] != "reviewed"


def touch(conn, survey_id: int, user) -> None:
    conn.execute("UPDATE surveys SET updated_by = ?, updated_at = ? WHERE id = ?", (user["id"], now(), survey_id))


async def gather_uploads(conn, form, house_id: int) -> list[dict]:
    saved = await save_uploads(form.getlist("files"), house_id)
    saved += await collect_direct_uploads(conn, form_str(form, "uploaded", 100000), house_id)
    return saved


def add_media(conn, saved, house_id: int, owner_type: str, owner_id: int, user_id: int) -> None:
    ts = now()
    for s in saved:
        conn.execute(
            """INSERT INTO media (house_id, owner_type, owner_id, stored_name, original_name, mime, size, uploaded_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (house_id, owner_type, owner_id, s["stored_name"], s["original_name"], s["mime"], s["size"], user_id, ts))


def remove_media(conn, form, house_id: int, owner_type: str, owner_id: int, user_id: int) -> None:
    ids = [int(x) for x in form.getlist("remove_media") if str(x).isdigit()]
    for mid in ids:
        conn.execute("""UPDATE media SET deleted_by = ?, deleted_at = ? WHERE id = ? AND house_id = ?
                        AND owner_type = ? AND owner_id = ? AND deleted_at IS NULL""",
                     (user_id, now(), mid, house_id, owner_type, owner_id))


async def save_photos(request, conn, form, house_id, owner_type, owner_id, user) -> bool:
    """사진 추가·삭제. 실패하면 안내를 띄우고 False."""
    try:
        saved = await gather_uploads(conn, form, house_id)
    except UserError as e:
        flash(request, e.key, "error", **e.args_)
        return False
    try:
        with conn:
            add_media(conn, saved, house_id, owner_type, owner_id, user["id"])
            remove_media(conn, form, house_id, owner_type, owner_id, user["id"])
    except Exception:
        remove_files(saved)
        raise
    return True


def survey_context(conn, house, survey):
    rooms = list_rooms(conn, survey["id"])
    odus = list_odus(conn, survey["id"])
    defaults = loads(survey["defaults"])
    opts = SO.load(conn)
    accs = {r["id"]: resolve_acc(defaults, r, opts) for r in rooms}
    return dict(
        house=house, survey=survey, rooms=rooms, odus=odus, defaults=defaults, accs=accs, survey_opts=opts,
        common=loads(survey["common"]),
        room_media=media_for(conn, "room", [r["id"] for r in rooms]),
        odu_media=media_for(conn, "odu", [o["id"] for o in odus]),
        board_media=media_for(conn, "board", [survey["id"]]).get(survey["id"], []),
        problems={r["id"]: room_problems(r, accs[r["id"]], opts) for r in rooms},
        outdoor_issues=outdoor_problems(odus, loads(survey["common"])),
    )


def page(request, user, name, conn, house, survey, **extra):
    ctx = survey_context(conn, house, survey)
    ctx.update(extra)
    return render(request, name, user=user, C=C, can_edit=can_edit(user, survey), **ctx)


# ---------- 시작 / 전체 ----------

@router.get("/houses/{house_id}/survey")
def survey_home(request: Request, house_id: int, user=Depends(current_user), conn=Depends(get_db)):
    house = get_house_or_404(conn, house_id, user)
    survey = get_survey(conn, house_id)
    if not survey:
        return render(request, "survey_start.html", user=user, house=house)
    if is_office(user) and request.query_params.get("edit") != "1":
        return page(request, user, "survey_review.html", conn, house, survey, empty=survey_is_empty(conn, survey),
                    scopes=conn.execute("SELECT version, created_at, locked_at FROM scope_versions WHERE house_id = ? ORDER BY version",
                                        (house_id,)).fetchall())
    return page(request, user, "survey_overview.html", conn, house, survey)


@router.post("/houses/{house_id}/survey/start")
def survey_start(request: Request, house_id: int, user=Depends(current_user), conn=Depends(get_db)):
    get_house_or_404(conn, house_id, user)
    if get_survey(conn, house_id):
        return redirect(f"/houses/{house_id}/survey")
    ts = now()
    lang = detect_lang(request)
    with conn:
        sid = conn.insert("""INSERT INTO surveys (house_id, status, created_by, created_at, updated_by, updated_at)
                             VALUES (?, 'draft', ?, ?, ?, ?)""", (house_id, user["id"], ts, user["id"], ts))
        rid = conn.insert("""INSERT INTO survey_rooms (survey_id, sort, name, created_at, updated_at)
                             VALUES (?, 1, ?, ?, ?)""", (sid, translate(lang, "방 {n}", n=1), ts, ts))
        conn.execute("INSERT INTO survey_odus (survey_id, sort, created_at) VALUES (?, 1, ?)", (sid, ts))
        audit(conn, house_id=house_id, actor_id=user["id"], action="survey_start")

    return redirect(f"/houses/{house_id}/survey/rooms/{rid}")


# ---------- 방 카드 (3a) ----------

@router.post("/houses/{house_id}/survey/rooms/new")
async def room_new(request: Request, house_id: int, user=Depends(current_user), conn=Depends(get_db)):
    get_house_or_404(conn, house_id, user)
    survey = survey_or_404(conn, house_id)
    if not can_edit(user, survey):
        raise Forbidden()
    form = await request.form()
    rooms = list_rooms(conn, survey["id"])
    copy_of = form_str(form, "copy_of", 12)
    name = form_str(form, "name", 80)
    if copy_of.isdigit():   # 같은 방에 실내기 한 대 더 (큰 방은 2대가 들어갈 수 있다)
        name = room_or_404(conn, survey["id"], int(copy_of))["name"]
    if not name:
        name = translate(detect_lang(request), "방 {n}", n=len(rooms) + 1)
    ts = now()
    with conn:
        rid = conn.insert("""INSERT INTO survey_rooms (survey_id, sort, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)""",
                          (survey["id"], (max([r["sort"] for r in rooms]) if rooms else 0) + 1, name, ts, ts))
        touch(conn, survey["id"], user)
    return redirect(f"/houses/{house_id}/survey/rooms/{rid}")


@router.get("/houses/{house_id}/survey/rooms/{room_id}")
def room_page(request: Request, house_id: int, room_id: int, user=Depends(current_user), conn=Depends(get_db)):
    house = get_house_or_404(conn, house_id, user)
    survey = survey_or_404(conn, house_id)
    room = room_or_404(conn, survey["id"], room_id)
    return page(request, user, "survey_room.html", conn, house, survey, room=room)


@router.post("/houses/{house_id}/survey/rooms/{room_id}")
async def room_save(request: Request, house_id: int, room_id: int, user=Depends(current_user), conn=Depends(get_db)):
    get_house_or_404(conn, house_id, user)
    survey = survey_or_404(conn, house_id)
    room = room_or_404(conn, survey["id"], room_id)
    if not can_edit(user, survey):
        raise Forbidden()
    form = await request.form()
    name = form_str(form, "name", 80) or room["name"]
    itype = form_str(form, "indoor_type", 20)
    kw = form_str(form, "kw", 10)
    with conn:
        conn.execute("""UPDATE survey_rooms SET name = ?, indoor_type = ?, kw = ?, memo = ?, updated_at = ? WHERE id = ?""",
                     (name, itype if itype in C.INDOOR_TYPES else room["indoor_type"],
                      kw if kw in C.KW_OPTIONS else room["kw"], form_str(form, "memo", 4000), now(), room_id))
        touch(conn, survey["id"], user)
    before = len(media_for(conn, "room", [room_id]).get(room_id, []))
    if not await save_photos(request, conn, form, house_id, "room", room_id, user):
        return redirect(f"/houses/{house_id}/survey/rooms/{room_id}")
    if room["photo_request"] and not is_office(user) and len(media_for(conn, "room", [room_id]).get(room_id, [])) > before:
        with conn:
            conn.execute("UPDATE survey_rooms SET photo_request = NULL WHERE id = ?", (room_id,))
    action = form_str(form, "action", 20)
    if action == "next":
        return redirect(f"/houses/{house_id}/survey/rooms/{room_id}/acc")
    if action == "stay":
        return redirect(f"/houses/{house_id}/survey/rooms/{room_id}")
    flash(request, "저장했습니다. 나중에 이어서 할 수 있습니다.")
    return redirect(f"/houses/{house_id}/survey")


@router.post("/houses/{house_id}/survey/rooms/{room_id}/remove")
def room_remove(request: Request, house_id: int, room_id: int, user=Depends(current_user), conn=Depends(get_db)):
    get_house_or_404(conn, house_id, user)
    survey = survey_or_404(conn, house_id)
    room_or_404(conn, survey["id"], room_id)
    if not can_edit(user, survey):
        raise Forbidden()
    with conn:
        conn.execute("UPDATE survey_rooms SET removed_at = ? WHERE id = ?", (now(), room_id))
        touch(conn, survey["id"], user)
    flash(request, "방 카드를 뺐습니다.")
    return redirect(f"/houses/{house_id}/survey")


# ---------- 액세서리 (3b) ----------

@router.get("/houses/{house_id}/survey/rooms/{room_id}/acc")
def acc_page(request: Request, house_id: int, room_id: int, user=Depends(current_user), conn=Depends(get_db)):
    house = get_house_or_404(conn, house_id, user)
    survey = survey_or_404(conn, house_id)
    room = room_or_404(conn, survey["id"], room_id)
    return page(request, user, "survey_acc.html", conn, house, survey, room=room)


@router.post("/houses/{house_id}/survey/rooms/{room_id}/acc")
async def acc_save(request: Request, house_id: int, room_id: int, user=Depends(current_user), conn=Depends(get_db)):
    get_house_or_404(conn, house_id, user)
    survey = survey_or_404(conn, house_id)
    room = room_or_404(conn, survey["id"], room_id)
    if not can_edit(user, survey):
        raise Forbidden()
    form = await request.form()
    opts = SO.load(conn)
    values = {"controls": [c for c in form.getlist("controls") if c in SO.active_keys(opts, "controls")]}
    values["lcd_colour"] = form_str(form, "lcd_colour", 10) if "lcd" in values["controls"] else None
    if values["lcd_colour"] not in (None, *SO.active_keys(opts, "lcd_colours")):
        values["lcd_colour"] = None
    for it in SO.active_items(opts):
        v = form_str(form, it["key"], 10)
        values[it["key"]] = v if v in SO.ANSWERS[it["answers"]] else None
    extra = form_str(form, "extra", 4000)
    with conn:
        if form_str(form, "action", 20) == "apply_all":
            # 프로젝트 기본값으로 저장하고, 이 방의 덮어쓴 값은 지운다 (다른 방에서 따로 고친 값은 그대로).
            # 그릴·플레넘·점검구는 이 방과 같은 실내기 형태의 기본값으로만 저장한다.
            defaults = loads(survey["defaults"])
            defaults["controls"], defaults["lcd_colour"] = values["controls"], values["lcd_colour"]
            defaults.setdefault("by_type", {})[room["indoor_type"] or "wall"] = {it["key"]: values[it["key"]] for it in SO.active_items(opts)}
            conn.execute("UPDATE surveys SET defaults = ? WHERE id = ?", (json.dumps(defaults), survey["id"]))
            conn.execute("UPDATE survey_rooms SET acc = ?, updated_at = ? WHERE id = ?",
                         (json.dumps({"extra": extra} if extra else {}), now(), room_id))
            flash(request, "모든 방에 적용했습니다. 다른 방은 다른 것만 바꾸면 됩니다.")
        else:
            base = base_acc(loads(survey["defaults"]), room["indoor_type"], opts)
            own = {k: v for k, v in values.items() if v != base.get(k)}   # 기본값과 다른 것만 이 방에 따로 저장
            if extra:
                own["extra"] = extra
            conn.execute("UPDATE survey_rooms SET acc = ?, updated_at = ? WHERE id = ?", (json.dumps(own), now(), room_id))
        touch(conn, survey["id"], user)
    if form_str(form, "action", 20) == "apply_all":
        return redirect(f"/houses/{house_id}/survey/rooms/{room_id}/acc")
    rooms = list_rooms(conn, survey["id"])
    ids = [r["id"] for r in rooms]
    nxt = ids[ids.index(room_id) + 1] if room_id in ids and ids.index(room_id) + 1 < len(ids) else None
    return redirect(f"/houses/{house_id}/survey/rooms/{nxt}" if nxt else f"/houses/{house_id}/survey/outdoor")


# ---------- 실외기·트렁킹·전기 (3c) ----------

COMMON_KEYS = {
    "system": C.SYSTEMS, "pipe_ok": C.YES_NO, "power_near": C.YES_NO, "electric_work": C.NEEDED,
    "trunking": C.TRUNKING, "trunking_colour": C.TRUNKING_COLOURS, "access": C.ACCESS,
}


@router.get("/houses/{house_id}/survey/outdoor")
def outdoor_page(request: Request, house_id: int, user=Depends(current_user), conn=Depends(get_db)):
    house = get_house_or_404(conn, house_id, user)
    survey = survey_or_404(conn, house_id)
    return page(request, user, "survey_outdoor.html", conn, house, survey)


@router.post("/houses/{house_id}/survey/outdoor")
async def outdoor_save(request: Request, house_id: int, user=Depends(current_user), conn=Depends(get_db)):
    house = get_house_or_404(conn, house_id, user)
    survey = survey_or_404(conn, house_id)
    if not can_edit(user, survey):
        raise Forbidden()
    form = await request.form()
    common = loads(survey["common"])
    for k, choices in COMMON_KEYS.items():
        v = form_str(form, k, 20)
        common[k] = v if v in choices else None
    common["trunking_other"] = form_str(form, "trunking_other", 80) if common.get("trunking_colour") == "other" else ""
    common["memo"] = form_str(form, "memo", 4000)
    with conn:
        for o in list_odus(conn, survey["id"]):
            mount = form_str(form, f"odu_{o['id']}_mount", 20)
            conn.execute("UPDATE survey_odus SET mount = ?, note = ? WHERE id = ?",
                         (mount if mount in C.ODU_MOUNTS else o["mount"], form_str(form, f"odu_{o['id']}_note", 500), o["id"]))
        conn.execute("UPDATE surveys SET common = ? WHERE id = ?", (json.dumps(common), survey["id"]))
        touch(conn, survey["id"], user)
    if form_str(form, "action", 20) == "autosave":   # 사진을 올리기 직전 자동 저장 (화면 이동 없음)
        return Response(status_code=204)
    if form_str(form, "action", 20) != "submit" or is_office(user):
        flash(request, "저장했습니다. 나중에 이어서 할 수 있습니다." if not is_office(user) else "저장했습니다.")
        return redirect(f"/houses/{house_id}/survey")
    # 회사로 보내기: 빠진 것이 없는지 확인
    ctx = survey_context(conn, house, get_survey(conn, house_id))
    missing = [p for r in ctx["rooms"] for p in ctx["problems"][r["id"]]] + ctx["outdoor_issues"]
    if not ctx["rooms"]:
        missing.insert(0, "방을 하나 이상 추가하세요.")
    if missing:
        return page(request, user, "survey_overview.html", conn, house, get_survey(conn, house_id),
                    error="빠진 항목이 있습니다. 표시된 곳을 채워 주세요.", status_code=400)
    with conn:
        conn.execute("UPDATE surveys SET status = 'submitted', submitted_by = ?, submitted_at = ? WHERE id = ?",
                     (user["id"], now(), survey["id"]))
        n_photos = sum(len(v) for v in ctx["room_media"].values()) + sum(len(v) for v in ctx["odu_media"].values()) + len(ctx["board_media"])
        audit(conn, house_id=house_id, actor_id=user["id"], action="survey_submit",
              detail=json.dumps({"rooms": len(ctx["rooms"]), "photos": n_photos}))
        # 현장조사 방문은 '오늘 작업 끝' 대신 제출로 끝난다 (남아 있으면 도착·취소 버튼이 계속 보임)
        conn.execute("""UPDATE visits SET status = 'done', updated_at = ? WHERE house_id = ? AND kind = 'survey'
                        AND status IN ('confirmed', 'proposed')""", (now(), house_id))
    flash(request, "현장조사를 회사로 보냈습니다.")
    return redirect(f"/houses/{house_id}/survey")


@router.post("/houses/{house_id}/survey/odus/new")
def odu_new(request: Request, house_id: int, user=Depends(current_user), conn=Depends(get_db)):
    get_house_or_404(conn, house_id, user)
    survey = survey_or_404(conn, house_id)
    if not can_edit(user, survey):
        raise Forbidden()
    odus = list_odus(conn, survey["id"])
    with conn:
        conn.execute("INSERT INTO survey_odus (survey_id, sort, created_at) VALUES (?, ?, ?)",
                     (survey["id"], (max([o["sort"] for o in odus]) if odus else 0) + 1, now()))
        touch(conn, survey["id"], user)
    return redirect(f"/houses/{house_id}/survey/outdoor")


@router.post("/houses/{house_id}/survey/odus/{odu_id}/remove")
def odu_remove(request: Request, house_id: int, odu_id: int, user=Depends(current_user), conn=Depends(get_db)):
    get_house_or_404(conn, house_id, user)
    survey = survey_or_404(conn, house_id)
    if not can_edit(user, survey):
        raise Forbidden()
    with conn:
        conn.execute("UPDATE survey_odus SET removed_at = ? WHERE id = ? AND survey_id = ?", (now(), odu_id, survey["id"]))
        touch(conn, survey["id"], user)
    return redirect(f"/houses/{house_id}/survey/outdoor")


@router.post("/houses/{house_id}/survey/photos/{owner_type}/{owner_id}")
async def photo_upload(request: Request, house_id: int, owner_type: str, owner_id: int,
                       user=Depends(current_user), conn=Depends(get_db)):
    """실외기·분전반 사진 (방 사진은 방 카드 저장 때 같이 올린다)."""
    get_house_or_404(conn, house_id, user)
    survey = survey_or_404(conn, house_id)
    if not can_edit(user, survey):
        raise Forbidden()
    if owner_type == "odu":
        if not conn.execute("SELECT 1 FROM survey_odus WHERE id = ? AND survey_id = ?", (owner_id, survey["id"])).fetchone():
            raise HTTPException(404)
    elif owner_type == "board":
        if owner_id != survey["id"]:
            raise HTTPException(404)
    else:
        raise HTTPException(404)
    form = await request.form()
    await save_photos(request, conn, form, house_id, owner_type, owner_id, user)
    return redirect(f"/houses/{house_id}/survey/outdoor#{owner_type}-{owner_id}")


# ---------- 회사 검토 (p.24) ----------

@router.post("/houses/{house_id}/survey/rooms/{room_id}/photo-request")
async def photo_request(request: Request, house_id: int, room_id: int, user=Depends(office_user), conn=Depends(get_db)):
    get_house_or_404(conn, house_id, user)
    survey = survey_or_404(conn, house_id)
    room = room_or_404(conn, survey["id"], room_id)
    text = form_str(await request.form(), "text", 500)
    with conn:
        conn.execute("UPDATE survey_rooms SET photo_request = ?, updated_at = ? WHERE id = ?", (text or None, now(), room_id))
        if text:
            audit(conn, house_id=house_id, actor_id=user["id"], action="survey_photo_request", detail=f"{room['name']}: {text}")
            if survey["status"] != "draft":
                conn.execute("UPDATE surveys SET status = 'draft', reviewed_by = NULL, reviewed_at = NULL WHERE id = ?", (survey["id"],))
                audit(conn, house_id=house_id, actor_id=user["id"], action="survey_reopen")
    flash(request, "설치업자에게 추가 사진을 요청했습니다. 설치업자가 올린 뒤 다시 보내면 확인할 일에 올라옵니다." if text else "추가 사진 요청을 지웠습니다.")
    return redirect(f"/houses/{house_id}/survey#room-{room_id}")


@router.post("/houses/{house_id}/survey/reviewed")
def survey_reviewed(request: Request, house_id: int, user=Depends(office_user), conn=Depends(get_db)):
    """작업 범위를 만들지 않고 '검토 완료'만 표시 (승인 대기에서 빠진다). 설치업자는 더 이상 고칠 수 없다."""
    get_house_or_404(conn, house_id, user)
    survey = survey_or_404(conn, house_id)
    if survey_is_empty(conn, survey):
        flash(request, "조사 내용이 비어 있습니다. 직접 고치거나 설치업자에게 작성을 맡기세요.", "error")
        return redirect(f"/houses/{house_id}/survey")
    if survey["status"] != "reviewed":
        with conn:
            conn.execute("UPDATE surveys SET status = 'reviewed', reviewed_by = ?, reviewed_at = ? WHERE id = ?",
                         (user["id"], now(), survey["id"]))
            audit(conn, house_id=house_id, actor_id=user["id"], action="survey_reviewed")
        flash(request, "현장조사를 검토 완료로 표시했습니다.")
    return redirect(f"/houses/{house_id}/survey")


@router.post("/houses/{house_id}/survey/reopen")
def survey_reopen(request: Request, house_id: int, user=Depends(office_user), conn=Depends(get_db)):
    """검토 완료를 되돌려 설치업자가 다시 고칠 수 있게 한다 (예: 추가 사진을 받아야 할 때)."""
    get_house_or_404(conn, house_id, user)
    survey = survey_or_404(conn, house_id)
    with conn:
        conn.execute("UPDATE surveys SET status = 'draft', reviewed_by = NULL, reviewed_at = NULL WHERE id = ?", (survey["id"],))
        audit(conn, house_id=house_id, actor_id=user["id"], action="survey_reopen")
    flash(request, "설치업자가 다시 고칠 수 있게 열었습니다.")
    return redirect(f"/houses/{house_id}/survey")


@router.post("/houses/{house_id}/scope/from-survey")
def scope_from_survey(request: Request, house_id: int, user=Depends(office_user), conn=Depends(get_db)):
    """현장조사 값을 그대로 복사해 작업 범위 새 버전을 만든다. 최종 모델·용량은 회사가 LATS로 정한 뒤 고친다."""
    house = get_house_or_404(conn, house_id, user)
    survey = survey_or_404(conn, house_id)
    if conn.execute("SELECT 1 FROM scope_versions WHERE house_id = ? AND locked_at IS NOT NULL", (house_id,)).fetchone():
        raise HTTPException(400, "확정된 작업 범위는 변경 요청(VO)으로만 바꿀 수 있습니다.")
    if survey_is_empty(conn, survey):
        flash(request, "조사 내용이 비어 있습니다. 직접 고치거나 설치업자에게 작성을 맡기세요.", "error")
        return redirect(f"/houses/{house_id}/survey")
    ctx = survey_context(conn, house, survey)
    data = {
        "rooms": [{"name": r["name"], "indoor_type": r["indoor_type"], "kw": r["kw"], "memo": r["memo"] or "",
                   "acc": ctx["accs"][r["id"]]} for r in ctx["rooms"]],
        "odus": [{"mount": o["mount"], "note": o["note"] or ""} for o in ctx["odus"]],
        "common": ctx["common"],
    }
    last = conn.execute("SELECT MAX(version) AS v FROM scope_versions WHERE house_id = ?", (house_id,)).fetchone()["v"] or 0
    ts = now()
    with conn:
        conn.insert("""INSERT INTO scope_versions (house_id, version, data, note, survey_id, created_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (house_id, last + 1, json.dumps(data), "현장조사 기준", survey["id"], user["id"], ts))
        conn.execute("UPDATE surveys SET status = 'reviewed', reviewed_by = ?, reviewed_at = ? WHERE id = ?",
                     (user["id"], ts, survey["id"]))
        audit(conn, house_id=house_id, actor_id=user["id"], action="scope_create", detail=str(last + 1))
        from .main import advance_stage
        advance_stage(conn, house_id, C.QUOTE_STAGE, user["id"])
    flash(request, "작업 범위 v{n}을 만들었습니다.", n=last + 1)
    return redirect(f"/houses/{house_id}/scope/{last + 1}")


# ---------- 사진 보기 ----------

@router.get("/media/{media_id}")
async def media_file(media_id: int, download: int = 0, thumb: int = 0, user=Depends(current_user), conn=Depends(get_db)):
    m = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
    if not m or (m["deleted_at"] and not is_office(user)):
        raise HTTPException(404)
    get_house_or_404(conn, m["house_id"], user)
    if thumb and not download and m["mime"] in THUMB_MIMES:   # 목록에는 작은 사진
        tk = await run_in_threadpool(ensure_thumb, m["stored_name"])
        if tk:
            return storage.response(tk, "image/jpeg", "thumb.jpg", False)
    return storage.response(m["stored_name"], m["mime"], m["original_name"], bool(download))
