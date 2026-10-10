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

    def put(self, key: str, fileobj, mime: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as out:
            while chunk := fileobj.read(1024 * 1024):
                out.write(chunk)

    def discard(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

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
