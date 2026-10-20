from __future__ import annotations

import importlib
import base64
from io import BytesIO
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import bike_publisher.config as config_module

# Streamlit reruns this file but may retain imported modules. Reload config so
# changed profile paths and settings fields take effect without a server reboot.
config_module = importlib.reload(config_module)
Settings = config_module.Settings
ensure_dirs = config_module.ensure_dirs

import bike_publisher.db as db_module
db_module = importlib.reload(db_module)
Database = db_module.Database
utc_now = db_module.utc_now

import bike_publisher.groups as groups_module
groups_module = importlib.reload(groups_module)
make_search_plan = groups_module.make_search_plan
normalize_group_url = groups_module.normalize_group_url
remove_groups = groups_module.remove_groups
save_group = groups_module.save_group
from bike_publisher.delivery import finish_delivery, group_gate
from bike_publisher.joined_groups import save_snapshot as save_joined_snapshot, update_permissions as update_join_permissions

import bike_publisher.facebook as facebook_module
facebook_module = importlib.reload(facebook_module)
FacebookPublisher = facebook_module.FacebookPublisher

import bike_publisher.account as account_module
account_module = importlib.reload(account_module)
save_account_status = account_module.save_account_status
activity_summary = account_module.activity_summary

import bike_publisher.worker_process as worker_process_module
worker_process_module = importlib.reload(worker_process_module)
ensure_worker_running = worker_process_module.ensure_worker_running
worker_state = worker_process_module.worker_state

from bike_publisher.importer import import_zip
from bike_publisher.photo_sorter import photo_sorter
import bike_publisher.photos as photos_module
photos_module = importlib.reload(photos_module)
add_photos = photos_module.add_photos
archive_photo = photos_module.archive_photo
reorder_photos = photos_module.reorder_photos
import bike_publisher.queue as queue_module
queue_module = importlib.reload(queue_module)
add_plan = queue_module.add_plan
approve_jobs = queue_module.approve_jobs
cancel_job = queue_module.cancel_job


settings = Settings.from_root(ROOT)
ensure_dirs(settings)
db = Database(settings.db_path)

st.set_page_config(page_title="FB Publisher", page_icon="🚲", layout="wide")
st.markdown("""
<style>
:root { color-scheme: dark; }
.block-container { max-width: 1320px; padding-top: 1.4rem; }
[data-testid="stMetric"] { background:#182131; border:1px solid #2b3950; border-radius:12px; padding:12px; }
button[kind="primary"] { border-radius:9px; }
</style>
""", unsafe_allow_html=True)


def bikes():
    return db.all("SELECT * FROM bikes WHERE status='active' ORDER BY id")


def groups():
    return db.all("SELECT * FROM groups_ WHERE archived=0 AND catalog=1 ORDER BY name COLLATE NOCASE")


@st.cache_data(show_spinner=False,max_entries=512)
def _photo_thumbnail_cached(path: str,mtime_ns:int,size:int) -> str:
    """Make a small inline preview for the local drag-and-drop component."""
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((240, 180))
            output = BytesIO()
            image.save(output, format="JPEG", quality=62, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    except (OSError, ValueError):
        return ""


def photo_thumbnail(path: str) -> str:
    try:
        stat=Path(path).stat()
        return _photo_thumbnail_cached(path,stat.st_mtime_ns,stat.st_size)
    except OSError:
        return ""


def bike_editor():
    st.title("Велосипеды")
    st.caption("Каталог, цены, описание и порядок фотографий хранятся в локальной базе проекта.")
    uploaded = st.file_uploader("Импортировать ZIP с велосипедами", type=["zip"], key="import_zip")
    if uploaded and st.button("Импортировать архив", type="primary"):
        target = settings.data_dir / "incoming" / uploaded.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(uploaded.getvalue())
        result = import_zip(target, settings.data_dir, db)
        st.success(f"Импортировано: создано велосипедов {result['bikes_created']}, обновлено описаний {result['descriptions']}, обработано фотографий {result['photos']}.")
    rows = bikes()
    if not rows:
        st.info("Каталог пуст. Загрузи ZIP с фотографиями и info.txt.")
        return
    st.caption("Выбери велосипед, чтобы отредактировать цену, описание и порядок фото.")
    primary_photos={}
    for photo in db.all("SELECT p.bike_id,p.path FROM photos p JOIN bikes b ON b.id=p.bike_id WHERE p.archived=0 AND b.status='active' ORDER BY p.bike_id,p.sort_order,p.id"):
        primary_photos.setdefault(photo['bike_id'],photo['path'])
    columns=st.columns(3)
    for row in rows:
        price_label = f"{row['price']} zł" if row["price"] else "цена не задана"
        with columns[(row['id']-1)%3]:
            with st.container(border=True):
                primary=primary_photos.get(row['id'])
                if primary:
                    preview=photo_thumbnail(primary)
                    if preview: st.image(preview,use_container_width=True)
                st.markdown(f"**{row['name']}** · {price_label}")
                snippet=' '.join((row['description'] or '').split())
                if snippet: st.caption(snippet[:120]+('…' if len(snippet)>120 else ''))
                if st.button("Редактировать",key=f"open_bike_{row['id']}",use_container_width=True):
                    st.session_state['selected_bike_id']=row['id']
                    st.rerun()
    selected_id=st.session_state.get('selected_bike_id')
    selected=next((item for item in rows if item['id']==selected_id),None)
    if selected is None:
        st.info("Выбери велосипед в каталоге, чтобы открыть редактор.")
        return
    st.subheader(f"Редактирование · {selected['name']}")
    if st.button("Закрыть редактор",key="close_bike_editor"):
        st.session_state.pop('selected_bike_id',None)
        st.rerun()
    for row in rows:
        if row['id']!=selected_id:
            continue
        price_label = f"{row['price']} zł" if row["price"] else "цена не задана"
        with st.expander(f"{row['name']}  ·  {price_label}", expanded=False):
            bike_id = row["id"]
            generation_key = f"photo_upload_generation_{bike_id}"
            upload_generation = st.session_state.get(generation_key, 0)
            uploaded_photos = st.file_uploader(
                "Добавить фото к этому велосипеду",
                type=["jpg", "jpeg", "png", "webp"],
                accept_multiple_files=True,
                key=f"bike_photo_upload_{bike_id}_{upload_generation}",
                help="JPG, PNG или WebP, до 30 МБ на фото. Можно выбрать несколько; они добавятся в конец списка.",
            )
            if st.button(
                "Добавить выбранные фото",
                key=f"add_bike_photos_{bike_id}_{upload_generation}",
                disabled=not uploaded_photos,
            ):
                try:
                    added = add_photos(db, bike_id, settings.data_dir, uploaded_photos)
                except (OSError, ValueError) as error:
                    st.error(str(error) or "Не удалось добавить фотографии.")
                else:
                    db.log("photos_added", f"Добавлено фотографий: {added}, велосипед {row['external_id']}")
                    st.session_state[generation_key] = upload_generation + 1
                    st.session_state["photo_notice"] = f"Добавлено фото: {added}. Они появились в конце списка."
                    st.rerun()
            photo_rows = db.all(
                "SELECT id,path FROM photos WHERE bike_id=? AND archived=0 "
                "ORDER BY sort_order,id",
                (bike_id,),
            )
            st.caption("Перетащи фото на нужное место — порядок сохранится автоматически. Первое фото будет главным.")
            sorter_items = [
                {
                    "id": photo["id"],
                    "position": index + 1,
                    "label": Path(photo["path"]).name,
                    "src": photo_thumbnail(photo["path"]),
                }
                for index, photo in enumerate(photo_rows)
            ]
            photo_action = photo_sorter(sorter_items, key=f"bike_photos_{bike_id}")
            if isinstance(photo_action, dict):
                event_id = photo_action.get("event_id")
                processed_key = f"processed_photo_event_{bike_id}"
                if event_id and st.session_state.get(processed_key) != event_id:
                    st.session_state[processed_key] = event_id
                    try:
                        action = photo_action.get("action")
                        if action == "reorder":
                            changed = reorder_photos(
                                db, bike_id, photo_action.get("photo_ids", [])
                            )
                            if changed:
                                db.log("photos_reordered", f"Изменён порядок фото велосипеда {row['external_id']}")
                        elif action == "archive":
                            if archive_photo(db, bike_id, int(photo_action["photo_id"])):
                                db.log("photo_removed", f"Фото удалено из списка велосипеда {row['external_id']}")
                                st.session_state["photo_notice"] = "Фото удалено из списка."
                        st.rerun()
                    except (ValueError, KeyError, TypeError) as error:
                        st.error(str(error) or "Не удалось изменить фотографии.")
            if st.session_state.get("photo_notice"):
                st.success(st.session_state.pop("photo_notice"))

            with st.form(f"edit_bike_{row['id']}"):
                price = st.text_input("Цена, zł", row["price"])
                description = st.text_area("Описание", row["description"], height=140)
                if st.form_submit_button("Сохранить изменения", type="primary"):
                    db.execute(
                        "UPDATE bikes SET price=?,description=?,updated_at=? WHERE id=?",
                        (price, description, utc_now(), row["id"]),
                    )
                    db.log("bike_updated", f"Изменён велосипед {row['external_id']}")
                    st.rerun()
            if st.button("Удалить велосипед", key=f"delete_bike_{row['id']}"):
                db.execute("UPDATE bikes SET status='deleted',updated_at=? WHERE id=?", (utc_now(), row["id"]))
                db.log("bike_deleted", f"Удалён велосипед {row['external_id']}")
                st.rerun()


def group_editor():
    st.title("Группы")
    for group_id in st.session_state.pop("reset_group_checkbox_ids", []):
        st.session_state.pop(f"group_enabled_{group_id}", None)
    for key in ("groups_to_remove",):
        if st.session_state.pop(f"clear_{key}_selection", False):
            st.session_state[key] = []
    if st.session_state.get("group_management_notice"):
        st.success(st.session_state.pop("group_management_notice"))
    st.caption('Добавь город и темы — поиск объединит варианты запросов и уберёт одинаковые ссылки.')
    c1, c2, c3 = st.columns(3)
    city = c1.text_input("Центр поиска", settings.city)
    city = " ".join(city.split()) or settings.city or "Poznań"
    radius_min = c2.number_input("От, км", min_value=0, max_value=500, value=settings.radius_min)
    radius_max = c3.number_input("До, км", min_value=1, max_value=500, value=settings.radius_max)
    nearby_text=st.text_input('Соседние города и районы (через запятую)',placeholder='Śrem, Gniezno, Swarzędz')
    region=st.text_input('Регион / воеводство',placeholder='Wielkopolskie')
    topic_options=['ogłoszenia','oddam sprzedam','kupię sprzedam','rowery','rowery używane','giełda','kupno sprzedaż','oddam za darmo']
    chosen_topics=st.multiselect('Темы поиска',topic_options,default=['ogłoszenia','oddam sprzedam','rowery'])
    nearby_cities=[value.strip() for value in re.split('[,;\n]',nearby_text) if value.strip()]
    plan = make_search_plan(city, radius_min, radius_max, nearby_cities, region, chosen_topics)
    with st.expander("Поисковые запросы"):
        st.code("\n".join(plan.queries), language="text")
    st.caption(
        f"Facebook не фильтрует результаты поиска по точному радиусу: "
        f"проверь расстояние вручную (ориентир {plan.radius_min_km}–{plan.radius_max_km} км)."
    )
    st.caption(
        "Новые кандидаты добавляются выключенными. Перед включением проверь, "
        "что в группе есть обычное поле публикации, а не только кнопка «Продать»."
    )
    extra_terms_text = st.text_input(
        "Дополнительные ключевые слова (через запятую)",
        placeholder="например: rowery MTB, велосипеды Познань",
    )
    search_limit = st.number_input('Кандидатов за поиск',min_value=10,max_value=100,value=50,step=10)
    with st.form("add_group"):
        name = st.text_input("Название группы")
        url = st.text_input("Ссылка на группу")
        group_city = st.text_input("Город/район", city)
        distance = st.number_input("Расстояние, км", min_value=0.0, max_value=1000.0, value=0.0)
        language = st.selectbox("Язык", ["pl", "ru", "mixed", "unknown"])
        if st.form_submit_button("Добавить группу"):
            try:
                status, saved_group_id = save_group(
                    db, name, url, group_city, distance, language
                )
            except ValueError as error:
                st.error(str(error))
            else:
                if status == "exists":
                    st.warning("Такая группа уже есть в активном списке.")
                elif status == "reactivated":
                    st.session_state.reset_group_checkbox_ids = [saved_group_id]
                    st.session_state.group_management_notice = (
                        "Группа возвращена в список выключенной. Проверь её и включи вручную, "
                        "если в ней можно публиковать обычные записи."
                    )
                    st.rerun()
                else:
                    st.rerun()
    st.subheader(f"Список групп · {len(groups())}")
    with st.expander(f"Каталог · {len(groups())} групп",expanded=True):
      with st.form("groups_table"):
        select_all = st.checkbox("Выбрать все группы")
        chosen = []
        for group in groups():
            checked = st.checkbox(f"{group['name']} · {group['city'] or 'город не указан'} · {group['distance_km'] or '?'} км", value=bool(group["enabled"]), key=f"group_enabled_{group['id']}")
            with st.expander(f"Сведения · {group['name']}"):
                st.markdown(f"[Открыть группу в Facebook]({group['url']})")
                st.caption(group.get('reason') or 'Добавлена вручную')
            if checked or select_all:
                chosen.append(group["id"])
        if st.form_submit_button("Сохранить выбранные"):
            for group in groups():
                db.execute("UPDATE groups_ SET enabled=?,updated_at=? WHERE id=?", (int(group["id"] in chosen), utc_now(), group["id"]))
            st.success("Список обновлён.")

    extra_queries = []
    for term in re.split(r"[,;\n]", extra_terms_text):
        term = " ".join(term.split())
        if term:
            extra_queries.append(
                term
                if city.casefold() in term.casefold()
                else f"{term} {city}"
            )
    search_queries = list(dict.fromkeys([*plan.queries, *extra_queries]))
    if st.button("Найти группы в Facebook", type="primary"):
        with st.spinner("Открываю поиск Facebook и собираю ссылки..."):
            try:
                candidates = FacebookPublisher(settings).discover_groups(
                    search_queries, limit=search_limit, city=city
                )
            except Exception as exc:
                st.error(f"Не удалось выполнить поиск: {exc}")
            else:
                added = 0
                for candidate in candidates:
                    clean = normalize_group_url(candidate["url"])
                    alias=db.one('SELECT identity FROM group_aliases WHERE alias=?',(clean,)) if clean else None
                    identity=alias['identity'] if alias else clean
                    if not clean or db.one("SELECT id FROM groups_ WHERE url=? OR identity=?", (clean,identity)):
                        continue
                    db.execute(
                        "INSERT INTO groups_(name,url,city,language,enabled,archived,created_at,updated_at,identity,reason) "
                        "VALUES(?,?,?,?,0,0,?,?,?,?)",
                        (candidate["name"], clean, city, "unknown", utc_now(), utc_now(),clean,candidate.get('reason','')),
                    )
                    added += 1
                db.log("groups_discovered", f"Найдено кандидатов: {len(candidates)}, добавлено новых: {added}")
                st.session_state.group_management_notice = (
                    f"Найдено кандидатов: {len(candidates)}; добавлено новых: {added}. "
                    "Новые группы отключены до проверки."
                )
                st.rerun()

    active_groups = groups()
    if active_groups:
        st.subheader("Удаление групп")
        active_by_id = {group["id"]: group for group in active_groups}
        selected_for_removal = st.multiselect(
            "Выбери группы для удаления",
            options=list(active_by_id),
            format_func=lambda group_id: (
                f"{active_by_id[group_id]['name']} · "
                f"{active_by_id[group_id]['city'] or 'город не указан'}"
            ),
            key="groups_to_remove",
        )
        if st.button(
            "Удалить выбранные группы",
            disabled=not selected_for_removal,
            key="remove_selected_groups",
        ):
            result = remove_groups(db, selected_for_removal)
            st.session_state.reset_group_checkbox_ids = selected_for_removal
            st.session_state.clear_groups_to_remove_selection = True
            st.session_state.group_management_notice = (
                f"Убрано из списка: {result['deleted'] + result['archived']}; "
                f"без истории удалено: {result['deleted']}; групп с историей скрыто: "
                f"{result['archived']}; ожидающих заданий отменено: "
                f"{result['cancelled_jobs']}."
                + (
                    f" Уже выполняются и не остановлены: {result['running_jobs']}."
                    if result["running_jobs"] else ""
                )
            )
            st.rerun()

        if st.session_state.get("confirm_clear_all_groups"):
            st.warning(
                f"Убрать все {len(active_groups)} активных групп? Ожидающие задания "
                "будут отменены; история публикаций и журнала сохранится."
            )
            confirm_col, cancel_col = st.columns(2)
            if confirm_col.button(
                "Да, убрать все группы", type="primary", key="confirm_clear_all_groups_yes"
            ):
                ids = [group["id"] for group in active_groups]
                result = remove_groups(db, ids)
                st.session_state.reset_group_checkbox_ids = ids
                st.session_state.clear_groups_to_remove_selection = True
                st.session_state.confirm_clear_all_groups = False
                st.session_state.group_management_notice = (
                    f"Все группы убраны из активного списка. Без истории удалено: "
                    f"{result['deleted']}; групп с историей скрыто: {result['archived']}; "
                    f"ожидающих заданий отменено: {result['cancelled_jobs']}."
                    + (
                        f" Уже выполняются и не остановлены: {result['running_jobs']}."
                        if result["running_jobs"] else ""
                    )
                )
                st.rerun()
            if cancel_col.button("Не очищать", key="cancel_clear_all_groups"):
                st.session_state.confirm_clear_all_groups = False
                st.rerun()
        elif st.button("Очистить все группы", key="start_clear_all_groups"):
            st.session_state.confirm_clear_all_groups = True
            st.rerun()

@st.dialog("Мои группы", width="large")
def joined_groups_dialog():
    account_id=db.get_setting('facebook_account_id','')
    if not account_id:
        st.warning('Сначала войди в Facebook и проверь подключение в настройках.')
        return
    st.caption('Этот список взят из групп, на которые подписан твой Facebook. Он не смешивается с найденными группами и ссылками.')
    search=st.text_input('Найти в моих группах',key='joined_group_search')
    c1,c2=st.columns([3,2])
    if c1.button('Обновить список из Facebook',type='primary',use_container_width=True):
        try:
            with st.spinner('Читаю список групп Facebook…'):
                rows=FacebookPublisher(settings).discover_my_groups()
                count=save_joined_snapshot(db,account_id,rows)
            st.success(f'Обновлено групп: {count}')
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
    stored=db.all("SELECT g.id,g.name,g.url,j.allowed,j.present,j.seen_at FROM joined_groups j JOIN groups_ g ON g.id=j.group_id WHERE j.account_id=? AND j.present=1 ORDER BY g.name COLLATE NOCASE",(account_id,))
    if not stored:
        st.info('Нажми «Обновить список из Facebook», чтобы загрузить свои группы.')
        return
    visible=[row for row in stored if search.casefold() in row['name'].casefold()]
    st.caption(f'Разрешено публикаций: {sum(bool(row["allowed"]) for row in stored)} из {len(stored)}')
    with st.form('joined_group_permissions'):
        chosen=[]
        for row in visible:
            gate=group_gate(db,row['id'])
            label=row['name'] + (f' · {gate["reason"]}' if not gate['allowed'] else '')
            if st.checkbox(label,value=bool(row['allowed']),key=f"join_allow_{account_id}_{row['id']}"):
                chosen.append(row['id'])
        if st.form_submit_button('Сохранить разрешённые группы',type='primary'):
            previous={row['id'] for row in stored if row['allowed']}
            # Search filtering must not silently clear choices outside the current result.
            chosen.extend(previous-{row['id'] for row in visible})
            update_join_permissions(db,account_id,chosen)
            st.success('Выбор сохранён.')
            st.rerun()


def queue_page():
    title_col, tools_col=st.columns([7,1])
    title_col.title("Публикации")
    with tools_col:
        with st.popover("⋯",help="Технический журнал worker"):
            worker_log=settings.data_dir/"logs"/"worker.log"
            if worker_log.exists():
                try:
                    lines=worker_log.read_text(encoding="utf-8",errors="replace").splitlines()
                    st.code("\n".join(lines[-60:]) or "Журнал пока пуст.",language="text")
                except OSError as exc:
                    st.caption(f"Не удалось прочитать журнал: {exc}")
            else:
                st.caption("Worker ещё не создавал технический журнал.")
    st.caption('Подготовь объявление, выбери способ и группы — бот покажет статус каждой отправки.')
    bs = bikes()
    gs = [g for g in groups() if g["enabled"]]
    catalog_gates={g['id']:group_gate(db,g['id']) for g in gs}
    available_catalog=[g for g in gs if catalog_gates[g['id']]['allowed']]
    if st.button('Мои группы · функция Facebook «Добавить группы»'):
        joined_groups_dialog()
    account_id=db.get_setting('facebook_account_id','')
    join_rows=db.all("SELECT g.id,g.name,j.allowed,j.present FROM joined_groups j JOIN groups_ g ON g.id=j.group_id WHERE j.account_id=? AND j.present=1 ORDER BY g.name COLLATE NOCASE",(account_id,)) if account_id else []
    if join_rows:
        st.caption(f'Мои группы: {sum(bool(row["allowed"]) for row in join_rows)} разрешено из {len(join_rows)}. Этот список хранится отдельно от каталога групп.')
    if not bs or (not gs and not join_rows):
        st.warning("Сначала добавь хотя бы один велосипед и одну включённую группу.")
    mode_label=st.radio(
        'Способ публикации',
        ['Обычная публикация в группу','Facebook «Добавить группы»'],
        horizontal=True,
        key='publish_mode',
    )
    joined_mode=mode_label.startswith('Facebook')
    if joined_mode:
        st.info('Выбирай группы ниже. Бот откроет одну публикацию и добавит остальные через Facebook «Добавить группы».')
    with st.form("queue_plan"):
        all_bikes = st.checkbox("Выбрать все велосипеды")
        bike_ids = st.multiselect("Велосипеды", options=[b["id"] for b in bs], default=[b["id"] for b in bs] if all_bikes else [], format_func=lambda value: next(b["name"] for b in bs if b["id"] == value))
        if joined_mode:
            # A single multiselect is less error-prone than dozens of
            # independent form checkboxes: the exact IDs submitted to the
            # queue are visible as chips before the user starts publishing.
            eligible_joined=[]; blocked_joined=[]
            for row in join_rows:
                gate=group_gate(db,row['id'])
                if row['allowed'] and row['present'] and gate['allowed']:
                    eligible_joined.append(row)
                else:
                    reason=('сначала разреши публикации в «Мои группы»' if not row['allowed'] else gate['reason'])
                    blocked_joined.append(f"{row['name']} · {reason}")
            select_all_joined=st.checkbox(
                f'Выбрать все разрешённые группы ({len(eligible_joined)})',
                key=f'joined_select_all_{account_id}',
                help='Если групп больше лимита Facebook, worker отправит их несколькими пакетами.',
            )
            group_ids=st.multiselect(
                'Группы для этой отправки',
                options=[row['id'] for row in eligible_joined],
                default=[row['id'] for row in eligible_joined] if select_all_joined else [],
                format_func=lambda value: next(row['name'] for row in eligible_joined if row['id']==value),
                key=f'joined_send_{account_id}',
                help='Можно выбрать несколько групп. Facebook сам разобьёт их на пакеты по своему текущему лимиту.',
            )
            if blocked_joined:
                with st.expander(f'Недоступные группы ({len(blocked_joined)})'):
                    st.caption('\n'.join(blocked_joined))
            st.caption(f'Выбрано для этой отправки: {len(group_ids)} групп')
        else:
            blocked=[g for g in gs if not catalog_gates[g['id']]['allowed']]
            if blocked:
                st.caption('Недоступны на 30 минут или требуют проверки: '+', '.join(f"{g['name']} ({catalog_gates[g['id']]['reason']})" for g in blocked[:6]))
            all_groups = st.checkbox("Выбрать все доступные группы")
            group_ids = st.multiselect("Группы из каталога", options=[g["id"] for g in available_catalog], default=[g["id"] for g in available_catalog] if all_groups else [], format_func=lambda value: next(g["name"] for g in available_catalog if g["id"] == value))
        start_date = st.date_input("Дата", date.today())
        local_now = datetime.now().astimezone()
        start_time = st.time_input(
            "Первое объявление",
            value=local_now.time().replace(second=0, microsecond=0, tzinfo=None),
        )
        delay = st.number_input("Интервал между велосипедами, минут (минимум 30)", min_value=30, max_value=1440, value=max(30,settings.default_delay_minutes), step=10)
        manual_approval = st.checkbox("Требовать ручное подтверждение каждого задания")
        c1, c2 = st.columns(2)
        publish_now = c1.form_submit_button("Опубликовать сейчас", type="primary", use_container_width=True)
        schedule = c2.form_submit_button("Запланировать", use_container_width=True)
        if publish_now or schedule:
            selected_bikes = [b["id"] for b in bs] if all_bikes else bike_ids
            selected_groups = group_ids if joined_mode else ([g["id"] for g in available_catalog] if all_groups else group_ids)
            if not selected_bikes or not selected_groups:
                st.error("Выбери велосипеды и группы.")
            elif joined_mode and (len(selected_bikes)!=1 or manual_approval):
                st.error('Для публикации через «Добавить группы» выбери один велосипед. Ручное подтверждение этой функции пока не поддерживается.')
            else:
                if publish_now:
                    start = datetime.now(timezone.utc)
                    approval_required = False
                else:
                    local_zone = datetime.now().astimezone().tzinfo
                    start = datetime.combine(start_date, start_time, tzinfo=local_zone)
                    start = start.astimezone(timezone.utc)
                    approval_required = manual_approval
                if joined_mode:
                    now=datetime.now(timezone.utc)
                    selected_groups=[group_id for group_id in selected_groups if group_gate(db,group_id,now=now)['allowed']]
                    if not selected_groups:
                        st.error('Все выбранные группы пока закрыты интервалом или требуют проверки.')
                        st.stop()
                count = add_plan(
                    db,
                    selected_bikes,
                    selected_groups,
                    start,
                    delay,
                    approval_required,
                    mode='joined_batch' if joined_mode else 'single',
                )
                try:
                    state, _ = ensure_worker_running(settings)
                except Exception as exc:
                    db.log("worker_start_failed", str(exc)[:1000])
                    st.error(
                        f"Задания добавлены, но публикация не запустилась: {exc}"
                    )
                else:
                    if publish_now:
                        action = "Chrome откроется в течение нескольких секунд."
                    else:
                        action = "Worker дождётся выбранного времени."
                    st.success(
                        f"Добавлено заданий: {count}. {action} "
                        f"Worker запущен (PID {state.pid})."
                    )
    st.subheader("Очередь")
    action_delay = getattr(settings, "action_delay_seconds", 2.5)
    between_posts_delay = getattr(settings, "between_posts_seconds", 60)
    st.caption(
        f"Темп: паузы по {action_delay:g} с между действиями "
        f"формы и {between_posts_delay} с между заданиями."
    )
    state = worker_state(settings)
    pending = db.one("SELECT COUNT(*) count FROM queue WHERE status='pending'")["count"]
    if state.running:
        st.success(f"Публикация запущена · worker PID {state.pid}")
    elif pending:
        st.warning("В очереди есть задания, но публикация остановлена.")
        if st.button("Запустить публикацию", type="primary"):
            try:
                state, _ = ensure_worker_running(settings)
            except Exception as exc:
                db.log("worker_start_failed", str(exc)[:1000])
                st.error(f"Не удалось запустить публикацию: {exc}")
            else:
                db.log("worker_started", f"Worker запущен из панели, PID {state.pid}")
                st.rerun()
    else:
        st.caption("Worker запустится автоматически после создания объявления.")
    queue_rows = db.all("SELECT q.id,b.name велосипед,g.name группа,q.scheduled_at,q.status,q.attempts,q.last_error,q.mode FROM queue q JOIN bikes b ON b.id=q.bike_id JOIN groups_ g ON g.id=q.group_id ORDER BY q.scheduled_at,q.id")
    labels={'pending':'Ожидает','waiting_approval':'Нужно подтвердить','running':'Отправляется','sent':'Отправлено','published':'Опубликовано','moderation':'Ждёт одобрения','unknown':'Проверь результат','failed':'Ошибка','skipped':'Пропущено','cancelled':'Отменено'}
    def present_queue_row(row):
        return {'№':row['id'],'Велосипед':row['велосипед'],'Группа':row['группа'],'Время':polish_time(row['scheduled_at']),'Статус':labels.get(row['status'],row['status']),'Способ':'Через «Добавить группы»' if row['mode']=='joined_batch' else 'По отдельной группе','Причина':row['last_error'] or ''}
    active=[present_queue_row(row) for row in queue_rows if row['status'] in ('pending','waiting_approval','running')]
    attention=[present_queue_row(row) for row in queue_rows if row['status'] in ('failed','unknown','moderation')]
    history=[present_queue_row(row) for row in queue_rows if row['status'] in ('sent','published','cancelled','skipped')]
    with st.expander(f'Активные · {len(active)}',expanded=True): st.dataframe(active,width='stretch',hide_index=True)
    with st.expander(f'Требуют внимания · {len(attention)}',expanded=bool(attention)): st.dataframe(attention,width='stretch',hide_index=True)
    with st.expander(f'История · {len(history)}'): st.dataframe(history,width='stretch',hide_index=True)
    unknown_jobs=[row for row in queue_rows if row['status']=='unknown']
    if unknown_jobs:
        st.subheader('Проверить неизвестный результат')
        st.caption('Только читает целевую группу. Повторная публикация не запускается.')
        unknown_by_id={row['id']:row for row in unknown_jobs}
        unknown_id=st.selectbox('Задание для проверки',options=list(unknown_by_id),format_func=lambda value:f"#{value} · {unknown_by_id[value]['велосипед']} → {unknown_by_id[value]['группа']}",key='unknown_delivery_check')
        if st.button('Проверить в Facebook',disabled=state.running,key='check_unknown_delivery'):
            try:
                with st.spinner('Проверяю публикацию в целевой группе…'):
                    delivery_job=queue_module.get_job(db,unknown_id)
                    result,url,detail=FacebookPublisher(settings).check_delivery(delivery_job)
                if result in ('published','sent','moderation'):
                    finish_delivery(db,delivery_job,result,url,detail)
                    st.success(f"Результат подтверждён: {detail}")
                else:
                    db.execute('UPDATE queue SET last_error=?,updated_at=? WHERE id=?',(detail,utc_now(),unknown_id))
                    st.warning(f"Пока не подтверждено. Повтор заблокирован. {detail}")
            except Exception as exc:
                st.error(f"Проверка не выполнена: {exc}")
            st.rerun()
    cancelable_jobs = db.all(
        "SELECT q.id,b.name bike_name,g.name group_name,q.scheduled_at,q.status "
        "FROM queue q JOIN bikes b ON b.id=q.bike_id "
        "JOIN groups_ g ON g.id=q.group_id "
        "WHERE q.status IN ('pending','waiting_approval') "
        "ORDER BY q.scheduled_at,q.id"
    )
    st.subheader("Отменить запланированную отправку")
    st.caption(
        "Отменить можно до начала публикации. Задание со статусом «Выполняется» "
        "уже запущено и здесь не отменяется."
    )
    if cancelable_jobs:
        cancelable_by_id = {row["id"]: row for row in cancelable_jobs}
        status_labels = {
            "pending": "ожидает отправки",
            "waiting_approval": "ожидает подтверждения",
        }
        selected_job_id = st.selectbox(
            "Задание",
            options=list(cancelable_by_id),
            format_func=lambda job_id: (
                f"#{job_id} · {cancelable_by_id[job_id]['bike_name']} → "
                f"{cancelable_by_id[job_id]['group_name']} · "
                f"{cancelable_by_id[job_id]['scheduled_at']} · "
                f"{status_labels[cancelable_by_id[job_id]['status']]}"
            ),
            key="cancel_queue_job_select",
        )
        if st.button("Отменить выбранную отправку", key="cancel_queue_job"):
            if cancel_job(db, selected_job_id):
                st.success(f"Запланированная отправка #{selected_job_id} отменена.")
                st.rerun()
            else:
                st.warning(
                    "Не удалось отменить: задание уже запущено или завершено."
                )
    else:
        st.caption("Нет запланированных заданий, которые можно отменить.")
    retry_candidates=db.all("SELECT q.id,g.id group_id,b.name bike_name,g.name group_name,q.last_error FROM queue q JOIN bikes b ON b.id=q.bike_id JOIN groups_ g ON g.id=q.group_id WHERE q.status='failed' AND q.last_error NOT LIKE ? ORDER BY q.id DESC",("%Не найдено окно создания публикации в группе%",))
    if retry_candidates:
        with st.expander(f"Повторить ошибку · {len(retry_candidates)}"):
            st.caption("Повтор возможен только для известной ошибки, если группа разрешена и не действует интервал. Неизвестный результат повторять нельзя.")
            retry_by_id={row['id']:row for row in retry_candidates}
            retry_id=st.selectbox('Задание для повтора',options=list(retry_by_id),format_func=lambda value:f"#{value} · {retry_by_id[value]['bike_name']} → {retry_by_id[value]['group_name']}",key='retry_failed_job')
            st.caption(retry_by_id[retry_id]['last_error'])
            if st.button('Проверить ограничения и повторить',disabled=state.running,key='retry_failed_job_button'):
                if not queue_module.retry_job(db,retry_id):
                    gate=group_gate(db,retry_by_id[retry_id]['group_id'])
                    st.warning(gate['reason'] or 'Группа снята с публикации, запрещена или её результаты требуют проверки.')
                else:
                    try:
                        ensure_worker_running(settings)
                    except Exception as exc:
                        st.error(f"Повтор добавлен в очередь, но worker не запустился: {exc}")
                    else:
                        st.success('Задание возвращено в очередь.')
                        st.rerun()
    composer_failures = db.all(
        "SELECT q.id,q.bike_id,q.group_id FROM queue q "
        "JOIN groups_ g ON g.id=q.group_id "
        "WHERE q.status='failed' AND g.archived=0 AND g.enabled=1 "
        "AND q.last_error LIKE ? ORDER BY q.id",
        ("%Не найдено окно создания публикации в группе%",),
    )
    failed_pairs = {(row["bike_id"], row["group_id"]) for row in composer_failures}
    if composer_failures:
        st.warning(
            "Эти задания обработала старая версия worker. Они не повторяются "
            f"сами, чтобы не отправить возможные дубли. Кнопка ниже запустит "
            f"{len(failed_pairs)} попытки — по одной на каждую уникальную пару "
            "велосипед/группа."
        )
    if composer_failures and st.button(
        f"Повторить {len(failed_pairs)} уникальные пары "
        f"(из {len(composer_failures)} старых ошибок)",
        type="primary",
    ):
        active_job = db.one("SELECT id FROM queue WHERE status='running' LIMIT 1")
        if active_job:
            st.warning(
                "Сейчас уже публикуется другое объявление. Дождись его завершения "
                "и затем повтори эти задания."
            )
        else:
            latest_per_pair = {}
            for row in composer_failures:
                latest_per_pair.setdefault((row["bike_id"], row["group_id"]), row)
            retry_ids = [row["id"] for row in latest_per_pair.values()]
            duplicate_ids = [
                row["id"]
                for row in composer_failures
                if row["id"] not in retry_ids
            ]
            placeholders = ",".join("?" for _ in retry_ids)
            with db.connect() as conn:
                conn.execute(
                    f"UPDATE queue SET status='pending',last_error='',updated_at=? "
                    f"WHERE id IN ({placeholders}) AND status='failed'",
                    (utc_now(), *retry_ids),
                )
                if duplicate_ids:
                    duplicate_placeholders = ",".join("?" for _ in duplicate_ids)
                    conn.execute(
                        f"UPDATE queue SET status='cancelled',"
                        f"last_error='Объединено с повтором того же велосипеда и группы',"
                        f"updated_at=? WHERE id IN ({duplicate_placeholders}) "
                        f"AND status='failed'",
                        (utc_now(), *duplicate_ids),
                    )
            try:
                state, _ = ensure_worker_running(settings)
            except Exception as exc:
                st.error(f"Задания подготовлены, но worker не запустился: {exc}")
            else:
                db.log(
                    "queue_retry_composer",
                    f"Перезапущены пары велосипед/группа: {len(retry_ids)}; "
                    f"объединены дубликаты: {len(duplicate_ids)}; worker PID {state.pid}",
                )
                st.success(
                    f"Повторяем заданий: {len(retry_ids)}. "
                    f"Дубликатов объединено: {len(duplicate_ids)}. "
                    "Старая версия worker будет остановлена; Chrome запустится "
                    "с обновлённым кодом."
                )
                st.rerun()
    network_failures = db.all(
        "SELECT id FROM queue WHERE status='failed' AND last_error LIKE ? "
        "ORDER BY id DESC",
        ("%ERR_NETWORK_ACCESS_DENIED%",),
    )
    if network_failures:
        st.error(
            "Facebook заблокирован сетевыми ограничениями текущей среды запуска. "
            "Ни одно из этих заданий не было опубликовано. Открой обычный "
            "Windows PowerShell вне Codex и запусти run.ps1 из папки проекта."
        )
    waiting = db.all("SELECT id FROM queue WHERE status='waiting_approval'")
    if waiting and st.button("Подтвердить все ожидающие"):
        approve_jobs(db, [row["id"] for row in waiting])
        db.log("queue_approved", f"Подтверждено заданий: {len(waiting)}")
        try:
            ensure_worker_running(settings)
        except Exception as exc:
            st.error(f"Задания подтверждены, но worker не запустился: {exc}")
        else:
            st.rerun()


def journal():
    st.title("Журнал")
    st.caption(
        "Очистка удаляет только записи событий приложения. Технический журнал "
        "worker.log и очередь публикаций не затрагиваются."
    )
    if st.session_state.get("confirm_clear_audit_log_pending"):
        st.warning("Точно удалить все записи журнала? Это действие нельзя отменить.")
        confirm_col, cancel_col = st.columns(2)
        if confirm_col.button(
            "Да, очистить журнал", type="primary", key="confirm_clear_audit_log_yes"
        ):
            deleted = db.clear_audit_log()
            st.session_state.confirm_clear_audit_log_pending = False
            st.success(f"Удалено записей: {deleted}.")
        if cancel_col.button("Не очищать", key="cancel_clear_audit_log"):
            st.session_state.confirm_clear_audit_log_pending = False
            st.rerun()
    elif st.button("Очистить журнал", key="start_clear_audit_log"):
        st.session_state.confirm_clear_audit_log_pending = True
        st.rerun()
    st.dataframe(db.all("SELECT created_at,event_type,message FROM audit_log ORDER BY id DESC LIMIT 300"), width="stretch", hide_index=True)


def polish_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(ZoneInfo("Europe/Warsaw")).strftime("%d.%m.%Y · %H:%M")
    except (ValueError, TypeError):
        return "—"


def account_card(publisher):
    profile_path = str(settings.browser_profile)
    user = db.get_setting("facebook_user", {})
    session = db.get_setting("facebook_session", {})
    if user.get("browser_profile") != profile_path:
        user = {}
    if session.get("browser_profile") != profile_path:
        session = {}
    with st.container(border=True):
        avatar_col, identity_col = st.columns([1, 7])
        with avatar_col:
            avatar = user.get("avatar_url", "")
            if avatar.startswith("https://"):
                try:
                    st.image(avatar, width=88)
                except Exception:
                    st.write("👤")
            else:
                st.write("👤")
        with identity_col:
            st.subheader(user.get("name") or "Твой Facebook-аккаунт")
            if user.get("account_id"):
                st.caption(f"ID аккаунта: {user['account_id']}")
                st.link_button("Открыть мой профиль", f"https://www.facebook.com/profile.php?id={user['account_id']}")
            if not user.get("name") or not avatar:
                st.caption("Имя и аватарка появятся после обновления данных или нового входа в Facebook.")
        state = session.get("state", "unknown")
        if state == "connected":
            st.success("Вход подтверждён при последней проверке")
        elif state == "disconnected":
            st.warning(session.get("message") or "Нужно войти в Facebook")
        elif session.get("message"):
            st.info(session["message"])
        elif publisher.profile_exists():
            st.info("Сохранён профиль браузера. Обнови данные, чтобы проверить вход и получить имя и аватарку.")
        else:
            st.info("Войди в Facebook, чтобы подключить аккаунт.")
        if session.get("checked_at"):
            st.caption(f"Последняя проверка: {polish_time(session['checked_at'])} (Польша)")
        if user.get("updated_at"):
            st.caption(f"Данные профиля обновлены: {polish_time(user['updated_at'])}")


def settings_page():
    st.title("Настройки")
    publisher = FacebookPublisher(settings)
    if notice := st.session_state.pop("account_notice", None):
        getattr(st, notice["kind"])(notice["message"])
    summary = activity_summary(db)
    state = worker_state(settings)
    busy = state.running and summary["running"] > 0
    st.subheader("Мой аккаунт")
    account_card(publisher)
    c1, c2, c3 = st.columns(3)
    if c1.button("Войти в Facebook", type="primary", width="stretch", disabled=busy):
        st.info("Откроется отдельное окно Google Chrome. Войди в свой Facebook; после успешного входа окно закроется автоматически.")
        try:
            with st.spinner("Ожидаю вход в открывшемся окне Chrome…"):
                account_id = publisher.login()
        except Exception as exc:
            st.error(f"Не удалось сохранить вход: {exc}")
        else:
            status = publisher.last_session or facebook_module.SessionStatus(True, "Вход сохранён.", account_id)
            save_account_status(db, status, str(settings.browser_profile), login=True)
            db.log("facebook_login", "Сессия Facebook сохранена")
            st.session_state.account_notice = {"kind": "success", "message": "Вход сохранён, данные аккаунта обновлены."}
            st.rerun()

    refresh = c2.button("Обновить имя и аватарку", width="stretch", disabled=busy)
    check = c3.button("Проверить вход", width="stretch", disabled=busy)
    if refresh or check:
        try:
            with st.spinner("Проверяю вход и читаю данные твоего профиля…"):
                status = publisher.verify_login()
        except Exception as exc:
            st.error(f"Проверка не выполнена: {exc}")
        else:
            save_account_status(db, status, str(settings.browser_profile))
            message = status.message
            if status.logged_in and not status.name:
                message += " Имя и аватарка пока не прочитаны: попробуй обновить данные ещё раз."
            st.session_state.account_notice = {"kind": "success" if status.logged_in else "warning", "message": message}
            st.rerun()
    if busy:
        st.caption("Сейчас бот публикует объявление. Данные аккаунта можно обновить после завершения задания.")

    st.subheader("Моя статистика")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Опубликовано всего", summary["sent"])
    m2.metric("Опубликовано сегодня", summary["sent_today"])
    m3.metric("Ожидают отправки", summary["pending"])
    m4.metric("Требуют внимания", summary["failed"] + summary["approval"] + summary.get('unknown',0) + summary.get('moderation',0))
    st.caption(
        f"В каталоге: {summary['bikes']} велосипедов и {summary['photos']} фото · "
        f"Включено групп: {summary['groups']} · "
        f"Ошибок: {summary['failed']} · Ждут подтверждения: {summary['approval']}"
    )
    with st.container(border=True):
        st.subheader("Ближайшая отправка")
        if summary["next_job"]:
            job = summary["next_job"]
            st.write(f"{job['bike_name']} → {job['group_name']}")
            st.write(f"🕒 {polish_time(job['scheduled_at'])} (Польша)")
            if not state.running:
                st.warning("Бот остановлен. Запусти его в разделе «Очередь», чтобы задания выполнялись.")
        elif summary["running"]:
            st.info("Задание уже обрабатывается. Следующих отправок пока нет.")
        else:
            st.write("Запланированных отправок пока нет.")
        st.caption("Бот работает" if state.running else "Бот остановлен")
        st.caption(f"Пауза между публикациями: {settings.between_posts_seconds} с")

    with st.expander("Технические сведения"):
        st.caption(f"Профиль Chrome: {settings.browser_profile}")
        st.caption(f"База данных: {settings.db_path}")


pages = {"🚲 Велосипеды": bike_editor, "👥 Группы": group_editor, "📅 Очередь": queue_page, "🧾 Журнал": journal, "⚙️ Настройки": settings_page}
default_page = 4 if not FacebookPublisher(settings).profile_exists() else 0
choice = st.sidebar.radio("Раздел", list(pages), index=default_page)
pages[choice]()
