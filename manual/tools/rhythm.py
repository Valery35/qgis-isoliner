# -*- coding: utf-8 -*-
"""Ритм текста: не средняя длина, а разброс.

Средняя ничего не говорит о том, как текст читается. Десять слов в среднем
даёт и живая проза с чередованием, и подряд идущие обрубки одной длины.
Считается разброс длин, доля коротких, доля абзацев, где все предложения
короткие, и самая длинная цепочка коротких предложений подряд.
"""
import json
import pathlib
import re
import statistics
import sys

RU = re.compile(r'[А-Яа-яЁё]')


def sentences_by_para(text, md=True):
    paras = []
    if md:
        body, fence = [], False
        for ln in text.split('\n'):
            s = ln.strip()
            if s.startswith('```'):
                fence = not fence
                continue
            if fence or s.startswith(('|', '#', '![', '- ', '* ')):
                continue
            body.append(ln)
        text = '\n'.join(body)
    text = re.sub(r'\*\*|`|\[[^\]]*\]\([^)]*\)', ' ', text)
    for para in text.split('\n\n'):
        para = ' '.join(para.split())
        if not RU.search(para):
            continue
        ss = []
        for s in re.split(r'(?<=[.!?])\s+', para):
            w = [x for x in s.split() if x.strip('.,()')]
            if len(w) >= 3:
                ss.append(len(w))
        if ss:
            paras.append(ss)
    return paras


def report(name, paras):
    flat = [n for p in paras for n in p]
    if not flat:
        print('%-26s пусто' % name)
        return
    short = sum(1 for n in flat if n <= 8)
    mid = sum(1 for n in flat if 9 <= n <= 18)
    long = sum(1 for n in flat if n >= 19)
    allshort = sum(1 for p in paras if len(p) >= 3 and max(p) <= 11)
    run = best = 0
    for p in paras:
        run = 0
        for n in p:
            run = run + 1 if n <= 8 else 0
            best = max(best, run)
    print('%-26s предложений %4d, средняя %4.1f, разброс %4.1f'
          % (name, len(flat), statistics.mean(flat),
             statistics.pstdev(flat)))
    print('%-26s коротких (<=8) %3d%%, средних 9-18 %3d%%, длинных (>=19) %3d%%'
          % ('', 100 * short // len(flat), 100 * mid // len(flat),
             100 * long // len(flat)))
    print('%-26s абзацев сплошь коротких %d из %d, цепочка коротких подряд %d'
          % ('', allshort, len(paras), best))


for path in sys.argv[1:]:
    if path.endswith('.json'):
        keys = json.load(open(path, encoding='utf-8'))
        paras = []
        for k in keys:
            if RU.search(k) and not k.lstrip().startswith('<'):
                paras += sentences_by_para(k, md=False)
        report('справка', paras)
    else:
        t = pathlib.Path(path).read_text(encoding='utf-8')
        report(pathlib.Path(path).name, sentences_by_para(t))
