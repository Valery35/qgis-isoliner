# -*- coding: utf-8 -*-
"""Приёмка выгрузочного архива для plugins.qgis.org.

Отличий от `accept.py` два. Проверяется, что папки `tests/` внутри нет
(сканер каталога ругается на тестовые файлы), и стоп-слова ищутся заодно в
`metadata.txt`, потому что описание и changelog оттуда видит пользователь
каталога.

Стоп-слова у рабочего архива всегда находятся, и все они лежат в тестах. У
выгрузочного их быть не должно ни одного. Если нашлось, слово уехало в код,
который уходит пользователю.

Запуск из корня рабочей копии:

    python3 manual/tools/accept_upload.py
"""
import ast
import configparser
import pathlib
import re
import shutil
import sys
import zipfile

Z = pathlib.Path('grid_isolines_upload.zip')
OUT = pathlib.Path('/tmp/claude-0/accept_up')
STOP = re.compile(r'членени|наблюдённ|честн[а-яё]*|врёт|—|−')
MUST = ['doc/Isoliner.pdf', 'doc/Isoliner_en.pdf', 'i18n.py',
        'algorithms.py', 'metadata.txt', 'icon.svg', '__init__.py']

if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
with zipfile.ZipFile(Z) as z:
    names = z.namelist()
    z.extractall(OUT)

fail = []
print('файлов в архиве:', len(names))

junk = [n for n in names
        if '__pycache__' in n or n.endswith(('.pyc', '/', '.DS_Store'))]
print('мусор и записи каталогов:', len(junk))
if junk:
    fail.append('в архиве мусор: %s' % junk[:3])

tests = [n for n in names if '/tests/' in n]
print('тестовых файлов:', len(tests))
if tests:
    fail.append('в выгрузочном архиве остались тесты')

pkg = OUT / 'grid_isolines'
n = 0
for p in sorted(pkg.rglob('*.py')):
    try:
        ast.parse(p.read_text(encoding='utf-8'), str(p))
    except SyntaxError as e:
        fail.append('%s не разбирается: %s' % (p.name, e))
    n += 1
print('модулей разобрано:', n)

cp = configparser.ConfigParser(interpolation=configparser.BasicInterpolation())
try:
    cp.read(pkg / 'metadata.txt', encoding='utf-8')
    print('версия в архиве:', cp['general']['version'])
except configparser.Error as e:
    fail.append('metadata.txt не читается интерполяцией: %s' % e)

hits = []
for p in sorted(list(pkg.rglob('*.py')) + [pkg / 'metadata.txt']):
    t = p.read_text(encoding='utf-8')
    for m in STOP.finditer(t):
        hits.append('%s: %s' % (p.name,
                                t[max(0, m.start() - 40):m.end() + 40]
                                .replace('\n', ' ')))
print('стоп-слова:', len(hits))
for h in hits[:5]:
    print('   •', h)
if hits:
    fail.append('стоп-слово в том, что уходит пользователю')

for f in MUST:
    q = pkg / f
    print('%-22s %s' % (f, 'есть %d' % q.stat().st_size if q.exists()
                        else 'НЕТ'))
    if not q.exists():
        fail.append('нет обязательного файла %s' % f)

if fail:
    print('\nПРИЁМКА НЕ ПРОЙДЕНА:')
    for f in fail:
        print('  -', f)
    sys.exit(1)
print('\nприёмка пройдена')
