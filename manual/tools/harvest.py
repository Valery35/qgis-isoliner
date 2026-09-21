# -*- coding: utf-8 -*-
"""Сложить пары «было -> стало» из рабочих пачек в один файл на диске.

Пачки лежали во временной папке сеанса и исчезли бы вместе с ней, а это
материал живой вычитки, а не выдуманные примеры. Каждая пара помечается
тем правилом, ради которого она сделана, чтобы её можно было взять
примером в свод правил.
"""
import json
import pathlib
import re

SRC = pathlib.Path('/tmp/claude-0')
OUT = pathlib.Path('/home/claude/rel/manual/text-edits-5.13.16.json')

ORDER = ['b1', 'b3', 'b4', 'b5', 'b6', 'b7', 'b8', 'b9', 'b10', 'b11',
         'b12', 'b13', 'b14', 'b2']

COLON = re.compile(r'[а-яё][)»\w]*:\s+[а-яё]')
LEX = ['наивн', 'косметик', 'ручка скорости', 'готовая еда', 'разные вещи',
       'роняет', 'обезвреж', 'скелет', 'хитмап', 'руками', 'вкусовщин',
       'липн', 'лечит', 'болезн', 'лесенк', 'торт', 'отшибе', 'сходит с рук',
       'гладь', 'трогается', 'переезжает', 'уронит', 'съедено', 'дырок',
       'всякой', 'тихой', 'послушно', 'вслепую', 'глаз', 'пляш',
       'выкосит', 'уезжает', 'вылезают', 'кучу', 'лечени', 'здоровой',
       'обрубок', 'тяжёлая', 'грубовато', 'распухнет', 'тяжелеет']
EVAL = ['важнее', 'полезнее', 'хуже', 'достоинство', 'правильнее',
        'бесполезен', 'слабее', 'дороже', 'неправильно', 'смысла не имеет']
COUNT = re.compile(r'\bчисл[оаеу]', re.I)


def rule(old, new):
    """Правило, ради которого сделана пара. Порядок проверок важен."""
    if '\u2212' in old:
        return 'минус пишется дефисом'
    if any(w in old.lower() for w in LEX) and \
            not any(w in new.lower() for w in LEX):
        return 'разговорная лексика'
    if any(w in old.lower() for w in EVAL) and \
            not any(w in new.lower() for w in EVAL):
        return 'утверждение без оценки'
    if COUNT.search(old) and not COUNT.search(new):
        return 'число и количество'
    if COLON.search(old) and not COLON.search(new):
        return 'двоеточие внутри предложения'
    if len(new) > len(old) * 0.9 and new.count('.') > old.count('.'):
        return 'длина предложения'
    return 'прочее'


pairs, seen = [], set()
for name in ORDER:
    p = SRC / ('%s.json' % name)
    if not p.exists():
        continue
    for old, new in json.load(open(p, encoding='utf-8')):
        key = (old, new)
        if key in seen:
            continue
        seen.add(key)
        pairs.append({'rule': rule(old, new), 'before': old, 'after': new,
                      'batch': name})

# правки руководства лежат не пачкой, а в скрипте
manfix = (SRC / 'manfix.py').read_text(encoding='utf-8')
for m in re.finditer(r"\(\s*'((?:[^'\\]|\\.)*)'\s*,\s*\n?\s*'((?:[^'\\]|\\.)*)'\s*\)",
                     manfix):
    old, new = m.group(1), m.group(2)
    if old in ('\\u2212',) or not old:
        continue
    if (old, new) in seen:
        continue
    seen.add((old, new))
    pairs.append({'rule': rule(old, new), 'before': old, 'after': new,
                  'batch': 'manual'})

OUT.parent.mkdir(parents=True, exist_ok=True)
json.dump({'version': '5.13.16',
           'note': 'Пары правок из вычитки контекстной справки и руководства.',
           'pairs': pairs},
          open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

from collections import Counter
c = Counter(p['rule'] for p in pairs)
print('пар сохранено:', len(pairs))
for k, v in c.most_common():
    print('  %-32s %d' % (k, v))
print('файл:', OUT)
