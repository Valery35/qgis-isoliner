# -*- coding: utf-8 -*-
"""Подписи параметров в руководстве против подписей в окне.

В руководстве у каждого инструмента таблица параметров, и первая колонка
это подпись, которую человек видит в диалоге. Переименование параметра в
коде таблицу не трогает, и она тихо расходится. Скрипт берёт первую
колонку каждой таблицы главы инструмента и ищет её среди настоящих
подписей.

Совпадением считается полное равенство или начало подписи, потому что
руководство вправе опустить хвост вида «, м (0 = авто)».

Запуск из корня рабочей копии:

    python3 manual/tools/manlabels.py
"""
import ast
import difflib
import re
import sys

CODE = 'grid_isolines/algorithms.py'
MAN = 'manual/manual.md'
RU = re.compile(r'[А-Яа-яЁё]')
HEAD = re.compile(r'^##\s+(\d\.\d\d)\s')
# В ячейке таблицы вертикальная черта экранирована, и по ней нельзя резать:
# «q = K·\\|∇h\\|» это одна ячейка, а не три.
ROW = re.compile(r'^\|\s*((?:[^|\\]|\\.)+?)\s*\|')


def labels():
    src = open(CODE, encoding='utf-8').read()
    out = set()
    for n in ast.walk(ast.parse(src)):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id.startswith('QgsProcessingParameter')):
            continue
        for arg in n.args[1:2]:
            for s in ast.walk(arg):
                if isinstance(s, ast.Constant) and isinstance(s.value, str) \
                        and RU.search(s.value):
                    out.add(s.value.strip())
    return out


def norm(x):
    x = x.replace('\\|', '|').replace('**', '').replace('`', '').strip()
    x = re.sub(r'\s+', ' ', x)
    # «(доп.)» в руководстве означает раздел «Дополнительно», а не часть
    # подписи. Это своя пометка руководства, и расхождением она не является.
    x = re.sub(r'\s*\((?:доп|Доп)\.\)\s*$', '', x)
    return x


LAB = labels()
LOW = {l.lower(): l for l in LAB}
SKIP = {'параметр', 'что задаёт', 'по умолчанию', 'поле', 'значение',
        'выход', 'вход', 'колонка', 'столбец', 'что это', 'зачем'}

bad = 0
cur = None
for line in open(MAN, encoding='utf-8'):
    h = HEAD.match(line)
    if h:
        cur = h.group(1)
        continue
    if not line.startswith('|') or cur is None:
        continue
    m = ROW.match(line)
    if not m:
        continue
    cell = norm(m.group(1))
    if not cell or not RU.search(cell) or cell.lower() in SKIP:
        continue
    if set(cell) <= set('-: '):
        continue
    low = cell.lower()
    if low in LOW:
        continue
    if any(k.startswith(low + ' ') or k.startswith(low + ',')
           or k.startswith(low + ':') or k.startswith(low + ' (')
           for k in LOW):
        continue
    near = difflib.get_close_matches(low, list(LOW), n=1, cutoff=0.80)
    if near:
        bad += 1
        print('%-5s «%s»\n      в окне «%s»' % (cur, cell, LOW[near[0]]))

print('\nрасхождений: %d' % bad)
sys.exit(0)
