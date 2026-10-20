from __future__ import annotations

from io import BytesIO
from .db import Database
from pathlib import Path
from uuid import uuid4

from PIL import Image


MAX_UPLOAD_BYTES = 30 * 1024 * 1024
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_FORMAT_SUFFIXES = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


def add_photos(db: Database, bike_id: int, data_dir: Path, uploads: list) -> int:
    """Validate and append user-selected image files to one bike."""
    if not uploads:
        return 0

    prepared: list[tuple[bytes, str, str]] = []
    for upload in uploads:
        original_name = Path(upload.name).name
        if Path(original_name).suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError(f"Файл «{original_name}» должен быть JPG, PNG или WebP.")
        content = upload.getvalue()
        if not content:
            raise ValueError(f"Файл «{original_name}» пустой.")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError(f"Файл «{original_name}» больше 30 МБ.")
        try:
            with Image.open(BytesIO(content)) as image:
                image_format = image.format
                image.verify()
        except (OSError, ValueError) as error:
            raise ValueError(f"Файл «{original_name}» не удалось прочитать как изображение.") from error
        suffix = IMAGE_FORMAT_SUFFIXES.get(image_format or "")
        if not suffix:
            raise ValueError(f"Формат файла «{original_name}» не поддерживается.")
        prepared.append((content, suffix, original_name))

    photo_dir = Path(data_dir) / "photos" / str(bike_id)
    photo_dir.mkdir(parents=True, exist_ok=True)
    written_paths: list[Path] = []
    try:
        with db.connect() as conn:
            bike = conn.execute(
                "SELECT id FROM bikes WHERE id=? AND status='active'", (bike_id,)
            ).fetchone()
            if not bike:
                raise ValueError("Выбранный велосипед больше недоступен.")
            row = conn.execute(
                "SELECT COALESCE(MAX(sort_order),-1) AS last_order FROM photos "
                "WHERE bike_id=? AND archived=0",
                (bike_id,),
            ).fetchone()
            next_order = int(row["last_order"]) + 1
            for offset, (content, suffix, _original_name) in enumerate(prepared):
                target = photo_dir / f"added_{uuid4().hex}{suffix}"
                target.write_bytes(content)
                written_paths.append(target)
                conn.execute(
                    "INSERT INTO photos(bike_id,path,sort_order) VALUES(?,?,?)",
                    (bike_id, str(target), next_order + offset),
                )
    except Exception:
        for path in written_paths:
            path.unlink(missing_ok=True)
        raise
    return len(prepared)


def reorder_photos(db: Database, bike_id: int, photo_ids: list[int]) -> bool:
    """Persist a complete ordering for the active photos of a bike."""
    requested = [int(photo_id) for photo_id in photo_ids]
    if len(requested) != len(set(requested)):
        raise ValueError("В списке фотографий есть повторяющиеся элементы.")

    with db.connect() as conn:
        active_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM photos WHERE bike_id=? AND archived=0 "
                "ORDER BY sort_order,id",
                (bike_id,),
            ).fetchall()
        ]
        if set(requested) != set(active_ids):
            raise ValueError("Список фотографий изменился. Обнови страницу и попробуй ещё раз.")
        if requested == active_ids:
            return False
        conn.executemany(
            "UPDATE photos SET sort_order=? WHERE id=? AND bike_id=? AND archived=0",
            [(order, photo_id, bike_id) for order, photo_id in enumerate(requested)],
        )
    return True


def archive_photo(db: Database, bike_id: int, photo_id: int) -> bool:
    """Hide a photo from posting while retaining its file and restore option."""
    with db.connect() as conn:
        changed = conn.execute(
            "UPDATE photos SET archived=1 WHERE id=? AND bike_id=? AND archived=0",
            (photo_id, bike_id),
        ).rowcount
        if changed:
            remaining = conn.execute(
                "SELECT id FROM photos WHERE bike_id=? AND archived=0 "
                "ORDER BY sort_order,id",
                (bike_id,),
            ).fetchall()
            conn.executemany(
                "UPDATE photos SET sort_order=? WHERE id=?",
                [(order, row["id"]) for order, row in enumerate(remaining)],
            )
    return changed == 1


def restore_photo(db: Database, bike_id: int, photo_id: int) -> bool:
    """Restore an archived photo at the end of the active photo order."""
    with db.connect() as conn:
        last_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order),-1) AS last_order FROM photos "
            "WHERE bike_id=? AND archived=0",
            (bike_id,),
        ).fetchone()["last_order"]
        changed = conn.execute(
            "UPDATE photos SET archived=0,sort_order=? "
            "WHERE id=? AND bike_id=? AND archived=1",
            (last_order + 1, photo_id, bike_id),
        ).rowcount
    return changed == 1
