"""방문·현장 (시안 p.4 오늘 할 일, p.5 날짜 선택, p.11 현장 도착 후, p.14 오늘 작업 끝, p.15 변경 요청, p.28 달력).

방문 상태: requested(설치업자에게 날짜 후보 요청) → options(후보 받음) → confirmed
           proposed(회사가 날짜 제안, '가능한가요?') → confirmed / declined
           done(오늘 작업 끝 보고됨), cancelled
위치는 '현장 도착'과 '오늘 작업 끝'을 누르는 순간에만 한 번씩 저장한다(백그라운드 추적 없음, UK GDPR).
설치업자에게는 본인 일정만 보인다.
"""
import json
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException

from . import constants as C
from .auth import Forbidden, current_user, office_user
from .db import audit, get_db, now
from .main import LONDON, UserError, advance_stage, flash, form_str, get_house_or_404, is_office, redirect, remove_files, render, safe_next
from .survey import add_media, gather_uploads, media_for

router = APIRouter()

ACTIVE = ("requested", "options", "proposed", "confirmed")      # 달력에 보이고 겹침을 검사하는 상태
BOOKED = ("proposed", "confirmed")                               # 날짜가 정해진 방문


def today() -> date:
    return datetime.now(LONDON).date()


def parse_date(s: str):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def parse_time(s: str) -> str | None:
    s = (s or "").strip()
    try:
        return datetime.strptime(s, "%H:%M").strftime("%H:%M") if s else None
    except ValueError:
        return None


def visit_or_404(conn, visit_id: int, user):
    v = conn.execute(
        """SELECT v.*, h.address, h.postcode, h.ref_no, h.stage, u.name AS installer_name
           FROM visits v JOIN houses h ON h.id = v.house_id LEFT JOIN users u ON u.id = v.installer_id WHERE v.id = ?""",
        (visit_id,)).fetchone()
    if not v:
        raise HTTPException(404)
    get_house_or_404(conn, v["house_id"], user)
    if not is_office(user) and v["installer_id"] != user["id"]:   # 설치업자는 본인 방문만
        raise HTTPException(404)
    return v


def overlaps(a_start, a_end, b_start, b_end) -> bool:
    """시간이 없으면 하루 종일로 본다. 하루에 여러 현장을 갈 수 있으므로 '날짜'가 아니라 '시간 겹침'으로 판단."""
    a0, a1 = a_start or "00:00", a_end or "23:59"
    b0, b1 = b_start or "00:00", b_end or "23:59"
    return a0 < b1 and b0 < a1


def booking_warnings(conn, installer, d: date, start, end, kind, house_id, ignore_visit=None) -> list[dict]:
    """예약할 때 띄우는 경고. 'block'은 막고, 나머지는 사유를 적으면 그대로 예약할 수 있다(기록에 남음)."""
    out = []
    house = conn.execute("SELECT on_hold FROM houses WHERE id = ?", (house_id,)).fetchone()
    if house and house["on_hold"]:
        out.append({"level": "warn", "key": "보류 중인 프로젝트입니다."})
    if kind == "install":
        if not conn.execute("SELECT 1 FROM scope_versions WHERE house_id = ? AND locked_at IS NOT NULL", (house_id,)).fetchone():
            out.append({"level": "warn", "key": "작업 범위가 아직 확정되지 않았습니다."})
        # 결제 관문 (회사 설정): 첫 시공은 계약금, 2차 시공부터는 중도금
        from .payments import gate_block
        first = not install_count(conn, house_id, exclude=ignore_visit)
        msg = gate_block(conn, house_id, "deposit_before_install" if first else "interim_before_next_install")
        if msg:
            out.append({"level": "block", "key": msg})
        from .documents import install_pack_ready
        if not install_pack_ready(conn, house_id):
            out.append({"level": "block", "key": "설치 전 필수 자료(건물 도면, 제품 카탈로그·사양서, 최종 LATS)가 빠져 시공을 예약할 수 없습니다."})
    if installer and d:
        rows = conn.execute(
            f"""SELECT v.id, v.start_time, v.end_time, h.address FROM visits v JOIN houses h ON h.id = v.house_id
                WHERE v.installer_id = ? AND v.date = ? AND v.status IN ({",".join("?" * len(BOOKED))}) AND v.id != ?""",
            (installer["id"], d.isoformat(), *BOOKED, ignore_visit or 0)).fetchall()
        for r in rows:
            if overlaps(start, end, r["start_time"], r["end_time"]):
                out.append({"level": "warn", "key": "{name}님은 이미 이 시간에 예약되어 있습니다: {address} {time}",
                            "args": {"name": installer["name"], "address": r["address"],
                                     "time": f"{r['start_time'] or ''}–{r['end_time'] or ''}".strip("–")}})
        declined = conn.execute("SELECT 1 FROM visits WHERE installer_id = ? AND date = ? AND status = 'declined'",
                                (installer["id"], d.isoformat())).fetchone()
        if declined:
            out.append({"level": "warn", "key": "{name}님이 이 날짜에 '불가'로 답한 적이 있습니다.", "args": {"name": installer["name"]}})
    if installer and kind == "install":     # 냉매 작업: 회사가 승인한 F-Gas가 방문일까지 유효해야 한다
        if not installer["fgas_approved_at"] or not installer["fgas_expiry"]:
            out.append({"level": "block", "key": "{name}님의 F-Gas 자격증이 아직 승인되지 않아 시공에 배정할 수 없습니다. 사용자 관리에서 자격증을 확인하세요.",
                        "args": {"name": installer["name"]}})
        elif installer["fgas_expiry"] < (d or today()).isoformat():
            out.append({"level": "block", "key": "{name}님의 F-Gas 자격이 {when}에 만료되어 이 날짜에 냉매 작업을 배정할 수 없습니다.",
                        "args": {"name": installer["name"], "when": installer["fgas_expiry"]}})
    return out


def install_count(conn, house_id: int, exclude=None) -> int:
    """이 프로젝트에 잡힌 시공 방문 수 (취소·불가 제외)."""
    return conn.execute("""SELECT COUNT(*) AS n FROM visits WHERE house_id = ? AND kind = 'install'
                           AND status NOT IN ('cancelled', 'declined') AND id != ?""", (house_id, exclude or 0)).fetchone()["n"]


def next_seq(conn, house_id: int) -> int:
    """새 시공 방문의 순번 (1차, 2차, …)."""
    row = conn.execute("""SELECT MAX(seq) AS n FROM visits WHERE house_id = ? AND kind = 'install'
                          AND status NOT IN ('cancelled', 'declined')""", (house_id,)).fetchone()
    return (row["n"] or 0) + 1


def rooms_for_tags(conn, house_id: int, lang) -> list[str]:
    """사진 방 태그: 최신 작업 범위의 방 → 없으면 현장조사 방, 마지막에 '실외'."""
    row = conn.execute("SELECT data FROM scope_versions WHERE house_id = ? ORDER BY version DESC LIMIT 1", (house_id,)).fetchone()
    names = [r["name"] for r in json.loads(row["data"]).get("rooms", [])] if row else [
        r["name"] for r in conn.execute(
            """SELECT r.name FROM survey_rooms r JOIN surveys s ON s.id = r.survey_id
               WHERE s.house_id = ? AND r.removed_at IS NULL ORDER BY r.sort, r.id""", (house_id,))]
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    from .i18n import translate
    return out + [translate(lang, "실외")]


# ---------- 회사: 달력·예약 (p.28) ----------

@router.get("/calendar")
def calendar_page(request: Request, week: str = "", house: int = 0, user=Depends(office_user), conn=Depends(get_db)):
    return render(request, "calendar.html", user=user, **calendar_ctx(conn, week, house), form={}, warnings=[])


def calendar_ctx(conn, week: str, house: int) -> dict:
    start = parse_date(week) or today()
    start = start - timedelta(days=start.weekday())               # 월요일
    days = [start + timedelta(days=i) for i in range(7)]
    installers = conn.execute("SELECT * FROM users WHERE role = 'installer' AND active = 1 ORDER BY name").fetchall()
    visits = conn.execute(
        f"""SELECT v.*, h.address, h.ref_no FROM visits v JOIN houses h ON h.id = v.house_id
            WHERE v.date >= ? AND v.date <= ? AND v.status IN ({",".join("?" * len(ACTIVE))}, 'done', 'declined')
            ORDER BY v.date, v.start_time""", (days[0].isoformat(), days[-1].isoformat(), *ACTIVE)).fetchall()
    cells: dict = {}
    for v in visits:
        cells.setdefault((v["installer_id"], v["date"]), []).append(v)
    # 겹침 표시
    clash_ids = set()
    for (iid, d), vs in cells.items():
        booked = [v for v in vs if v["status"] in BOOKED]
        for i, a in enumerate(booked):
            for b in booked[i + 1:]:
                if overlaps(a["start_time"], a["end_time"], b["start_time"], b["end_time"]):
                    clash_ids |= {a["id"], b["id"]}
    houses = conn.execute("SELECT id, address, ref_no FROM houses ORDER BY address").fetchall()
    fgas_soon = [u for u in installers if u["fgas_expiry"] and u["fgas_expiry"] <= (today() + timedelta(days=30)).isoformat()]
    return dict(days=days, installers=installers, cells=cells, clash_ids=clash_ids,
                houses=houses, house_id=house, fgas_soon=fgas_soon, C=C, prev_week=(start - timedelta(days=7)).isoformat(),
                next_week=(start + timedelta(days=7)).isoformat(), today=today().isoformat())


@router.post("/visits")
async def visit_create(request: Request, user=Depends(office_user), conn=Depends(get_db)):
    form = await request.form()
    house_id = int(form_str(form, "house_id", 12) or 0)
    house = get_house_or_404(conn, house_id, user)
    kind = form_str(form, "kind", 20)
    mode = form_str(form, "mode", 20)          # confirmed / proposed / requested
    raw_inst = form_str(form, "installer_id", 12)
    installer = conn.execute("SELECT * FROM users WHERE id = ? AND role = 'installer' AND active = 1",
                             (int(raw_inst) if raw_inst.isdigit() else 0,)).fetchone()
    d = parse_date(form_str(form, "date", 10))
    start, end = parse_time(form_str(form, "start_time", 5)), parse_time(form_str(form, "end_time", 5))
    note = form_str(form, "note", 500)
    override = form_str(form, "override_reason", 500)
    from_project = form_str(form, "from", 20) == "project"      # 프로젝트 화면에서 예약
    back = f"/houses/{house_id}#book" if from_project else f"/calendar?week={(d or today()).isoformat()}&house={house_id}"
    if kind not in C.VISIT_KINDS or mode not in ("confirmed", "proposed", "requested") or not installer:
        flash(request, "방문 종류, 설치업자, 예약 방식을 고르세요.", "error")
        return redirect(back)
    if mode != "requested" and not d:
        flash(request, "날짜를 고르세요.", "error")
        return redirect(back)
    if start and end and end <= start:
        flash(request, "끝나는 시간이 시작 시간보다 늦어야 합니다.", "error")
        return redirect(back)
    warnings = booking_warnings(conn, installer, d if mode != "requested" else None, start, end, kind, house_id)
    if any(w["level"] == "block" for w in warnings) or (warnings and not override):
        # 경고를 보여주고 다시 묻는다 (막는 경고가 아니면 사유를 적고 '그대로 예약' 가능 — 기록에 남음)
        return render(request, "visit_book.html", user=user, house=house, C=C, warnings=warnings, status_code=409,
                      form={k: form_str(form, k, 500) for k in form.keys()},
                      installers=conn.execute("SELECT * FROM users WHERE role = 'installer' AND active = 1 ORDER BY name").fetchall())
    ts = now()
    with conn:
        vid = conn.insert(
            """INSERT INTO visits (house_id, kind, seq, installer_id, date, start_time, end_time, status, note, override_reason,
                                   created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (house_id, kind, next_seq(conn, house_id) if kind == "install" else None, installer["id"],
             d.isoformat() if d and mode != "requested" else None, start, end, mode, note, override or None, user["id"], ts, ts))
        if not conn.execute("SELECT 1 FROM house_members WHERE house_id = ? AND user_id = ?", (house_id, installer["id"])).fetchone():
            conn.execute("INSERT INTO house_members (house_id, user_id, added_by, added_at) VALUES (?, ?, ?, ?)",
                         (house_id, installer["id"], user["id"], ts))
            audit(conn, house_id=house_id, actor_id=user["id"], action="member_add", detail=installer["name"])
        audit(conn, house_id=house_id, actor_id=user["id"], action="visit_create",
              detail=json.dumps({"kind": kind, "date": d.isoformat() if d else None, "mode": mode, "installer": installer["name"], "installer_id": installer["id"], "seq": next_seq(conn, house_id) - 1 if kind == "install" else None,
                                 "override": override or None}, ensure_ascii=False))
    flash(request, {"requested": "{name}님에게 가능한 날짜 2~3개를 골라 달라고 요청했습니다.",
                    "proposed": "{name}님에게 가능 여부를 물었습니다.",
                    "confirmed": "방문을 예약했습니다."}[mode], name=installer["name"])
    if from_project:
        return redirect(f"/houses/{house_id}#visits")
    return redirect(f"/calendar?week={(d or today()).isoformat()}")


@router.post("/visits/{visit_id}/confirm")
async def visit_confirm_option(request: Request, visit_id: int, user=Depends(office_user), conn=Depends(get_db)):
    """설치업자가 보낸 후보 중 하루를 확정 (고객과 통화한 뒤)."""
    v = visit_or_404(conn, visit_id, user)
    form = await request.form()
    d = parse_date(form_str(form, "date", 10))
    if v["status"] not in ("options", "requested") or not d:
        flash(request, "확정할 날짜를 고르세요.", "error")
        return redirect(f"/houses/{v['house_id']}#visits")
    start, end = parse_time(form_str(form, "start_time", 5)), parse_time(form_str(form, "end_time", 5))
    override = form_str(form, "override_reason", 500)
    installer = conn.execute("SELECT * FROM users WHERE id = ?", (v["installer_id"] or 0,)).fetchone()
    warnings = booking_warnings(conn, installer, d, start, end, v["kind"], v["house_id"], ignore_visit=visit_id)
    if any(w["level"] == "block" for w in warnings) or (warnings and not override):
        for w in warnings:
            flash(request, w["key"], "error", **w.get("args", {}))
        if not any(w["level"] == "block" for w in warnings):
            flash(request, "그래도 확정하려면 사유를 적고 다시 확정하세요. 기록에 남습니다.", "error")
        return redirect(f"/houses/{v['house_id']}#confirm-{visit_id}")
    with conn:
        conn.execute("""UPDATE visits SET date = ?, start_time = ?, end_time = ?, status = 'confirmed',
                        override_reason = COALESCE(?, override_reason), updated_at = ? WHERE id = ?""",
                     (d.isoformat(), start, end, override or None, now(), visit_id))
        audit(conn, house_id=v["house_id"], actor_id=user["id"], action="visit_update",
              detail=json.dumps({"date": d.isoformat(), "status": "confirmed", "override": override or None}, ensure_ascii=False))
    flash(request, "{date}로 확정했습니다.", date=d.isoformat())
    return redirect(f"/houses/{v['house_id']}#visits")


@router.post("/visits/{visit_id}/reschedule")
async def visit_reschedule(request: Request, visit_id: int, user=Depends(office_user), conn=Depends(get_db)):
    """날짜·시간·설치업자 바꾸기. 예약할 때와 같은 경고를 확인한다(막는 경고는 넘길 수 없음, 나머지는 사유를 적으면 진행)."""
    v = visit_or_404(conn, visit_id, user)
    form = await request.form()
    back = safe_next(form_str(form, "back", 200)) if form_str(form, "back", 200) else f"/houses/{v['house_id']}#visits"
    if v["status"] in ("done", "cancelled"):
        flash(request, "끝났거나 취소된 방문은 바꿀 수 없습니다.", "error")
        return redirect(back)
    raw_inst = form_str(form, "installer_id", 12)
    installer = conn.execute("SELECT * FROM users WHERE id = ? AND role = 'installer' AND active = 1",
                             (int(raw_inst) if raw_inst.isdigit() else (v["installer_id"] or 0),)).fetchone()
    d = parse_date(form_str(form, "date", 10))
    start, end = parse_time(form_str(form, "start_time", 5)), parse_time(form_str(form, "end_time", 5))
    mode = form_str(form, "mode", 20)
    if not installer or not d:
        flash(request, "날짜와 설치업자를 고르세요.", "error")
        return redirect(back)
    if start and end and end <= start:
        flash(request, "끝나는 시간이 시작 시간보다 늦어야 합니다.", "error")
        return redirect(back)
    override = form_str(form, "override_reason", 500)
    warnings = booking_warnings(conn, installer, d, start, end, v["kind"], v["house_id"], ignore_visit=visit_id)
    if any(w["level"] == "block" for w in warnings) or (warnings and not override):
        for w in warnings:
            flash(request, w["key"], "error", **w.get("args", {}))
        if not any(w["level"] == "block" for w in warnings):
            flash(request, "그래도 바꾸려면 사유를 적고 다시 저장하세요. 기록에 남습니다.", "error")
        return redirect(back)
    status = "proposed" if mode == "proposed" else "confirmed"
    ts = now()
    with conn:
        conn.execute("""UPDATE visits SET installer_id = ?, date = ?, start_time = ?, end_time = ?, status = ?,
                        override_reason = COALESCE(?, override_reason), updated_at = ? WHERE id = ?""",
                     (installer["id"], d.isoformat(), start, end, status, override or None, ts, visit_id))
        if not conn.execute("SELECT 1 FROM house_members WHERE house_id = ? AND user_id = ?", (v["house_id"], installer["id"])).fetchone():
            conn.execute("INSERT INTO house_members (house_id, user_id, added_by, added_at) VALUES (?, ?, ?, ?)",
                         (v["house_id"], installer["id"], user["id"], ts))
            audit(conn, house_id=v["house_id"], actor_id=user["id"], action="member_add", detail=installer["name"])
        audit(conn, house_id=v["house_id"], actor_id=user["id"], action="visit_update",
              detail=json.dumps({"from": v["date"], "date": d.isoformat(), "installer": installer["name"], "installer_id": installer["id"], "status": status,
                                 "override": override or None}, ensure_ascii=False))
    flash(request, "일정을 바꿨습니다." if status == "confirmed" else "일정을 바꾸고 {name}님에게 가능 여부를 물었습니다.", name=installer["name"])
    return redirect(back)


@router.post("/visits/{visit_id}/cancel")
def visit_cancel(request: Request, visit_id: int, user=Depends(office_user), conn=Depends(get_db)):
    v = visit_or_404(conn, visit_id, user)
    with conn:
        conn.execute("UPDATE visits SET status = 'cancelled', updated_at = ? WHERE id = ?", (now(), visit_id))
        audit(conn, house_id=v["house_id"], actor_id=user["id"], action="visit_update", detail=json.dumps({"status": "cancelled"}))
    flash(request, "방문을 취소했습니다.")
    return redirect(f"/calendar?week={v['date'] or today().isoformat()}")


# ---------- 설치업자: 오늘 할 일 (p.4) — '프로젝트' 화면 위쪽에 보여준다 ----------

@router.get("/today")
def today_page(request: Request, user=Depends(current_user)):
    """예전 주소. 메뉴를 줄이면서 '오늘 할 일'은 '프로젝트' 화면 위쪽으로 합쳤다."""
    return redirect("/approvals" if is_office(user) else "/houses")


def today_context(conn, user) -> dict:
    t = today().isoformat()
    mine = conn.execute(
        """SELECT v.*, h.address, h.postcode, h.on_hold,
                  (SELECT at FROM checkins c WHERE c.visit_id = v.id AND c.kind = 'arrive' ORDER BY id DESC LIMIT 1) AS arrived_at
           FROM visits v JOIN houses h ON h.id = v.house_id
           WHERE v.installer_id = ? AND v.status NOT IN ('cancelled') ORDER BY v.date, v.start_time""", (user["id"],)).fetchall()
    todays = [v for v in mine if v["date"] == t and v["status"] in ("confirmed", "done")]
    upcoming = [v for v in mine if v["date"] and v["date"] > t and v["status"] in ("confirmed", "proposed")][:8]
    proposals = [v for v in mine if v["status"] == "proposed"]
    missed = [v for v in mine if v["status"] == "confirmed" and v["kind"] != "survey" and v["date"] and v["date"] < t]
    date_requests = [v for v in mine if v["status"] == "requested"]
    surveys = conn.execute(
        """SELECT s.id, s.house_id, h.address, h.postcode,
                  (SELECT COUNT(*) FROM survey_rooms r WHERE r.survey_id = s.id AND r.removed_at IS NULL) AS rooms,
                  (SELECT COUNT(*) FROM survey_rooms r WHERE r.survey_id = s.id AND r.removed_at IS NULL
                     AND r.indoor_type IS NOT NULL AND r.kw IS NOT NULL) AS done_rooms,
                  (SELECT COUNT(*) FROM survey_rooms r WHERE r.survey_id = s.id AND r.removed_at IS NULL AND r.photo_request IS NOT NULL) AS requests
           FROM surveys s JOIN houses h ON h.id = s.house_id JOIN house_members m ON m.house_id = s.house_id AND m.user_id = ?
           WHERE s.status = 'draft' OR EXISTS (SELECT 1 FROM survey_rooms r WHERE r.survey_id = s.id AND r.photo_request IS NOT NULL
                                               AND r.removed_at IS NULL AND s.status != 'reviewed')""", (user["id"],)).fetchall()
    from .documents import unacked_docs
    from .scope import unacked_scopes
    scopes = unacked_scopes(conn, user)
    docs = unacked_docs(conn, user)
    doc_houses: dict = {}
    for d in docs:                                   # 프로젝트마다 한 장씩
        doc_houses.setdefault(d["house_id"], {"address": d["address"], "n": 0})["n"] += 1
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    decisions = conn.execute(
        """SELECT x.*, h.address FROM variations x JOIN houses h ON h.id = x.house_id
           WHERE x.created_by = ? AND x.status != 'pending' AND x.decided_at >= ? ORDER BY x.decided_at DESC""",
        (user["id"], since)).fetchall()
    rooms = {}
    for v in todays:
        row = conn.execute("SELECT data FROM scope_versions WHERE house_id = ? ORDER BY version DESC LIMIT 1", (v["house_id"],)).fetchone()
        rooms[v["id"]] = len(json.loads(row["data"]).get("rooms", [])) if row else 0
    return dict(todays=todays, upcoming=upcoming, proposals=proposals, date_requests=date_requests, surveys=surveys,
                scopes=scopes, docs=docs, doc_houses=doc_houses, rooms=rooms, missed=missed, decisions=decisions, C=C, today=today())


# ---------- 설치업자: 날짜 고르기 (p.5) / 가능 여부 답 ----------

@router.get("/visits/{visit_id}/dates")
def dates_page(request: Request, visit_id: int, month: str = "", user=Depends(current_user), conn=Depends(get_db)):
    v = visit_or_404(conn, visit_id, user)
    first = (parse_date(month + "-01") if month else None) or today().replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    weeks, d = [], first - timedelta(days=first.weekday())
    while d < nxt or d.weekday() != 0:
        if d.weekday() == 0:
            weeks.append([])
        weeks[-1].append(d)
        d += timedelta(days=1)
    busy = {r["date"] for r in conn.execute(
        "SELECT date FROM visits WHERE installer_id = ? AND status IN ('confirmed', 'proposed') AND date IS NOT NULL",
        (v["installer_id"],))}                                   # 본인 일정만 (다른 설치업자 일정은 절대 보이지 않음)
    chosen = [r["date"] for r in conn.execute("SELECT date FROM visit_date_options WHERE visit_id = ? ORDER BY date", (visit_id,))]
    return render(request, "visit_dates.html", user=user, visit=v, weeks=weeks, first=first, busy=busy, chosen=chosen,
                  today=today(), C=C, prev_month=(first - timedelta(days=1)).strftime("%Y-%m"), next_month=nxt.strftime("%Y-%m"))


@router.post("/visits/{visit_id}/dates")
async def dates_send(request: Request, visit_id: int, user=Depends(current_user), conn=Depends(get_db)):
    v = visit_or_404(conn, visit_id, user)
    if v["installer_id"] != user["id"] or v["status"] not in ("requested", "options"):
        raise Forbidden()
    picked = sorted({d for d in (await request.form()).getlist("date") if (pd := parse_date(d)) and pd >= today()})
    if not 1 <= len(picked) <= 3:
        flash(request, "가능한 날짜를 1~3개 고르세요.", "error")
        return redirect(f"/visits/{visit_id}/dates")
    ts = now()
    with conn:
        conn.execute("DELETE FROM visit_date_options WHERE visit_id = ?", (visit_id,))
        for d in picked:
            conn.execute("INSERT INTO visit_date_options (visit_id, date, created_by, created_at) VALUES (?, ?, ?, ?)",
                         (visit_id, d, user["id"], ts))
        conn.execute("UPDATE visits SET status = 'options', updated_at = ? WHERE id = ?", (ts, visit_id))
        audit(conn, house_id=v["house_id"], actor_id=user["id"], action="visit_dates", detail=", ".join(picked))
    flash(request, "날짜 {n}개를 회사에 보냈습니다. 회사가 고객과 통화해 하루를 확정합니다.", n=len(picked))
    return redirect("/houses")


@router.post("/visits/{visit_id}/reply")
async def visit_reply(request: Request, visit_id: int, user=Depends(current_user), conn=Depends(get_db)):
    """'가능한가요?'에 예 / 아니요."""
    v = visit_or_404(conn, visit_id, user)
    if v["installer_id"] != user["id"] or v["status"] != "proposed":
        raise Forbidden()
    form = await request.form()
    yes = form_str(form, "answer", 5) == "yes"
    with conn:
        conn.execute("UPDATE visits SET status = ?, updated_at = ? WHERE id = ?", ("confirmed" if yes else "declined", now(), visit_id))
        audit(conn, house_id=v["house_id"], actor_id=user["id"], action="visit_reply",
              detail=json.dumps({"date": v["date"], "answer": "yes" if yes else "no"}))
    flash(request, "가능하다고 답했습니다. 일정이 확정됐습니다." if yes else "불가로 답했습니다. 회사가 다른 날짜나 다른 설치업자를 정합니다.")
    back = form_str(form, "back", 200)
    return redirect(safe_next(back) if back else "/houses")


# ---------- 현장 도착 후 (p.11) ----------

def site_context(conn, v, user, lang):
    checkins = conn.execute("SELECT * FROM checkins WHERE visit_id = ? ORDER BY id", (v["id"],)).fetchall()
    photos = conn.execute("""SELECT * FROM media WHERE owner_type = 'visit' AND owner_id = ? AND deleted_at IS NULL ORDER BY id""",
                          (v["id"],)).fetchall()
    from .documents import latest_docs
    docs = latest_docs(conn, v["house_id"], user)
    variations = conn.execute("SELECT * FROM variations WHERE house_id = ? ORDER BY number DESC", (v["house_id"],)).fetchall()
    reports = conn.execute("""SELECT d.*, u.name AS author_name, r.name AS reviewer_name FROM day_reports d JOIN users u ON u.id = d.user_id
                              LEFT JOIN users r ON r.id = d.reviewed_by WHERE d.visit_id = ? ORDER BY d.id""", (v["id"],)).fetchall()
    from .attendance import visit_attendance, STATUS_LABELS, LATE_MIN
    return dict(reports=reports, report_media=media_for(conn, "report", [r["id"] for r in reports]),
                att=visit_attendance(conn, v) if is_office(user) else None, ATT_LABELS=STATUS_LABELS, LATE_MIN=LATE_MIN,
                work_labels=C.WORK_DONE, is_today=v["date"] == today().isoformat(), today_iso=today().isoformat(), visit=v, checkins=checkins, arrived=next((c for c in checkins if c["kind"] == "arrive"), None),
                finished=next((c for c in checkins if c["kind"] == "finish"), None), photos=photos,
                tags=rooms_for_tags(conn, v["house_id"], lang), new_docs=sum(1 for d in docs if not d["my_ack"]),
                variations=variations, C=C)


@router.get("/visits/{visit_id}")
def site_page(request: Request, visit_id: int, user=Depends(current_user), conn=Depends(get_db)):
    from .i18n import detect_lang
    v = visit_or_404(conn, visit_id, user)
    return render(request, "visit.html", user=user, **site_context(conn, v, user, detect_lang(request)))


def _checkin(conn, v, user, kind, form) -> None:
    def num(k):
        try:
            return float(form_str(form, k, 30))
        except ValueError:
            return None
    conn.execute("INSERT INTO checkins (visit_id, user_id, kind, at, lat, lng, accuracy) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (v["id"], user["id"], kind, now(), num("lat"), num("lng"), num("accuracy")))
    audit(conn, house_id=v["house_id"], actor_id=user["id"], action=kind, detail=v["kind"])


@router.post("/visits/{visit_id}/arrive")
async def site_arrive(request: Request, visit_id: int, user=Depends(current_user), conn=Depends(get_db)):
    v = visit_or_404(conn, visit_id, user)
    if v["installer_id"] != user["id"]:
        raise Forbidden()
    if v["status"] != "confirmed" or v["date"] != today().isoformat():
        flash(request, "현장 도착은 확정된 방문 당일에만 누를 수 있습니다.", "error")
        return redirect(f"/visits/{visit_id}")
    if not conn.execute("SELECT 1 FROM checkins WHERE visit_id = ? AND kind = 'arrive'", (visit_id,)).fetchone():
        form = await request.form()
        with conn:
            _checkin(conn, v, user, "arrive", form)
            if v["kind"] == "install":          # 시공 시작 → 프로젝트 단계도 맞춘다
                advance_stage(conn, v["house_id"], C.INSTALL_STAGE, user["id"])
    if v["kind"] == "survey":
        return redirect(f"/houses/{v['house_id']}/survey")
    return redirect(f"/visits/{visit_id}")


@router.post("/visits/{visit_id}/photos")
async def site_photos(request: Request, visit_id: int, user=Depends(current_user), conn=Depends(get_db)):
    v = visit_or_404(conn, visit_id, user)
    form = await request.form()
    tag = form_str(form, "tag", 80) or None
    try:
        saved = await gather_uploads(conn, form, v["house_id"])
    except UserError as e:
        flash(request, e.key, "error", **e.args_)
        return redirect(f"/visits/{visit_id}")
    try:
        with conn:
            add_media(conn, saved, v["house_id"], "visit", visit_id, user["id"])
            if saved:
                conn.execute(f"""UPDATE media SET tag = ?, visit_id = ? WHERE owner_type = 'visit' AND owner_id = ?
                                 AND stored_name IN ({",".join("?" * len(saved))})""",
                             (tag, visit_id, visit_id, *[s["stored_name"] for s in saved]))
            for mid, t in ((k[4:], form_str(form, k, 80)) for k in form.keys() if k.startswith("tag_")):   # 나중에 태그 고치기
                if mid.isdigit():
                    conn.execute("UPDATE media SET tag = ? WHERE id = ? AND owner_type = 'visit' AND owner_id = ?", (t or None, int(mid), visit_id))
    except Exception:
        remove_files(saved)
        raise
    if saved:
        flash(request, "사진 {n}장을 저장했습니다.", n=len(saved))
    return redirect(f"/visits/{visit_id}#photos")


# ---------- 오늘 작업 끝 (p.14) ----------

@router.get("/visits/{visit_id}/finish")
def finish_page(request: Request, visit_id: int, user=Depends(current_user), conn=Depends(get_db)):
    from .i18n import detect_lang
    v = visit_or_404(conn, visit_id, user)
    if v["status"] == "done":
        flash(request, "이 방문은 이미 작업 끝 보고를 보냈습니다.")
        return redirect(f"/visits/{visit_id}")
    return render(request, "visit_finish.html", user=user, **site_context(conn, v, user, detect_lang(request)))


@router.post("/visits/{visit_id}/finish")
async def finish_send(request: Request, visit_id: int, user=Depends(current_user), conn=Depends(get_db)):
    from .i18n import detect_lang
    v = visit_or_404(conn, visit_id, user)
    if v["installer_id"] != user["id"]:
        raise Forbidden()
    if v["status"] == "done":
        flash(request, "이 방문은 이미 작업 끝 보고를 보냈습니다.")
        return redirect(f"/visits/{visit_id}")
    form = await request.form()
    work = [w for w in form.getlist("work") if w in C.WORK_DONE]
    problem = form_str(form, "problem", 5) == "yes"
    note = form_str(form, "note", 4000)
    try:
        saved = await gather_uploads(conn, form, v["house_id"])
    except UserError as e:
        flash(request, e.key, "error", **e.args_)
        return redirect(f"/visits/{visit_id}/finish")
    error = None
    if not work and v["kind"] == "install":
        error = "오늘 한 작업을 하나 이상 고르세요."
    elif problem and (not note or not saved):
        error = "문제가 있으면 사진과 메모가 꼭 필요합니다."
    if error:
        remove_files(saved)
        ctx = site_context(conn, v, user, detect_lang(request))
        return render(request, "visit_finish.html", user=user, error=error, form={"work": work, "problem": form_str(form, "problem", 5), "note": note},
                      status_code=400, **ctx)
    try:
        with conn:
            rid = conn.insert("""INSERT INTO day_reports (visit_id, house_id, user_id, work, has_problem, note, created_at)
                                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
                              (visit_id, v["house_id"], user["id"], json.dumps(work), 1 if problem else 0, note, now()))
            add_media(conn, saved, v["house_id"], "report", rid, user["id"])
            _checkin(conn, v, user, "finish", form)
            if v["kind"] == "install":          # 도착을 안 누르고 보고만 보낸 경우에도 단계를 맞춘다
                advance_stage(conn, v["house_id"], C.INSTALL_STAGE, user["id"])
            conn.execute("UPDATE visits SET status = 'done', updated_at = ? WHERE id = ?", (now(), visit_id))
    except Exception:
        remove_files(saved)
        raise
    flash(request, "작업 끝 보고를 보냈습니다. 회사가 확인한 뒤 고객에게 전달됩니다.")
    return redirect("/houses")


# ---------- 문제 / 회사에 메시지 ----------

@router.post("/houses/{house_id}/messages")
async def message_send(request: Request, house_id: int, user=Depends(current_user), conn=Depends(get_db)):
    get_house_or_404(conn, house_id, user)
    form = await request.form()
    kind = form_str(form, "kind", 20)
    if kind not in ("problem", "message"):
        raise HTTPException(400)
    body = form_str(form, "body", 4000)
    vid = form_str(form, "visit_id", 12)
    back = f"/visits/{vid}" if vid.isdigit() else f"/houses/{house_id}"
    if not body:
        flash(request, "내용을 입력하세요.", "error")
        return redirect(back)
    try:
        saved = await gather_uploads(conn, form, house_id)
    except UserError as e:
        flash(request, e.key, "error", **e.args_)
        return redirect(back)
    try:
        with conn:
            mid = conn.insert("""INSERT INTO messages (house_id, kind, visit_id, user_id, body, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
                              (house_id, kind, int(vid) if vid.isdigit() else None, user["id"], body, now()))
            add_media(conn, saved, house_id, "message", mid, user["id"])
            audit(conn, house_id=house_id, actor_id=user["id"], action=kind, detail=body[:60])
    except Exception:
        remove_files(saved)
        raise
    flash(request, "문제를 회사에 보고했습니다." if kind == "problem" else "회사에 메시지를 보냈습니다.")
    return redirect(back)


# ---------- 변경 요청 (p.15) ----------

@router.get("/houses/{house_id}/variations")
def variations_page(request: Request, house_id: int, visit: int = 0, user=Depends(current_user), conn=Depends(get_db)):
    house = get_house_or_404(conn, house_id, user)
    rows = conn.execute("""SELECT x.*, u.name AS author_name, d.name AS decider_name FROM variations x JOIN users u ON u.id = x.created_by
                           LEFT JOIN users d ON d.id = x.decided_by WHERE x.house_id = ? ORDER BY x.number DESC""", (house_id,)).fetchall()
    return render(request, "variations.html", user=user, house=house, variations=rows, visit_id=visit, C=C,
                  photos=media_for(conn, "variation", [r["id"] for r in rows]))


@router.post("/houses/{house_id}/variations")
async def variation_create(request: Request, house_id: int, user=Depends(current_user), conn=Depends(get_db)):
    get_house_or_404(conn, house_id, user)
    form = await request.form()
    kind = form_str(form, "kind", 20)
    body = form_str(form, "body", 4000)
    vid = form_str(form, "visit_id", 12)
    if kind not in C.VARIATION_KINDS or not body:
        flash(request, "변경 종류를 고르고 내용을 적어 주세요.", "error")
        return redirect(f"/houses/{house_id}/variations")
    try:
        saved = await gather_uploads(conn, form, house_id)
    except UserError as e:
        flash(request, e.key, "error", **e.args_)
        return redirect(f"/houses/{house_id}/variations")
    try:
        with conn:
            n = (conn.execute("SELECT MAX(number) AS n FROM variations WHERE house_id = ?", (house_id,)).fetchone()["n"] or 0) + 1
            xid = conn.insert("""INSERT INTO variations (house_id, number, visit_id, kind, body, status, created_by, created_at)
                                 VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                              (house_id, n, int(vid) if vid.isdigit() else None, kind, body, user["id"], now()))
            if is_office(user):    # 회사가 직접 남긴 요청은 '확인할 일'에 올리지 않는다 (고객 승인 결과만 표시하면 됨)
                conn.execute("UPDATE variations SET reviewed_by = ?, reviewed_at = ? WHERE id = ?", (user["id"], now(), xid))
            add_media(conn, saved, house_id, "variation", xid, user["id"])
            audit(conn, house_id=house_id, actor_id=user["id"], action="variation_create", detail=f"VO-{n}")
    except Exception:
        remove_files(saved)
        raise
    flash(request, "VO-{n} 변경 요청을 기록했습니다. 고객 승인 결과를 표시하세요." if is_office(user) else
          "VO-{n} 변경 요청을 회사로 보냈습니다. 고객 승인 전에는 이 부분을 시공하지 마세요.", n=n)
    return redirect(f"/houses/{house_id}/variations")


@router.post("/variations/{var_id}/decide")
async def variation_decide(request: Request, var_id: int, user=Depends(office_user), conn=Depends(get_db)):
    """고객 승인 결과를 회사가 표시 (v1: 고객 화면 없이 회사가 직접). 금액은 앱 밖(견적 기록)에서 다룬다."""
    x = conn.execute("SELECT * FROM variations WHERE id = ?", (var_id,)).fetchone()
    if not x:
        raise HTTPException(404)
    get_house_or_404(conn, x["house_id"], user)
    form = await request.form()
    status = form_str(form, "status", 20)
    if status not in ("approved", "rejected", "pending"):
        raise HTTPException(400)
    with conn:
        conn.execute("""UPDATE variations SET status = ?, decided_by = ?, decided_at = ?, decision_note = ?,
                        reviewed_by = COALESCE(reviewed_by, ?), reviewed_at = COALESCE(reviewed_at, ?) WHERE id = ?""",
                     (status, user["id"], now(), form_str(form, "note", 500) or None, user["id"], now(), var_id))
        audit(conn, house_id=x["house_id"], actor_id=user["id"], action="variation_decide", detail=f"VO-{x['number']}:{status}")
    flash(request, "VO-{n}: {status}", n=x["number"], status=C.VARIATION_STATUS[status])
    back = form_str(form, "back", 200)
    return redirect(safe_next(back) if back else f"/houses/{x['house_id']}/variations")
