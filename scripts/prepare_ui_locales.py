"""Build static UI-only locale catalogs; prints an apply_patch patch to stdout.

Developer utility, never called by the application. Only public interface phrases
are sent for translation; no database, account, product or group data is read.
Review generated translations before applying. No runtime translation API needed.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import ast
import json
import re
import sys
import time
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ('zh-CN', 'hi', 'es', 'ar', 'fr', 'bn', 'pt', 'ja')
TEMPLATES = {
    'zh-CN': ['语言','照片 {n}','{n} 张照片','{n}/{total}','已检查：'],
    'hi': ['भाषा','फ़ोटो {n}','{n} फ़ोटो','{n}/{total}','जाँचा गया:'],
    'es': ['Idioma','Foto {n}','{n} fotos','{n} de {total}','Revisado:'],
    'ar': ['اللغة','الصورة {n}','{n} صور','{n} من {total}','تم التحقق:'],
    'fr': ['Langue','Photo {n}','{n} photos','{n} sur {total}','Vérifié :'],
    'bn': ['ভাষা','ছবি {n}','{n}টি ছবি','{n}/{total}','যাচাই করা হয়েছে:'],
    'pt': ['Idioma','Foto {n}','{n} fotos','{n} de {total}','Verificado:'],
    'ja': ['言語','写真 {n}','写真 {n} 枚','{n}/{total}','確認済み：'],
}


def translate_batch(language, pairs):
    query = '\n'.join(f'@@{index}@@ {value}' for index, value in pairs)
    url = 'https://translate.googleapis.com/translate_a/single?' + urlencode({
        'client': 'gtx', 'sl': 'en', 'tl': language, 'dt': 't', 'q': query,
    })
    for attempt in range(3):
        try:
            with urlopen(url, timeout=35) as response:
                result = json.loads(response.read())
            text = ''.join(part[0] or '' for part in result[0])
            matches = list(re.finditer(r'@@\s*(\d+)\s*@@', text))
            translations = {int(match[1]): text[match.end():matches[n+1].start() if n+1 < len(matches) else None].strip()
                            for n, match in enumerate(matches)}
            if set(translations) != {index for index, _ in pairs} or not all(translations.values()):
                raise ValueError(f'Translation markers lost for {language}')
            return translations
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


def main():
    source = (ROOT / 'web' / 'i18n.js').read_text(encoding='utf-8')
    rows = ast.literal_eval(re.search(r'const rows = (\[.*?\n  \]);', source, re.S)[1])
    extras = [
        ['Фото {n}', 'Photo {n}', 'Zdjęcie {n}'],
        ['{n} фото', '{n} photos', '{n} zdjęć'],
        ['{n} из {total}', '{n} of {total}', '{n} z {total}'],
        ['Проверено:', 'Checked:', 'Sprawdzono:'],
    ]
    rows.extend(extras)
    # Preserve the first translation when an old catalog contains duplicate keys.
    rows = list({row[0]: row for row in reversed(rows)}.values())[::-1]
    batches, batch, size = [], [], 0
    for index, row in enumerate(rows):
        if batch and size + len(row[1]) > 1800:
            batches.append(batch)
            batch, size = [], 0
        batch.append((index, row[1]))
        size += len(row[1]) + 14
    if batch:
        batches.append(batch)
    languages = tuple(sys.argv[1:]) or LANGUAGES
    if any(language not in LANGUAGES for language in languages):
        raise ValueError('Unsupported language')
    jobs = [(lang, part) for lang in languages for part in batches]
    results = {lang: {} for lang in languages}
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = [(lang, pool.submit(translate_batch, lang, part)) for lang, part in jobs]
        for lang, future in pending:
            results[lang].update(future.result())
    target = ROOT / 'web' / 'locales.js'
    previous = target.read_text(encoding='utf-8') if target.exists() else ''
    catalogs = json.loads(previous.split('window.MARKET_LOCALES = ',1)[1].strip().removesuffix(';')) if previous else {}
    for language in languages:
        catalogs[language] = {row[0]: results[language][index] for index, row in enumerate(rows)}
        catalogs[language].update(zip(('Язык','Фото {n}','{n} фото','{n} из {total}','Проверено:'),TEMPLATES[language]))
    if 'ja' in catalogs:
        catalogs['ja'].update({'Публикация':'投稿','Публикации':'投稿','Новая публикация':'新規投稿'})
    if 'zh-CN' in catalogs:
        catalogs['zh-CN'].update({'Группы':'群组','Группы Facebook':'Facebook 群组'})
    updated = ('"use strict";\n// Static UI-only translations. No translation service is used at runtime.\n'
        '// User listings, group names, profile names and messages are never sent for translation.\n'
        'window.MARKET_LOCALES = '+json.dumps(catalogs,ensure_ascii=False,indent=2)+';\n')
    print('*** Begin Patch')
    print(f'*** {"Update" if previous else "Add"} File: {target.as_posix()}')
    if previous:
        print('@@')
        for line in previous.splitlines(): print('-'+line)
    for line in updated.splitlines(): print('+'+line)
    print('*** End Patch')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
