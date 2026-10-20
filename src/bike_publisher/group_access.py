"""Group access signals, without treating pending moderation as rejection."""
from __future__ import annotations

import re


PENDING = r'(?:Ожидает подтверждения администратора|Ожидают одобрения|Публикации на рассмотрении|На проверке|Pending posts|Awaiting admin approval|Pending admin approval|Oczekujące posty|Oczekuje na zatwierdzenie)'
REJECTED = r'(?:Отклон[её]нные публикации|Отклонено|Declined posts|Rejected posts|Odrzucone posty)'


def _count(text: str, label: str) -> int | None:
    # A count must be attached to its own heading, not picked from nearby dates,
    # member totals, another post or a different moderation heading.
    for pattern in (rf'{label}\s*[:·(]?\s*(\d[\d\u00a0 ]*)\s*(?:\)|публикаци\w*|posts?\b|post\w*\b|$)',
                    rf'(\d+)\s+(?:публикаци\w*\s+)?{label}'):
        match = re.search(pattern, text, re.I | re.M)
        if match:
            return int(re.sub(r'\s', '', match.group(1)))
    return None


def classify_access(text: str, *, composer: bool = False, sell: bool = False,
                    membership: bool | None = None, join_pending: bool = False) -> dict:
    result = {'joined': membership, 'pending_count': _count(text, PENDING),
              'rejected_count': _count(text, REJECTED)}
    pending = bool(re.search(PENDING, text, re.I))
    blocked = re.search(r'Вас заблокировали в этой группе|Вы заблокированы в этой группе|Вам запрещено публиковать в этой группе|Вы временно отстранены от участия в этой группе|You(?: have been| are|.ve been) (?:blocked|banned|suspended) (?:from|in) this group|Zablokowano Cię w tej grupie|Zawieszono Cię w tej grupie', text, re.I)
    removed = re.search(r'Вас (?:удалили|исключили) из (?:этой )?группы|You(?: have been|.ve been| were) removed from (?:this|the) group|Usunięto Cię z (?:tej )?grupy', text, re.I)
    limited = re.search(r'Достигнуто ограничение по контенту на рассмотрении в этой группе|Вы достигли лимита (?:публикаций|контента) на рассмотрении|You(?: have|.ve)? reached (?:the|your) (?:limit (?:for|of) pending|pending (?:post|content) limit)|Osiągnięto limit oczekujących (?:postów|treści)', text, re.I)
    if blocked:
        state, detail = 'blocked', blocked.group(0)
        result['joined'] = None  # A posting suspension does not imply expulsion.
    elif removed:
        state, detail = 'removed', removed.group(0)
        result['joined'] = False
    elif join_pending:
        state, detail = 'pending', 'Запрос на вступление ожидает подтверждения'
        result['joined'] = False
    elif membership is False:
        state, detail = 'not_member', 'Facebook показывает кнопку вступления; причина отсутствия в группе неизвестна'
    elif limited:
        state, detail = 'moderation_limit', limited.group(0)
    elif composer:
        state, detail = 'ready', 'Есть обычное поле публикации'
    elif sell:
        state, detail = 'sell_only', 'Только «Продать»'
    elif pending:
        state, detail = 'moderation', 'Публикации ожидают одобрения; обычное поле публикации недоступно'
    else:
        state, detail = 'unavailable', 'Обычное поле публикации не найдено; блокировка не подтверждена'
    result.update(state=state, detail=detail)
    return result
