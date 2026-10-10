import hmac
import json
import mimetypes
import re
import secrets
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import constants as C
from .auth import Forbidden, LoginRequired, admin_user, current_user, hash_password, office_user, verify_password
from .config import COOKIE_SECURE, DATABASE_URL, MAX_UPLOAD_BYTES, ON_VERCEL, SECRET_KEY
from .db import IntegrityError, audit, connect, ensure_db, get_db, get_setting, now, set_setting
from .storage import storage
from .i18n import LANGS, detect_lang, fmt_datetime, translate, translate_message

APP_DIR = Path(__file__).resolve().parent
# 서버리스에서는 서버 디스크에 저장한 사진이 사라지므로, 사진 저장소(R2)가 연결되어야만 첨부를 받는다
UPLOADS_ENABLED = storage.direct or not ON_VERCEL
LONDON = ZoneInfo(C.TIMEZONE)

# 업로드 허용 형식. SVG/HTML 등 스크립트가 들어갈 수 있는 형식은 받지 않는다.
ALLOWED_UPLOADS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "application/pdf": ".pdf",
}

@asynccontextmanager
async def lifespan(_app):
    ensure_db()
    yield


app = FastAPI(title=C.BRAND, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="aircon_session",
    max_age=60 * 60 * 24 * 30,
    same_site="lax",
    https_only=COOKIE_SECURE,
)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


# ---------- 템플릿 헬퍼 ----------

class UserError(Exception):
    """사용자에게 보여줄 오류. 메시지는 번역 키 + 인자."""

    def __init__(self, key: str, **args):
        super().__init__(key)
        self.key, self.args_ = key, args


@pass_context
def fmt_dt(ctx, value: str | None) -> str:
    if not value:
        return ""
    return fmt_datetime(ctx.get("lang", "ko"), datetime.fromisoformat(value).astimezone(LONDON))


@pass_context
def fmt_local(ctx, value: str | None) -> str:
    """datetime-local 입력값(런던 현지시각)을 보기 좋게."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    return fmt_datetime(ctx.get("lang", "ko"), dt)


def fmt_size(n: int) -> str:
    return f"{n / 1024 / 1024:.1f}MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)}KB"


templates.env.filters["dt"] = fmt_dt
templates.env.filters["localdt"] = fmt_local
templates.env.filters["size"] = fmt_size
templates.env.filters["fromjson"] = lambda s: json.loads(s) if s else {}
templates.env.globals.update(
    BRAND=C.BRAND,
    STATUSES=C.STATUSES,
    ROLES=C.ROLES,
    ENTRY_TYPES=C.ENTRY_TYPES,
    PROPERTY_TYPES=C.PROPERTY_TYPES,
    CONVERSATION_CHANNELS=C.CONVERSATION_CHANNELS,
    AUDIT_ACTIONS=C.AUDIT_ACTIONS,
    HOUSE_LABELS=C.HOUSE_LABELS,
    DIRECT_UPLOAD=storage.direct,
    # 서버리스에서 DB가 연결되지 않으면 데이터가 사라지므로 화면에 경고를 띄운다
    EPHEMERAL=ON_VERCEL and not DATABASE_URL,
    UPLOADS_ENABLED=UPLOADS_ENABLED,
)


def render(request: Request, name: str, user=None, status_code: int = 200, **ctx):
    lang = detect_lang(request)

    def _(text, **kwargs):
        return translate(lang, text, **kwargs)

    flashes = [{"kind": f["kind"], "message": translate_message(lang, f["key"], f.get("args", {}))}
               for f in request.session.pop("flash", [])]
    if isinstance(ctx.get("error"), UserError):
        ctx["error"] = translate_message(lang, ctx["error"].key, ctx["error"].args_)
    elif ctx.get("error"):
        ctx["error"] = _(ctx["error"])
    if ctx.get("message"):
        ctx["message"] = _(ctx["message"])
    here = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    if user is not None and "unread_total" not in ctx:
        db = getattr(request.state, "db", None)
        if db is not None:
            ctx["unread_total"] = unread_count(db, user)
        else:
            db = connect()
            try:
                ctx["unread_total"] = unread_count(db, user)
            finally:
                db.close()
    return templates.TemplateResponse(
        request, name,
        {"user": user, "flashes": flashes, "lang": lang, "_": _, "LANGS": LANGS, "here": here,
         "is_admin": bool(user) and user["role"] in C.ADMIN_ROLES,
         "is_office": bool(user) and user["role"] in C.OFFICE_ROLES, **ctx},
        status_code=status_code,
    )


def flash(request: Request, key: str, kind: str = "ok", **args) -> None:
    """메시지는 번역 키로 저장했다가 보여줄 때 그 사람의 언어로 번역한다."""
    request.session.setdefault("flash", []).append({"kind": kind, "key": key, "args": args})


def safe_next(url: str) -> str:
    return url if url.startswith("/") and not url.startswith("//") else "/houses"


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    return redirect(f"/login?next={request.url.path}")


@app.exception_handler(Forbidden)
async def _forbidden(request: Request, exc: Forbidden):
    return render(request, "error.html", message="권한이 없습니다.", status_code=403)


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    message = {404: "페이지를 찾을 수 없습니다."}.get(exc.status_code, exc.detail or "오류가 발생했습니다.")
    return render(request, "error.html", message=message, status_code=exc.status_code)


# ---------- 공통 조회/권한 ----------

def readable_types(user) -> list[str]:
    return [t for t, rule in C.ENTRY_RULES.items() if user["role"] in rule["read"]]


def can_write(user, entry_type: str) -> bool:
    return user["role"] in C.ENTRY_RULES[entry_type]["write"]


def can_edit(user, entry) -> bool:
    if entry["author_id"] == user["id"]:
        return True
    # 다른 사람이 쓴 견적·집주인 대화는 관리자만 고칠 수 있다 (매니저는 본인 것만)
    return user["role"] in C.ADMIN_ROLES and entry["type"] in ("quote", "conversation")


def unread_condition(user) -> tuple[str, list]:
    """현장 h 에 이 사용자가 아직 확인하지 않은 활동(본인 작업 제외, 볼 수 있는 기록만)이 있는지 판단하는 SQL."""
    types = readable_types(user)
    sql = f"""EXISTS (
        SELECT 1 FROM audit_log l LEFT JOIN entries e ON e.id = l.entry_id
        WHERE l.house_id = h.id AND l.actor_id != ?
          AND l.id > COALESCE((SELECT last_seen_id FROM house_views WHERE user_id = ? AND house_id = h.id), ?)
          AND (l.entry_id IS NULL OR e.type IN ({",".join("?" * len(types))})))"""
    return sql, [user["id"], user["id"], user["seen_baseline"], *types]


def unread_count(conn, user) -> int:
    sql, params = unread_condition(user)
    return conn.execute(f"SELECT COUNT(*) AS n FROM houses h WHERE {sql}", params).fetchone()["n"]


def get_house_or_404(conn, house_id: int):
    house = conn.execute(
        """SELECT h.*, cu.name AS creator_name, uu.name AS updater_name
           FROM houses h
           JOIN users cu ON cu.id = h.created_by
           LEFT JOIN users uu ON uu.id = h.updated_by
           WHERE h.id = ?""",
        (house_id,),
    ).fetchone()
    if not house:
        raise HTTPException(404)
    return house


def get_entry_or_404(conn, entry_id: int, user):
    entry = conn.execute(
        """SELECT e.*, a.name AS author_name, a.role AS author_role, u.name AS updater_name
           FROM entries e
           JOIN users a ON a.id = e.author_id
           LEFT JOIN users u ON u.id = e.updated_by
           WHERE e.id = ?""",
        (entry_id,),
    ).fetchone()
    if not entry or entry["type"] not in readable_types(user):
        raise HTTPException(404)
    return entry


def form_str(form, key: str, limit: int = 5000) -> str:
    value = form.get(key) or ""
    return value.strip()[:limit] if isinstance(value, str) else ""


def entry_fields(entry_type: str, form) -> dict:
    keys = {
        "survey": ["property_type", "floor_area", "rooms", "floors", "units_wanted"],
        "quote": ["amount", "units"],
        "conversation": ["channel", "occurred_at"],
    }[entry_type]
    return {k: form_str(form, k, 200) for k in keys}


async def save_uploads(files: list, house_id: int) -> list[dict]:
    """업로드 파일을 저장소에 저장. 하나라도 문제가 있으면 이번에 저장한 파일을 정리하고 UserError."""
    saved: list[dict] = []
    if not UPLOADS_ENABLED and any(isinstance(f, UploadFile) and f.filename for f in files):
        raise UserError("사진 저장소가 아직 연결되지 않아 첨부할 수 없습니다. 내용만 저장해 주세요.")
    try:
        for f in files:
            if not isinstance(f, UploadFile) or not f.filename:
                continue
            mime = (f.content_type or "").lower()
            if mime not in ALLOWED_UPLOADS:
                guessed = mimetypes.guess_type(f.filename)[0] or ""
                if guessed not in ALLOWED_UPLOADS:
                    raise UserError("'{name}' 형식은 올릴 수 없습니다. (사진, 동영상, PDF만 가능)", name=f.filename)
                mime = guessed
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as tmp:
                size = 0
                while chunk := await f.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise UserError("'{name}' 파일이 너무 큽니다. (최대 {mb}MB)",
                                        name=f.filename, mb=MAX_UPLOAD_BYTES // 1024 // 1024)
                    tmp.write(chunk)
                tmp.seek(0)
                key = f"{house_id}/{uuid.uuid4().hex}{ALLOWED_UPLOADS[mime]}"
                await run_in_threadpool(storage.put, key, tmp, mime)
            saved.append({"stored_name": key, "original_name": f.filename[:200], "mime": mime, "size": size})
    except Exception:
        remove_files(saved)
        raise
    return saved


async def collect_direct_uploads(conn, raw: str, house_id: int) -> list[dict]:
    """브라우저가 저장소에 직접 올린 파일을 확인한다. 실제로 올라갔는지, 형식·크기가 맞는지 저장소에서 다시 본다."""
    if not raw or not storage.direct:
        return []
    try:
        items = json.loads(raw)
        assert isinstance(items, list)
    except Exception:
        raise UserError("업로드 정보가 올바르지 않습니다. 다시 시도하세요.")
    saved = []
    for it in items[:50]:
        key = str(it.get("key", "")) if isinstance(it, dict) else ""
        name = (str(it.get("name") or "file") if isinstance(it, dict) else "file")[:200]
        m = re.fullmatch(rf"{house_id}/[0-9a-f]{{32}}(\.[a-z0-9]+)", key)
        if not m:
            raise UserError("업로드 정보가 올바르지 않습니다. 다시 시도하세요.")
        if conn.execute("SELECT 1 FROM attachments WHERE stored_name = ?", (key,)).fetchone():
            raise UserError("업로드 정보가 올바르지 않습니다. 다시 시도하세요.")
        info = await run_in_threadpool(storage.head, key)
        if not info:
            raise UserError("'{name}' 업로드가 끝나지 않았습니다. 다시 시도하세요.", name=name)
        size, mime = info
        if ALLOWED_UPLOADS.get(mime) != m.group(1):
            await run_in_threadpool(storage.discard, key)
            raise UserError("'{name}' 형식은 올릴 수 없습니다. (사진, 동영상, PDF만 가능)", name=name)
        if size > MAX_UPLOAD_BYTES:
            await run_in_threadpool(storage.discard, key)
            raise UserError("'{name}' 파일이 너무 큽니다. (최대 {mb}MB)", name=name, mb=MAX_UPLOAD_BYTES // 1024 // 1024)
        saved.append({"stored_name": key, "original_name": name, "mime": mime, "size": size})
    return saved


def remove_files(saved: list[dict]) -> None:
    """DB에 연결되지 못한(저장 실패한) 업로드만 정리한다."""
    for s in saved:
        try:
            storage.discard(s["stored_name"])
        except Exception:
            pass


def insert_attachments(conn, saved: list[dict], entry_id: int, house_id: int, user_id: int, ts: str) -> None:
    for s in saved:
        conn.execute(
            """INSERT INTO attachments (entry_id, house_id, stored_name, original_name, mime, size, uploaded_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (entry_id, house_id, s["stored_name"], s["original_name"], s["mime"], s["size"], user_id, ts),
        )


def attachments_by_entry(conn, entry_ids: list[int]) -> dict[int, list]:
    if not entry_ids:
        return {}
    marks = ",".join("?" * len(entry_ids))
    rows = conn.execute(
        f"""SELECT a.*, u.name AS uploader_name FROM attachments a JOIN users u ON u.id = a.uploaded_by
            WHERE a.entry_id IN ({marks}) AND a.deleted_at IS NULL ORDER BY a.id""",
        entry_ids,
    ).fetchall()
    result: dict[int, list] = {}
    for r in rows:
        result.setdefault(r["entry_id"], []).append(r)
    return result


# ---------- 인증 ----------

@app.get("/")
def index():
    return redirect("/houses")


@app.get("/login")
def login_page(request: Request, next: str = "/houses"):
    return render(request, "login.html", next=next)


@app.post("/login")
async def login(request: Request, conn=Depends(get_db)):
    form = await request.form()
    email = form_str(form, "email").lower()
    password = form.get("password") or ""
    next_url = safe_next(form_str(form, "next") or "/houses")
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user or not verify_password(password, user["password_hash"]):
        return render(request, "login.html", next=next_url, email=email,
                      error="이메일 또는 비밀번호가 맞지 않습니다.", status_code=400)
    if not user["active"]:
        return render(request, "login.html", next=next_url, email=email,
                      error="비활성화된 계정입니다. 관리자에게 문의하세요.", status_code=403)
    lang = user["lang"] or detect_lang(request)
    request.session.clear()
    request.session.update(user_id=user["id"], lang=lang)
    return redirect(next_url)


@app.post("/logout")
def logout(request: Request):
    lang = detect_lang(request)
    request.session.clear()
    request.session["lang"] = lang
    return redirect("/login")


@app.get("/lang/{code}")
def set_lang(request: Request, code: str, next: str = "/houses", conn=Depends(get_db)):
    if code in LANGS:
        request.session["lang"] = code
        if request.session.get("user_id"):
            with conn:
                conn.execute("UPDATE users SET lang = ? WHERE id = ?", (code, request.session["user_id"]))
    return redirect(safe_next(next))


@app.get("/signup")
def signup_page(request: Request, code: str = ""):
    # 초대 링크(/signup?code=...)로 들어오면 초대코드를 미리 채운다
    return render(request, "signup.html", form={"invite_code": code.strip()[:100]})


@app.post("/signup")
async def signup(request: Request, conn=Depends(get_db)):
    form = await request.form()
    data = {k: form_str(form, k, 200) for k in ("name", "email", "phone", "invite_code")}
    data["email"] = data["email"].lower()
    password = form.get("password") or ""

    role = None
    for r in C.ROLES:
        code = get_setting(conn, f"invite_code_{r}")
        if code and data["invite_code"] and hmac.compare_digest(code, data["invite_code"]):
            role = r

    error = None
    if not data["name"] or "@" not in data["email"]:
        error = "이름과 이메일을 정확히 입력하세요."
    elif len(password) < 8:
        error = "비밀번호는 8자 이상이어야 합니다."
    elif password != (form.get("password2") or ""):
        error = "비밀번호 확인이 일치하지 않습니다."
    elif role is None:
        error = "초대코드가 올바르지 않습니다. 관리자에게 초대코드를 받아주세요."
    if error:
        return render(request, "signup.html", form=data, error=error, status_code=400)

    lang = detect_lang(request)
    try:
        with conn:
            new_id = conn.insert(
                """INSERT INTO users (email, name, phone, password_hash, role, lang, seen_baseline, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, (SELECT COALESCE(MAX(id), 0) FROM audit_log), ?)""",
                (data["email"], data["name"], data["phone"], hash_password(password), role, lang, now()),
            )
    except IntegrityError:
        return render(request, "signup.html", form=data, error="이미 가입된 이메일입니다.", status_code=400)

    request.session.clear()
    request.session.update(user_id=new_id, lang=lang)
    flash(request, "{role} 계정으로 가입되었습니다.", role=C.ROLES[role])
    return redirect("/houses")


# ---------- 집(현장) ----------

SORTS = {
    "activity": ("최근 활동순", "last_activity_id DESC"),
    "created": ("최근 등록순", "h.created_at DESC"),
    "oldest": ("오래된 등록순", "h.created_at ASC"),
    "address": ("주소순", "LOWER(h.address) ASC"),
}


@app.get("/houses")
def house_list(request: Request, user=Depends(current_user), conn=Depends(get_db)):
    p = request.query_params
    f = {
        "q": p.get("q", "").strip(),
        "status": [s for s in p.getlist("status") if s in C.STATUSES],
        "sort": p.get("sort", "activity") if p.get("sort") in SORTS else "activity",
    }

    where, params = ["1 = 1"], []
    if f["q"]:
        like = f"%{f['q']}%"
        cols = ["h.address", "h.postcode", "h.landlord_name", "h.memo"]
        if user["role"] in C.OFFICE_ROLES:
            cols += ["h.landlord_phone", "h.landlord_email"]
        # 우편번호는 공백 없이 입력해도 찾을 수 있게 (예: SW1A1AA)
        where.append("(" + " OR ".join(f"{c} {conn.like} ?" for c in cols) + f" OR REPLACE(h.postcode, ' ', '') {conn.like} ?)")
        params += [like] * len(cols) + [f"%{f['q'].replace(' ', '')}%"]
    if f["status"]:
        where.append(f"h.status IN ({','.join('?' * len(f['status']))})")
        params += f["status"]

    unread_sql, unread_params = unread_condition(user)
    sql = f"""SELECT h.*, {unread_sql} AS unread,
                     (SELECT MAX(created_at) FROM audit_log WHERE house_id = h.id) AS last_activity,
                     (SELECT MAX(id) FROM audit_log WHERE house_id = h.id) AS last_activity_id,
                     (SELECT COUNT(*) FROM entries WHERE house_id = h.id AND type = 'survey') AS survey_count,
                     (SELECT COUNT(*) FROM entries WHERE house_id = h.id AND type = 'quote') AS quote_count
              FROM houses h WHERE {" AND ".join(where)}
              ORDER BY {SORTS[f["sort"]][1]}, h.id DESC"""
    houses = conn.execute(sql, unread_params + params).fetchall()
    counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM houses GROUP BY status")}
    filtered = bool(f["q"] or f["status"])
    return render(request, "houses.html", user=user, houses=houses, counts=counts, total=sum(counts.values()),
                  f=f, sorts=SORTS, filtered=filtered)


@app.get("/houses/new")
def house_new_page(request: Request, user=Depends(office_user)):
    return render(request, "house_form.html", user=user, house=None)


HOUSE_KEYS = ("address", "postcode", "landlord_name", "landlord_phone", "landlord_email", "memo")


@app.post("/houses/new")
async def house_create(request: Request, user=Depends(office_user), conn=Depends(get_db)):
    form = await request.form()
    data = {k: form_str(form, k) for k in HOUSE_KEYS}
    data["postcode"] = data["postcode"].upper()
    if not data["address"]:
        return render(request, "house_form.html", user=user, house=data, error="주소를 입력하세요.", status_code=400)
    ts = now()
    with conn:
        house_id = conn.insert(
            f"""INSERT INTO houses ({", ".join(HOUSE_KEYS)}, status, created_by, created_at, updated_by, updated_at)
                VALUES ({", ".join("?" * len(HOUSE_KEYS))}, ?, ?, ?, ?, ?)""",
            (*data.values(), C.STATUSES[0], user["id"], ts, user["id"], ts),
        )
        audit(conn, house_id=house_id, actor_id=user["id"], action="house_create", detail=data["address"])
    flash(request, "집이 등록되었습니다. 설치업자가 현장정보를 올릴 수 있습니다.")
    return redirect(f"/houses/{house_id}")


@app.get("/houses/{house_id}")
def house_detail(request: Request, house_id: int, tab: str = "all", q: str = "",
                 user=Depends(current_user), conn=Depends(get_db)):
    house = get_house_or_404(conn, house_id)
    types = readable_types(user)
    marks = ",".join("?" * len(types))

    entries = conn.execute(
        f"""SELECT e.*, a.name AS author_name, a.role AS author_role, u.name AS updater_name
            FROM entries e JOIN users a ON a.id = e.author_id LEFT JOIN users u ON u.id = e.updated_by
            WHERE e.house_id = ? AND e.type IN ({marks}) ORDER BY e.created_at DESC, e.id DESC""",
        (house_id, *types),
    ).fetchall()
    files = attachments_by_entry(conn, [e["id"] for e in entries])

    history = conn.execute(
        f"""SELECT l.*, u.name AS actor_name, e.type AS entry_type
            FROM audit_log l JOIN users u ON u.id = l.actor_id LEFT JOIN entries e ON e.id = l.entry_id
            WHERE l.house_id = ? AND (l.entry_id IS NULL OR e.type IN ({marks}))
            ORDER BY l.created_at DESC, l.id DESC""",
        (house_id, *types),
    ).fetchall()

    # 정렬은 시각(초 단위)이 아니라 변경이력 번호로 해서 같은 초에 생긴 항목도 순서가 정확하다
    created_log = {h["entry_id"]: h["id"] for h in history if h["action"] == "entry_create"}
    timeline = [{"kind": "entry", "order": created_log.get(e["id"], 0), "entry": e} for e in entries]
    timeline += [{"kind": "status", "order": h["id"], "log": h} for h in history if h["action"] == "status"]
    timeline.sort(key=lambda x: x["order"], reverse=True)
    if tab in C.ENTRY_TYPES:
        timeline = [t for t in timeline if t["kind"] == "entry" and t["entry"]["type"] == tab]
    elif tab == "status":
        timeline = [t for t in timeline if t["kind"] == "status"]
    q = q.strip()
    if q:
        needle = q.lower()

        def matches(t):
            if t["kind"] == "status":
                return needle in f"{t['log']['detail']} {t['log']['snapshot'] or ''} {t['log']['actor_name']}".lower()
            e = t["entry"]
            names = " ".join(a["original_name"] for a in files.get(e["id"], []))
            values = " ".join(json.loads(e["fields"] or "{}").values())
            return needle in f"{e['body'] or ''} {values} {names} {e['author_name']}".lower()

        timeline = [t for t in timeline if matches(t)]

    # 마지막으로 본 뒤에 다른 사람이 한 활동은 N 표시, 그리고 지금 본 것으로 기록
    seen = conn.execute("SELECT last_seen_id FROM house_views WHERE user_id = ? AND house_id = ?",
                        (user["id"], house_id)).fetchone()
    last_seen = seen["last_seen_id"] if seen else user["seen_baseline"]
    fresh = [h for h in history if h["id"] > last_seen and h["actor_id"] != user["id"]]
    new_logs = {h["id"] for h in fresh}
    new_entries = {h["entry_id"] for h in fresh if h["entry_id"]}
    with conn:
        conn.execute(
            """INSERT INTO house_views (user_id, house_id, last_seen_id)
               VALUES (?, ?, (SELECT COALESCE(MAX(id), 0) FROM audit_log WHERE house_id = ?))
               ON CONFLICT(user_id, house_id) DO UPDATE SET last_seen_id = CASE
                 WHEN excluded.last_seen_id > house_views.last_seen_id THEN excluded.last_seen_id
                 ELSE house_views.last_seen_id END""",
            (user["id"], house_id, house_id),
        )

    type_counts = {t: sum(1 for e in entries if e["type"] == t) for t in types}
    writable = [t for t in C.ENTRY_TYPES if can_write(user, t)]
    return render(
        request, "house.html", user=user, house=house, timeline=timeline, files=files, history=history,
        tab=tab, q=q, types=types, type_counts=type_counts, writable=writable,
        new_logs=new_logs, new_entries=new_entries,
        can_edit=lambda e: can_edit(user, e),
    )


@app.get("/houses/{house_id}/edit")
def house_edit_page(request: Request, house_id: int, user=Depends(office_user), conn=Depends(get_db)):
    return render(request, "house_form.html", user=user, house=get_house_or_404(conn, house_id))


@app.post("/houses/{house_id}/edit")
async def house_edit(request: Request, house_id: int, user=Depends(office_user), conn=Depends(get_db)):
    house = get_house_or_404(conn, house_id)
    form = await request.form()
    data = {k: form_str(form, k) for k in HOUSE_KEYS}
    data["postcode"] = data["postcode"].upper()
    if not data["address"]:
        return render(request, "house_form.html", user=user, house={**dict(house), **data},
                      error="주소를 입력하세요.", status_code=400)
    before = {k: house[k] or "" for k in HOUSE_KEYS}
    changed = [k for k in HOUSE_KEYS if before[k] != data[k]]
    if changed:
        with conn:
            conn.execute(
                f"UPDATE houses SET {', '.join(f'{k} = ?' for k in HOUSE_KEYS)}, updated_by = ?, updated_at = ? WHERE id = ?",
                (*data.values(), user["id"], now(), house_id),
            )
            audit(conn, house_id=house_id, actor_id=user["id"], action="house_edit",
                  detail=",".join(changed), snapshot=before)
        flash(request, "집 정보가 수정되었습니다.")
    return redirect(f"/houses/{house_id}")


@app.post("/houses/{house_id}/status")
async def house_status(request: Request, house_id: int, user=Depends(office_user), conn=Depends(get_db)):
    house = get_house_or_404(conn, house_id)
    form = await request.form()
    new_status = form_str(form, "status")
    note = form_str(form, "note", 500)
    if new_status not in C.STATUSES:
        raise HTTPException(400, "알 수 없는 상태입니다.")
    if new_status != house["status"]:
        with conn:
            conn.execute("UPDATE houses SET status = ?, updated_by = ?, updated_at = ? WHERE id = ?",
                         (new_status, user["id"], now(), house_id))
            audit(conn, house_id=house_id, actor_id=user["id"], action="status",
                  detail=f"{house['status']} → {new_status}", snapshot={"note": note} if note else None)
        flash(request, "상태가 '{status}'(으)로 변경되었습니다.", status=new_status)
    return redirect(f"/houses/{house_id}")


# ---------- 기록 (현장정보 / 견적 / 집주인 대화) ----------

@app.post("/houses/{house_id}/entries/{entry_type}")
async def entry_create(request: Request, house_id: int, entry_type: str,
                       user=Depends(current_user), conn=Depends(get_db)):
    if entry_type not in C.ENTRY_TYPES:
        raise HTTPException(404)
    if not can_write(user, entry_type):
        raise Forbidden()
    get_house_or_404(conn, house_id)
    form = await request.form()
    fields = entry_fields(entry_type, form)
    body = form_str(form, "body", 20000)
    uploads = form.getlist("files")

    if not body and not any(fields.values()) and not any(getattr(f, "filename", "") for f in uploads) \
            and not form_str(form, "uploaded", 100000).strip("[] "):
        flash(request, "내용을 입력하거나 파일을 첨부하세요.", "error")
        return redirect(f"/houses/{house_id}?tab={entry_type}#new")
    try:
        saved = await save_uploads(uploads, house_id)
        saved += await collect_direct_uploads(conn, form_str(form, "uploaded", 100000), house_id)
    except UserError as e:
        flash(request, e.key, "error", **e.args_)
        return redirect(f"/houses/{house_id}?tab={entry_type}#new")

    ts = now()
    try:
        with conn:
            entry_id = conn.insert(
                """INSERT INTO entries (house_id, type, author_id, fields, body, created_at, updated_by, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (house_id, entry_type, user["id"], json.dumps(fields, ensure_ascii=False), body, ts, user["id"], ts),
            )
            insert_attachments(conn, saved, entry_id, house_id, user["id"], ts)
            audit(conn, house_id=house_id, entry_id=entry_id, actor_id=user["id"], action="entry_create",
                  detail=str(len(saved)))
    except Exception:
        remove_files(saved)
        raise
    flash(request, "{type} 기록이 저장되었습니다.", type=C.ENTRY_TYPES[entry_type])
    return redirect(f"/houses/{house_id}#entry-{entry_id}")


@app.post("/houses/{house_id}/uploads")
async def upload_urls(request: Request, house_id: int, user=Depends(current_user), conn=Depends(get_db)):
    """브라우저가 저장소(R2)에 사진을 바로 올릴 수 있는 1회용 주소를 발급한다."""
    if not storage.direct:
        raise HTTPException(404)
    get_house_or_404(conn, house_id)
    lang = detect_lang(request)
    try:
        files = (await request.json()).get("files")
        assert isinstance(files, list) and 0 < len(files) <= 50
    except Exception:
        return JSONResponse({"error": translate(lang, "업로드 정보가 올바르지 않습니다. 다시 시도하세요.")}, 400)
    out = []
    for f in files:
        name = str(f.get("name") or "file")[:200]
        mime = str(f.get("type") or "").lower() or (mimetypes.guess_type(name)[0] or "")
        if mime not in ALLOWED_UPLOADS:
            return JSONResponse({"error": translate(lang, "'{name}' 형식은 올릴 수 없습니다. (사진, 동영상, PDF만 가능)", name=name)}, 400)
        if int(f.get("size") or 0) > MAX_UPLOAD_BYTES:
            return JSONResponse({"error": translate(lang, "'{name}' 파일이 너무 큽니다. (최대 {mb}MB)",
                                                    name=name, mb=MAX_UPLOAD_BYTES // 1024 // 1024)}, 400)
        key = f"{house_id}/{uuid.uuid4().hex}{ALLOWED_UPLOADS[mime]}"
        out.append({"key": key, "type": mime, "url": await run_in_threadpool(storage.presign_put, key, mime)})
    return {"uploads": out}


@app.get("/entries/{entry_id}/edit")
def entry_edit_page(request: Request, entry_id: int, user=Depends(current_user), conn=Depends(get_db)):
    entry = get_entry_or_404(conn, entry_id, user)
    if not can_edit(user, entry):
        raise Forbidden()
    return render_entry_edit(request, conn, user, entry)


def render_entry_edit(request, conn, user, entry, error=None, status_code=200):
    house = get_house_or_404(conn, entry["house_id"])
    files = attachments_by_entry(conn, [entry["id"]]).get(entry["id"], [])
    revisions = conn.execute(
        """SELECT l.*, u.name AS actor_name FROM audit_log l JOIN users u ON u.id = l.actor_id
           WHERE l.entry_id = ? ORDER BY l.created_at DESC, l.id DESC""",
        (entry["id"],),
    ).fetchall()
    return render(request, "entry_edit.html", user=user, house=house, entry=entry, files=files,
                  revisions=revisions, error=error, status_code=status_code)


@app.post("/entries/{entry_id}/edit")
async def entry_edit(request: Request, entry_id: int, user=Depends(current_user), conn=Depends(get_db)):
    entry = get_entry_or_404(conn, entry_id, user)
    if not can_edit(user, entry):
        raise Forbidden()
    form = await request.form()
    fields = entry_fields(entry["type"], form)
    body = form_str(form, "body", 20000)
    remove_ids = {int(x) for x in form.getlist("remove_files") if str(x).isdigit()}

    try:
        saved = await save_uploads(form.getlist("files"), entry["house_id"])
        saved += await collect_direct_uploads(conn, form_str(form, "uploaded", 100000), entry["house_id"])
    except UserError as e:
        return render_entry_edit(request, conn, user, entry, error=e, status_code=400)

    old_fields = json.loads(entry["fields"] or "{}")
    content_changed = old_fields != fields or (entry["body"] or "") != body
    current_files = attachments_by_entry(conn, [entry_id]).get(entry_id, [])
    to_remove = [f for f in current_files if f["id"] in remove_ids]

    if not content_changed and not saved and not to_remove:
        return redirect(f"/houses/{entry['house_id']}#entry-{entry_id}")

    ts = now()
    house_id = entry["house_id"]
    try:
        with conn:
            if content_changed:
                conn.execute(
                    "UPDATE entries SET fields = ?, body = ?, updated_by = ?, updated_at = ?, edit_count = edit_count + 1 WHERE id = ?",
                    (json.dumps(fields, ensure_ascii=False), body, user["id"], ts, entry_id),
                )
                audit(conn, house_id=house_id, entry_id=entry_id, actor_id=user["id"], action="entry_edit",
                      snapshot={"fields": old_fields, "body": entry["body"] or ""})
            else:
                conn.execute("UPDATE entries SET updated_by = ?, updated_at = ? WHERE id = ?", (user["id"], ts, entry_id))
            if saved:
                insert_attachments(conn, saved, entry_id, house_id, user["id"], ts)
                audit(conn, house_id=house_id, entry_id=entry_id, actor_id=user["id"], action="attachment_add",
                      detail=", ".join(s["original_name"] for s in saved))
            for f in to_remove:
                # 기록 보존을 위해 파일은 지우지 않고 삭제 표시만 한다.
                conn.execute("UPDATE attachments SET deleted_by = ?, deleted_at = ? WHERE id = ?", (user["id"], ts, f["id"]))
                audit(conn, house_id=house_id, entry_id=entry_id, actor_id=user["id"], action="attachment_delete",
                      detail=f["original_name"])
    except Exception:
        remove_files(saved)
        raise
    flash(request, "기록이 수정되었습니다.")
    return redirect(f"/houses/{house_id}#entry-{entry_id}")


@app.get("/files/{file_id}")
def file_download(file_id: int, download: int = 0, user=Depends(current_user), conn=Depends(get_db)):
    f = conn.execute(
        "SELECT a.*, e.type AS entry_type FROM attachments a JOIN entries e ON e.id = a.entry_id WHERE a.id = ?",
        (file_id,),
    ).fetchone()
    # 삭제 표시된 파일은 관리자만 열람 가능(기록 확인용)
    if not f or f["entry_type"] not in readable_types(user) or (f["deleted_at"] and user["role"] not in C.OFFICE_ROLES):
        raise HTTPException(404)
    return storage.response(f["stored_name"], f["mime"], f["original_name"], bool(download))


# ---------- 사용자 관리 (관리자) ----------

@app.get("/users")
def users_page(request: Request, user=Depends(office_user), conn=Depends(get_db)):
    users = conn.execute("SELECT * FROM users ORDER BY role DESC, created_at").fetchall()
    return render(request, "users.html", user=user, users=users, manageable=C.MANAGEABLE[user["role"]])


@app.get("/invites")
def invites_page(request: Request, user=Depends(office_user), conn=Depends(get_db)):
    codes = {r: get_setting(conn, f"invite_code_{r}") for r in C.MANAGEABLE[user["role"]]}
    return render(request, "invites.html", user=user, codes=codes)


@app.post("/users/{user_id}/toggle")
def user_toggle(request: Request, user_id: int, user=Depends(office_user), conn=Depends(get_db)):
    if user_id == user["id"]:
        flash(request, "본인 계정은 비활성화할 수 없습니다.", "error")
        return redirect("/users")
    target = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        raise HTTPException(404)
    if target["role"] not in C.MANAGEABLE[user["role"]]:  # 매니저는 관리자 계정을 건드릴 수 없다
        raise Forbidden()
    with conn:
        conn.execute("UPDATE users SET active = 1 - active WHERE id = ?", (user_id,))
    return redirect("/users")


@app.post("/users/{user_id}/role")
async def user_role(request: Request, user_id: int, user=Depends(admin_user), conn=Depends(get_db)):
    role = form_str(await request.form(), "role")
    if role not in C.ROLES:
        raise HTTPException(400, "알 수 없는 역할입니다.")
    if user_id == user["id"]:
        flash(request, "본인 역할은 바꿀 수 없습니다.", "error")  # 관리자가 한 명도 없어지는 일을 막는다
        return redirect("/users")
    target = conn.execute("SELECT name, role FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        raise HTTPException(404)
    if target["role"] != role:
        with conn:
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        flash(request, "{name}님의 역할을 {role}(으)로 바꿨습니다.", name=target["name"], role=C.ROLES[role])
    return redirect("/users")


@app.post("/invite-codes/{role}/regenerate")
def invite_regenerate(request: Request, role: str, user=Depends(admin_user), conn=Depends(get_db)):
    if role not in C.ROLES:
        raise HTTPException(404)
    with conn:
        set_setting(conn, f"invite_code_{role}", secrets.token_urlsafe(9))
    flash(request, "{role} 초대코드를 새로 발급했습니다. 이전 코드로는 더 이상 가입할 수 없습니다.", role=C.ROLES[role])
    return redirect("/invites")
