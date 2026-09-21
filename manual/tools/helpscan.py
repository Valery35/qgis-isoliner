# -*- coding: utf-8 -*-
"""Сплошной обход справок модуля: где текст нарушает свод правил.

Берётся каждая справка инструмента и каждая подсказка параметра, меряется
ритм, пунктуация и лексика. Печатается список, отсортированный по весу
нарушений, чтобы править сверху вниз.
"""
import ast
import json
import re
import sys

PATH = 'grid_isolines/algorithms.py'
RU = re.compile(r'[А-Яа-яЁё]')
NUM = re.compile(r'^\s*(\d+\.\d+)')

STOP = {
    r'\bнаивн': 'наивная',
    r'\bсофт\b': 'софт',
    r'\bкучк': 'кучка',
    r'\bгладь\b': 'гладь',
    r'\bскучн': 'скучный',
    r'\bлаг(?:а|у|ов|и)?\b': 'лаг',
    r'\bчленени': 'членение',
    r'\bнаблюдённ': 'наблюдённый',
    r'\bмерк(?:а|и|е|у|ой|ам|ами|ах)\b': 'мерка',
    r'\bчестн': 'честный',
    r'\bврёт\b': 'врёт',
    r'\bручка скорости': 'ручка скорости',
    r'\bпростыми словами': 'простыми словами',
    r'\bпо сути\b': 'по сути',
    r'\bгрубо говоря': 'грубо говоря',
    r'\bкак бы\b': 'как бы',
}
COUNTED = ('ячеек ячейки точек точки проб пар скважин узлов уровней соседей '
    'итераций проходов объектов линий вершин отметок замен цепочек столбцов '
    'интервалов реализаций структур стволов туров бинов классов профилей '
    'створов уступов размеров плиток тел граней колонок разрывов элементов '
    'файлов слоёв полигонов записей строк сечений шагов').split()
COUNT = re.compile(r'\bчисл[оаеуы]м?\s+(%s)\b' % '|'.join(COUNTED), re.I)
COLON = re.compile(r'[а-яё][)»\w]*:\s+[а-яё]')
# Точка после сокращения не кончает предложение. «упёрся в макс.\n# расстояние» иначе распадается на две короткие фразы, и мера показывает
# цепочку коротких там, где её нет.
ABBR = ('макс', 'мин', 'доп', 'опц', 'ед', 'град', 'см', 'т', 'напр',
        'вкл', 'выкл', 'кв', 'руб', 'стр', 'рис')
SPLIT = re.compile(r'(?<=[.!?])\s+')
SEMI = re.compile(r'[а-яё];')
DASH = re.compile(r'[—–]')
MINUS = re.compile(r'−')


def split_sentences(para):
    """Резать по точке, но не по точке сокращения."""
    out = []
    for piece in SPLIT.split(para):
        if out:
            tail = out[-1].rstrip('.').rsplit(' ', 1)[-1].rstrip('.')
            if tail.lower() in ABBR:
                out[-1] = out[-1] + ' ' + piece
                continue
        out.append(piece)
    return out


def paragraphs(text):
    """Предложения по абзацам. Цепочка коротких рвётся на границе абзаца.

    Считать её по всей справке подряд неверно: между абзацами у читателя
    пустая строка, и ряд коротких фраз через неё не тянется. Мера давала
    цепочку 4 там, где в каждом абзаце их было не больше двух.
    """
    return [sentences(p) for p in text.split('\n\n')]


def sentences(text):
    out = []
    for para in [text]:
        lines = [x for x in para.split('\n')
                 if not x.lstrip().startswith(('\u2022', '<', '|'))]
        para = ' '.join(' '.join(lines).split())
        if not RU.search(para):
            continue
        for s in split_sentences(para):
            w = [x for x in s.split() if RU.search(x)]
            if len(w) >= 3:
                out.append((s, len(w)))
    return out


def measure(text):
    ss = [x for g in paragraphs(text) for x in g]
    n = len(ss)
    d = {'n': n}
    if n:
        ln = [x[1] for x in ss]
        d['mean'] = sum(ln) / float(n)
        d['short'] = sum(1 for x in ln if x <= 8) / float(n)
        d['long'] = sum(1 for x in ln if x >= 19) / float(n)
        best = 0
        for group in paragraphs(text):
            run = 0
            for _, w in group:
                run = run + 1 if w <= 8 else 0
                best = max(best, run)
        d['run'] = best
    return d


def flaws(text):
    f = []
    for rx, name in STOP.items():
        if re.search(rx, text, re.I):
            f.append('стоп-слово «%s»' % name)
    if COUNT.search(text):
        f.append('«число» вместо «количество»')
    if MINUS.search(text):
        f.append('минус U+2212')
    if DASH.search(text):
        f.append('длинное тире')
    if SEMI.search(text):
        f.append('точка с запятой')
    c = len(COLON.findall(text))
    if c:
        f.append('двоеточие внутри фразы x%d' % c)
    return f


def const(node):
    """Первый крупный строковый литерал внутри функции."""
    best = None
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if best is None or len(sub.value) > len(best):
                best = sub.value
    return best


src = open(PATH, encoding='utf-8').read()
tree = ast.parse(src)
rows = []
for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
    help_txt = num = disp = None
    for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
        if fn.name == 'shortHelpString':
            help_txt = const(fn)
        elif fn.name == 'displayName':
            disp = const(fn)
    if help_txt is None:
        continue
    m = NUM.match(disp or '')
    num = m.group(1) if m else '?'
    d = measure(help_txt)
    rows.append({'num': num, 'cls': cls.name, 'disp': (disp or '')[:60],
                 'len': len(help_txt), 'm': d, 'f': flaws(help_txt)})

rows.sort(key=lambda r: r['num'])
bad = 0
print('%-6s %6s %5s %5s %5s %5s %4s  %s' %
      ('№', 'знаков', 'предл', 'сред', 'кор%', 'длин%', 'цепь', 'замечания'))
for r in rows:
    d, f = r['m'], r['f']
    warn = list(f)
    if d['n']:
        if d['mean'] > 13.0:
            warn.append('средняя %.1f' % d['mean'])
        if d['short'] > 0.67:
            warn.append('коротких %d%%' % (100 * d['short']))
        if d['run'] > 4:
            warn.append('цепочка %d' % d['run'])
    if r['len'] > 4500:
        warn.append('длина %d' % r['len'])
    if not warn:
        continue
    bad += 1
    print('%-6s %6d %5d %5.1f %4d%% %4d%% %4d  %s' %
          (r['num'], r['len'], d['n'], d.get('mean', 0),
           100 * d.get('short', 0), 100 * d.get('long', 0), d.get('run', 0),
           '; '.join(warn)))
print('\nвсего справок %d, с замечаниями %d' % (len(rows), bad))
