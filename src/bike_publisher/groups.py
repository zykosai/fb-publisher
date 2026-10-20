from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from urllib.parse import quote, unquote, urlsplit

from .db import Database, utc_now


def clean_group_name(value: str) -> str:
    """Remove Facebook accessibility/activity labels, not words in group titles."""
    value = ' '.join(value.split())
    value = re.sub(r'^(?:(?:Фото профиля|Profile picture(?: of)?|Zdjęcie profilowe)(?:\s*[:：]\s*|\s+))+', '', value, flags=re.I)
    value = re.sub(r'[.·]\s*\d+\s*(?:ч\.?|мин\.?|hr|hrs|h|min)\s*$', '', value, flags=re.I)
    return ' '.join(value.split()).strip()


@dataclass(frozen=True)
class SearchPlan:
    city: str
    radius_min_km: int
    radius_max_km: int
    queries: tuple[str, ...]


def make_search_plan(
    city: str = "Poznań",
    radius_min_km: int = 15,
    radius_max_km: int = 200,
    nearby_cities: tuple[str, ...] | list[str] = (),
    region: str = '',
    topics: tuple[str, ...] | list[str] = (),
) -> SearchPlan:
    city = " ".join((city or "Poznań").split())
    radius_min_km = max(0, int(radius_min_km))
    radius_max_km = max(radius_min_km, int(radius_max_km))
    standard_topics = (
        'ogłoszenia', 'oddam sprzedam', 'kupię sprzedam', 'rowery',
        'oddam za darmo', 'rowery ogłoszenia', 'kupię sprzedam rower',
        'sprzedam rower używany', 'kupno sprzedaż', 'oddám sprzedám',
        "grupa rowerowa",
        "rowery kupię sprzedam",
        "rowery używane",
        "giełda rowerowa",
        "sprzedam rower",
        "kupię rower",
        "rowery okolice",
        "куплю продам велосипед",
        "продам велосипед",
        "велосипеды купить продать",
        "велосипедная барахолка",
    )
    chosen_topics = tuple(dict.fromkeys([*standard_topics, *topics]))
    raw_cities = tuple(dict.fromkeys([city, *(" ".join(item.split()) for item in nearby_cities if item.strip())]))
    def unaccent(value: str) -> str:
        decomposed=unicodedata.normalize('NFKD',value)
        return ''.join(ch for ch in decomposed if not unicodedata.combining(ch)).replace('ł','l')
    cities=tuple(dict.fromkeys(value for place in raw_cities for value in (place,unaccent(place)) if value))
    region_variants=tuple(dict.fromkeys((region,unaccent(region)))) if region else ()
    places = tuple(dict.fromkeys([*(f"{place} {area}".strip() for place in cities for area in region_variants), *cities]))
    queries = tuple(dict.fromkeys(f"{topic} {place}".strip() for topic in chosen_topics for place in places))
    return SearchPlan(city, radius_min_km, radius_max_km, queries)


def normalize_group_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"}:
        return ""
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host not in {"facebook.com", "m.facebook.com", "mbasic.facebook.com", "web.facebook.com"}:
        return ""
    match = re.match(r"^/groups/([^/?#]+)(?:/.*)?$", parsed.path, flags=re.I)
    if not match:
        return ""
    group_id_or_slug = unquote(match.group(1)).casefold()
    if group_id_or_slug in {"create", "discover", "feed", "search", "joins", "join", "notifications", "manage", "your_groups", "categories"}:
        return ""
    return f"https://www.facebook.com/groups/{quote(group_id_or_slug, safe='-_.')}"


def save_group(
    db: Database,
    name: str,
    url: str,
    city: str,
    distance_km: float,
    language: str,
) -> tuple[str, int | None]:
    """Add a group, or reactivate a previously hidden group with history."""
    clean_name = clean_group_name(name or '')
    clean_url = normalize_group_url(url)
    if not clean_name or not clean_url:
        raise ValueError("Укажи название и прямую ссылку Facebook-группы вида facebook.com/groups/…")

    alias=db.one('SELECT identity FROM group_aliases WHERE alias=?',(clean_url,))
    identity=alias['identity'] if alias else clean_url
    existing = db.one(
        "SELECT id,name,archived,catalog FROM groups_ WHERE url=? OR identity=? ORDER BY catalog DESC,id LIMIT 1",
        (clean_url,identity),
    )
    now = utc_now()
    from .account_groups import attach_group
    if existing:
        attach_group(db, existing['id'])
        if not existing["archived"] and existing['catalog']:
            previous_name = clean_group_name(existing['name'])
            if previous_name and previous_name != existing['name']:
                db.execute('UPDATE groups_ SET name=?,updated_at=? WHERE id=?', (previous_name, now, existing['id']))
            return "exists", existing["id"]
        db.execute(
            "UPDATE groups_ SET name=?,city=?,distance_km=?,language=?,enabled=0,"
            "archived=0,catalog=1,updated_at=? WHERE id=?",
            (clean_name, city, distance_km, language, now, existing["id"]),
        )
        db.log("group_reactivated", f"Скрытая группа возвращена в список отключённой: {clean_name}")
        return "reactivated", existing["id"]

    group_id = db.execute(
        "INSERT INTO groups_(name,url,city,distance_km,language,created_at,updated_at,identity) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (clean_name, clean_url, city, distance_km, language, now, now,clean_url),
    )
    db.log("group_added", clean_name)
    attach_group(db, group_id)
    return "added", group_id


def remove_groups(db: Database, group_ids: list[int]) -> dict[str, int]:
    """Remove groups from active use while preserving any linked queue history."""
    ids = list(dict.fromkeys(int(group_id) for group_id in group_ids))
    result = {"deleted": 0, "archived": 0, "cancelled_jobs": 0, "running_jobs": 0}
    if not ids:
        return result
    with db.connect() as conn:
        for group_id in ids:
            row = conn.execute(
                "SELECT id FROM groups_ WHERE id=? AND archived=0", (group_id,)
            ).fetchone()
            if not row:
                continue
            result["cancelled_jobs"] += conn.execute(
                "UPDATE queue SET status='cancelled',last_error='Группа удалена из списка',"
                "updated_at=? WHERE group_id=? AND mode='single' AND status IN ('pending','waiting_approval')",
                (utc_now(), group_id),
            ).rowcount
            result["running_jobs"] += conn.execute(
                "SELECT COUNT(*) FROM queue WHERE group_id=? AND status='running'",
                (group_id,),
            ).fetchone()[0]
            references = conn.execute(
                "SELECT COUNT(*) FROM queue WHERE group_id=?", (group_id,)
            ).fetchone()[0]
            references += conn.execute('SELECT COUNT(*) FROM joined_groups WHERE group_id=?',(group_id,)).fetchone()[0]
            references += conn.execute('SELECT COUNT(*) FROM deliveries WHERE group_id=?',(group_id,)).fetchone()[0]
            if references:
                conn.execute(
                    "UPDATE groups_ SET enabled=0,archived=1,catalog=0,updated_at=? WHERE id=?",
                    (utc_now(), group_id),
                )
                result["archived"] += 1
            else:
                conn.execute("DELETE FROM groups_ WHERE id=?", (group_id,))
                result["deleted"] += 1
    removed = result["deleted"] + result["archived"]
    if removed:
        db.log(
            "groups_removed",
            f"Из активного списка убрано групп: {removed}; скрыто с историей: "
            f"{result['archived']}; отменено ожидающих заданий: {result['cancelled_jobs']}",
        )
    return result


def restore_groups(db: Database, group_ids: list[int]) -> int:
    ids = list(dict.fromkeys(int(group_id) for group_id in group_ids))
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    with db.connect() as conn:
        changed = conn.execute(
            f"UPDATE groups_ SET archived=0,enabled=0,updated_at=? "
            f"WHERE archived=1 AND id IN ({placeholders})",
            (utc_now(), *ids),
        ).rowcount
    if changed:
        db.log("groups_restored", f"Восстановлено групп из архива: {changed}")
    return int(changed)


def group_relevance(name: str, city: str) -> int:
    def fold(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value.casefold())
        return "".join(char for char in normalized if not unicodedata.combining(char)).replace("ł", "l")

    label = fold(name)
    city_key = fold(city.strip())
    bike_terms = (
        "rower", "rowery", "rowerow", "rowerowa", "велосипед", "велики", "bike",
    )
    trade_terms = (
        "sprzed", "sprzedaz", "kupie", "kupno", "sprzeda", "продам", "продажа", "куплю",
    )
    score = 5 * sum(term in label for term in bike_terms)
    score += 2 * sum(term in label for term in trade_terms)
    if city_key and city_key in label:
        score += 3
    return score
