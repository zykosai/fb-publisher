"""Conservative layout improvements; never invent product claims or alter the catalog."""
import re


def validate_post_heading(value) -> str:
    if not isinstance(value, str) or len(value.strip()) > 160 or '\n' in value or '\r' in value:
        raise ValueError('Заголовок должен быть одной строкой до 160 символов')
    return value.strip()


def style_description(description: str, *, promote_heading: bool = False) -> tuple[str, str]:
    text = description.replace('\r\n', '\n')
    text = re.sub(r'(?m)^[\s\u2800\u200b]*$', '', text)
    # Repair glued equipment/contact lines without changing their contents.
    text = re.sub(r'(?<=\S)(?=[🔧⚙🛑💡📍📐🔎🔘])', '\n', text)
    text = re.sub(r'(?<=[.!?])\s*(?=kontakt\s*:)', '\n\n', text, flags=re.I)
    lines, footer = [], []
    for line in text.splitlines():
        line = line.strip()
        if re.fullmatch(r'[⚠!\s\ufe0f]*WIĘCEJ ROWERÓW ZNAJDZIESZ NA MOIM PROFILU[!⚠\s\ufe0f]*', line, re.I):
            footer.append(line)
        else:
            lines.append(line)
    body = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
    # Only promote an explicitly supplied Polish sale sentence, not an internal
    # catalog label like "Велосипед №1" or guessed model/specifications.
    match = re.match(r'Na sprzedaż\s+([^\n.!?]{3,100})[.!?](?:\s+|$)', body, re.I)
    heading = ''
    if match and promote_heading:
        heading = match[1][0].upper() + match[1][1:]
        body = body[match.end():].strip()
    if footer:
        body = '\n\n'.join(filter(None, [body, '\n'.join(footer)]))
    return heading, body
