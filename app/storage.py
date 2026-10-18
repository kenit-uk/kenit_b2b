"""첨부파일 저장소.

S3_BUCKET 이 설정되어 있으면 S3 호환 저장소(Cloudflare R2 등)에, 없으면 로컬 UPLOAD_DIR 에 저장한다.
운영 환경에서는 서버 디스크가 사라져도 파일이 남도록 반드시 S3 저장소를 쓴다.
앱은 정상 동작 중 파일을 지우지 않는다(업로드 도중 실패해 DB에 연결되지 않은 파일만 정리).
"""
import os
from pathlib import Path
from urllib.parse import quote

from fastapi.responses import FileResponse, RedirectResponse
from starlette.exceptions import HTTPException

from .config import UPLOAD_DIR

S3_BUCKET = os.environ.get("S3_BUCKET")
UPLOAD_PREFIX = "uploads/"


def content_disposition(filename: str, download: bool) -> str:
    kind = "attachment" if download else "inline"
    ascii_name = filename.encode("ascii", "ignore").decode().replace('"', "") or "file"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


class LocalStorage:
    name = "local"
    direct = False  # 브라우저 → 저장소 직접 업로드 미지원(파일이 앱 서버를 거쳐 저장됨)

    def put(self, key: str, fileobj, mime: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as out:
            while chunk := fileobj.read(1024 * 1024):
                out.write(chunk)

    def discard(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def response(self, key: str, mime: str, filename: str, download: bool):
        path = self._path(key)
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(
            path, media_type=mime,
            headers={"Content-Disposition": content_disposition(filename, download),
                     "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"},
        )

    def _path(self, key: str) -> Path:
        root = UPLOAD_DIR.resolve()
        path = (root / key).resolve()
        if root not in path.parents:
            raise HTTPException(404)
        return path


class S3Storage:
    name = "s3"
    direct = True  # 브라우저가 서명된 주소로 저장소에 바로 올린다(서버리스 요청 크기 제한 회피)

    def __init__(self):
        import boto3
        from botocore.config import Config

        self.bucket = S3_BUCKET
        self.client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT") or None,
            region_name=os.environ.get("S3_REGION", "auto"),
            aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY"),
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                          retries={"max_attempts": 5, "mode": "standard"}),
        )

    def put(self, key: str, fileobj, mime: str) -> None:
        self.client.upload_fileobj(fileobj, self.bucket, UPLOAD_PREFIX + key, ExtraArgs={"ContentType": mime})

    def discard(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=UPLOAD_PREFIX + key)

    def exists(self, key: str) -> bool:
        return self.head(key) is not None

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=UPLOAD_PREFIX + key)["Body"].read()

    def presign_put(self, key: str, mime: str) -> str:
        return self.client.generate_presigned_url(
            "put_object", Params={"Bucket": self.bucket, "Key": UPLOAD_PREFIX + key, "ContentType": mime},
            ExpiresIn=900,
        )

    def head(self, key: str):
        """업로드된 파일의 (크기, 형식). 없으면 None."""
        from botocore.exceptions import ClientError

        try:
            r = self.client.head_object(Bucket=self.bucket, Key=UPLOAD_PREFIX + key)
        except ClientError:
            return None
        return r["ContentLength"], (r.get("ContentType") or "").lower()

    def response(self, key: str, mime: str, filename: str, download: bool):
        # 파일은 저장소에서 바로 내려받게 한다(앱 서버를 거치지 않아 빠르고 전송 비용도 들지 않는다).
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": UPLOAD_PREFIX + key, "ResponseContentType": mime,
                    "ResponseContentDisposition": content_disposition(filename, download)},
            ExpiresIn=600,
        )
        return RedirectResponse(url, status_code=307, headers={"Cache-Control": "private, max-age=300"})


storage = S3Storage() if S3_BUCKET else LocalStorage()


# ---------- 미리보기(썸네일) ----------
THUMB_MIMES = ("image/jpeg", "image/png", "image/webp", "image/gif")
THUMB_SIZE = 480


def thumb_key(key: str) -> str:
    return key + ".thumb.jpg"


def ensure_thumb(key: str) -> str | None:
    """목록용 작은 사진(긴 변 480px)을 처음 볼 때 만들어 저장해 두고 그 키를 돌려준다. 만들 수 없으면 None(원본을 쓴다)."""
    tk = thumb_key(key)
    try:
        if storage.exists(tk):
            return tk
        import io
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(storage.read(key))) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((THUMB_SIZE, THUMB_SIZE))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            out = io.BytesIO()
            im.save(out, "JPEG", quality=75, optimize=True)
        out.seek(0)
        storage.put(tk, out, "image/jpeg")
        return tk
    except Exception as e:  # 깨진 파일·지원 안 되는 형식 등
        print(f"[thumb] {key}: {e}")
        return None
