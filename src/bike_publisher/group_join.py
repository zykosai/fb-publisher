"""Wait for membership, not a timer; handle only explicit optional join prompts."""
import re
import time


SKIP = re.compile(r'^\s*(Не сейчас|Пропустить|Позже|Not now|Skip|Maybe later|Pomiń|Nie teraz|Później)\s*$', re.I)
CLOSE = re.compile(r'^\s*(Закрыть|Close|Zamknij)\s*$', re.I)
SUBMIT = re.compile(r'^\s*(Вступить(?: в группу)?|Присоединиться(?: к группе)?|Отправить(?: запрос)?|Join(?: group)?|Submit(?: request)?|Send request|Dołącz(?: do grupy)?|Wyślij(?: prośbę)?)\s*$', re.I)


class GroupAccountError(RuntimeError):
    """Account/session issue: do not retry joining other groups."""


class JoinRateLimited(GroupAccountError):
    """Facebook explicitly limits account actions, not just one group."""


def fatal_group_error(error):
    return isinstance(error, GroupAccountError) or any(marker in str(error).lower() for marker in (
        'target page, context or browser has been closed', 'browser has been closed',
        'context has been closed', 'connection closed', 'browser closed'))


def join_limit_text(page):
    # A feed post quoting a restriction is not Facebook restricting this account.
    text = page.evaluate('''() => {
        const lines = [], walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) {
            const node = walker.currentNode, el = node.parentElement;
            if (!el || el.closest('[role="feed"],[role="article"],script,style') || !el.checkVisibility({checkVisibilityCSS:true})) continue;
            const text = node.textContent.trim();
            if (text) lines.push(text);
        }
        return lines.join('\\n');
    }''')
    match = re.search(r'ограничиваем частоту|limit how often|temporarily blocked|ograniczamy częstotliwość', text, re.I)
    return ' '.join(text[max(0, match.start()-100):match.end()+300].split()) if match else ''


def finish_join(publisher, page, *, timeout_seconds=20):
    """Never answer questions, accept rules, cancel requests or retry a join blindly."""
    deadline = time.monotonic() + timeout_seconds
    actions = []
    handled = set()
    while True:
        publisher.checkpoint()
        access = publisher._inspect_group_access(page)
        limit = join_limit_text(page)
        if limit:
            if access.get('joined') is True or access['state'] == 'pending':
                return {**access, 'join_attempted': True, 'join_actions': actions, 'join_rate_limit': limit}
            raise JoinRateLimited(f'Facebook ограничивает действия аккаунта: {limit}. Новые вступления остановлены.')
        if '/checkpoint/' in page.url.lower() or publisher._is_login_url(page.url.lower()):
            raise GroupAccountError('Facebook просит подтвердить вход. Вступление остановлено.')
        dialogs = [dialog for dialog in page.get_by_role('dialog').all() if dialog.is_visible()]
        for dialog in dialogs:
            text = dialog.inner_text(timeout=2000)
            signature = text[:4000]
            if signature in handled:
                continue
            skip = publisher._first_visible([dialog.get_by_role('button', name=SKIP)], timeout_ms=100)
            if skip is not None:
                publisher.checkpoint()
                actions.append(skip.inner_text().strip())
                handled.add(signature)
                skip.click()
                break
            # Mandatory answers and rule acceptance require the user's input.
            fields = dialog.locator('textarea,input:not([type="hidden"]),[role="textbox"],[role="checkbox"],[role="radio"]')
            inputs = [field for field in fields.all() if field.is_visible()]
            has_questions = bool(inputs) or bool(re.search(
                r'ответ\w* на вопрос|вопросы для вступления|правила группы|membership questions|answer (?:the|these|a few) questions|group rules|pytania|regulamin', text, re.I))
            if has_questions and access.get('joined') is not True:
                return {'state': 'questions', 'joined': None, 'join_attempted': True,
                        'request_sent': access['state'] == 'pending',
                        'detail': 'Нужны ответы или принятие правил вручную: ' + ' '.join(text.split())[:900],
                        'join_actions': actions}
            close = publisher._first_visible([dialog.get_by_role('button', name=CLOSE)], timeout_ms=100)
            if close is not None and (access.get('joined') is True or access['state'] == 'pending'):
                actions.append('Закрыто необязательное окно')
                handled.add(signature)
                close.click()
                break
            submit = publisher._first_visible([dialog.get_by_role('button', name=SUBMIT)], timeout_ms=100)
            if submit is not None and not has_questions and re.search(r'групп|group|grup', text, re.I):
                publisher.checkpoint()
                actions.append(submit.inner_text().strip())
                handled.add(signature)
                submit.click()
                break
        else:
            if access.get('joined') is True or access['state'] == 'pending':
                return {**access, 'join_attempted': True, 'join_actions': actions}
        if time.monotonic() >= deadline:
            return {'state': 'join_unconfirmed', 'joined': None, 'join_attempted': True,
                    'detail': 'Кнопка вступления нажата, но Facebook не подтвердил членство или отправку запроса. Повторный клик не выполнялся.',
                    'join_dialogs': [d.inner_text(timeout=2000)[:1500] for d in dialogs if d.is_visible()],
                    'join_actions': actions}
        # This is an actual response wait and must NOT use the Turbo-skippable pause.
        page.wait_for_timeout(250)
