"""정산·결제 관리 (시안 Payments): 프로젝트마다 계약금·중도금·잔금을 회사가 손으로 표시한다.

결제 관문(회사 설정)을 켜면 그 결제를 받아야 다음으로 갈 수 있다.
  계약금 → 첫 시공 예약, 중도금 → 2차 시공부터 예약, 잔금 → 프로젝트 완료.
가격·결제는 회사 전용이라 설치업자에게는 보이지 않는다.
"""
import json
import re
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException

from . import constants as C
from .auth import admin_user, office_user
from .db import audit, get_db, get_setting, now, set_setting
from .main import flash, form_str, get_house_or_404, redirect, render, safe_next

router = APIRouter()
GATES_KEY = "payment_gates"


def gates(conn) -> dict:
    """켜진 관문 (처음에는 모두 꺼짐 — 회사가 정하면 켠다)."""
    raw = get_setting(conn, GATES_KEY)
    saved = json.loads(raw) if raw else {}
    return {k: bool(saved.get(k, False)) for k in C.PAYMENT_GATES}


def house_payments(conn, house_id: int) -> dict:
    rows = {r["kind"]: r for r in conn.execute("SELECT * FROM payments WHERE house_id = ?", (house_id,))}
    return {k: rows.get(k) for k in C.PAYMENT_KINDS}


def is_received(conn, house_id: int, kind: str) -> bool:
    r = conn.execute("SELECT status FROM payments WHERE house_id = ? AND kind = ?", (house_id, kind)).fetchone()
    return bool(r) and r["status"] == "received"


def gate_block(conn, house_id: int, gate: str) -> str | None:
    """관문이 켜져 있고 아직 못 받았으면 안내 문구 (번역 키)."""
    if not gates(conn).get(gate):
        return None
    kind = C.PAYMENT_GATES[gate][1]
    if is_received(conn, house_id, kind):
        return None
    return {"deposit": "계약금을 받기 전이라 시공을 잡을 수 없습니다. 정산·결제 관리에서 '받음'으로 표시하세요.",
            "interim": "중도금을 받기 전이라 다음 시공(2차부터)을 잡을 수 없습니다. 정산·결제 관리에서 '받음'으로 표시하세요.",
            "balance": "잔금을 받기 전이라 프로젝트를 완료할 수 없습니다. 정산·결제 관리에서 '받음'으로 표시하세요."}[kind]


def blocked_now(conn, house) -> str | None:
    """지금 단계에서 결제 때문에 막힌 것 (표의 '지금 막힌 것' 칸)."""
    g = gates(conn)
    st = house["stage"]
    if g["deposit_before_install"] and st <= C.PREP_STAGE and not is_received(conn, house["id"], "deposit"):
        return "시공 예약 막힘"
    if g["interim_before_next_install"] and st == C.INSTALL_STAGE and not is_received(conn, house["id"], "interim"):
        return "다음 시공 예약 막힘"
    if g["balance_before_complete"] and st >= C.HANDOVER_STAGE and not is_received(conn, house["id"], "balance"):
        return "완료 막힘"
    return None


@router.get("/payments")
def payments_page(request: Request, user=Depends(office_user), conn=Depends(get_db)):
    houses = conn.execute("""SELECT h.* FROM houses h WHERE h.stage < 6 OR EXISTS
                             (SELECT 1 FROM payments p WHERE p.house_id = h.id AND p.kind = 'balance' AND p.status != 'received')
                             ORDER BY h.stage DESC, h.updated_at DESC""").fetchall()
    return render(request, "payments.html", user=user, houses=houses, C=C, gates=gates(conn),
                  sums={h["id"]: summary(conn, h) for h in houses},
                  blocked={h["id"]: blocked_now(conn, h) for h in houses}, today=date.today().isoformat())


def money(v):
    """'£1,200.50' → Decimal('1200.50'). 비었으면 None, 숫자가 아니면 ValueError."""
    v = (v or "").replace("£", "").replace(",", "").strip()
    if not v:
        return None
    if not re.fullmatch(r"\d+(\.\d{1,2})?", v):
        raise ValueError(v)
    return Decimal(v)


def summary(conn, house) -> dict:
    """총액·결제별 금액·받은 합계·남은 금액 (잔금은 총액 − 계약금 − 중도금)."""
    pays = house_payments(conn, house["id"])
    amt = {k: (money(p["amount"]) if p and p["amount"] else None) for k, p in pays.items()}
    total = money(house["contract_total"]) if house["contract_total"] else None
    received = sum((amt[k] or 0) for k, p in pays.items() if p and p["status"] == "received")
    return {"total": total, "amount": amt, "pays": pays, "received": received,
            "remaining": (total - received) if total is not None else None}


@router.post("/houses/{house_id}/payments")
async def payment_save(request: Request, house_id: int, user=Depends(office_user), conn=Depends(get_db)):
    """한 번에 저장: 총 계약 금액, 계약금·중도금 금액(잔금은 자동), 세 결제의 상태·날짜."""
    house = get_house_or_404(conn, house_id, user)
    form = await request.form()
    back = form_str(form, "back", 200)
    go_back = lambda: redirect(safe_next(back) if back else "/payments")
    try:
        total, deposit, interim = (money(form_str(form, k, 30)) for k in ("total", "deposit_amount", "interim_amount"))
    except ValueError:
        flash(request, "금액은 숫자로 적어 주세요 (예: 1,200).", "error")
        return go_back()
    statuses = {k: form_str(form, f"{k}_status", 20) or "not_due" for k in C.PAYMENT_KINDS}
    if any(v not in C.PAYMENT_STATUS for v in statuses.values()):
        raise HTTPException(400)
    if total is None and (deposit or interim or any(v != "not_due" for v in statuses.values())):
        flash(request, "먼저 총 계약 금액을 적어 주세요.", "error")
        return go_back()
    deposit, interim = deposit or Decimal(0), interim or Decimal(0)
    if total is not None and deposit + interim > total:
        flash(request, "계약금과 중도금을 더한 금액이 총 계약 금액보다 큽니다.", "error")
        return go_back()
    amounts = {"deposit": deposit, "interim": interim, "balance": (total - deposit - interim) if total is not None else Decimal(0)}
    for k, st in statuses.items():
        if st != "not_due" and not amounts[k]:
            flash(request, "{what} 금액이 없어서 청구함·받음으로 할 수 없습니다.", "error", what=C.PAYMENT_KINDS[k])
            return go_back()

    def day(k):
        v = form_str(form, k, 10)
        try:
            return date.fromisoformat(v).isoformat() if v else None
        except ValueError:
            return None
    old = house_payments(conn, house_id)
    ts, today_s = now(), date.today().isoformat()
    with conn:
        if (house["contract_total"] or None) != (str(total) if total is not None else None):
            conn.execute("UPDATE houses SET contract_total = ? WHERE id = ?", (str(total) if total is not None else None, house_id))
            audit(conn, house_id=house_id, actor_id=user["id"], action="payment_total", detail=str(total) if total is not None else "")
        for k, st in statuses.items():
            invoiced = day(f"{k}_invoiced_on") or (today_s if st in ("invoiced", "received") else None)
            received = (day(f"{k}_received_on") or today_s) if st == "received" else None
            amount = str(amounts[k]) if amounts[k] else None
            if old[k]:
                conn.execute("""UPDATE payments SET amount = ?, status = ?, invoiced_on = ?, received_on = ?, updated_by = ?, updated_at = ?
                                WHERE house_id = ? AND kind = ?""", (amount, st, invoiced, received, user["id"], ts, house_id, k))
            else:
                conn.execute("""INSERT INTO payments (house_id, kind, amount, status, invoiced_on, received_on, updated_by, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (house_id, k, amount, st, invoiced, received, user["id"], ts))
            if (old[k]["status"] if old[k] else "not_due") != st:
                audit(conn, house_id=house_id, actor_id=user["id"], action="payment", detail=f"{k}:{st}")
    flash(request, "결제 정보를 저장했습니다.")
    return go_back()


@router.post("/payments/gates")
async def gates_save(request: Request, user=Depends(admin_user), conn=Depends(get_db)):
    form = await request.form()
    with conn:
        set_setting(conn, GATES_KEY, json.dumps({k: form.get(k) == "1" for k in C.PAYMENT_GATES}))
    flash(request, "결제 관문 설정을 저장했습니다.")
    return redirect("/payments#gates")
