"""계정 복구·관리.

- 비밀번호를 잊었을 때: 등록된 휴대폰으로 문자 코드를 받아 새 비밀번호를 정한다.
  (이메일 발송 서비스가 없으므로) 휴대폰이 없으면 관리자·매니저가 만든 1회용 재설정 링크(24시간)를 쓴다.
- 관리자·매니저는 자기가 관리할 수 있는 사람의 휴대폰 번호를 바꾸거나 지울 수 있다.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException

from . import constants as C
from . import sms
from .auth import Forbidden, hash_password, office_user
from .db import IntegrityError, get_db, now
from .main import detect_lang, flash, form_str, redirect, render, translate

router = APIRouter()
RESET_TTL = timedelta(hours=24)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _password_error(p1: str, p2: str) -> str | None:
    if len(p1) < 8:
        return "비밀번호는 8자 이상이어야 합니다."
    if p1 != p2:
        return "비밀번호 확인이 일치하지 않습니다."
    return None


# ---------- 비밀번호 찾기 (문자 코드) ----------

@router.get("/forgot")
def forgot_page(request: Request):
    return render(request, "forgot.html", step="email")


@router.post("/forgot")
async def forgot_send(request: Request, conn=Depends(get_db)):
    email = form_str(await request.form(), "email", 200).lower()
    user = conn.execute("SELECT * FROM users WHERE email = ? AND active = 1", (email,)).fetchone() if email else None
    # 가입 여부를 알 수 없도록 화면은 항상 같게 보여준다
    if user and user["phone_e164"] and sms.SMS_ENABLED:
        text = translate(detect_lang(request), "{brand} 비밀번호 재설정 코드: {{code}} (10분간 유효)", brand=C.BRAND_SHORT)
        sms.request_code(conn, user["phone_e164"], "reset", f"{text}\n\n@{request.url.hostname} #{{code}}", user_id=user["id"])
        request.session["reset_user"] = user["id"]
    else:
        request.session.pop("reset_user", None)
    request.session["reset_email"] = email
    return render(request, "forgot.html", step="code", email=email)


@router.post("/forgot/verify")
async def forgot_verify(request: Request, conn=Depends(get_db)):
    form = await request.form()
    uid = request.session.get("reset_user")
    user = conn.execute("SELECT * FROM users WHERE id = ? AND active = 1", (uid,)).fetchone() if uid else None
    error = _password_error(form.get("password") or "", form.get("password2") or "")
    if not error:
        error = "코드가 만료되었습니다. 코드를 다시 받으세요." if not user else \
            sms.check_code(conn, user["phone_e164"], "reset", form_str(form, "code", 20), user_id=user["id"])
    if error:
        return render(request, "forgot.html", step="code", email=request.session.get("reset_email", ""), error=error, status_code=400)
    with conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(form.get("password")), user["id"]))
    request.session.pop("reset_user", None)
    flash(request, "비밀번호를 바꿨습니다. 새 비밀번호로 로그인하세요.")
    return redirect("/login")


# ---------- 1회용 재설정 링크 (관리자·매니저가 만듦) ----------

def _managed_target(conn, actor, user_id: int):
    t = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not t:
        raise HTTPException(404)
    if t["role"] not in C.MANAGEABLE[actor["role"]] or t["id"] == actor["id"]:
        raise Forbidden()
    return t


@router.post("/users/{user_id}/reset-link")
def reset_link_create(request: Request, user_id: int, user=Depends(office_user), conn=Depends(get_db)):
    target = _managed_target(conn, user, user_id)
    token = secrets.token_urlsafe(24)
    ts = datetime.now(timezone.utc)
    with conn:
        conn.execute("UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL", (now(), user_id))  # 이전 링크 무효
        conn.execute("""INSERT INTO password_resets (user_id, token_hash, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?)""",
                     (user_id, _token_hash(token), user["id"], ts.isoformat(timespec="seconds"),
                      (ts + RESET_TTL).isoformat(timespec="seconds")))
    link = f"{request.url.scheme}://{request.url.netloc}/reset/{token}"
    request.session["reset_link"] = {"name": target["name"], "link": link}   # 한 번만 보여준다
    return redirect("/users#reset-link")


def _valid_reset(conn, token: str):
    r = conn.execute("""SELECT p.*, u.name, u.active FROM password_resets p JOIN users u ON u.id = p.user_id
                        WHERE p.token_hash = ?""", (_token_hash(token),)).fetchone()
    if not r or r["used_at"] or r["expires_at"] < now() or not r["active"]:
        return None
    return r


@router.get("/reset/{token}")
def reset_page(request: Request, token: str, conn=Depends(get_db)):
    r = _valid_reset(conn, token)
    return render(request, "reset.html", token=token, valid=bool(r), person=r["name"] if r else "", status_code=200 if r else 410)


@router.post("/reset/{token}")
async def reset_save(request: Request, token: str, conn=Depends(get_db)):
    r = _valid_reset(conn, token)
    if not r:
        return render(request, "reset.html", token=token, valid=False, status_code=410)
    form = await request.form()
    error = _password_error(form.get("password") or "", form.get("password2") or "")
    if error:
        return render(request, "reset.html", token=token, valid=True, person=r["name"], error=error, status_code=400)
    with conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(form.get("password")), r["user_id"]))
        conn.execute("UPDATE password_resets SET used_at = ? WHERE id = ?", (now(), r["id"]))
    flash(request, "비밀번호를 정했습니다. 로그인하세요.")
    return redirect("/login")


# ---------- 관리자·매니저: 휴대폰 번호 바꾸기 ----------

@router.post("/users/{user_id}/phone")
async def user_phone(request: Request, user_id: int, user=Depends(office_user), conn=Depends(get_db)):
    target = _managed_target(conn, user, user_id)
    raw = form_str(await request.form(), "phone", 40)
    if not raw:
        phone = None
    else:
        phone = sms.normalize_phone(raw)
        if not phone:
            flash(request, "휴대폰 번호를 확인하세요. 영국 휴대폰은 07…, 한국 휴대폰은 010…, 다른 나라는 +국가번호로 입력하세요.", "error")
            return redirect("/users")
    try:
        with conn:
            conn.execute("UPDATE users SET phone_e164 = ?, phone = ? WHERE id = ?",
                         (phone, sms.format_phone(phone) if phone else None, user_id))
    except IntegrityError:
        flash(request, "이미 다른 계정에 등록된 번호입니다.", "error")
        return redirect("/users")
    flash(request, "{name}님의 휴대폰 번호를 바꿨습니다." if phone else "{name}님의 휴대폰 번호를 지웠습니다.", name=target["name"])
    return redirect("/users")
