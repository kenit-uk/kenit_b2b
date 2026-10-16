"""휴대폰 문자 인증.

- 번호는 국제 형식(+447700900123)으로 저장한다. 영국 번호는 07700 900123 처럼 입력해도 된다.
- 코드는 6자리, 10분 유효, 5번 틀리면 다시 받아야 한다. 같은 번호로는 1분에 1번, 1시간에 5번까지만 보낸다.
- 코드는 해시로만 저장한다.
- 발송: TWILIO_* 환경변수가 있으면 Twilio로 보내고, 없으면(로컬 개발) 서버 로그에 코드를 찍는다.
  Vercel에서 Twilio가 설정되지 않았으면 문자 로그인을 끈다.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .config import ON_VERCEL, SECRET_KEY

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
RESEND_AFTER = timedelta(seconds=60)
MAX_PER_HOUR = 5


def normalize_phone(raw: str, default_country: str = "44") -> str | None:
    """입력한 번호를 +국가번호 형식으로. 올바르지 않으면 None."""
    s = re.sub(r"[\s\-().]", "", raw or "")
    if s.startswith("+"):
        digits = s[1:]
    elif s.startswith("00"):
        digits = s[2:]
    elif s.startswith("0"):
        digits = default_country + s[1:]
    elif s.startswith(default_country) and len(s) > 11:
        digits = s
    else:
        digits = default_country + s
    if not digits.isdigit() or not 10 <= len(digits) <= 15:
        return None
    if digits.startswith("44") and len(digits) != 12:  # 영국 번호는 국가번호 포함 12자리
        return None
    return "+" + digits


def format_phone(e164: str | None) -> str:
    """+447700900123 → +44 7700 900123"""
    if not e164:
        return ""
    if e164.startswith("+44") and len(e164) == 13:
        return f"+44 {e164[3:7]} {e164[7:]}"
    return e164


def mask_phone(e164: str) -> str:
    """+447700900123 → +44 •••• ••0123"""
    shown = format_phone(e164)
    return shown[:4] + re.sub(r"\d", "•", shown[4:-4]) + shown[-4:] if len(shown) > 8 else shown


def _hash(phone: str, code: str) -> str:
    return hmac.new(SECRET_KEY.encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ---------- 발송 ----------

class ConsoleSender:
    """로컬 개발용: 문자를 보내지 않고 서버 로그에 찍는다."""

    name = "console"

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, to: str, text: str) -> None:
        self.sent.append((to, text))
        print(f"[SMS 시험 모드 / test mode] → {to}: {text}")


class TwilioSender:
    name = "twilio"

    def __init__(self):
        self.sid = os.environ["TWILIO_ACCOUNT_SID"]
        self.token = os.environ["TWILIO_AUTH_TOKEN"]
        self.sender = os.environ.get("TWILIO_FROM", "")  # 발신 번호 또는 영문 발신자명(예: Woodstone)
        self.service = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "")

    def send(self, to: str, text: str) -> None:
        data = {"To": to, "Body": text}
        if self.service:
            data["MessagingServiceSid"] = self.service
        else:
            data["From"] = self.sender
        req = urllib.request.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json",
            data=urllib.parse.urlencode(data).encode(),
            headers={"Authorization": "Basic " + base64.b64encode(f"{self.sid}:{self.token}".encode()).decode()},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise SmsError(f"Twilio {e.code}: {e.read()[:300]!r}") from e


class SmsError(Exception):
    pass


if os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN"):
    sender = TwilioSender()
elif not ON_VERCEL:
    sender = ConsoleSender()
else:
    sender = None

SMS_ENABLED = sender is not None


# ---------- 코드 발급·확인 ----------

def request_code(conn, phone: str, purpose: str, text_template: str, user_id: int | None = None) -> str | None:
    """코드를 만들어 보낸다. 성공하면 None, 실패하면 화면에 보여줄 메시지(번역 키)."""
    if not SMS_ENABLED:
        return "문자 인증이 아직 설정되지 않았습니다. 이메일로 로그인하세요."
    now = datetime.now(timezone.utc)
    recent = conn.execute(
        "SELECT created_at FROM phone_codes WHERE phone = ? AND created_at >= ? ORDER BY id DESC",
        (phone, _iso(now - timedelta(hours=1))),
    ).fetchall()
    if recent and recent[0]["created_at"] >= _iso(now - RESEND_AFTER):
        return "방금 코드를 보냈습니다. 1분 뒤에 다시 받을 수 있습니다."
    if len(recent) >= MAX_PER_HOUR:
        return "코드를 너무 많이 요청했습니다. 1시간 뒤에 다시 시도하세요."
    code = f"{secrets.randbelow(10**6):06d}"
    with conn:
        conn.execute(
            """INSERT INTO phone_codes (phone, purpose, user_id, code_hash, attempts, created_at, expires_at)
               VALUES (?, ?, ?, ?, 0, ?, ?)""",
            (phone, purpose, user_id, _hash(phone, code), _iso(now), _iso(now + CODE_TTL)),
        )
    try:
        sender.send(phone, text_template.format(code=code))
    except SmsError as e:
        print(f"[SMS 발송 실패] {e}")
        return "문자를 보내지 못했습니다. 잠시 후 다시 시도하세요."
    return None


def check_code(conn, phone: str, purpose: str, code: str, user_id: int | None = None) -> str | None:
    """코드를 확인한다. 맞으면 None, 틀리면 화면에 보여줄 메시지(번역 키)."""
    now = _iso(datetime.now(timezone.utc))
    owner = "user_id IS NULL" if user_id is None else "user_id = ?"
    row = conn.execute(
        f"""SELECT * FROM phone_codes WHERE phone = ? AND purpose = ? AND used_at IS NULL AND {owner}
            ORDER BY id DESC LIMIT 1""",
        (phone, purpose) if user_id is None else (phone, purpose, user_id),
    ).fetchone()
    if not row or row["expires_at"] < now:
        return "코드가 만료되었습니다. 코드를 다시 받으세요."
    if row["attempts"] >= MAX_ATTEMPTS:
        return "코드를 여러 번 틀렸습니다. 코드를 다시 받으세요."
    code = re.sub(r"\D", "", code or "")
    with conn:
        if not hmac.compare_digest(row["code_hash"], _hash(phone, code)):
            conn.execute("UPDATE phone_codes SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
            left = MAX_ATTEMPTS - row["attempts"] - 1
            return "코드가 맞지 않습니다." if left > 0 else "코드를 여러 번 틀렸습니다. 코드를 다시 받으세요."
        conn.execute("UPDATE phone_codes SET used_at = ? WHERE id = ?", (now, row["id"]))
    return None
