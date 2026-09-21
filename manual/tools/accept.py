# -*- coding: utf-8 -*-
"""Приёмка собранного архива: проверка идёт по распакованному, не по дереву."""
import ast
import configparser
import pathlib
import re
import shutil
import subprocess
import sys
import zipfile

ZIP = pathlib.Path('grid_isolines.zip')
OUT = pathlib.Path('/tmp/claude-0/accept')
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
with zipfile.ZipFile(ZIP) as z:
    names = z.namelist()
    z.extractall(OUT)
print('файлов в архиве:', len(names))

bad = [n for n in names if '__pycache__' in n or '.pytest_cache' in n
       or n.endswith('.pyc') or n.endswith('.DS_Store') or n.endswith('/')]
print('мусор и записи каталогов:', bad[:5], 'всего', len(bad))

pkg = OUT / 'grid_isolines'
n = 0
for p in sorted(pkg.rglob('*.py')):
    ast.parse(p.read_text(encoding='utf-8'), str(p))
    n += 1
print('модулей разобрано:', n)

cp = configparser.ConfigParser(interpolation=configparser.BasicInterpolation())
cp.read(pkg / 'metadata.txt', encoding='utf-8')
print('версия в архиве:', cp['general']['version'])

STOP = re.compile(r'членени|наблюдённ|честн[а-яё]*|врёт|—|−')
hits = []
for p in sorted(pkg.rglob('*.py')):
    t = p.read_text(encoding='utf-8')
    for m in STOP.finditer(t):
        hits.append('%s: %s' % (p.name, t[max(0, m.start() - 40):m.end() + 25]
                                .replace('\n', ' ')))
t = (pkg / 'metadata.txt').read_text(encoding='utf-8')
for m in STOP.finditer(t):
    hits.append('metadata.txt: %s' % t[max(0, m.start() - 40):m.end() + 25])
print('стоп-слова:', len(hits))
for h in hits[:8]:
    print('   •', h)

for f in ('doc/Isoliner.pdf', 'doc/Isoliner_en.pdf'):
    p = pkg / f
    print(f, 'есть' if p.exists() else 'НЕТ', p.stat().st_size if p.exists() else '')
