# -*- coding: utf-8 -*-
"""Сверка справок с окном: ссылки на инструменты и имена параметров.

Три проверки, каждая из которых уже ловила настоящую ошибку.

1. Ссылка «N.NN» указывает на существующий инструмент. Номер печатается
   вместе с названием того, куда он ведёт, чтобы видно было, о том ли
   инструменте речь. Справка «Примера для фракталов» отсылала за проверкой
   в пример реки и в импорт створов, и номера при этом были настоящие.
2. Имя параметра, выделенное жирным, совпадает с подписью в окне. Подписи
   берутся вторым позиционным аргументом конструкторов
   QgsProcessingParameter*.
3. Один и тот же параметр во всех инструментах назван одинаково.

Запуск из корня рабочей копии:

    python3 manual/tools/refcheck.py
"""
import ast
import difflib
import re
import sys

PATH = 'grid_isolines/algorithms.py'
RU = re.compile(r'[А-Яа-яЁё]')
NUM = re.compile(r'^\s*(\d+\.\d+)')
BOLD = re.compile(r'\*\*([^*]{3,45})\*\*')
# У инструментов вторая часть номера от 01 до 23. Всё, что больше, это
# значение в тексте, вроде коэффициента релаксации 1.85 или угла 1.33.
REF = re.compile(r'\b([1-9]\.(?:0\d|1\d|2[0-3]))\b')


def biggest(node):
    """Самый длинный строковый литерал внутри функции."""
    best = None
    for s in ast.walk(node):
        if isinstance(s, ast.Constant) and isinstance(s.value, str):
            if best is None or len(s.value) > len(best):
                best = s.value
    return best


def labels_of(init):
    out = set()
    for n in ast.walk(init):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id.startswith('QgsProcessingParameter')):
            continue
        for arg in n.args[1:2]:
            for s in ast.walk(arg):
                if isinstance(s, ast.Constant) and isinstance(s.value, str) \
                        and RU.search(s.value):
                    out.add(s.value.strip())
    return out


def stem(x):
    return x.split('(')[0].split(',')[0].strip().lower()


src = open(PATH, encoding='utf-8').read()
tree = ast.parse(src)
tools = {}
for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
    h = d = init = None
    for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
        if fn.name == 'shortHelpString':
            h = biggest(fn)
        elif fn.name == 'displayName':
            d = biggest(fn)
        elif fn.name == 'initAlgorithm':
            init = fn
    m = NUM.match(d or '')
    if not m or h is None:
        continue
    tools[m.group(1)] = {'name': (d or '').strip(), 'help': h,
                         'labels': labels_of(init) if init else set()}

bad = 0

print('== ссылки на инструменты')
for num in sorted(tools):
    for mm in REF.finditer(tools[num]['help']):
        r = mm.group(1)
        if r not in tools:
            bad += 1
            print('  %-5s -> %-5s ТАКОГО ИНСТРУМЕНТА НЕТ' % (num, r))
        elif r[0] != num[0]:
            print('  %-5s -> %-5s %s  (другая группа, проверить по смыслу)'
                  % (num, r, tools[r]['name'][5:45]))

print('\n== имена параметров в справке против подписей в окне')
for num in sorted(tools):
    stems = {stem(l): l for l in tools[num]['labels']}
    for mm in BOLD.finditer(tools[num]['help']):
        nm = mm.group(1).strip()
        if not RU.search(nm) or nm.endswith(('.', ':')) or nm[0].islower():
            continue
        st = stem(nm)
        if st in stems:
            continue
        # справка вправе звать параметр началом его подписи: «Мёртвая зона»
        # вместо «Мёртвая зона по высоте, м». Это читается, ищется в окне и
        # замечанием не считается.
        if any(k.startswith(st + ' ') or k.startswith(st + ':')
               for k in stems):
            continue
        near = difflib.get_close_matches(st, list(stems), n=1, cutoff=0.68)
        if near:
            bad += 1
            print('  %-5s **%s** -> в окне «%s»' % (num, nm, stems[near[0]]))

# Скобка с пояснением нуля или пустого значения пишется одинаково во всём
# модуле. Разные уточнения у одной подписи это норма, «Разломы (линии,
# барьеры влияния)» и «Разломы (линии, разрыв изолиний)» говорят о разном.
# Разные ЗНАКИ при одном и том же смысле это не норма.
print('\n== запись пояснения нуля и пустого значения')
SEP = re.compile(r'\((пусто|empty|0)\s*(=|:|-)\s')
shapes = {}
for num in sorted(tools):
    for l in tools[num]['labels']:
        m = SEP.search(l)
        if m:
            shapes.setdefault(m.group(2), []).append((num, l))
if len(shapes) > 1:
    for sep in sorted(shapes):
        if sep == '=':
            continue
        for num, l in shapes[sep]:
            bad += 1
            print('  %-5s «%s» - принят знак равенства' % (num, l))
else:
    print('  одинаково во всех подписях')

print('\n== один параметр, разные подписи (справочно, не замечание)')
byline = {}
for num in sorted(tools):
    for l in tools[num]['labels']:
        byline.setdefault(stem(l), set()).add(l)
same = [(st, f) for st, f in sorted(byline.items()) if len(f) > 1]
print('  подписей с разными уточнениями: %d' % len(same))

print('\nзамечаний: %d' % bad)
sys.exit(0)
