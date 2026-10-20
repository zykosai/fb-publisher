"use strict";
let state,
  selectedBike,
  photoOrder = [],
  photoBusy = false,
  initialized = false;
let renderPending = false;
let bulkPreviewToken = "";
let marketplaceOpen = false;
let accountsOpen = true;
let addAccountOpen = false;
try { accountsOpen = localStorage.getItem('market-accounts-open') !== 'false'; } catch (_) {}
let helpOpen = false;
let groupDrag = null;
let suppressGroupClickUntil = 0;
const bikesChosen = new Set(),
  groupsChosen = new Set(),
  groupsMarked = new Set();
let groupFilter = "",
  delay = 90,
  startAt = "";
const content = document.getElementById("content"),
  notice = document.getElementById("notice"),
  editor = document.getElementById("editor");
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
const time = (value) =>
  value
    ? new Date(value).toLocaleString(I18n.locale, {
        timeZone: "Europe/Warsaw",
        dateStyle: "short",
        timeStyle: "short",
      })
    : "—";
const groupStates = {
  ready: "Можно",
  sell_only: "Только «Продать»",
  unchecked: "Не проверена",
  unavailable: "Нет поля",
  private: "Закрытая группа",
  pending: "Вступление",
  questions: "Нужны ответы",
  join_unconfirmed: "Вступление не подтверждено",
  check_failed: "Пропущена: ошибка",
  moderation: "На проверке",
  moderation_limit: "Лимит",
  not_member: "Не участник",
  removed: "Исключили",
  blocked: "Блок",
};
const statuses = {
  pending: "Ожидает",
  running: "Публикуется",
  sent: "Отправлено",
  published: "Опубликовано",
  moderation: "На проверке у администратора",
  unknown: "Результат не подтверждён",
  failed: "Не отправлено",
  skipped: "Пропущено",
  cancelled: "Отменено",
  waiting_approval: "Ожидает",
};
function tell(message, error = false) {
  notice.textContent = message;
  notice.classList.toggle("error", error);
  I18n.apply(notice);
}
async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Ошибка подключения");
  return data;
}
function page() {
  return location.hash.slice(1) || "bikes";
}
function picks(items, chosen, type) {
  return items
    .filter((item) => type !== "bike" || item.status === "active")
    .map(
      (item) =>
        `<label class="pick"><input type="checkbox" data-pick="${type}" value="${item.id}" ${chosen.has(item.id) ? "checked" : ""}><span data-user-content>${escape(item.name)}${type === "bike" && item.price ? ` · ${escape(item.price)} zł` : ""}</span></label>`,
    )
    .join("");
}
function bikeCard(bike) {
  const sold = bike.status === "sold";
  return `<article class="card ${sold ? "is-sold" : ""}">
    <div class="card-cover">${bike.photos.length
      ? `<img loading="lazy" src="/photo/${bike.photos[0]}" alt="${escape(bike.name)}" data-user-content>`
      : '<div class="empty">Добавь фотографии</div>'}
      ${sold ? '<span class="sold-label">SOLD · Продан</span>' : ""}
      ${bike.source === "facebook" ? '<span class="import-label">Import FB</span>' : ""}</div>
    <div class="body"><h2 data-user-content>${escape(bike.name)}</h2>
      <span class="badge">${bike.price ? `${escape(bike.price)} zł · ` : ''}${bike.photos.length} фото</span>
      <p data-user-content>${escape(bike.description.slice(0, 125))}${bike.description.length > 125 ? "…" : ""}</p>
      <div class="card-actions"><button class="secondary" data-action="edit" data-id="${bike.id}">Редактировать</button>
      ${(state.workspace?.preset || 'goods') === 'goods' ? `<button class="${sold ? "secondary" : ""}" data-action="sold" data-id="${bike.id}" aria-pressed="${sold}">${sold ? "Отменить SOLD" : "Отметить SOLD"}</button>` : ''}
      <button class="danger" data-action="delete-item" data-id="${bike.id}">Удалить из каталога</button></div>
    </div></article>`;
}
function marketplacePanel() {
  const marketplace = state.marketplace || {listings: [], preview: null};
  const preview = marketplace.preview;
  return `<details class="panel marketplace" ${marketplaceOpen ? "open" : ""}>
    <summary>Импорт из Marketplace</summary>
    <p class="muted">Ничего на Facebook не изменяется. Сначала предпросмотр, затем подтверждение импорта.</p>
    <button class="secondary" data-action="marketplace-scan" ${state.operation.busy ? "disabled" : ""}>Найти мои объявления</button>
    <p class="muted">Поиск показывает активные объявления.</p>
    <label>Ссылка на своё объявление Marketplace<input id="marketplace-url" placeholder="https://www.facebook.com/marketplace/item/…"></label>
    <button class="secondary" data-action="marketplace-preview" ${state.operation.busy ? "disabled" : ""}>Предпросмотр</button>
    ${marketplace.listings.length ? `<h3>Объявления Marketplace</h3><div class="marketplace-list">${marketplace.listings.map(item => `<button class="secondary" data-action="marketplace-preview" data-url="${escape(item.url)}" ${state.operation.busy ? "disabled" : ""}><span data-user-content>${escape(item.name)}</span></button>`).join("")}</div>` : ""}
    ${preview ? `<section class="panel"><h2 data-user-content>${escape(preview.name)}</h2><span class="badge">Import FB · ${escape(preview.price)} zł</span>
      <p class="marketplace-description" data-user-content>${escape(preview.description)}</p>
      <p class="muted">Порядок фото из Facebook. Первое будет главным.</p>
      ${preview.low_resolution ? '<p class="warning">Facebook отдал небольшие фотографии. Проверь качество перед импортом.</p>' : ""}
      <div class="marketplace-photos">${preview.photos.map((photo, index) => `<div><a href="${escape(photo.url)}" target="_blank" rel="noopener"><img src="${escape(photo.url)}" alt="Фото ${index + 1}"></a><small>${index + 1} · ${photo.width}×${photo.height}</small></div>`).join("")}</div>
      ${preview.existing ? '<p class="warning">Товар уже есть в каталоге. Обновление заменит описание, цену и порядок фото. SOLD сохранится.</p>' : ""}
      ${preview.existing?.status === "deleted" ? '<p class="warning">Удалённый товар не восстанавливается импортом.</p>' : `<button data-action="marketplace-commit" data-id="${escape(preview.id)}">${preview.existing ? "Обновить существующий товар" : "Добавить в товары"}</button>`}
    </section>` : ""}</details>`;
}
function groupBadges(group) {
  const check = group.check;
  const waiting = check.pending_count;
  const rejected = check.rejected_count;
  const title = escape(`${check.detail || ""}${check.checked_at ? ` · Проверено: ${time(check.checked_at)}` : ""}`);
  let html = `<small class="badge ${escape(check.state)}" title="${title}">${escape(groupStates[check.state] || check.state)}${check.state === "moderation" && waiting != null ? ` · ${Number(waiting)}` : ""}${group.joined ? " · моя группа" : ""}</small>`;
  if (check.state !== "moderation" && waiting != null && waiting > 0)
    html += `<small class="badge moderation" title="Ожидают решения администратора. Это не отказ.">На проверке · ${Number(waiting)}</small>`;
  if (rejected != null && rejected > 0)
    html += `<small class="badge rejected" title="Facebook явно показал отклонённые публикации">Отклонено · ${Number(rejected)}</small>`;
  return `<span class="group-badges">${html}</span>`;
}
function groupRow(group) {
  return `<label class="pick"><input type="checkbox" data-pick="marked" value="${group.id}" ${groupsMarked.has(group.id) ? "checked" : ""}><span data-user-content><a class="group-name" href="${escape(group.url)}" target="_blank" rel="noopener">${escape(group.name)}</a></span>${groupBadges(group)}</label>`;
}
function presetOptions(selected) {
  return [['goods','Товары'],['services','Услуги'],['creator','Контент / ИИ']].map(([value,label]) =>
    `<option value="${value}" ${selected === value ? 'selected' : ''}>${escape(I18n.t(label))}</option>`).join('');
}
function accountPanel() {
  const workspace = state.workspace || {id:'',preset:'goods'};
  return `<details class="panel accounts-panel" ${accountsOpen ? 'open' : ''}><summary><h2>Сохранённые аккаунты</h2></summary><div class="accounts-content">
    <p class="muted">Каждый аккаунт имеет отдельный браузер, товары, группы, очередь и паузу. Несколько аккаунтов могут работать одновременно.</p>
    <div class="account-grid">${(state.accounts || []).map(account => `<article class="panel">
      <h3 data-user-content>${escape(account.label)}</h3>
      <p data-user-content>${escape(account.user.name || account.account_id || I18n.t('Вход не выполнен'))}</p>
      <span class="badge">${account.running && !account.paused ? 'Работает' : 'На паузе'}</span>
      <div class="row"><button class="secondary" data-action="account-select" data-id="${escape(account.id)}" ${account.id === workspace.id || state.operation.busy ? 'disabled' : ''}>Выбрать аккаунт</button>
      <button data-action="account-start" data-id="${escape(account.id)}" ${!account.account_id || state.operation.busy ? 'disabled' : ''}>Запустить аккаунт</button>
      <button class="secondary" data-action="account-pause" data-id="${escape(account.id)}">Пауза аккаунта</button></div>
    </article>`).join('')}</div>
    ${workspace.id ? `<div class="account-settings"><h3>Настройки выбранного аккаунта</h3><label>Название профиля<input id="account-label" maxlength="80" value="${escape(workspace.label)}"></label>
    <label>Режим интерфейса<select id="account-preset">${presetOptions(workspace.preset)}</select></label>
    <button class="secondary" data-action="account-save">Сохранить профиль</button></div>` : ''}
    <details class="account-create" ${addAccountOpen ? 'open' : ''}><summary>Добавить аккаунт</summary><label>Название профиля<input id="new-account-label" maxlength="80" placeholder="Например: магазин или услуги"></label>
    <label>Режим интерфейса<select id="new-account-preset">${presetOptions('goods')}</select></label>
    <button data-action="account-add" ${state.operation.busy ? 'disabled' : ''}>Создать отдельный профиль</button>
    <p class="muted">После создания нажми «Войти в выбранный аккаунт». Пароль вводится только на Facebook и не хранится в приложении.</p></details>
    </div></details>`;
}
function helpPanel() {
  const tips = {
    bikes: 'Создай объявление или импортируй товар. Фото добавляются после первого сохранения. Заголовок и предпросмотр находятся в редакторе.',
    groups: 'Загрузи свои группы, отметь нужные и проверь доступ. Вступление и одобрение администратора — разные статусы. Проверку можно отменить.',
    publish: 'Выбери объявления и проверенные группы, создай план и нажми «Запустить». Время начала и интервал задаются отдельно от Турбо.',
    history: 'Здесь результаты по группам. Очистка истории не удаляет публикации в Facebook.',
    profile: 'Добавляй и выбирай аккаунты. Переключение панели не останавливает остальные аккаунты; у каждого своя кнопка паузы.'
  };
  return `<button id="help-toggle" aria-label="${escape(I18n.t('Как пользоваться'))}" title="${escape(I18n.t('Как пользоваться'))}" aria-expanded="${helpOpen}" aria-controls="help-popup">?</button>
    <aside id="help-popup" class="panel help-panel" role="dialog" aria-label="${escape(I18n.t('Как пользоваться'))}" ${helpOpen ? '' : 'hidden'}>
    <div class="row"><h2>Как пользоваться</h2><button id="help-close" class="secondary" aria-label="${escape(I18n.t('Закрыть'))}">×</button></div>
    <p>${escape(tips[page()] || tips.bikes)}</p>
    ${['groups','publish'].includes(page()) ? '<p>Зажми левую кнопку мыши на галочке группы и веди вниз или вверх. Повторное протягивание снимает отметки. У края экрана список прокручивается.</p>' : ''}
    <ol><li>Аккаунты: добавь профиль и войди в Facebook.</li><li>Каталог: добавь описание и при необходимости фотографии.</li>
    <li>Группы: загрузи и проверь места публикации.</li><li>Публикация: создай план, затем запусти нужный аккаунт.</li></ol>
    <p class="muted">Турбо сокращает паузы, но не защищает от ограничений Facebook. При ограничении действия аккаунта останавливаются.</p></aside>`;
}
function bulkDescriptionsPanel() {
  return `<details class="panel" data-bulk-panel><summary>Массовое изменение описаний</summary>
    <p class="muted">Только товары текущего аккаунта, включая проданные. Публикации в Facebook не изменятся.</p>
    <button class="secondary" data-action="bulk-clean-preview">Убрать рекламу профиля</button>
    <p class="muted">Или найди и замени свой текст во всех описаниях. Пустая замена удаляет найденный текст.</p>
    <label>Найти текст<textarea id="bulk-find" rows="2"></textarea></label>
    <label>Заменить на<textarea id="bulk-replacement" rows="2"></textarea></label>
    <button class="secondary" data-action="bulk-replace-preview">Показать замены</button>
    <button class="secondary" data-action="bulk-undo">Отменить последнее массовое изменение</button>
    <div id="bulk-preview" hidden></div></details>`;
}
async function showBulkPreview(body) {
  const result = await api("/api/descriptions/preview", body);
  bulkPreviewToken = result.token;
  const target = document.getElementById("bulk-preview");
  target.innerHTML = `<p>Будет изменено товаров: ${result.edits.length}</p>${result.edits.map(edit =>
    `<details><summary data-user-content>${escape(edit.name)}</summary><p>До</p><pre style="white-space:pre-wrap" data-user-content>${escape(edit.before)}</pre><p>После</p><pre style="white-space:pre-wrap" data-user-content>${escape(edit.after)}</pre></details>`).join("")}
    ${result.edits.length ? '<button data-action="bulk-apply">Применить изменения</button>' : ''}`;
  target.hidden = false;
  I18n.apply(target);
}
function render() {
  if (!state) return;
  const turboButton = document.getElementById('turbo');
  turboButton.textContent = state.turbo ? 'Турбо: вкл.' : 'Турбо: выкл.';
  turboButton.setAttribute('aria-pressed', String(!!state.turbo));
  turboButton.classList.toggle('secondary', !state.turbo);
  renderPending = false;
  const preset = state.workspace?.preset || 'goods';
  const presetTitle = {goods: 'Товары', services: 'Услуги', creator: 'Контент'}[preset];
  document.getElementById('catalog-nav').textContent = presetTitle;
  document.getElementById('active-account').textContent = state.workspace?.label || state.user.name || '';
  document.getElementById("status").textContent = state.activity.bike
    ? `${state.activity.bike} · ${state.paused ? "останавливаю черновик…" : state.activity.stage || "публикуется"}`
    : state.paused
      ? "На паузе"
      : "Работает";
  document
    .querySelectorAll("nav a")
    .forEach((link) =>
      link.classList.toggle("active", link.hash === `#${page()}`),
    );
  if (page() === "bikes") {
    content.innerHTML = `<h1>Твои товары</h1><p class="muted">Первое фото — главное. Проданные товары остаются здесь, но не участвуют в публикациях.</p>${marketplacePanel()}<div class="toolbar"><label class="secondary">Импорт ZIP<input type="file" id="zip" accept=".zip"></label><button class="secondary" data-action="import">Импортировать</button></div><div class="grid">${state.bikes.map(bikeCard).join("") || '<div class="empty">Каталог пуст. Импортируй свой ZIP или объявления Marketplace.</div>'}</div>`;
    content.querySelector('.toolbar').insertAdjacentHTML('beforebegin', bulkDescriptionsPanel());
    content.querySelector('.toolbar').insertAdjacentHTML('afterbegin', '<button data-action="new-item">Добавить объявление</button>');
    if (preset !== 'goods') {
      content.querySelector('h1').textContent = preset === 'services' ? 'Твои услуги' : 'Твой контент';
      content.querySelector('h1 + p').textContent = 'Создай объявление с описанием. Цена и фотографии необязательны.';
      const empty = content.querySelector('.grid .empty');
      if (empty) empty.textContent = 'Каталог пуст. Нажми «Добавить объявление».';
      content.querySelector('.marketplace').hidden = true;
      content.querySelector('.toolbar label').hidden = true;
      content.querySelector('[data-action="import"]').hidden = true;
    }
  } else if (page() === "groups") {
    const filtered = state.groups.filter((g) =>
      g.name.toLocaleLowerCase().includes(groupFilter.toLocaleLowerCase()),
    );
    content.innerHTML = `<h1>Группы Facebook</h1><p class="muted">Проверка показывает доступ, очередь модерации и явные отказы. Ожидание одобрения не означает отказ.</p><div class="toolbar"><button data-action="sync">Загрузить мои группы</button><button class="secondary" data-action="check">Проверить отмеченные</button><button class="secondary" data-action="join">Вступить в открытые и проверить</button></div><input id="group-filter" placeholder="Найти группу в списке" value="${escape(groupFilter)}"><div class="row"><button class="secondary" data-action="mark-all">Отметить видимые</button><button class="secondary" data-action="select-ready">Выбрать подходящие для публикации</button><button class="danger" data-action="remove-groups">Удалить отмеченные</button></div><div class="panel">${filtered.map(groupRow).join("") || '<p class="muted">Групп пока нет.</p>'}</div><details class="panel"><summary>Найти новые группы или добавить ссылку</summary><label>Город и тема<input id="search-query" placeholder="ogłoszenia Gniezno"></label><button data-action="search">Найти в Facebook</button><h3>Добавить вручную</h3><input id="group-name" placeholder="Название"><input id="group-url" placeholder="https://www.facebook.com/groups/…"><button class="secondary" data-action="add-group">Добавить</button></details>`;
    content.querySelector('[data-action="remove-groups"]').insertAdjacentHTML("afterend",
      `<button class="secondary" data-action="restore-groups" title="Восстановление доступно 5 минут после удаления" ${state.deleted_groups_count ? "" : "disabled"}>Вернуть удалённые · ${Number(state.deleted_groups_count || 0)}</button>`);
    if (state.operation.busy && state.operation.kind === "group_check") {
      content.querySelector('.toolbar').insertAdjacentHTML('beforeend', `<button class="danger" data-action="cancel-check" ${state.operation.cancelling ? "disabled" : ""}>Отменить проверку</button>`);
    }
  } else if (page() === "publish") {
    const suitable = state.groups.filter(
      (g) => g.joined && g.check.state === "ready",
    );
    content.innerHTML = `<h1>Новая публикация</h1><p class="muted">Товары отправляются в порядке выбора. Один пост — исходная группа и до 9 дополнительных. Большой список разделится на пакеты Facebook.</p><div class="panel"><h2>1. Товары</h2>${picks(state.bikes, bikesChosen, "bike")}<p class="muted">Порядок: <span data-user-content>${[...bikesChosen].map((id) => escape(state.bikes.find((b) => b.id === id)?.name)).join(" → ")}</span>${bikesChosen.size ? "" : "выбери товары"}</p></div><div class="panel"><h2>2. Проверенные группы</h2>${picks(suitable, groupsChosen, "group") || '<p class="muted">Перейди в «Группы»: загрузи и проверь свои группы.</p>'}</div><div class="panel"><h2>3. Расписание</h2><label>Интервал после завершения товара, минут<input id="delay" type="number" min="0" max="10080" value="${delay}"></label><label>Начать в указанное время (Польша), или оставь пустым для отправки сейчас<input id="start-at" type="datetime-local" value="${escape(startAt)}"></label><p class="muted">Перед отправкой бот ждёт 5 секунд. Пауза вверху останавливает подготовку текущего объявления.</p><button data-action="plan">Добавить план</button></div>`;
  } else if (page() === "history") {
    content.innerHTML = `<div class="row history-heading"><h1>Публикации</h1><button class="secondary" data-action="clear-history">Очистить историю</button></div><p class="muted">Последние 30 товаров. Открой запись, чтобы увидеть результаты по группам.</p>${
      state.history
        .map((p) => {
          const accepted = p.groups.filter((g) =>
            ["sent", "published", "moderation"].includes(g.status),
          ).length;
          const pending = p.groups.some((g) =>
            ["pending", "running"].includes(g.status),
          );
          const uncertain = p.groups.filter((g) => g.status === "unknown").length;
          const progress = `${accepted} из ${p.groups.length} подтверждено${uncertain ? ` · без подтверждения: ${uncertain}` : ""}`;
          return `<details class="panel history"><summary><strong data-user-content>${escape(p.name)}</strong> · ${pending ? `В процессе · ${progress}` : progress}<p class="muted">${time(pending ? p.scheduled_at : p.updated_at)}</p></summary>${p.groups.map((g) => `<p><span data-user-content>${escape(g.name)}</span> — ${escape(statuses[g.status] || g.status)}${g.last_error ? `<br><small class="muted">${escape(g.last_error)}</small>` : ""}</p>`).join("")}${pending ? `<button class="secondary" data-action="cancel" data-plan="${escape(p.plan_id)}">Отменить оставшиеся отправки плана</button>` : ""}</details>`;
        })
        .join("") || '<div class="empty">Публикаций пока нет.</div>'
    }`;
  } else if (page() === "profile") {
    const user = state.user,
      avatar = String(user.avatar_url || "").startsWith("https://")
        ? `<img class="avatar" src="${escape(user.avatar_url)}" alt="Аватар">`
        : '<span class="stat">👤</span>';
    content.innerHTML = `<h1>Профиль</h1><div class="panel row">${avatar}<div><h2 ${user.name ? 'data-user-content' : ''}>${escape(user.name || "Твой Facebook-аккаунт")}</h2><p class="muted">${escape(state.session.message || "Войди в Facebook на компьютере")}</p></div></div><div class="stats panel"><div class="stat">${state.sent_today}<small>Объявлений сегодня</small></div><div class="stat">${state.groups.filter((g) => groupsChosen.has(g.id)).length}<small>Выбрано групп</small></div><div class="stat">${state.next_job ? time(state.next_job.scheduled_at) : "—"}<small>Ближайшая отправка</small></div></div><div class="toolbar"><button data-action="login">Войти / сменить аккаунт</button><button class="secondary" data-action="refresh-profile">Обновить профиль</button></div><p class="muted">Вход открывается на компьютере. Для смены аккаунта выйди из Facebook в этом окне и войди в другой аккаунт.</p>`;
    const sales = state.sales || { count: 0, total: "0.00" };
    if (state.operation.busy && state.operation.kind === "login") {
      content.insertAdjacentHTML("afterbegin", `<div class="panel"><p>Смени аккаунт в открытом окне Chrome. Когда закончишь, нажми кнопку ниже. Окно не закроется до подтверждения; время ожидания — 10 минут.</p><button data-action="finish-login">Готово, проверить вход</button></div>`);
    }
    if (preset === 'goods') content.querySelector(".stats").insertAdjacentHTML("afterend",
      `<div class="stats sales-panel panel"><div class="stat">${sales.count}<small>Товаров продано</small></div><div class="stat">${new Intl.NumberFormat(I18n.locale, { style: "currency", currency: "PLN", maximumFractionDigits: 2 }).format(Number(sales.total))}<small>Сумма продаж · по цене при отметке SOLD</small></div></div>`);
    content.insertAdjacentHTML('afterbegin', accountPanel());
    content.querySelector('[data-action="login"]').textContent = 'Войти в выбранный аккаунт';
    content.querySelector('[data-action="login"]').closest('.toolbar').nextElementSibling.textContent = 'Для другого Facebook создай отдельный профиль. Не выходи из старого аккаунта ради переключения: выбери сохранённый профиль выше.';
    const finishButton = content.querySelector('[data-action="finish-login"]');
    if (finishButton) finishButton.previousElementSibling.textContent = 'Войди в Facebook в открытом окне выбранного профиля, затем нажми «Готово, проверить вход».';
  }
  if (page() === 'publish') {
    content.querySelector('.panel h2').textContent = `1. ${presetTitle}`;
    content.querySelector('#delay').parentElement.firstChild.textContent = 'Интервал после завершения объявления, минут';
    if (preset !== 'goods') {
      content.querySelector('h1 + p').textContent = 'Объявления отправляются в порядке выбора. Один пост — исходная группа и до 9 дополнительных. Большой список разделится на пакеты Facebook.';
      if (!bikesChosen.size) content.querySelector('.panel p').lastChild.textContent = 'выбери объявления';
    }
  }
  document.getElementById('help-widget').innerHTML = helpPanel();
  if (state.turbo && page() === 'publish') {
    const note = [...content.querySelectorAll('p.muted')].find(p => p.textContent.startsWith('Перед отправкой бот ждёт 5 секунд.'));
    if (note) note.textContent = 'Турбо: без искусственной паузы перед отправкой. Пауза вверху останавливает подготовку текущего объявления.';
  }
  I18n.apply();
}
document.getElementById("language").onchange = (event) => { I18n.set(event.target.value); render(); I18n.apply(); };
const ask = (message) => confirm(I18n.t(message));
function renderUpdatedState(force = false) {
  const focused = document.activeElement;
  const filterFocused = focused?.id === "group-filter";
  const editing = content.contains(focused) && focused.matches('input:not([type="checkbox"]):not([type="button"]),textarea,select,[contenteditable="true"]');
  if (!force && (groupDrag || editor.open || content.querySelector('[data-bulk-panel][open]') || (editing && !filterFocused))) return;
  if (!force && !renderPending) return;
  const selection = filterFocused ? [focused.selectionStart, focused.selectionEnd] : null;
  render();
  if (selection) {
    const filter = document.getElementById("group-filter");
    filter?.focus({ preventScroll: true });
    filter?.setSelectionRange(...selection);
  }
}
content.addEventListener("focusout", () => setTimeout(() => renderUpdatedState(), 0));
async function refresh(force = false) {
  try {
    const next = await api("/api/state");
    const changed = JSON.stringify(next) !== JSON.stringify(state);
    renderPending ||= changed;
    const accountChanged = state?.user.account_id !== next.user.account_id || state?.workspace?.id !== next.workspace?.id;
    if (accountChanged) bulkPreviewToken = "";
    state = next;
    for (const id of groupsChosen) {
      if (!state.groups.some((group) => group.id === id && group.joined && group.check.state === "ready"))
        groupsChosen.delete(id);
    }
    for (const id of bikesChosen) {
      if (!state.bikes.some((bike) => bike.id === id && bike.status === "active"))
        bikesChosen.delete(id);
    }
    if (!initialized || accountChanged) {
      marketplaceOpen = false;
      bikesChosen.clear();
      groupFilter = '';
      if (editor.open) editor.close();
      groupsChosen.clear();
      groupsMarked.clear();
      state.groups
        .filter((g) => g.selected)
        .forEach((g) => groupsChosen.add(g.id));
      initialized = true;
    }
    if (state.operation.busy) tell(state.operation.message);
    else if (state.operation.error) tell(state.operation.error, true);
    else if (state.operation.message) tell(state.operation.message);
    renderUpdatedState(force || accountChanged);
  } catch (error) {
    tell(error.message, true);
  }
}
async function action(path, body = {}) {
  const result = await api(path, body);
  if (result.message) tell(result.message);
  await refresh(true);
  return result;
}
document.getElementById("pause").onclick = () =>
  action("/api/pause").catch((error) => tell(error.message, true));
document.getElementById('turbo').onclick = async (event) => {
  event.target.disabled = true;
  try { await action('/api/turbo', {enabled: !state.turbo}); }
  catch (error) { tell(error.message, true); }
  finally { event.target.disabled = false; }
};
document.getElementById("start").onclick = () =>
  action("/api/start").catch((error) => tell(error.message, true));
window.addEventListener("hashchange", render);
document.getElementById('help-widget').addEventListener('click', event => {
  if (event.target.closest('#help-toggle')) helpOpen = !helpOpen;
  else if (event.target.closest('#help-close')) helpOpen = false;
  else return;
  document.getElementById('help-widget').innerHTML = helpPanel();
  I18n.apply();
  document.getElementById(helpOpen ? 'help-close' : 'help-toggle').focus();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && helpOpen) {
    helpOpen = false;
    document.getElementById('help-widget').innerHTML = helpPanel();
    I18n.apply();
    document.getElementById('help-toggle').focus();
  }
});
content.addEventListener('toggle', event => {
  if (event.target.matches('.marketplace') && event.target.isConnected) marketplaceOpen = event.target.open;
  if (event.target.matches('.accounts-panel') && event.target.isConnected) {
    accountsOpen = event.target.open;
    try { localStorage.setItem('market-accounts-open', String(accountsOpen)); } catch (_) {}
  }
  if (event.target.matches('.account-create') && event.target.isConnected) addAccountOpen = event.target.open;
}, true);
content.addEventListener('click', event => {
  if (event.target.closest('.marketplace > summary')) {
    marketplaceOpen = !event.target.closest('.marketplace').open;
  }
  if (event.target.closest('.accounts-panel > summary')) {
    accountsOpen = !event.target.closest('.accounts-panel').open;
    try { localStorage.setItem('market-accounts-open', String(accountsOpen)); } catch (_) {}
  }
  if (event.target.closest('.account-create > summary')) addAccountOpen = !event.target.closest('.account-create').open;
}, true);

// Paint a continuous range while holding the mouse, without rerendering rows
// under the pointer or sending a request for every checkbox.
function paintGroupRow(input) {
  const index = groupDrag.inputs.indexOf(input);
  if (index < 0) return;
  for (let i = Math.min(index, groupDrag.last); i <= Math.max(index, groupDrag.last); i++) {
    const row = groupDrag.inputs[i];
    row.checked = groupDrag.checked;
    groupDrag.checked ? groupDrag.chosen.add(Number(row.value)) : groupDrag.chosen.delete(Number(row.value));
  }
  groupDrag.last = index;
}
function dragScroll() {
  if (!groupDrag) return;
  const edge = 65, bottom = innerHeight - 65;
  const delta = groupDrag.y < edge ? -14 : groupDrag.y > bottom ? 14 : 0;
  if (delta) window.scrollBy(0, delta);
  const row = document.elementFromPoint(groupDrag.x, groupDrag.y)?.closest('.pick');
  const input = row?.querySelector(`input[data-pick="${groupDrag.type}"]`);
  if (input) paintGroupRow(input);
  groupDrag.frame = requestAnimationFrame(dragScroll);
}
content.addEventListener('pointerdown', event => {
  if (event.pointerType !== 'mouse' || event.button !== 0 || event.target.closest('a,button')) return;
  const input = event.target.closest('.pick')?.querySelector('input[data-pick="marked"],input[data-pick="group"]');
  if (!input || input.disabled) return;
  event.preventDefault();
  const type = input.dataset.pick;
  groupDrag = {type, inputs:[...content.querySelectorAll(`input[data-pick="${type}"]`)],
    chosen:type === 'group' ? groupsChosen : groupsMarked, checked:!input.checked, last:0, x:event.clientX, y:event.clientY};
  groupDrag.last = groupDrag.inputs.indexOf(input);
  paintGroupRow(input);
  document.body.classList.add('selecting-groups');
  groupDrag.frame = requestAnimationFrame(dragScroll);
});
document.addEventListener('pointermove', event => {
  if (!groupDrag) return;
  groupDrag.x = event.clientX;
  groupDrag.y = event.clientY;
});
function finishGroupDrag() {
  if (!groupDrag) return;
  const type = groupDrag.type;
  cancelAnimationFrame(groupDrag.frame);
  groupDrag = null;
  suppressGroupClickUntil = performance.now() + 250;
  document.body.classList.remove('selecting-groups');
  if (type === 'group') api('/api/groups/select', {ids:[...groupsChosen]}).catch(error => tell(error.message,true));
  render();
}
document.addEventListener('pointerup', finishGroupDrag);
document.addEventListener('pointercancel', finishGroupDrag);
window.addEventListener('blur', finishGroupDrag);
content.addEventListener('click', event => {
  if (event.detail > 0 && performance.now() < suppressGroupClickUntil && event.target.closest('.pick')) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }
}, true);
content.addEventListener("change", (event) => {
  const input = event.target;
  if (input.dataset.pick) {
    const set = {
      bike: bikesChosen,
      group: groupsChosen,
      marked: groupsMarked,
    }[input.dataset.pick];
    input.checked
      ? set.add(Number(input.value))
      : set.delete(Number(input.value));
    if (input.dataset.pick === "group")
      api("/api/groups/select", { ids: [...groupsChosen] }).catch((error) =>
        tell(error.message, true),
      );
    render();
  }
  if (input.id === "delay") delay = Number(input.value);
  if (input.id === "start-at") startAt = input.value;
});
content.addEventListener("input", (event) => {
  if (event.target.id === "bulk-find" || event.target.id === "bulk-replacement") {
    bulkPreviewToken = "";
    document.getElementById("bulk-preview").hidden = true;
  }
  if (event.target.id === "group-filter") {
    groupFilter = event.target.value;
    const position = event.target.selectionStart;
    render();
    const input = document.getElementById("group-filter");
    input.focus();
    input.setSelectionRange(position, position);
  }
});
content.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  button.disabled = true;
  try {
    switch (button.dataset.action) {
      case 'account-add':
        await action('/api/accounts/add', {label: document.getElementById('new-account-label').value, preset: document.getElementById('new-account-preset').value});
        break;
      case 'account-select':
        await action('/api/accounts/select', {id: button.dataset.id});
        break;
      case 'account-save':
        await action('/api/accounts/update', {label: document.getElementById('account-label').value, preset: document.getElementById('account-preset').value});
        break;
      case 'account-start':
      case 'account-pause':
        await action(button.dataset.action === 'account-start' ? '/api/accounts/start' : '/api/accounts/pause', {id: button.dataset.id});
        break;
      case 'new-item':
        openNewEditor();
        break;
      case 'delete-item':
        if (ask('Удалить объявление из каталога? Ожидающие отправки будут отменены. Уже опубликованные посты в Facebook останутся.')) {
          await action(`/api/bikes/${button.dataset.id}/delete`);
        }
        break;
      case "cancel-check":
        await action('/api/groups/check/cancel');
        break;
      case "bulk-clean-preview":
        await showBulkPreview({mode: 'remove_promo'});
        break;
      case "bulk-replace-preview":
        await showBulkPreview({mode: 'replace', find: document.getElementById('bulk-find').value, replacement: document.getElementById('bulk-replacement').value});
        break;
      case "bulk-apply":
        if (bulkPreviewToken && ask('Применить изменения ко всем товарам из предпросмотра?')) {
          await action('/api/descriptions/apply', {token: bulkPreviewToken});
          bulkPreviewToken = '';
        }
        break;
      case "bulk-undo":
        if (ask('Отменить последнее массовое изменение описаний?')) await action('/api/descriptions/undo');
        break;
      case "marketplace-scan":
        await action("/api/marketplace/scan");
        break;
      case "marketplace-preview":
        await action("/api/marketplace/preview", {url: button.dataset.url || document.getElementById("marketplace-url").value});
        break;
      case "marketplace-commit": {
        const preview = state.marketplace.preview;
        if (preview.existing && !ask("Подтвердить замену описания, цены и всех фотографий этого товара?")) break;
        await action("/api/marketplace/commit", {id: button.dataset.id, replace: !!preview.existing});
        break;
      }
      case "sold": {
        const bike = state.bikes.find((item) => item.id === Number(button.dataset.id));
        const sold = bike.status !== "sold";
        if (sold && !ask(`${bike.name} · ${bike.price} zł\nSOLD?`)) break;
        await action(`/api/bikes/${bike.id}/sold`, { sold });
        tell(sold ? "Продажа сохранена. Товар исключён из публикаций." : "Продажа отменена. Товар снова доступен для публикации; старые отправки не возобновляются.");
        break;
      }
      case "clear-history":
        if (ask("Очистить историю завершённых публикаций? Текущие задания и статистика сохранятся.")) {
          await action("/api/history/clear");
          tell("История очищена. Текущие задания сохранены.");
        }
        break;
      case "edit":
        openEditor(Number(button.dataset.id));
        break;
      case "sync":
        await action("/api/groups/sync");
        break;
      case "check":
      case "join":
        if (!groupsMarked.size) throw new Error("Отметь группы для проверки");
        await action("/api/groups/check", {
          ids: [...groupsMarked],
          join: button.dataset.action === "join",
        });
        break;
      case "mark-all":
        state.groups
          .filter((g) =>
            g.name
              .toLocaleLowerCase()
              .includes(groupFilter.toLocaleLowerCase()),
          )
          .forEach((g) => groupsMarked.add(g.id));
        render();
        break;
      case "select-ready":
        groupsChosen.clear();
        state.groups
          .filter((g) => g.joined && g.check.state === "ready")
          .forEach((g) => groupsChosen.add(g.id));
        await action("/api/groups/select", { ids: [...groupsChosen] });
        tell("Подходящие группы выбраны. Перейди в «Публикация».");
        break;
      case "remove-groups":
        if (
          groupsMarked.size &&
          ask("Удалить отмеченные группы только у текущего аккаунта? На других аккаунтах они останутся.")
        ) {
          await action("/api/groups/delete", { ids: [...groupsMarked] });
          groupsMarked.clear();
        }
        break;
      case "restore-groups":
        if (ask("Вернуть группы, удалённые за последние 5 минут? Отменённые публикации не возобновятся.")) {
          await action("/api/groups/restore");
        }
        break;
      case "search":
        await action("/api/groups/search", {
          query: document.getElementById("search-query").value,
        });
        break;
      case "add-group":
        await action("/api/groups/add", {
          name: document.getElementById("group-name").value,
          url: document.getElementById("group-url").value,
        });
        break;
      case "plan":
        await action("/api/plan", {
          bikes: [...bikesChosen],
          groups: [...groupsChosen],
          delay,
          start: startAt || null,
        });
        location.hash = "history";
        break;
      case "cancel":
        await action("/api/cancel", { plan_id: button.dataset.plan });
        break;
      case "login":
        await action("/api/login");
        break;
      case "finish-login":
        await action("/api/login/finish");
        break;
      case "refresh-profile":
        await action("/api/profile/refresh");
        break;
      case "import": {
        const file = document.getElementById("zip").files[0];
        if (!file) throw new Error("Выбери ZIP");
        await action("/api/import", { data: await fileData(file) });
        tell("Каталог импортирован");
        break;
      }
    }
  } catch (error) {
    tell(error.message, true);
  } finally {
    button.disabled = false;
  }
});
function openEditor(id) {
  document.getElementById("post-preview").hidden = true;
  selectedBike = state.bikes.find((b) => b.id === id);
  photoOrder = [...selectedBike.photos];
  document.getElementById("editor-title").textContent = selectedBike.name;
  document.getElementById('product-name').value = selectedBike.name;
  document.getElementById("post-heading").value = selectedBike.post_heading || "";
  toggleHeading(!!selectedBike.post_heading);
  document.getElementById("price").value = selectedBike.price;
  document.getElementById("description").value = selectedBike.description;
  document.getElementById("upload").value = "";
  renderPhotos();
  editor.showModal();
}
function openNewEditor() {
  selectedBike = null;
  photoOrder = [];
  document.getElementById('bike-form').reset();
  toggleHeading(false);
  document.getElementById('editor-title').textContent = I18n.t('Новое объявление');
  document.getElementById('post-preview').hidden = true;
  renderPhotos();
  editor.showModal();
  document.getElementById('product-name').focus();
}
document.getElementById("close-editor").onclick = () => editor.close();
function toggleHeading(enabled) {
  document.getElementById('heading-fields').hidden = !enabled;
  document.getElementById('add-heading').hidden = enabled;
  if (!enabled) document.getElementById('post-heading').value = '';
  document.getElementById('post-preview').hidden = true;
}
document.getElementById('add-heading').onclick = () => {
  toggleHeading(true);
  document.getElementById('post-heading').focus();
};
document.getElementById('remove-heading').onclick = () => {
  toggleHeading(false);
  document.getElementById('add-heading').focus();
};
document.getElementById("preview-post").onclick = async () => {
  try {
    const result = await api("/api/post/preview", {
      post_heading: document.getElementById("post-heading").value,
      price: document.getElementById("price").value,
      description: document.getElementById("description").value,
    });
    const preview = document.getElementById("post-preview");
    if (result.heading) {
      const rest = result.text.slice(result.heading.length);
      preview.innerHTML = `<strong>${escape(result.heading)}</strong>${escape(rest)}`;
    } else preview.textContent = result.text;
    preview.hidden = false;
    tell("Предпросмотр готов. Жирный заголовок применяется, если редактор Facebook его поддерживает.");
  } catch (error) { tell(error.message, true); }
};
document.getElementById("bike-form").onsubmit = async (event) => {
  event.preventDefault();
  try {
    const created = !selectedBike;
    const result = await api(created ? '/api/items/create' : `/api/bikes/${selectedBike.id}`, {
      name: document.getElementById('product-name').value,
      post_heading: document.getElementById("post-heading").value,
      price: document.getElementById("price").value,
      description: document.getElementById("description").value,
    });
    tell("Товар сохранён");
    await refresh();
    if (created) openEditor(result.id);
  } catch (error) {
    tell(error.message, true);
  }
};
function renderPhotos() {
  document.getElementById('add-photos').disabled = !selectedBike;
  document.getElementById('upload').disabled = !selectedBike;
  const root = document.getElementById("photos");
  root.innerHTML = photoOrder
    .map(
      (id, index) =>
        `<div class="photo" draggable="true" data-id="${id}"><small>${index === 0 ? "Главное фото" : `Фото ${index + 1}`}</small><img draggable="false" src="/photo/${id}" alt="Фото ${index + 1}"><div class="row"><button type="button" data-move="-1" data-id="${id}" aria-label="Переместить влево">←</button><button type="button" data-move="1" data-id="${id}" aria-label="Переместить вправо">→</button><button type="button" class="danger" data-delete="${id}">✕</button></div></div>`,
    )
    .join("");
  root.querySelectorAll(".photo").forEach((tile) => {
    tile.ondragstart = (event) => {
      event.dataTransfer.setData("text/plain", tile.dataset.id);
      tile.classList.add("dragging");
    };
    tile.ondragend = () => tile.classList.remove("dragging");
    tile.ondragover = (event) => event.preventDefault();
    tile.ondrop = (event) => {
      event.preventDefault();
      movePhoto(
        Number(event.dataTransfer.getData("text/plain")),
        photoOrder.indexOf(Number(tile.dataset.id)),
      );
    };
  });
  I18n.apply(editor);
}
async function movePhoto(id, to) {
  if (
    photoBusy ||
    !photoOrder.includes(id) ||
    to < 0 ||
    to >= photoOrder.length
  )
    return;
  photoBusy = true;
  const next = photoOrder.filter((item) => item !== id);
  next.splice(to, 0, id);
  try {
    await api(`/api/bikes/${selectedBike.id}/order`, { ids: next });
    photoOrder = next;
    renderPhotos();
  } catch (error) {
    tell(error.message, true);
  } finally {
    photoBusy = false;
  }
}
document.getElementById("photos").onclick = async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.move)
    movePhoto(
      Number(button.dataset.id),
      photoOrder.indexOf(Number(button.dataset.id)) +
        Number(button.dataset.move),
    );
  if (button.dataset.delete && !photoBusy) {
    photoBusy = true;
    try {
      const id = Number(button.dataset.delete);
      await api(`/api/bikes/${selectedBike.id}/photo/delete`, { id });
      photoOrder = photoOrder.filter((item) => item !== id);
      renderPhotos();
    } catch (error) {
      tell(error.message, true);
    } finally {
      photoBusy = false;
    }
  }
};
async function fileData(file) {
  if (file.size > 45 * 1024 * 1024)
    throw new Error("Файл слишком большой для веб-загрузки: максимум 45 МБ");
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}
document.getElementById("add-photos").onclick = async (event) => {
  event.target.disabled = true;
  try {
    const files = [...document.getElementById("upload").files];
    if (!files.length) throw new Error("Выбери фотографии");
    if (files.reduce((sum, file) => sum + file.size, 0) > 45 * 1024 * 1024)
      throw new Error("Добавляй фото порциями до 45 МБ");
    const uploads = await Promise.all(
      files.map(async (file) => ({
        name: file.name,
        data: await fileData(file),
      })),
    );
    await api(`/api/bikes/${selectedBike.id}/photos`, { files: uploads });
    await refresh();
    photoOrder = [...state.bikes.find((b) => b.id === selectedBike.id).photos];
    renderPhotos();
    document.getElementById("upload").value = "";
    tell("Фотографии добавлены");
  } catch (error) {
    tell(error.message, true);
  } finally {
    event.target.disabled = false;
  }
};
if ("serviceWorker" in navigator && window.isSecureContext)
  navigator.serviceWorker.register("/sw.js").catch(() => {});
I18n.apply();
refresh(true);
setInterval(() => refresh(), 3000);
