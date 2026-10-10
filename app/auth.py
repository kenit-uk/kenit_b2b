import hashlib
import hmac
import secrets

from fastapi import Depends, Request

from .db import get_db


class LoginRequired(Exception):
    pass


class Forbidden(Exception):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest.hex(), digest_hex)


def current_user(request: Request, conn=Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        raise LoginRequired()
    user = conn.execute("SELECT * FROM users WHERE id = ? AND active = 1", (user_id,)).fetchone()
    if not user:
        request.session.clear()
        raise LoginRequired()
    return user


def staff_user(user=Depends(current_user)):
    if user["role"] != "staff":
        raise Forbidden()
    return user
