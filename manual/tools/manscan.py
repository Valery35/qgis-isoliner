# -*- coding: utf-8 -*-
"""Тот же стилевой скан по руководствам и README."""
import pathlib
import re

FILES = ['manual/manual.md', 'manual/manual_en.md', 'README.md', 'README_en.md',
         'CHANGELOG.md']
WORDS = [
    r'наивн\w*', r'честн\w*', r'врё\w*', r'соблазн\w*', r'предъяв\w*',
    r'кучк\w*', r'скучн\w*', r'членени\w*', r'наблюдённ\w*', r'софт\w*',
    r'главные грабли', r'\bруками\b', r'лесенк\w*', r'липн\w*',
    r'вкусовщин\w*', r'на отшибе', r'сходит с рук', r'свадебн\w+ торт',
    r'косметик\w*', r'ручка скорости', r'готовая еда', r'роняет',
    r'обезвреж\w*', r'скелет(?!\s+местности)\w*', r'разные вещи',
    r'хитмап\w*',
    r'важнее', r'полезнее', r'достоинство', r'не в простоте',
    r'угадывать вслепую', r'−',
]
for f in FILES:
    p = pathlib.Path(f)
    if not p.exists():
        print('нет файла:', f)
        continue
    t = p.read_text(encoding='utf-8')
    for w in WORDS:
        for m in re.finditer(w, t, re.I):
            print('%-18s %s  …%s…' % (w, p.name,
                                      t[max(0, m.start() - 55):m.end() + 55]
                                      .replace('\n', ' ')))
print('скан закончен')
