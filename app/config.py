import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# Vercel 같은 서버리스 환경은 /tmp 외에는 쓸 수 없고, /tmp도 수시로 비워진다.
ON_VERCEL = bool(os.environ.get("VERCEL"))
_default_root = Path("/tmp") if ON_VERCEL else BASE_DIR
DATA_DIR = Path(os.environ.get("DATA_DIR", _default_root / "data"))
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", _default_root / "uploads"))
DB_PATH = DATA_DIR / "app.db"

# 설정하면 SQLite 대신 PostgreSQL(Neon 등)을 쓴다. Vercel의 Neon 연동은 DATABASE_URL 을 넣어준다.
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _secret_key() -> str:
    """SECRET_KEY 환경변수가 없으면 data/ 에 한 번 생성해 두고 재사용한다(재시작해도 로그인 유지)."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    if ON_VERCEL:
        raise RuntimeError("SECRET_KEY 환경변수를 설정하세요 (Vercel에서는 필수).")
    path = DATA_DIR / ".secret_key"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


SECRET_KEY = _secret_key()
# HTTPS로 운영할 때는 COOKIE_SECURE=1
COOKIE_SECURE = os.environ.get("COOKIE_SECURE") == "1"

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024
