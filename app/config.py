import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", BASE_DIR / "uploads"))
DB_PATH = DATA_DIR / "app.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _secret_key() -> str:
    """SECRET_KEY 환경변수가 없으면 data/ 에 한 번 생성해 두고 재사용한다(재시작해도 로그인 유지)."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = DATA_DIR / ".secret_key"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


SECRET_KEY = _secret_key()
# HTTPS로 운영할 때는 COOKIE_SECURE=1
COOKIE_SECURE = os.environ.get("COOKIE_SECURE") == "1"

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024
ALLOWED_MIME_PREFIXES = ("image/", "video/")
ALLOWED_MIME_EXACT = ("application/pdf",)
