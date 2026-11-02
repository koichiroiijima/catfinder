from __future__ import annotations

import asyncio
import io
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

ALLOWED_FORMATS: dict[str, tuple[str, str]] = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "WEBP": (".webp", "image/webp"),
}

MAX_PIXELS = 40_000_000


class ImageValidationError(Exception):
    """A user-facing validation failure with an HTTP status code."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class StoredImage:
    id: str
    filename: str
    path: Path
    media_type: str
    size_bytes: int
    data: bytes = field(repr=False)


def display_filename(raw: str | None) -> str:
    """Keep only a safe basename for display; never use it as a path."""
    name = Path((raw or "").replace("\\", "/")).name.replace("\x00", "").strip()
    return name[:200] or "image"


def _store_image(
    data: bytes,
    filename: str | None,
    dest_dir: Path,
    max_bytes: int,
) -> StoredImage:
    if not data:
        raise ImageValidationError("空のファイルです。")
    if len(data) > max_bytes:
        raise ImageValidationError(
            f"画像が大きすぎます（上限 {max_bytes // (1024 * 1024)}MB）。",
            status_code=413,
        )

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            detected_format = image.format
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise ImageValidationError("画像の解像度が大きすぎます（上限 4000万画素）。")
            image.load()
    except ImageValidationError:
        raise
    except Image.DecompressionBombError as exc:
        raise ImageValidationError("画像の解像度が大きすぎます。") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageValidationError("画像として読み込めないファイルです。") from exc

    if detected_format not in ALLOWED_FORMATS:
        raise ImageValidationError("JPEG / PNG / WebP のみ対応しています。")

    if width <= 0 or height <= 0:
        raise ImageValidationError("画像のサイズが不正です。")

    suffix, media_type = ALLOWED_FORMATS[detected_format]
    image_id = uuid.uuid4().hex
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{image_id}{suffix}"
    path.write_bytes(data)

    return StoredImage(
        id=image_id,
        filename=display_filename(filename),
        path=path,
        media_type=media_type,
        size_bytes=len(data),
        data=data,
    )


async def save_image(
    upload: UploadFile,
    dest_dir: Path,
    *,
    max_bytes: int,
) -> StoredImage:
    """Read one upload, then validate and persist it off the event loop.

    Validation trusts the decoded bytes, not the extension or Content-Type.
    The stored path uses a generated UUID so user input cannot affect it.
    """
    data = await upload.read(max_bytes + 1)
    return await asyncio.to_thread(
        _store_image,
        data,
        upload.filename,
        dest_dir,
        max_bytes,
    )
