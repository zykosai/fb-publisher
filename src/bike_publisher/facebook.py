from __future__ import annotations

import os
import re
import time
import unicodedata
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse, parse_qs

from .config import Settings
from .control import PublicationPaused
from .groups import clean_group_name, group_relevance, normalize_group_url
from .group_access import classify_access
from .post_style import style_description
from .speed import turbo_enabled
from .group_join import finish_join, GroupAccountError


@dataclass(frozen=True)
class SessionStatus:
    logged_in: bool
    message: str
    account_id: str = ""
    name: str = ""
    avatar_url: str = ""
    profile_url: str = ""
    checked: bool = True


class UnsupportedGroupError(RuntimeError):
    """The group has no ordinary discussion post composer."""


class BatchUnavailable(RuntimeError):
    """The native group picker is unavailable before any submission."""


@dataclass(frozen=True)
class SubmissionReceipt:
    state: str
    url: str
    detail: str = ''


class FacebookPublisher:
    """Open Facebook through a persistent Playwright browser profile."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_session: SessionStatus | None = None
        self.last_batch_selection: dict[str, object] = {}
        self.control = None
        self.progress = None
        self.submission_started = False

    def checkpoint(self):
        if self.control is not None and not self.submission_started:
            self.control()

    def report(self, message):
        if self.progress is not None:
            self.progress(message)

    def launch(self, *, headless: bool | None = None):
        from playwright.sync_api import sync_playwright

        use_headless = self.settings.headless if headless is None else headless
        if os.name != "nt" and not os.environ.get("DISPLAY") and not use_headless:
            raise RuntimeError("На VPS не задан DISPLAY. Запусти VNC/Xvfb и повтори с DISPLAY=:99.")

        self.settings.browser_profile.mkdir(parents=True, exist_ok=True)
        playwright = sync_playwright().start()
        launch_options = {
            "user_data_dir": str(self.settings.browser_profile),
            "headless": use_headless,
            "args": ["--window-size=1100,850", "--window-position=40,40", "--disable-background-mode"],
            "viewport": {"width": 1440, "height": 1000} if use_headless else {"width": 1080, "height": 720},
        }
        if self.settings.browser_channel:
            launch_options["channel"] = self.settings.browser_channel

        try:
            context = playwright.chromium.launch_persistent_context(**launch_options)
        except Exception as exc:
            details = str(exc)
            missing_channel = self.settings.browser_channel and (
                "is not found" in details
                or "Executable doesn't exist" in details
                or "Chromium distribution" in details
            )
            if missing_channel:
                launch_options.pop("channel", None)
                try:
                    context = playwright.chromium.launch_persistent_context(**launch_options)
                except Exception as fallback_exc:
                    playwright.stop()
                    raise self._launch_error(fallback_exc) from fallback_exc
                return playwright, context
            playwright.stop()
            raise self._launch_error(exc) from exc
        return playwright, context

    @staticmethod
    def _launch_error(exc: Exception) -> RuntimeError:
        details = str(exc)
        if "ProcessSingleton" in details or "profile directory" in details or "Lock file" in details:
            return RuntimeError(
                "Профиль Chrome уже занят. Закрой окно входа FB Publisher "
                "и останови worker, затем повтори."
            )
        if "Executable doesn't exist" in details:
            return RuntimeError(
                "Браузер Playwright не установлен. Снова запусти run.ps1, "
                "чтобы установить его."
            )
        first_line = details.splitlines()[0] if details else type(exc).__name__
        return RuntimeError(f"Не удалось открыть Google Chrome: {first_line}")

    def profile_exists(self) -> bool:
        try:
            return self.settings.browser_profile.is_dir() and any(
                self.settings.browser_profile.iterdir()
            )
        except OSError:
            return False

    def login(self, timeout_seconds: int = 600, *, confirmed=None) -> str:
        """Open a visible browser and wait for the user to finish Facebook login."""
        playwright, context = self.launch(headless=False)
        self.last_session = None
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                "https://www.facebook.com/",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            print(
                "Браузер открыт. Войди в Facebook вручную. "
                + ("Затем нажми «Готово, проверить вход» на сайте бота." if confirmed is not None
                   else "После успешного входа это окно закроется автоматически."),
                flush=True,
            )
            self.report("Окно входа открыто. Смени аккаунт в Chrome, затем нажми «Готово, проверить вход» на сайте бота.")
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                try:
                    account_id = self._account_id(context)
                    active_page = context.pages[-1] if context.pages else page
                    url = active_page.url.lower()
                except Exception as exc:
                    raise RuntimeError(
                        "Окно входа было закрыто до завершения авторизации."
                    ) from exc
                if (
                    account_id
                    and (confirmed is None or confirmed())
                    and not self._is_login_url(url)
                    and "/checkpoint/" not in url
                ):
                    name, avatar_url, profile_url = self._read_profile_info(active_page, navigate=False)
                    if not name:
                        try:
                            active_page.goto(
                                f"https://www.facebook.com/profile.php?id={account_id}",
                                wait_until="domcontentloaded", timeout=30000,
                            )
                        except Exception:
                            pass
                        name, avatar_url, profile_url = self._read_profile_info(active_page, navigate=False)
                    self.last_session = SessionStatus(
                        True, "Вход подтверждён. Сессия готова для публикаций.",
                        account_id, name, avatar_url, profile_url,
                    )
                    return account_id
                try:
                    active_page.wait_for_timeout(1000)
                except Exception as exc:
                    raise RuntimeError("Окно входа закрыто. Нажми «Войти / сменить аккаунт» и после входа — «Готово, проверить вход».") from exc
            raise RuntimeError(
                "Время ожидания входа истекло. "
                "Нажми «Войти в Facebook» и попробуй ещё раз."
            )
        finally:
            try:
                context.close()
            finally:
                playwright.stop()

    def verify_login(self) -> SessionStatus:
        """Verify the saved session without publishing anything."""
        if not self.profile_exists():
            return SessionStatus(False, "Профиль браузера ещё не создан.")

        playwright, context = self.launch(headless=True)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                "https://www.facebook.com/me",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(1000)
            url = page.url.lower()
            account_id = self._account_id(context)
            if "/checkpoint/" in url:
                return SessionStatus(
                    False,
                    "Facebook просит подтвердить вход. "
                    "Открой окно входа и пройди проверку.",
                    account_id,
                )
            if account_id and not self._is_login_url(url):
                name, avatar_url, profile_url = self._read_profile_info(page, navigate=False)
                if not name:
                    try:
                        page.goto(
                            f"https://www.facebook.com/profile.php?id={account_id}",
                            wait_until="domcontentloaded", timeout=30000,
                        )
                    except Exception:
                        pass
                    name, avatar_url, profile_url = self._read_profile_info(page, navigate=False)
                return SessionStatus(
                    True,
                    "Вход подтверждён. Сессия готова для публикаций.",
                    account_id,
                    name, avatar_url, profile_url,
                )
            return SessionStatus(
                False,
                "Сессия Facebook отсутствует или истекла. Выполни вход заново.",
            )
        except Exception as exc:
            return SessionStatus(False, f"Не удалось проверить вход: {exc}", checked=False)
        finally:
            try:
                context.close()
            finally:
                playwright.stop()

    @staticmethod
    def _valid_profile_name(value: str) -> bool:
        text = str(value or '').strip()
        return bool(text and text.lower() not in {'facebook', 'редактировать профиль', 'edit profile', 'edytuj profil', 'дополнить историю', 'add to story', 'dodaj do relacji', 'фото', 'photos', 'zdjęcia', 'публикации', 'posts', 'posty'} and not re.fullmatch(
            r'[\d\s.,kкmм]*[—–·:-]?\s*(?:друзья|друзей|друзі|friends|znajomi|znajomych|obserwujący|followers|подписчики|підписники)[\d\s.,kкmм—–·:-]*', text, re.I))

    @staticmethod
    def _read_profile_info(page, *, navigate: bool = True) -> tuple[str, str, str]:
        """Read the signed-in user's public profile header; failure won't break login."""
        try:
            if navigate:
                page.goto("https://www.facebook.com/me", wait_until="domcontentloaded", timeout=30000)
            if FacebookPublisher._is_login_url(page.url.lower()) or "/checkpoint/" in page.url.lower():
                return "", "", ""
            # Facebook sometimes renders /me without an h1 (or delays the
            # profile header while the shell loads). Do not fail the whole
            # identity refresh just because that one selector is absent.
            try:
                page.wait_for_timeout(1200)
            except Exception:
                pass
            info = page.evaluate(r"""() => {
                const visible = el => !!el && el.getBoundingClientRect().width > 0 &&
                    el.getBoundingClientRect().height > 0;
                const validName = value => value && value.toLowerCase() !== 'facebook' &&
                    !/^(?:редактировать профиль|edit profile|edytuj profil|дополнить историю|add to story|dodaj do relacji|фото|photos|zdjęcia|публикации|posts|posty)$/i.test(value) &&
                    !/^[\d\s.,kкmм]*[—–·:-]?\s*(?:друзья|друзей|друзі|friends|znajomi|znajomych|obserwujący|followers|подписчики|підписники)[\d\s.,kкmм—–·:-]*$/i.test(value);
                const mainHeadings = [...document.querySelectorAll('[role="main"] h1, main h1')];
                const heading = [...mainHeadings, ...document.querySelectorAll('h1')]
                    .find(el => visible(el) && validName(el.innerText.trim())) ||
                    [...document.querySelectorAll('[role="main"] span, main span')].find(el => {
                        const text = el.innerText.trim();
                        return visible(el) && parseFloat(getComputedStyle(el).fontSize) >= 28 &&
                            text.length <= 120 && !text.includes('\n') && validName(text);
                    });
                const title = document.title.replace(/^\(\d+\)\s*/, '').replace(/\s*[|–—-]\s*Facebook\s*$/i, '').trim();
                const profileLinks = [...document.querySelectorAll('a[href]')]
                    .filter(visible).filter(el => {
                        const link = new URL(el.href, location.href), own = new URL(location.href);
                        if (link.searchParams.has('sk')) return false;
                        if (link.pathname === '/me' || link.pathname === '/me/') return true;
                        if (own.pathname !== '/' && own.pathname !== '/profile.php') return link.pathname === own.pathname;
                        return own.pathname === '/profile.php' && link.pathname === own.pathname &&
                            !!own.searchParams.get('id') && link.searchParams.get('id') === own.searchParams.get('id');
                    });
                const imageNames = [...document.querySelectorAll('img[alt]')]
                    .filter(visible)
                    .map(el => (el.getAttribute('alt') || '').trim())
                    .filter(value => validName(value) && value.length > 1 &&
                        !/profile|picture|фото|zdjęcie/i.test(value));
                const name = (heading?.innerText?.trim() ||
                    (validName(title) && /[|–—-]\s*Facebook\s*$/i.test(document.title) ? title : '') ||
                    profileLinks.map(el => (el.innerText || el.getAttribute('aria-label') || '').trim())
                        .find(value => validName(value) && value.length > 1) ||
                    imageNames[0] || '');
                const photoLabel = /profile (?:picture|photo)|фото профиля|фотографи.*профил|zdjęcie profilowe/i;
                let root = heading || document.body;
                let avatar = '';
                // Stay near the profile header to avoid selecting a photo from a post.
                for (let depth = 0; root && depth < 8 && !avatar; depth++, root = root.parentElement) {
                    const candidates = [...root.querySelectorAll('img, svg image')].filter(visible);
                    const labelled = candidates.find(el => {
                        const label = [el.getAttribute('alt'), el.getAttribute('aria-label'),
                            el.closest('a')?.getAttribute('aria-label'),
                            el.closest('[role="button"]')?.getAttribute('aria-label')].filter(Boolean).join(' ');
                        const r = el.getBoundingClientRect();
                        return photoLabel.test(label) || (label === name && r.width >= 64);
                    });
                    const square = candidates.find(el => {
                        const r = el.getBoundingClientRect();
                        return r.width >= 100 && r.height >= 100 && Math.abs(r.width - r.height) < 10;
                    });
                    const el = labelled || square;
                    if (el) avatar = el.currentSrc || el.getAttribute('src') ||
                        el.getAttribute('href') || el.getAttribute('xlink:href') || '';
                }
                // Last fallback: the account menu/avatar in Facebook's top
                // bar is available even when the profile header is deferred.
                if (!avatar) {
                    const top = [...document.querySelectorAll('img')].filter(visible)
                        .filter(el => { const r=el.getBoundingClientRect(); return r.top < 180 && r.width >= 32 && r.height >= 32; })
                        .sort((a,b) => b.width*a.height - a.width*b.height)[0];
                    if (top) avatar = top.currentSrc || top.src || '';
                }
                return {name, avatar};
            }""")
            name = " ".join(str(info.get("name") or "").split())[:200]
            if not FacebookPublisher._valid_profile_name(name):
                name = ''
            avatar = str(info.get("avatar") or "")
            if not avatar.startswith("https://"):
                avatar = ""
            return name, avatar, page.url if name else ""
        except Exception:
            return "", "", ""

    @staticmethod
    def _account_id(context) -> str:
        cookies = context.cookies("https://www.facebook.com/")
        return next(
            (
                cookie["value"]
                for cookie in cookies
                if cookie.get("name") == "c_user"
            ),
            "",
        )

    @staticmethod
    def _is_login_url(url: str) -> bool:
        return "/login" in url or "login.php" in url

    def publish(self, job: dict, photo_paths: list[str], *, recipients: list[dict] | None = None, before_submit=None):
        self.checkpoint()
        playwright, context = self.launch()
        try:
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(
                    job["group_url"],
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
            except Exception as exc:
                if "ERR_NETWORK_ACCESS_DENIED" in str(exc):
                    raise RuntimeError(
                        "Среда запуска запретила сетевой доступ к Facebook "
                        "(ERR_NETWORK_ACCESS_DENIED). Запусти run.ps1 в обычном "
                        "Windows PowerShell вне ограниченной среды Codex."
                    ) from exc
                raise
            self._pause(page, multiplier=2)
            if not self._account_id(context) or self._is_login_url(page.url.lower()):
                raise RuntimeError(
                    "Нет активного входа в Facebook. "
                    "Открой веб-панель → Настройки → Войти в Facebook."
                )
            expected = getattr(self.settings, 'expected_account_id', '')
            if expected and self._account_id(context) != expected:
                raise GroupAccountError('Нет активного входа нужного аккаунта: в этом браузерном профиле другой Facebook. Отправка остановлена.')
            if "/checkpoint/" in page.url.lower():
                raise RuntimeError(
                    "Facebook просит подтвердить вход. "
                    "Открой веб-панель → Настройки → Войти в Facebook."
                )

            text = self._build_text(job)
            composer_trigger = self._find_post_composer(page, timeout_ms=5000)
            if composer_trigger is None:
                discussion_tab = self._first_visible(
                    [
                        page.get_by_role("tab", name=self._discussion_names()),
                        page.get_by_role("link", name=self._discussion_names()),
                        page.get_by_text(self._discussion_names(), exact=True),
                    ],
                    timeout_ms=2500,
                )
                if discussion_tab is not None:
                    discussion_tab.click()
                    self._pause(page, multiplier=1.5)
                    composer_trigger = self._find_post_composer(
                        page, timeout_ms=12000
                    )

            textbox = dialog = None
            if composer_trigger is None:
                sell_button = self._first_visible(
                    [
                        page.get_by_role("button", name=self._sell_names()),
                        page.get_by_text(self._sell_names(), exact=True),
                    ],
                    timeout_ms=1500,
                )
                if sell_button is not None:
                    raise UnsupportedGroupError(
                        "Пропущена группа: доступна кнопка «Продать», "
                        "а обычного поля «Напишите что-нибудь…» нет."
                    )
                else:
                    raise UnsupportedGroupError(
                        "Пропущена группа: после открытия вкладки «Обсуждение» "
                        "поле «Напишите что-нибудь…» не найдено."
                    )

            if textbox is None:
                composer_trigger.click()
                self._pause(page)
                textbox, dialog = self._find_post_editor(page, timeout_ms=15000)
                if textbox is None:
                    raise RuntimeError(
                        "Поле публикации открылось, но редактор текста не найден."
                    )
            self.checkpoint()
            self._fill_post_text(textbox, text, bold_heading=bool(str(job.get('post_heading') or '').strip()))
            self._pause(page)
            if photo_paths:
                self.report(f"Загружаю {len(photo_paths)} фото и проверяю порядок")
                self._upload_photos(page, photo_paths, dialog)
            confirmed_recipients = recipients
            if recipients is not None:
                confirmed_recipients = self._add_group_recipients(page, dialog, recipients)
                if not confirmed_recipients:
                    raise RuntimeError('Facebook не добавил ни одной выбранной группы. Публикацию не отправлял.')
            self.report("Проверка объявления перед отправкой" if turbo_enabled(self.settings) else "Проверка объявления перед отправкой: 5 секунд")
            self._pause(page, multiplier=max(1, 5 / self.settings.action_delay_seconds))
            submit = page.get_by_role(
                "button", name=self._submit_names()
            ).last
            if not submit.is_visible(timeout=15000):
                raise RuntimeError("Не найдена кнопка отправки публикации")
            if self._has_rate_limit(page):
                raise RuntimeError("Facebook ограничиваем частоту публикаций: поставь бот на паузу")
            self.checkpoint()
            self.submission_started = True
            if before_submit:
                before_submit(confirmed_recipients or [job])
            submit.click()
            self.report("Проверяю результат отправки в Facebook")
            try:
                state = self._confirm_submission(page, textbox, dialog, timeout_seconds=45)
            except RuntimeError:
                # Disappearance of the form isn't a receipt. Read the actual
                # destination before deciding whether this attempt is unknown.
                state='unknown'
            if recipients is not None:
                # A packet acknowledgement is not proof of publication in every group.
                # Reconciliation is persisted by the worker and starts after two minutes.
                pending_state = 'moderation' if state == 'moderation' else 'sent' if state != 'unknown' else 'unknown'
                receipt={row['id']:(pending_state, '', 'Проверка уведомлений об отправке через 2 минуты.')
                         for row in confirmed_recipients}
                receipt.update({row['id']:('deferred','','Ожидает следующего пакета с учётом лимита Facebook') for row in recipients if row not in confirmed_recipients})
                return receipt
            return SubmissionReceipt(state,'','Проверка уведомлений об отправке через 2 минуты.')
        finally:
            self.submission_started = False
            try:
                context.close()
            finally:
                playwright.stop()

    def publish_batch(self, jobs: list[dict], photo_paths: list[str], *, before_submit=None) -> dict:
        if not jobs:
            return {}
        primary={**jobs[0], 'group_url':jobs[0]['group_url']}
        self._missing_recipients=[]
        def mark_selected(rows):
            if before_submit:
                before_submit(rows)
        url_or_receipt=self.publish(primary,photo_paths,recipients=jobs,before_submit=mark_selected)
        if not isinstance(url_or_receipt,dict):
            raise RuntimeError('Facebook не вернул результат пакетной отправки.')
        receipt=url_or_receipt
        missing_ids={item['id'] for item in getattr(self,'_missing_recipients',[])}
        # Not selectable as an additional recipient here does not mean that
        # ordinary posting is unavailable. Keep it for a later packet/anchor.
        receipt.update({item['id']:('deferred','','Группа не найдена в этом окне; остаётся для следующего пакета.') for item in jobs if item['id'] in missing_ids})
        return url_or_receipt

    def _add_group_recipients(self, page, post_dialog, recipients: list[dict]) -> list[dict]:
        add_button=self._first_enabled_visible([
            post_dialog.get_by_role('button',name=re.compile(r'Добавить группы|Add groups|Dodaj grupy',re.I)),
            page.get_by_role('button',name=re.compile(r'Добавить группы|Add groups|Dodaj grupy',re.I)),
            post_dialog.locator('[aria-label*="Добавить группы" i], [aria-label*="Add groups" i], [aria-label*="Dodaj grupy" i]'),
            page.locator('[aria-label*="Добавить группы" i], [aria-label*="Add groups" i], [aria-label*="Dodaj grupy" i]'),
        ],timeout_ms=15000)
        if add_button is None:
            raise BatchUnavailable('В окне публикации Facebook нет функции «Добавить группы».')
        try:
            add_button.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        add_button.click()
        picker=self._find_group_picker(page, timeout_ms=8000)
        if picker is None:
            raise RuntimeError('Facebook не открыл окно выбора групп.')
        details=picker.inner_text(timeout=3000)
        cap_match=re.search(r'(?:не\s+более|не\s+больше|max(?:imum)?(?:\s+of)?|no\s+more\s+than|up\s+to|maksymalnie|nie\s+więcej\s+niż)\s*(\d+)\s*(?:групп|groups?|grup)',details,re.I)
        if not cap_match:
            raise BatchUnavailable('Не удалось прочитать текущий лимит групп Facebook. Пакет не отправлен.')
        cap=int(cap_match.group(1))
        # Facebook's picker is not consistently exposed as role=dialog or
        # role=checkbox. Include native and aria-backed controls within the
        # verified picker root, not similarly labelled controls elsewhere.
        checkboxes=picker.locator('[role="checkbox"], input[type="checkbox"], [aria-checked="true"], [aria-checked="false"]')
        selected=[]; missing=[]
        primary=recipients[0] if recipients else None
        if primary:
            # The group whose page launched the composer is already the
            # post's recipient; Facebook omits it from its additional-groups
            # picker. publish_batch always opens the composer on recipients[0].
            selected.append(primary)
        else:
            raise RuntimeError('Для пакетной публикации не задана исходная группа.')
        # Clear leftover selected groups in this picker before applying our list.
        for index in range(checkboxes.count()):
            checkbox = checkboxes.nth(index)
            if self._checkbox_checked(checkbox):
                label = checkbox.get_attribute('aria-label', timeout=500) or ''
                if not self._is_exact_group_label(label, primary['group_name']):
                    self.checkpoint()
                    checkbox.click(timeout=3000)
        for recipient in recipients:
            self.checkpoint()
            self.report(f"Выбираю группу: {recipient['group_name']}")
            if primary is recipient and recipient in selected:
                continue
            # The group whose page opened the composer is already selected by
            # Facebook and is intentionally absent from this picker. The
            # Facebook limit applies to additional groups, not that primary
            # destination.
            if len(selected) - (1 if primary else 0) >= cap:
                continue
            group_title = clean_group_name(recipient['group_name'])
            # Pick visible rows directly. Scroll the picker for unloaded rows;
            # never perform a separate Facebook search for each recipient.
            self._scroll_group_picker(picker, reset=True)
            end_repeats = 0
            for scroll_attempt in range(64):
                self.checkpoint()
                if picker.get_by_text(group_title, exact=True).count():
                    break
                moved = self._scroll_group_picker(picker)
                end_repeats = 0 if moved else end_repeats + 1
                page.wait_for_timeout(250)
                if end_repeats >= 3:
                    break
            matches=[]
            # Prefer the visible row containing an exact group-title element.
            # Facebook frequently renders the checkbox and title several DOM
            # levels apart, so matching a generic ancestor can hit a sibling.
            try:
                candidate = self._group_title_checkbox(picker, group_title)
                if candidate is not None:
                    matches.append(candidate)
            except Exception:
                pass
            if matches:
                # exact title lookup succeeded; skip the broad fallback.
                pass
            else:
                matches=[]
            if not matches:
                labels_by_control = checkboxes.evaluate_all("""controls => controls.map((el,index) => {
                    const labels=[el.getAttribute('aria-label')||''];
                    let parent=el.parentElement;
                    for(let depth=0;depth<4 && parent;depth++,parent=parent.parentElement) {
                        const text=(parent.innerText||'').trim();
                        if(text.length<500)labels.push(text);
                    }
                    return {index,labels};
                })""")
                for item in labels_by_control:
                    if any(self._is_exact_group_label(label, recipient['group_name']) for label in item['labels']):
                        matches.append(checkboxes.nth(item['index']))
            # Similar names must never cause a different Facebook group to be
            # selected. If the UI exposes multiple indistinguishable controls,
            # skip the recipient and report it instead of guessing.
            if len(matches)!=1:
                missing.append(recipient)
                continue
            match=matches[0]
            # Selecting a group inserts another copy of its title in the chip
            # strip above the list. A locator anchored to title.nth(0) then
            # resolves to that chip instead of the original checkbox row.
            control_index = checkboxes.evaluate_all(
                '(controls, target) => controls.indexOf(target)', match.element_handle(timeout=1000)
            )
            if control_index < 0:
                missing.append(recipient)
                continue
            match = checkboxes.nth(control_index)
            checked = self._checkbox_checked(match)
            if not checked:
                for _attempt in range(3):
                    try:
                        match.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass
                    # A virtual list may recycle rows when brought into view.
                    # Re-resolve the exact title before freezing its control index.
                    page.wait_for_timeout(100)
                    current = self._group_title_checkbox(picker, group_title)
                    if current is not None:
                        control_index = checkboxes.evaluate_all(
                            '(controls, target) => controls.indexOf(target)', current.element_handle(timeout=1000)
                        )
                        if control_index < 0:
                            break
                        match = checkboxes.nth(control_index)
                    if self._checkbox_checked(match):
                        break
                    match.click(timeout=3000)
                    deadline=time.monotonic()+2.5
                    while time.monotonic()<deadline:
                        if self._checkbox_checked(match):
                            break
                        page.wait_for_timeout(200)
                    if self._checkbox_checked(match):
                        break
            if self._checkbox_checked(match):
                selected.append(recipient)
            else:
                missing.append(recipient)
        if not selected:
            raise RuntimeError('Ни одна из разрешённых групп не появилась в окне Facebook.')
        done=self._first_visible([
            picker.get_by_role('button',name=re.compile(r'Готово|Done|Zrobione',re.I)),
            picker.get_by_role('button',name=re.compile(r'Выбрать|Select|Wybierz',re.I)),
        ],timeout_ms=1500)
        if done is None:
            raise RuntimeError('Окно выбора Facebook не показало кнопку «Готово». Публикация не отправлена.')
        done.click(timeout=5000)
        picker.wait_for(state='hidden', timeout=8000)
        self._missing_recipients=missing
        self.last_batch_selection={
            'requested': len(recipients),
            'selected': len(selected),
            'additional_selected': max(0,len(selected)-(1 if primary else 0)),
            'missing': [item.get('group_name','') for item in missing],
            'limit': cap,
        }
        return selected

    @staticmethod
    def _group_title_checkbox(picker, group_title: str):
        titles = picker.get_by_text(group_title, exact=True)
        for index in range(titles.count()):
            title = titles.nth(index)
            if not title.is_visible(timeout=100):
                continue
            for depth in range(9):
                row = title if depth == 0 else title.locator('xpath=' + '/..' * depth)
                controls = row.locator('[role="checkbox"], input[type="checkbox"], [aria-checked="true"], [aria-checked="false"]')
                count = controls.count()
                if count > 1:
                    break  # Never choose the first checkbox of a multi-row ancestor.
                if count == 1 and controls.first.is_visible(timeout=100):
                    return controls.first
        return None

    @staticmethod
    def _scroll_group_picker(picker, *, reset: bool = False) -> bool:
        return picker.evaluate("""(root, reset) => {
            const scrollers=[root,...root.querySelectorAll('*')].filter(el =>
                el.clientHeight>0 && el.scrollHeight>el.clientHeight+40 &&
                /auto|scroll/.test(getComputedStyle(el).overflowY));
            scrollers.sort((a,b) => (b.scrollHeight-b.clientHeight)-(a.scrollHeight-a.clientHeight));
            const scroller=scrollers[0];
            if(!scroller)return false;
            const before=scroller.scrollTop;
            scroller.scrollTop=reset ? 0 : before+Math.max(250,scroller.clientHeight*.8);
            return Math.abs(scroller.scrollTop-before)>1;
        }""", reset)

    @classmethod
    def _find_group_picker(cls, page, *, timeout_ms: int):
        """Find the visible native recipient-picker panel by its heading/cap."""
        deadline=time.monotonic()+timeout_ms/1000
        heading_pattern=re.compile(r'^(?:Добавить группы|Add groups|Dodaj grupy)$',re.I)
        while time.monotonic()<deadline:
            picker = cls._first_visible([
                page.locator('[role="dialog"][aria-label="Добавить группы"]:visible, [role="dialog"][aria-label="Add groups"]:visible, [role="dialog"][aria-label="Dodaj grupy"]:visible').last
            ], timeout_ms=100)
            if picker is not None:
                for depth in range(9):
                    root = picker if depth == 0 else picker.locator('xpath=' + '/..' * depth)
                    text = root.inner_text(timeout=500)
                    if root.locator('[contenteditable="true"]').count():
                        break
                    if re.search(r'(?:не\s+более|no\s+more\s+than|up\s+to|maksymalnie|nie\s+więcej\s+niż)\s*\d+\s*(?:групп|groups?|grup)', text, re.I) and root.locator('input[type="checkbox"], [role="checkbox"]').count():
                        return root
                page.wait_for_timeout(150)
                continue
            try:
                dialogs=page.get_by_role('dialog')
                for index in range(dialogs.count()-1,-1,-1):
                    dialog=dialogs.nth(index)
                    if dialog.is_visible(timeout=100):
                        text=dialog.inner_text(timeout=500)
                        if re.search(r'(?:не\s+более|no\s+more\s+than|up\s+to|maksymalnie|nie\s+więcej\s+niż)\s*\d+\s*(?:групп|groups?|grup)',text,re.I):
                            return dialog
                headings=page.get_by_text(heading_pattern,exact=True)
                for index in range(headings.count()-1,-1,-1):
                    heading=headings.nth(index)
                    if not heading.is_visible(timeout=100):
                        continue
                    for depth in range(1,10):
                        root=heading.locator('xpath='+('/..'*depth))
                        try:
                            text=root.inner_text(timeout=300)
                            has_controls=root.locator('input, [role="checkbox"], [aria-checked]').count()>0
                            has_cap=bool(re.search(r'(?:не\s+более|no\s+more\s+than|up\s+to|maksymalnie|nie\s+więcej\s+niż)\s*\d+\s*(?:групп|groups?|grup)',text,re.I))
                            if has_controls and has_cap:
                                return root
                        except Exception:
                            continue
            except Exception:
                pass
            page.wait_for_timeout(150)
        return None

    @staticmethod
    def _checkbox_checked(checkbox) -> bool:
        try:
            return bool(checkbox.evaluate("el => el.matches('input[type=checkbox]') ? el.checked : el.getAttribute('aria-checked') === 'true'", timeout=500))
        except Exception:
            try:
                return bool(checkbox.is_checked(timeout=500))
            except Exception:
                return False

    def _probe_group_delivery(self,page,job:dict,text:str,account_id:str, *, target_url: str = '') -> tuple[str,str,str]:
        """A destination is confirmed only by the user's matching post there."""
        try:
            self.checkpoint()
            page.goto(target_url or job['group_url'],wait_until='domcontentloaded',timeout=30000)
            articles=page.get_by_role('article')
            try:
                articles.first.wait_for(state='visible',timeout=5000)
            except Exception:
                return 'unknown',job['group_url'],'Запись не подтверждена в этой группе. Проверь её перед повтором.'
            from .db import Database
            from .workspaces import selected_database
            profile=selected_database(Database(self.settings.db_path), getattr(self.settings, 'account_slot', '') or None).get_setting('facebook_user',{})
            profile_url=profile.get('profile_url','') if profile.get('account_id')==account_id else ''
            normalized=self._normalise_post_text(text)
            try:
                page.wait_for_function(r"""expected => {
                    const normalise=text=>(text||'').toLocaleLowerCase().replace(/[^\p{L}\p{N}_\s]/gu,' ').replace(/\s+/g,' ').trim();
                    return [...document.querySelectorAll('[role="article"]')].some(article =>
                        normalise(article.innerText).includes(expected) && article.querySelector('a[href*="/posts/"]'));
                }""",arg=normalized[:160],timeout=10000)
            except Exception:
                pass
            for index in range(min(articles.count(),15)):
                self.checkpoint()
                article=articles.nth(index)
                raw_content=article.inner_text(timeout=1000)
                content=self._normalise_post_text(raw_content)
                if normalized[:160] not in content and normalized[:12] in content:
                    # A collapsed feed card may show only the price. Expand
                    # its text before testing the full description fingerprint.
                    more = self._first_visible([
                        article.get_by_role('button', name=re.compile(r'^(?:Ещё|Еще|See more|Zobacz więcej|Więcej)$', re.I)),
                        article.get_by_text(re.compile(r'^(?:Ещё|Еще|See more|Zobacz więcej|Więcej)$', re.I), exact=True)
                    ], timeout_ms=150)
                    if more is not None:
                        more.click(timeout=1000)
                        raw_content = article.inner_text(timeout=1000)
                        content = self._normalise_post_text(raw_content)
                if normalized[:160] not in content:
                    continue
                own=False
                permalink = target_url if target_url and urlparse(page.url).path.rstrip('/') == urlparse(target_url).path.rstrip('/') else ''
                author_seen=False
                canonical_group=''
                for link in article.locator('a[href]').all()[:80]:
                    try:
                        href=link.get_attribute('href', timeout=500) or ''
                    except Exception:
                        continue
                    # The author is the first group-user/profile link, not a
                    # mention of the account in another person's text/comments.
                    author=re.search(r'/user/(\d+)(?:[/?#]|$)|[?&]id=(\d+)(?:[&#]|$)',href)
                    if not author_seen and author:
                        author_seen=True
                        own=account_id in author.groups()
                        numeric_group=re.search(r'/groups/(\d+)/user/',href)
                        if numeric_group:
                            canonical_group=f"https://www.facebook.com/groups/{numeric_group.group(1)}"
                    elif not author_seen and profile_url and href.split('?')[0].rstrip('/')==profile_url.split('?')[0].rstrip('/'):
                        author_seen=True
                        own=True
                    if '/posts/' in href or 'multi_permalinks=' in href:
                        post_link = self._post_permalink(href)
                        if post_link:
                            permalink = post_link
                if own and permalink:
                    if canonical_group:
                        from .delivery import bind_identity
                        bind_identity(Database(self.settings.db_path),job['group_id'],canonical_group)
                    if re.search(r'на проверк|ожидает одобр|pending approval|awaiting approval|oczekuje na zatwierdzenie',raw_content,re.I):
                        return 'moderation',permalink,'Facebook показывает твой пост как ожидающий одобрения.'
                    return 'published',permalink or job['group_url'],'Запись с этим текстом найдена от твоего аккаунта в группе.'
            return 'unknown',job['group_url'],'Facebook подтвердил пакет, но отдельный результат этой группы пока не найден. Повтор заблокирован до проверки.'
        except PublicationPaused:
            raise
        except Exception as exc:
            return 'unknown',job['group_url'],f'Не удалось проверить отдельный результат группы: {str(exc)[:300]}'

    @staticmethod
    def _normalise_post_text(text:str) -> str:
        # Facebook renders emoji as images, so inner_text can omit them.
        return ' '.join(re.sub(r'[^\w\s]',' ',text.casefold()).split())

    @staticmethod
    def _normalise_group_label(text:str) -> str:
        value=unicodedata.normalize('NFKD',clean_group_name(text).casefold())
        value=''.join(char for char in value if not unicodedata.combining(char) and unicodedata.category(char)!='Cf')
        return ' '.join(re.sub(r'[^\w]+',' ',value).split())

    @classmethod
    def _is_exact_group_label(cls,candidate:str,expected:str) -> bool:
        """Match a picker checkbox only to an exact visible group-title line."""
        target=cls._normalise_group_label(expected)
        if not target:
            return False
        return any(
            cls._normalise_group_label(line)==target
            for line in candidate.splitlines()
            if line.strip()
        )

    @classmethod
    def _post_permalink(cls, href: str) -> str:
        parsed = urlparse('https://www.facebook.com' + href if href.startswith('/') else href)
        if parsed.scheme != 'https' or parsed.hostname not in ('www.facebook.com', 'facebook.com', 'm.facebook.com'):
            return ''
        route = re.match(r'^/groups/([^/]+)(?:/posts/(\d+))?/?$', parsed.path)
        if not route:
            return ''
        query = parse_qs(parsed.query)
        post_id = route.group(2) or query.get('multi_permalinks', query.get('post_id', ['']))[0]
        return f'https://www.facebook.com/groups/{route.group(1)}/posts/{post_id}/' if post_id.isdigit() else ''

    @classmethod
    def _notification_candidate(cls, row: dict, job: dict) -> str:
        """Match only a fresh delivery notification for the requested group."""
        parsed = urlparse(row.get('href', ''))
        if parsed.scheme != 'https' or parsed.hostname not in ('www.facebook.com', 'facebook.com', 'm.facebook.com'):
            return ''
        query = parse_qs(parsed.query)
        if query.get('notif_t', [''])[0] not in ('group_crossposting_published', 'group_post_approved', 'group_post_pending'):
            return ''
        # These notification links carry a microsecond event timestamp. Do not
        # attribute an older approval of the same bicycle to the current attempt.
        if job.get('attempted_at'):
            try:
                event = datetime.fromtimestamp(int(query.get('notif_id', [''])[0]) / 1_000_000, timezone.utc)
                attempted = datetime.fromisoformat(job['attempted_at'])
                if event < attempted - timedelta(seconds=30) or event > datetime.now(timezone.utc) + timedelta(minutes=5):
                    return ''
            except (ValueError, OverflowError, OSError):
                return ''
        route = re.match(r'^/groups/([^/]+)(?:/posts/(\d+))?/?$', parsed.path)
        if not route:
            return ''
        post_id = route.group(2) or query.get('multi_permalinks', query.get('post_id', ['']))[0]
        if not post_id.isdigit():
            return ''
        identity_matches = normalize_group_url(f'https://www.facebook.com/groups/{route.group(1)}') == normalize_group_url(job['group_url'])
        label = re.search(r'(?:в группе|in (?:the )?group|w grupie)\s+([^\n]+)', row.get('text', ''), re.I)
        label_matches = bool(label and cls._normalise_group_label(label.group(1)) == cls._normalise_group_label(job['group_name']))
        if not (identity_matches or label_matches):
            return ''
        return cls._post_permalink(row['href'])

    def _read_delivery_notifications(self, page) -> list[dict]:
        self.checkpoint()
        self.report('Проверяю уведомления Facebook об отправке и одобрении')
        page.goto('https://www.facebook.com/', wait_until='domcontentloaded', timeout=30000)
        bell = page.get_by_role('button', name=re.compile(r'Уведомления|Notifications|Powiadomienia', re.I)).first
        bell.click(timeout=10000)
        try:
            page.locator('a[href*="notif_t=group_"]').first.wait_for(state='visible', timeout=5000)
        except Exception:
            return []
        # React may render the rows incrementally. Wait for a stable list once,
        # rather than treating the first visible notification as the entire packet.
        previous = -1
        stable = 0
        for _ in range(8):
            self.checkpoint()
            count = page.locator('a[href*="notif_t=group_"]').count()
            stable = stable + 1 if count == previous else 0
            previous = count
            if stable >= 2:
                break
            page.wait_for_timeout(500)
        collected = {}
        end_repeats = 0
        for _ in range(20):
            self.checkpoint()
            rows = page.locator('a[href*="notif_t=group_"]').evaluate_all(
                'links => links.map(a => ({text:a.innerText,href:a.href}))'
            )
            for row in rows:
                collected[row['href']] = row
            moved = page.evaluate("""() => {
                const nodes=[...document.querySelectorAll('*')].filter(el => {
                    const box=el.getBoundingClientRect();
                    return box.width>0 && box.width<850 && el.clientHeight>50 &&
                        el.scrollHeight>el.clientHeight+40 && /auto|scroll/.test(getComputedStyle(el).overflowY) &&
                        el.querySelector('a[href*="notif_t=group_"]');
                }).sort((a,b)=>(b.scrollHeight-b.clientHeight)-(a.scrollHeight-a.clientHeight));
                const scroller=nodes[0];
                if(!scroller)return false;
                const before=scroller.scrollTop;
                scroller.scrollTop+=Math.max(250,scroller.clientHeight*.8);
                return scroller.scrollTop>before+1;
            }""")
            end_repeats = 0 if moved else end_repeats + 1
            page.wait_for_timeout(500)
            if end_repeats >= 3:
                break
        return list(collected.values())

    def check_deliveries(self, jobs: list[dict]) -> dict:
        """Read notifications once for the packet; never open recipient groups."""
        self.checkpoint()
        from .db import Database
        db = Database(self.settings.db_path)
        jobs = [{**job, **(db.one('SELECT attempted_at,origin_queue_id FROM deliveries WHERE queue_id=?', (job['id'],)) or {})} for job in jobs]
        playwright,context=self.launch(headless=True)
        try:
            account_id=self._account_id(context)
            if not account_id:
                raise RuntimeError('Нет активной сессии Facebook для проверки результата.')
            expected = getattr(self.settings, 'expected_account_id', '')
            if expected and account_id != expected:
                raise GroupAccountError('Нет активного входа нужного аккаунта для проверки результата.')
            page=context.pages[0] if context.pages else context.new_page()
            notifications = self._read_delivery_notifications(page)
            results = {}
            for job in jobs:
                self.checkpoint()
                result = ('unknown', '', 'Уведомление о результате пока не найдено. Повторная отправка не запускается.')
                # Without an attempt timestamp an old notification cannot safely
                # be attributed to this task.
                for notification in notifications if job.get('attempted_at') else []:
                    url = self._notification_candidate(notification, job)
                    if not url:
                        continue
                    kind = parse_qs(urlparse(notification['href']).query)['notif_t'][0]
                    state = 'moderation' if kind == 'group_post_pending' else 'published'
                    detail = 'Facebook сообщил в уведомлениях: ожидает одобрения администратора.' if state == 'moderation' else 'Facebook подтвердил публикацию в уведомлениях.'
                    result = (state, url, detail)
                    if state == 'published':
                        break
                results[job['id']] = result
            for job in jobs:
                if job.get('origin_queue_id') != job['id'] or results[job['id']][0] != 'unknown':
                    continue
                child_confirmed = any(row.get('origin_queue_id') == job['id'] and row.get('attempted_at') == job.get('attempted_at') and row['id'] != job['id'] and results[row['id']][0] == 'published' for row in jobs)
                prior_child = db.one("SELECT queue_id FROM deliveries WHERE origin_queue_id=? AND attempted_at=? AND queue_id<>? AND state='published' LIMIT 1", (job['id'], job.get('attempted_at'), job['id']))
                if child_confirmed or prior_child:
                    from .delivery import PACKET_ACCEPTED_DETAIL
                    results[job['id']] = ('sent', '', PACKET_ACCEPTED_DETAIL)
            return results
        finally:
            context.close(); playwright.stop()

    def check_delivery(self,job:dict) -> tuple[str,str,str]:
        return self.check_deliveries([job])[job['id']]

    @staticmethod
    def _group_access_text(page) -> str:
        # Ignore feed posts (including other people's moderation complaints),
        # hidden dialogs and navigation. Read only the group's visible chrome.
        return page.evaluate("""() => {
            const root = document.querySelector('[role="main"]') || document.body;
            const lines = [];
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {
                const node = walker.currentNode, parent = node.parentElement;
                if (!parent || parent.closest('[role="feed"],[role="article"],[role="navigation"],[role="dialog"],script,style')) continue;
                if (!parent.checkVisibility({checkOpacity:true, checkVisibilityCSS:true})) continue;
                const text = node.textContent.trim();
                if (text) lines.push(text);
            }
            return lines.join('\\n').slice(0, 24000);
        }""")

    @staticmethod
    def _group_membership_button(page, names):
        """Only header actions belong to this group, not feed recommendations."""
        for button in page.get_by_role('button', name=names).all():
            if button.is_visible() and button.evaluate("""el => {
                if (el.closest('[role="feed"],[role="article"],[role="dialog"],[role="navigation"],[role="complementary"]')) return false;
                const main = document.querySelector('[role="main"]');
                if (main && !main.contains(el)) return false;
                const tabs = (main || document.body).querySelector('[role="tablist"]');
                return !tabs || !!(el.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING);
            }"""):
                return button
        return None

    def _group_join_button(self, page):
        return self._group_membership_button(page, re.compile(
            r'^\s*(Вступить(?: в группу)?|Присоединиться(?: к группе)?|Join(?: group)?|Dołącz(?: do grupy)?)\s*$', re.I))

    def _inspect_group_access(self, page, *, composer=False, sell=False) -> dict:
        join_button = self._group_join_button(page)
        member = self._group_membership_button(page, re.compile(
            r'^\s*(В группе|Вы участник|Joined|Member|Należysz(?: do grupy)?|Członek(?: grupy)?)\s*$', re.I))
        pending = self._group_membership_button(page, re.compile(
            r'^(Отменить запрос|Запрос отправлен|Запрос подан|Cancel request|Request sent|Requested|Anuluj prośbę|Prośba wysłana|Wysłano prośbę)', re.I))
        return classify_access(self._group_access_text(page), composer=composer, sell=sell,
                               membership=True if member is not None else False if join_button is not None else None,
                               join_pending=pending is not None)

    @contextmanager
    def group_check_session(self):
        """Reuse a single browser for a check batch, including cancellation cleanup."""
        self.checkpoint()
        playwright, context = self.launch()
        self._group_check_context = context
        try:
            yield
        finally:
            self._group_check_context = None
            try:
                context.close()
            finally:
                playwright.stop()

    def inspect_group(self, url: str, *, join: bool = False, expected_account_id: str | None = None) -> dict:
        """Check discussion access; join only an explicitly identified public group."""
        self.checkpoint()
        context = getattr(self, '_group_check_context', None)
        owns_browser = context is None
        if owns_browser:
            playwright, context = self.launch()
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            self._pause(page, multiplier=2)
            actual_account = self._account_id(context)
            if not actual_account or self._is_login_url(page.url.lower()):
                raise GroupAccountError("Нет активного входа в Facebook. Войди в разделе «Профиль».")
            if expected_account_id and actual_account != expected_account_id:
                raise GroupAccountError('Facebook-аккаунт изменился. Проверка группы остановлена без сохранения.')
            access = self._inspect_group_access(page)
            join_result = None
            self.checkpoint()
            if access['state'] in ('blocked', 'removed', 'moderation_limit', 'pending'):
                return access
            join_button = self._group_join_button(page)
            if join and access.get('joined') is not True and join_button is not None:
                body = self._group_access_text(page)
                public = bool(re.search(r"Общедоступная группа|Public group|Grupa publiczna", body, re.I))
                private = bool(re.search(r"Закрытая группа|Private group|Grupa prywatna", body, re.I))
                if not public or private:
                    return {"state": "private", "detail": "Автовступление доступно только в подтверждённо открытые группы", "joined": False}
                self.checkpoint()
                self.report('Нажимаю «Вступить» и жду подтверждения Facebook')
                join_button.click()
                join_result = finish_join(self, page)
                if join_result['state'] in ('questions', 'join_unconfirmed'):
                    return join_result
                # Confirm against a newly loaded group, not only an optimistic UI.
                self.checkpoint()
                page.reload(wait_until='domcontentloaded', timeout=45000)
                page.get_by_role('heading', level=1).first.wait_for(state='visible', timeout=10000)
                confirm_deadline = time.monotonic() + 8
                while True:
                    self.checkpoint()
                    confirmed = self._inspect_group_access(page)
                    if confirmed.get('joined') is True or confirmed['state'] == 'pending' or time.monotonic() >= confirm_deadline:
                        break
                    page.wait_for_timeout(250)
                if confirmed.get('joined') is not True and confirmed['state'] != 'pending':
                    return {'state': 'join_unconfirmed', 'joined': None, 'join_attempted': True,
                            'join_actions': join_result.get('join_actions', []),
                            'join_rate_limit': join_result.get('join_rate_limit', ''),
                            'detail': 'После обновления Facebook не подтвердил вступление или запрос. Повторный клик не выполнялся.'}
                if confirmed['state'] == 'pending':
                    return {**confirmed, 'join_attempted': True, 'join_actions': join_result.get('join_actions', []), 'join_rate_limit': join_result.get('join_rate_limit', '')}
            trigger = self._find_post_composer(page, timeout_ms=5000)
            if trigger is None:
                discussion = self._first_visible([
                    page.get_by_role("tab", name=self._discussion_names()),
                    page.get_by_role("link", name=self._discussion_names()),
                ], timeout_ms=1500)
                if discussion is not None:
                    discussion.click()
                    self._pause(page)
                    trigger = self._find_post_composer(page, timeout_ms=8000)
            sell = None if trigger is not None else self._first_visible([page.get_by_role("button", name=self._sell_names())], timeout_ms=1000)
            self.checkpoint()
            result = self._inspect_group_access(page, composer=trigger is not None, sell=sell is not None)
            if join_result:
                result.update(join_attempted=True, join_actions=join_result.get('join_actions', []))
                if join_result.get('join_rate_limit'):
                    result['join_rate_limit'] = join_result['join_rate_limit']
            return result
        finally:
            if owns_browser:
                try:
                    context.close()
                finally:
                    playwright.stop()

    def discover_groups(
        self, queries: list[str], limit: int = 20, city: str = ""
    ) -> list[dict[str, str]]:
        """Collect, de-duplicate, and rank group links from Facebook search."""
        playwright, context = self.launch()
        found: dict[str, dict[str, str]] = {}
        try:
            page = context.pages[0] if context.pages else context.new_page()
            for query in queries:
                page.goto(
                    "https://www.facebook.com/search/groups/?"
                    + urlencode({"q": query}),
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                try:
                    page.locator("a[href*='/groups/']").first.wait_for(
                        state="visible", timeout=max(1800, round(self.settings.action_delay_seconds * 1000))
                    )
                except Exception:
                    pass
                for scroll_round in range(2):
                    for anchor in page.locator("a[href*='/groups/']").all():
                        href = anchor.get_attribute("href") or ""
                        if href.startswith("/"):
                            href = "https://www.facebook.com" + href
                        canonical_url = normalize_group_url(href)
                        if not canonical_url:
                            continue
                        try:
                            label = (
                                anchor.inner_text(timeout=1000)
                                or anchor.get_attribute("aria-label")
                                or ""
                            )
                        except Exception:
                            label = ""
                        label = " ".join(label.split())
                        label = re.sub(r"\s*[|–-]\s*Facebook\s*$", "", label, flags=re.I)
                        label = clean_group_name(label)
                        if len(label) < 3 or label.casefold() in {
                            "groups", "group", "группы", "группа", "grupy", "grupa"
                        }:
                            continue
                        candidate = {"name": label[:200], "url": canonical_url, "reason": f"По запросу: {query}"}
                        current = found.get(canonical_url)
                        if not current or group_relevance(label, city) > group_relevance(
                            current["name"], city
                        ):
                            found[canonical_url] = candidate
                    if scroll_round < 1:
                        page.mouse.wheel(0, 900)
                        prior=page.locator("a[href*='/groups/']").count()
                        try:
                            page.wait_for_function(
                                "count => document.querySelectorAll(\"a[href*='/groups/']\").length > count",
                                arg=prior, timeout=1000)
                        except Exception:
                            pass
        finally:
            try:
                context.close()
            finally:
                playwright.stop()
        ranked = sorted(
            found.values(),
            key=lambda candidate: (
                -group_relevance(candidate["name"], city),
                candidate["name"].casefold(),
            ),
        )
        return ranked[: max(1, int(limit))]

    def discover_my_groups(self) -> list[dict[str, str]]:
        """Read group links exposed by Facebook's joined-groups directory."""
        playwright, context = self.launch(headless=True)
        found: dict[str, dict[str, str]] = {}
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto('https://www.facebook.com/groups/joins/', wait_until='domcontentloaded', timeout=60000)
            if not self._account_id(context) or self._is_login_url(page.url.lower()):
                raise RuntimeError('Нет активного входа в Facebook. Войди через настройки и обнови список «Мои группы».')
            more = re.compile(r'Показать ещё|Показать еще|See more|Show more|Zobacz więcej|Pokaż więcej', re.I)
            for _ in range(30):
                self._collect_group_links(page, found, reason='Мои группы Facebook')
                button = self._first_visible([page.get_by_role('button', name=more)], timeout_ms=250)
                if button:
                    try:
                        button.click(timeout=800)
                    except Exception:
                        button = None
                if not button:
                    previous = len(found)
                    page.mouse.wheel(0, 1200)
                    page.wait_for_timeout(500)
                    self._collect_group_links(page, found, reason='Мои группы Facebook')
                    if len(found) == previous:
                        break
            return sorted(found.values(), key=lambda row: row['name'].casefold())
        finally:
            try:
                context.close()
            finally:
                playwright.stop()

    @staticmethod
    def _collect_group_links(page, found: dict, *, reason: str) -> None:
        for anchor in page.locator('a[href*="/groups/"]').all():
            href = anchor.get_attribute('href') or ''
            if href.startswith('/'):
                href = 'https://www.facebook.com' + href
            canonical = normalize_group_url(href)
            if not canonical:
                continue
            try:
                name = ' '.join((anchor.inner_text(timeout=500) or anchor.get_attribute('aria-label', timeout=500) or '').split())
            except Exception:
                name = ''
            name = re.sub(r'\s*[|–-]\s*Facebook\s*$', '', name, flags=re.I)
            if re.search(r'не\s*прочитан',name,re.I) and re.search(r'\sв группе\s',name,re.I):
                name=re.split(r'\sв группе\s',name,flags=re.I)[-1]
            name=re.split(r'\s+(?:Последние действия|Последняя активность|Недавняя активность|Recent activity|Your recent activity|Ostatnia aktywność|Ostatnie działania)\s*:?',name,flags=re.I)[0]
            name=clean_group_name(name.strip(' .·\u00a0'))
            if len(name) < 3 or name.casefold() in {'groups','group','группы','grupy'}:
                continue
            found.setdefault(canonical, {'name': name[:200], 'url': canonical, 'reason': reason})

    @staticmethod
    def _composer_names():
        return re.compile(
            r"Напишите что-нибудь|Написать что-нибудь|"
            r"Write something|What's on your mind|"
            r"Napisz coś|O czym myślisz",
            re.I,
        )

    @staticmethod
    def _discussion_names():
        return re.compile(r"Обсуждение|Discussion|Dyskusja", re.I)

    @staticmethod
    def _sell_names():
        return re.compile(r"^\s*(Продать|Sell|Sprzedaj)\s*$", re.I)

    @staticmethod
    def _photo_names():
        return re.compile(
            r"Фото\s*/\s*видео|Фото и видео|Photo\s*/\s*video|"
            r"Photo and video|Zdjęcie.*(?:wideo|film)|Zdjęcia.*(?:wideo|film)",
            re.I,
        )

    @staticmethod
    def _photo_preview_count(scope) -> int | None:
        """Count visible photo tiles plus photos collapsed into Facebook's '+N' tile."""
        visible_images = 0
        try:
            images = scope.locator("img")
            for index in range(images.count()):
                image = images.nth(index)
                if not image.is_visible(timeout=100):
                    continue
                try:
                    metrics = image.evaluate(
                        """img => {
                            const rect = img.getBoundingClientRect();
                            return {
                                src: img.currentSrc || img.src || '',
                                width: rect.width,
                                height: rect.height,
                                naturalWidth: img.naturalWidth,
                                naturalHeight: img.naturalHeight,
                            };
                        }"""
                    )
                    is_local_preview = str(metrics["src"]).startswith(
                        ("blob:", "data:image/")
                    )
                    is_large_image = (
                        metrics["width"] >= 64
                        and metrics["height"] >= 64
                        and metrics["naturalWidth"] >= 100
                        and metrics["naturalHeight"] >= 100
                    )
                    if is_local_preview or is_large_image:
                        visible_images += 1
                except Exception:
                    # Older/fake locators may not expose evaluate(); the
                    # Facebook previews in the UI are still recognized by src.
                    try:
                        src = image.get_attribute("src") or ""
                    except Exception:
                        src = ""
                    if src.startswith(("blob:", "data:image/")):
                        visible_images += 1
        except Exception:
            return None

        try:
            composer_text = scope.inner_text(timeout=1000)
        except Exception:
            composer_text = ""

        collapsed_counts = []
        collapsed_patterns = (
            r"(?:и\s*)?ещ[её]\s*(\d+)",
            r"and\s+(\d+)\s+more",
            r"i\s+jeszcze\s+(\d+)",
            r"i\s+(\d+)\s+więcej",
        )
        for pattern in collapsed_patterns:
            collapsed_counts.extend(
                int(value)
                for value in re.findall(pattern, composer_text, re.I)
            )
        # Facebook's “+N more” tile replaces one of the visible thumbnails;
        # it is not N thumbnails in addition to the visible image tiles.
        collapsed = max(collapsed_counts, default=0)
        return visible_images - 1 + collapsed if collapsed else visible_images

    @classmethod
    def _wait_for_photo_preview(
        cls, page, scope, expected_count: int, *, timeout_seconds: float, check=None
    ) -> int | None:
        """Wait until Facebook renders the complete batch of attached images."""
        deadline = time.monotonic() + timeout_seconds
        count = None
        while True:
            if check:
                check()
            current_count = cls._photo_preview_count(scope)
            if current_count is not None:
                count = current_count
            if count is not None and count >= expected_count:
                return count
            if time.monotonic() >= deadline:
                return count
            page.wait_for_timeout(500)

    @staticmethod
    def _find_multiple_image_input(page):
        """Return an image-capable multi-file input, if Facebook exposes one."""
        try:
            inputs = page.locator('input[type="file"][multiple]')
            for index in range(inputs.count()):
                candidate = inputs.nth(index)
                accept = (candidate.get_attribute("accept") or "").lower()
                if not accept or "image" in accept:
                    return candidate
        except Exception:
            pass
        return None

    def _upload_photos(self, page, photo_paths: list[str], dialog, *, verify_order: bool = True) -> None:
        self.checkpoint()
        if not photo_paths:
            return
        scope = dialog if dialog is not None else page
        known_before = self._photo_preview_count(scope)
        if known_before:
            raise RuntimeError(
                f"Черновик Facebook уже содержит {known_before} фото. "
                "Очисти фото в этом черновике перед новой загрузкой."
            )
        minimum_count = (known_before or 0) + len(photo_paths)

        def find_photo_button():
            try:
                return self._first_visible(
                    [
                        scope.get_by_role("button", name=self._photo_names()),
                        page.get_by_role("button", name=self._photo_names()),
                        scope.get_by_text(self._photo_names(), exact=False),
                    ],
                    timeout_ms=1500,
                )
            except Exception:
                return None

        # Use Facebook's composer control first so the files go to the post's
        # photo picker, not an unrelated hidden input elsewhere on the page.
        photo_button = find_photo_button()
        chooser = None
        chooser_error = None
        if photo_button is not None:
            try:
                with page.expect_file_chooser(timeout=5000) as chooser_info:
                    photo_button.click()
                chooser = chooser_info.value
            except Exception as exc:
                chooser_error = exc

        if chooser is not None and chooser.is_multiple():
            # Playwright preserves this list order in the single native file
            # selection, so Facebook receives the configured primary photo first.
            chooser.set_files(photo_paths)
        else:
            image_input = self._find_multiple_image_input(page)
            if image_input is not None:
                image_input.set_input_files(photo_paths)
            elif chooser is not None:
                raise RuntimeError(
                    "Facebook открыл только выбор одного файла. Не удалось "
                    "найти пакетное поле загрузки фото; публикация остановлена, "
                    "чтобы не менять порядок или состав объявления."
                )
            elif photo_button is None:
                raise RuntimeError(
                    "Facebook не открыл выбор фотографий, и пакетное поле "
                    "загрузки не найдено."
                ) from chooser_error
            else:
                raise RuntimeError(
                    "Не удалось открыть пакетную загрузку фото Facebook. "
                    "Однофайловая загрузка отключена, чтобы не нарушить порядок."
                ) from chooser_error

        # Wait once for the entire batch. Don't start another picker action per
        # photo: Facebook's media grid can reorder/replace items during that flow.
        preview_count = self._wait_for_photo_preview(
            page, scope, minimum_count, timeout_seconds=60, check=self.checkpoint
        )
        if preview_count is None:
            raise RuntimeError(
                "Facebook принял файлы, но не показал проверяемые фото в форме. "
                "Публикация остановлена, чтобы не отправить объявление без фото."
            )
        if preview_count < minimum_count:
            raise RuntimeError(
                "Facebook принял не все фотографии: "
                f"{preview_count} из {minimum_count}. Публикация остановлена, "
                "чтобы не отправлять неполное объявление."
            )
        if verify_order:
            self._verify_photo_order(page,photo_paths,check=self.checkpoint)

    @classmethod
    def _verify_photo_order(cls,page,photo_paths:list[str], *, check=None) -> None:
        """Compare Facebook's full photo editor to the exact local file sequence."""
        edit=cls._first_visible([
            page.get_by_role('button',name=re.compile(r'Редактировать всё|Редактировать все|Edit all|Edytuj wszystko',re.I)),
            page.get_by_text(re.compile(r'Редактировать всё|Редактировать все|Edit all|Edytuj wszystko',re.I),exact=False),
        ],timeout_ms=4000)
        if edit is None:
            raise RuntimeError('Facebook не открыл полный список фотографий для проверки порядка.')
        edit.click()
        photo_editor=cls._find_photo_editor(page,expected_images=len(photo_paths),timeout_ms=10000)
        if photo_editor is None:
            raise RuntimeError('Facebook открыл окно фотографий, но его редактор не загрузился.')
        deadline=time.monotonic()+20
        browser_hashes=[]
        while time.monotonic()<deadline:
            if check:
                check()
            try:
                browser_hashes=photo_editor.locator('[draggable="true"]').evaluate_all("""async tiles=>{
                    // One draggable tile is one attachment. Facebook renders
                    // two image layers per tile and keeps the composer behind it.
                    const selected=tiles.map(tile=>tile.querySelector('img')).filter(Boolean);
                    return await Promise.all(selected.map(async img=>{
                        const bytes=await (await fetch(img.currentSrc||img.src, {
                            signal: AbortSignal.timeout(5000)
                        })).arrayBuffer();
                        const hash=await crypto.subtle.digest('SHA-256',bytes);
                        return [...new Uint8Array(hash)].map(v=>v.toString(16).padStart(2,'0')).join('');
                    }));
                }""")
            except Exception:
                browser_hashes=[]
            if len(browser_hashes)==len(photo_paths):
                break
            page.wait_for_timeout(250)
        if len(browser_hashes)!=len(photo_paths):
            try:
                editor_debug=photo_editor.evaluate("root=>({role:root.getAttribute('role'),tag:root.tagName,images:root.querySelectorAll('img').length,text:(root.innerText||'').slice(0,160)})")
            except Exception:
                editor_debug={}
            raise RuntimeError(f"В полном редакторе Facebook найдено {len(browser_hashes)} фото из {len(photo_paths)}. Объявление не отправлено. DOM: {editor_debug}")
        import hashlib
        from pathlib import Path
        expected=[hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in photo_paths]
        if browser_hashes!=expected:
            raise RuntimeError('Порядок фотографий в Facebook отличается от сохранённого. Объявление не отправлено.')
        ready=cls._first_visible([photo_editor.get_by_role('button',name=re.compile(r'^Готово$|^Done$|^Zrobione$',re.I))],timeout_ms=3000)
        if ready is None:
            raise RuntimeError('Facebook не показал кнопку «Готово» после проверки фотографий.')
        ready.click()
        page.wait_for_timeout(250)

    @classmethod
    def _find_photo_editor(cls,page,*,expected_images:int,timeout_ms:int):
        """Select the photo editor itself, not the enclosing post or page DOM."""
        deadline=time.monotonic()+timeout_ms/1000
        heading_pattern=re.compile(r'^(?:Фото\/видео|Photo\/video|Zdjęcia i filmy)$',re.I)
        while time.monotonic()<deadline:
            editor = cls._first_visible([
                page.locator('[role="dialog"][aria-label="Фото/видео"]:visible, [role="dialog"][aria-label="Photo/video"]:visible, [role="dialog"][aria-label="Zdjęcia i filmy"]:visible').last
            ], timeout_ms=100)
            if editor is not None:
                for depth in range(9):
                    root = editor if depth == 0 else editor.locator('xpath=' + '/..' * depth)
                    if root.locator('[contenteditable="true"]').count():
                        break
                    if root.locator('[draggable="true"]').count() >= expected_images:
                        return root
                page.wait_for_timeout(150)
                continue
            heading=cls._first_visible([page.get_by_text(heading_pattern,exact=True)],timeout_ms=100)
            if heading is not None:
                for depth in range(1,10):
                    root=heading.locator('xpath='+('/..'*depth))
                    try:
                        text=root.inner_text(timeout=200)
                        image_count=root.locator('[draggable="true"]').count()
                        if root.locator('[contenteditable="true"]').count():
                            break
                        if image_count>=expected_images and re.search(r'Добавить фото/видео|Add photos?/videos?|Dodaj zdjęcia',text,re.I) and re.search(r'Готово|Done|Zrobione',text,re.I):
                            return root
                    except Exception:
                        break
            page.wait_for_timeout(150)
        return None

    @classmethod
    def _find_post_composer(cls, page, *, timeout_ms: int):
        names = cls._composer_names()
        return cls._first_visible(
            [
                page.get_by_role("button", name=names),
                page.get_by_role("textbox", name=names),
                page.locator(
                    '[aria-label*="Напишите что-нибудь" i], '
                    '[aria-label*="Написать что-нибудь" i], '
                    '[aria-label*="Write something" i], '
                    '[aria-label*="Napisz coś" i], '
                    '[placeholder*="Напишите что-нибудь" i], '
                    '[placeholder*="Write something" i], '
                    '[data-placeholder*="Напишите что-нибудь" i], '
                    '[data-placeholder*="Write something" i]'
                ),
                page.get_by_text(names, exact=False),
            ],
            timeout_ms=timeout_ms,
        )

    @classmethod
    def _find_post_editor(cls, page, *, timeout_ms: int):
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            dialog = cls._first_visible(
                [page.get_by_role("dialog")], timeout_ms=100
            )
            if dialog is not None:
                editor = cls._first_visible(
                    [
                        dialog.get_by_role("textbox", name=cls._composer_names()),
                        dialog.locator('[contenteditable="true"][role="textbox"]'),
                        dialog.locator('[contenteditable="true"]'),
                        dialog.locator("textarea"),
                    ],
                    timeout_ms=100,
                    prefer_last=True,
                )
                if editor is not None:
                    return editor, dialog

            editor = cls._first_visible(
                [
                    page.get_by_role("textbox", name=cls._composer_names()),
                    page.locator('[contenteditable="true"][role="textbox"]'),
                    page.locator('[contenteditable="true"]'),
                    page.locator("textarea"),
                ],
                timeout_ms=100,
                prefer_last=True,
            )
            if editor is not None:
                return editor, dialog
            if time.monotonic() >= deadline:
                return None, dialog
            time.sleep(0.2)

    @staticmethod
    def _confirm_submission(
        page, textbox, dialog, *, timeout_seconds: int = 45
    ) -> str:
        moderation_names = re.compile(r"на проверк|ожидает одобр|pending approval|awaiting approval|oczekuje na zatwierdzenie",re.I)
        success_names = re.compile(
            r"Публикация опубликована|Публикация отправлена|"
            r"Your post (?:is|has been) published|Post shared|"
            r"Opublikowano|Twój post został opublikowany|Post created|"
            r"Your post has been submitted|Post submitted",
            re.I,
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if FacebookPublisher._has_rate_limit(page):
                raise RuntimeError("Facebook ограничил частоту публикаций")
            if dialog is not None:
                try:
                    if not dialog.is_visible(timeout=100):
                        body=page.locator('body').inner_text(timeout=1000)
                        if moderation_names.search(body): return 'moderation'
                        if success_names.search(body): return 'sent'
                        raise RuntimeError('Форма закрылась без подтверждения Facebook. Результат неизвестен; проверь группу перед повтором.')
                except RuntimeError:
                    raise
                except Exception:
                    pass
            # Disappearance or clearing of the textbox isn't a submission receipt.
            try:
                confirmation = page.get_by_text(success_names, exact=False)
                if confirmation.count() and confirmation.first.is_visible(timeout=100):
                    message=confirmation.first.inner_text(timeout=300)
                    return 'moderation' if moderation_names.search(message) else 'sent'
            except Exception:
                pass
            page.wait_for_timeout(750)
        raise RuntimeError(
            "Facebook не подтвердил отправку: форма публикации осталась открыта. "
            "Проверь сообщение об ошибке в окне браузера."
        )

    @staticmethod
    def _first_visible(locators, *, timeout_ms: int, prefer_last: bool = False):
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            for locator in locators:
                try:
                    count = locator.count()
                    indexes = range(count - 1, -1, -1) if prefer_last else range(count)
                    for index in indexes:
                        candidate = locator.nth(index)
                        if candidate.is_visible(timeout=100):
                            return candidate
                except Exception:
                    continue
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.2)

    @staticmethod
    def _first_enabled_visible(locators, *, timeout_ms: int, prefer_last: bool = False):
        """Find a visible control after Facebook finishes its loading overlay."""
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            for locator in locators:
                try:
                    count = locator.count()
                    indexes = range(count - 1, -1, -1) if prefer_last else range(count)
                    for index in indexes:
                        candidate = locator.nth(index)
                        if not candidate.is_visible(timeout=100) or not candidate.is_enabled(timeout=100):
                            continue
                        try:
                            disabled = candidate.get_attribute('aria-disabled')
                            if disabled and disabled.casefold() == 'true':
                                continue
                        except Exception:
                            pass
                        return candidate
                except Exception:
                    continue
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.2)

    @staticmethod
    def _submit_names():
        return re.compile(
            r"Опубликовать|Publish|Post|Отправить|Opublikuj|Publikuj",
            re.I,
        )

    @staticmethod
    def _has_rate_limit(page) -> bool:
        body = page.locator("body").inner_text(timeout=3000).lower()
        return (
            "ограничиваем частоту" in body
            or "limit how often" in body
            or "temporarily blocked" in body
            or "ograniczamy częstotliwość" in body
        )

    def _pause(self, page, *, multiplier: float = 1.0) -> None:
        remaining = max(500, round(self.settings.action_delay_seconds * multiplier * 1000))
        while remaining > 0:
            self.checkpoint()
            if turbo_enabled(self.settings):
                break
            duration = min(250, remaining)
            page.wait_for_timeout(duration)
            remaining -= duration
        self.checkpoint()

    @staticmethod
    def _remove_duplicate_price(description: str, price: str) -> str:
        if not price:
            return description.strip()
        price_line = re.compile(
            rf"^\s*(?:cena|цена|price)\s*[:=\-]?\s*{re.escape(price)}"
            rf"(?:\s*(?:zł|zl|pln))?\s*(?:[–—-]\s*)?(.*)$",
            re.I,
        )
        cleaned = []
        for line in description.splitlines():
            match = price_line.match(line)
            if match:
                line = match.group(1).strip()
                if line:
                    line = line[0].upper() + line[1:]
            cleaned.append(line)
        return re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned)).strip()

    @staticmethod
    def _fill_post_text(textbox, text: str, *, bold_heading: bool = False) -> bool:
        """Use native rich text when supported, leaving plain text intact otherwise."""
        textbox.fill(text)
        if not bold_heading or not text or textbox.get_attribute('contenteditable') != 'true':
            return False
        try:
            textbox.press('Control+Home')
            textbox.press('Shift+End')
            textbox.press('Control+b')
            bold = textbox.evaluate('''root => {
                const selection = window.getSelection();
                if (!selection || !selection.rangeCount) return false;
                const range = selection.getRangeAt(0);
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                let found = false;
                while (walker.nextNode()) {
                    const node = walker.currentNode;
                    if (!node.textContent.trim() || !range.intersectsNode(node)) continue;
                    if (parseInt(getComputedStyle(node.parentElement).fontWeight, 10) < 600) return false;
                    found = true;
                }
                return found;
            }''')
            # Formatting must never eat a character, alter the price or lose text.
            actual = textbox.inner_text()
            if ' '.join(actual.split()) != ' '.join(text.split()):
                textbox.fill(text)
                return False
            return bold
        except Exception:
            # Some Facebook composer variants do not implement rich text.
            # Restore the complete plain text; a closed editor still raises here.
            textbox.fill(text)
            return False
        finally:
            textbox.press('Control+End')

    @staticmethod
    def _build_text(job: dict) -> str:
        lines = []
        price = str(job.get("price") or "").strip()
        description = str(job.get("description") or "").strip()
        description = FacebookPublisher._remove_duplicate_price(description, price)
        custom_heading = str(job.get('post_heading') or '').strip()
        _, description = style_description(description, promote_heading=False)
        if custom_heading:
            lines.append(custom_heading)
        if description:
            if lines:
                lines.append("")
            lines.append(description)
        if price:
            if lines:
                lines.append('')
            lines.append(f'Cena: {price} zł')
        return "\n".join(lines)
