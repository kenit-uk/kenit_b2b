"""설치업자 출결: 예약한 시각·현장 위치와 실제 '현장 도착'·'오늘 작업 끝' 기록을 비교한다.

- 시각은 서버가 받은 시각이라 설치업자가 고칠 수 없다.
- 현장 좌표는 영국 우편번호로 찾는다 (postcodes.io, 무료 공공 서비스). 못 찾으면 거리는 '알 수 없음'.
- 지각: 예약 시작보다 LATE_MIN분 넘게 늦은 도착. 위치 이상: 현장에서 FAR_M 미터 넘게 떨어진 곳에서 도착을 누름.
- 노쇼: 예약일이 지나도 도착 기록이 없음. 오늘은 시작 NOT_ARRIVED_MIN분이 지나도 없으면 '도착 기록 없음'.
"""
import json
import math
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException

from . import constants as C
from .auth import office_user
from .db import get_db
from .main import LONDON, render

router = APIRouter()

LATE_MIN = 15
FAR_M = 500
NOT_ARRIVED_MIN = 60
POSTCODE_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$")


def lookup_postcode(postcode: str):
    """영국 우편번호 → (위도, 경도). 실패하면 None (네트워크 문제로 화면이 느려지지 않게 짧게 기다린다)."""
    pc = (postcode or "").upper().strip()
    if not POSTCODE_RE.match(pc) or os.environ.get("GEOCODE_OFF"):
        return None
    try:
        url = "https://api.postcodes.io/postcodes/" + urllib.request.quote(pc.replace(" ", ""))
        with urllib.request.urlopen(url, timeout=3) as r:
            res = json.load(r).get("result") or {}
        return (res["latitude"], res["longitude"]) if res.get("latitude") is not None else None
    except Exception:
        return None


def site_coords(conn, house):
    """현장 좌표 (한 번 찾으면 저장. 우편번호가 바뀌면 다시 찾는다)."""
    pc = (house["postcode"] or "").upper().strip()
    if house["lat"] is not None and house["geo_postcode"] == pc:
        return house["lat"], house["lng"]
    if house["geo_postcode"] == pc and house["lat"] is None and pc:
        return None                         # 이미 찾아봤는데 없던 우편번호
    found = lookup_postcode(pc)
    with conn:
        conn.execute("UPDATE houses SET lat = ?, lng = ?, geo_postcode = ? WHERE id = ?",
                     (found[0] if found else None, found[1] if found else None, pc, house["id"]))
    return found


def distance_m(a, b) -> int:
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return int(6371000 * 2 * math.asin(math.sqrt(h)))


def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(LONDON)


def visit_attendance(conn, v, house=None, now=None) -> dict:
    """방문 하나의 출결."""
    now = now or datetime.now(LONDON)
    cks = {c["kind"]: c for c in conn.execute("SELECT * FROM checkins WHERE visit_id = ? ORDER BY id", (v["id"],))}
    arrive, finish = cks.get("arrive"), cks.get("finish")
    start = None
    if v["date"]:
        hh, mm = (v["start_time"] or "08:00").split(":")
        start = datetime.fromisoformat(v["date"]).replace(hour=int(hh), minute=int(mm), tzinfo=LONDON)
    out = {"start": start, "arrive": arrive, "finish": finish, "arrived_at": None, "finished_at": None,
           "late_min": None, "distance": None, "loc": None, "worked_min": None, "status": None}
    if arrive:
        out["arrived_at"] = _local(arrive["at"])
        if start:
            out["late_min"] = int((out["arrived_at"] - start).total_seconds() // 60)
        if arrive["lat"] is None:
            out["loc"] = "none"
        else:
            house = house or conn.execute("SELECT * FROM houses WHERE id = ?", (v["house_id"],)).fetchone()
            site = site_coords(conn, house)
            if site:
                out["distance"] = distance_m(site, (arrive["lat"], arrive["lng"]))
                out["loc"] = "far" if out["distance"] > FAR_M else "ok"
            else:
                out["loc"] = "unknown"
    if finish:
        out["finished_at"] = _local(finish["at"])
        if out["arrived_at"]:
            out["worked_min"] = int((out["finished_at"] - out["arrived_at"]).total_seconds() // 60)
    # 상태 한 가지
    if v["status"] == "cancelled":
        out["status"] = "cancelled"
    elif arrive:
        out["status"] = "late" if (out["late_min"] or 0) > LATE_MIN else "on_time"
    elif finish or v["status"] == "done":          # 다녀왔지만(보고·조사 제출) 도착을 안 누름
        out["status"] = "no_arrival"
    elif start and v["status"] == "confirmed":
        if start.date() < now.date():
            out["status"] = "no_show"
        elif now > start + timedelta(minutes=NOT_ARRIVED_MIN):
            out["status"] = "not_arrived"
        else:
            out["status"] = "upcoming"
    return out


STATUS_LABELS = {"on_time": "정시 도착", "late": "지각", "no_show": "노쇼", "not_arrived": "도착 기록 없음", "no_arrival": "도착 기록 빠짐",
                 "upcoming": "예정", "cancelled": "취소됨"}


def installer_summary(conn, user_id: int, days: int = 30) -> dict:
    """최근 N일 출결 요약 (지난 방문만 센다)."""
    since = (datetime.now(LONDON).date() - timedelta(days=days)).isoformat()
    today = datetime.now(LONDON).date().isoformat()
    rows = conn.execute("""SELECT v.* FROM visits v WHERE v.installer_id = ? AND v.date >= ? AND v.date <= ?
                           AND v.status IN ('confirmed', 'done')""", (user_id, since, today)).fetchall()
    s = {"visits": 0, "on_time": 0, "late": 0, "no_show": 0, "not_arrived": 0, "no_arrival": 0, "far": 0, "no_loc": 0, "late_total": 0}
    for v in rows:
        a = visit_attendance(conn, v)
        if a["status"] == "upcoming":
            continue
        s["visits"] += 1
        s[a["status"]] = s.get(a["status"], 0) + 1
        if a["status"] == "late":
            s["late_total"] += a["late_min"]
        s["far"] += a["loc"] == "far"
        s["no_loc"] += a["loc"] == "none"
    s["late_avg"] = s["late_total"] // s["late"] if s["late"] else 0
    s["problems"] = s["late"] + s["no_show"] + s["far"] + s["no_loc"] + s["no_arrival"]
    return s


def overdue_visits(conn) -> list:
    """오늘 시작 시각이 한참 지났는데 도착 기록이 없는 방문 (홈 '지금 처리할 것')."""
    today = datetime.now(LONDON).date().isoformat()
    rows = conn.execute("""SELECT v.*, h.address, u.name AS installer_name FROM visits v JOIN houses h ON h.id = v.house_id
                           LEFT JOIN users u ON u.id = v.installer_id
                           WHERE v.date = ? AND v.status = 'confirmed'
                             AND NOT EXISTS (SELECT 1 FROM checkins c WHERE c.visit_id = v.id AND c.kind = 'arrive')""",
                        (today,)).fetchall()
    return [v for v in rows if visit_attendance(conn, v)["status"] == "not_arrived"]


@router.get("/users/{user_id}/attendance")
def attendance_page(request: Request, user_id: int, days: int = 30, user=Depends(office_user), conn=Depends(get_db)):
    target = conn.execute("SELECT * FROM users WHERE id = ? AND role = 'installer'", (user_id,)).fetchone()
    if not target:
        raise HTTPException(404)
    days = max(7, min(days, 365))
    since = (datetime.now(LONDON).date() - timedelta(days=days)).isoformat()
    visits = conn.execute("""SELECT v.*, h.address, h.postcode, h.ref_no FROM visits v JOIN houses h ON h.id = v.house_id
                             WHERE v.installer_id = ? AND v.date >= ? AND v.status IN ('confirmed', 'done')
                             ORDER BY v.date DESC, v.start_time DESC""", (user_id, since)).fetchall()
    rows = [(v, visit_attendance(conn, v)) for v in visits]
    return render(request, "attendance.html", user=user, target=target, rows=rows, days=days, C=C,
                  summary=installer_summary(conn, user_id, days), STATUS_LABELS=STATUS_LABELS, LATE_MIN=LATE_MIN, FAR_M=FAR_M)
