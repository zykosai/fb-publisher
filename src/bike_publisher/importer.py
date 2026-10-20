from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

from .db import Database, utc_now


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def safe_extract(zip_path: Path, destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as archive:
        root = destination.resolve()
        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"Небезопасный путь в архиве: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)
            extracted.append(target)
    return extracted


def parse_info(text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = re.match(r"^(\d+)\s*[-:)]\s*(.*)$", line)
        if match:
            current = match.group(1)
            result[current] = {}
            line = match.group(2).strip()
        if current is None:
            continue
        _parse_line_into(result[current], line)
    return result


def _parse_line_into(fields: dict[str, str], line: str) -> None:
    """Extract independent price, wheel and frame values from free-form notes.

    Examples supported: ``kola 26 rama ml cena 1700``, ``1300 xl 29`` and
    ``Rama m 559``. Unknown values are ignored instead of being copied into a
    different field.
    """
    normalized = line.lower().replace("ё", "е").replace("ł", "l")
    normalized = re.sub(r"[,;]", " ", normalized)
    wheel_match = re.search(r"(?:колес(?:о|а)|kola|koła|wheel(?:s)?)\s*[:=\-]?\s*(26|27(?:[.,]5)?|28|29)\b", normalized)
    frame_match = re.search(r"(?:рама|rama|frame)\s*[:=\-]?\s*(xxl|xl|ml|xs|s|m|l)\b", normalized)
    price_match = re.search(r"(?:цена|cena|price)\s*[:=\-]?\s*(\d+(?:[.,]\d+)?)\b", normalized)
    if wheel_match:
        fields["wheels"] = wheel_match.group(1).replace(",", ".")
    if frame_match:
        fields["frame"] = frame_match.group(1).upper()
    if price_match:
        fields["price"] = price_match.group(1).replace(",", ".")

    # Compact notes often omit labels. A 26/27.5/28/29 token is a wheel size,
    # a size letter is a frame, and a remaining 3+ digit number is the price.
    if "wheels" not in fields:
        compact_wheel = re.search(r"\b(26|27(?:[.,]5)?|28|29)\b", normalized)
        if compact_wheel:
            fields["wheels"] = compact_wheel.group(1).replace(",", ".")
    if "frame" not in fields:
        compact_frame = re.search(r"\b(xxl|xl|ml|xs|s|m|l)\b", normalized)
        if compact_frame:
            fields["frame"] = compact_frame.group(1).upper()
    if "price" not in fields:
        numbers = re.findall(r"\b\d{3,5}(?:[.,]\d+)?\b", normalized)
        if numbers:
            fields["price"] = numbers[-1].replace(",", ".")


def _default_name(fields: dict[str, str], number: str) -> str:
    return f"Велосипед №{number}"


def _read_bike_description(bike_dir: Path) -> str:
    description_file = next(
        (path for path in bike_dir.iterdir() if path.suffix.lower() == ".txt"),
        None,
    )
    if description_file is None:
        return ""
    raw = description_file.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1251", errors="replace")
    lines = [line.replace("ㅤ", "").rstrip() for line in text.splitlines()]
    normalized = re.sub(r"\n{3,}", "\n\n", "\n".join(lines).strip())
    return normalized.replace("**", "").replace("dojazды", "dojazdy")


def import_zip(zip_path: Path, data_dir: Path, db: Database) -> dict[str, int]:
    incoming = data_dir / "incoming" / zip_path.stem
    if incoming.exists():
        shutil.rmtree(incoming)
    files = safe_extract(zip_path, incoming)
    info_file = next((path for path in files if path.name.lower() == "info.txt"), None)
    info = parse_info(info_file.read_text(encoding="utf-8", errors="replace")) if info_file else {}
    bike_dirs = sorted({path.parent for path in files if path.suffix.lower() in IMAGE_EXTENSIONS}, key=lambda p: p.name)
    created = 0
    photos = 0
    descriptions = 0
    for bike_dir in bike_dirs:
        number = bike_dir.name
        if not number.isdigit():
            continue
        fields = info.get(number, {})
        description = _read_bike_description(bike_dir)
        descriptions += bool(description)
        now = utc_now()
        account = db.get_setting('facebook_account_id', '')
        scoped_number = f'zip:{account}:{number}' if account else number
        existing = db.one("SELECT id FROM bikes WHERE external_id IN (?,?) AND owner_account_id=?", (number, scoped_number, account))
        if existing:
            bike_id = existing["id"]
            updates = {"name": _default_name(fields, number), "updated_at": now}
            updates.update({key: value for key, value in fields.items() if key in {"price", "frame", "wheels"} and value})
            if description:
                updates["description"] = description
            assignments = ",".join(f"{column}=?" for column in updates)
            db.execute(
                f"UPDATE bikes SET {assignments} WHERE id=?",
                (*updates.values(), bike_id),
            )
        else:
            external_id = scoped_number if db.one('SELECT id FROM bikes WHERE external_id=?', (number,)) else number
            bike_id = db.execute("INSERT INTO bikes(external_id,name,price,description,frame,wheels,note,created_at,updated_at,owner_account_id) VALUES(?,?,?,?,?,?,?,?,?,?)", (external_id, _default_name(fields, number), fields.get("price", ""), description, fields.get("frame", ""), fields.get("wheels", ""), "Импортировано из veliwer.zip", now, now, account))
            created += 1
        images = sorted((p for p in bike_dir.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS), key=lambda p: p.name)
        photo_folder = data_dir / 'photos' / f'product-{bike_id}'
        if existing:
            previous = db.one('SELECT path FROM photos WHERE bike_id=? ORDER BY id LIMIT 1', (bike_id,))
            if previous and Path(previous['path']).resolve().is_relative_to((data_dir / 'photos').resolve()):
                # Reuse legacy numbered folders; reimport must not duplicate every photo.
                photo_folder = Path(previous['path']).parent
        for index, image in enumerate(images):
            target = photo_folder / image.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(image, target)
            db.execute("INSERT OR IGNORE INTO photos(bike_id,path,sort_order) VALUES(?,?,?)", (bike_id, str(target), index))
            photos += 1
    db.log(
        "import",
        f"Импортирован архив {zip_path.name}: велосипедов создано {created}, "
        f"описаний обновлено {descriptions}, фотографий обработано {photos}",
    )
    return {
        "bikes_created": created,
        "descriptions": descriptions,
        "photos": photos,
        "files": len(files),
    }
