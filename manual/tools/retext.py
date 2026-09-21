# -*- coding: utf-8 -*-
"""Замена предложения в размеченном тексте, где строки перенесены.

    python3 manual/tools/retext.py файл.md пачка.json

Пачка это список пар [было, стало], записанных в одну строку. Совпадение
ищется с любыми переносами внутри, замена переносится заново по ширине
абзаца. Прямая замена по тексту тут не работает: предложение почти всегда
разорвано переносом в произвольном месте.
"""
import json
import pathlib
import re
import sys

WIDTH = 78


def wrap(text, width=WIDTH):
    out, cur = [], ''
    for w in text.split():
        if cur and len(cur) + len(w) + 1 > width:
            out.append(cur)
            cur = w
        else:
            cur = (cur + ' ' + w) if cur else w
    if cur:
        out.append(cur)
    return '\n'.join(out)


def pattern(old):
    """Между словами допускаются переносы и знаки разметки.

    Измеритель печатает предложение уже без `**` и обратных кавычек, и
    скопированная из его вывода строка иначе не находится в исходнике.
    """
    return re.compile(r'[*`\s]+'.join(
        re.escape(w.strip('*`')) for w in old.split()))


def main(path, batch):
    p = pathlib.Path(path)
    t = p.read_text(encoding='utf-8')
    pairs = json.load(open(batch, encoding='utf-8'))
    done = miss = 0
    for pair in pairs:
        old, new = pair[0], pair[1]
        every = len(pair) > 2 and pair[2] == 'all'
        rx = pattern(old)
        ms = list(rx.finditer(t))
        if not ms or (len(ms) > 1 and not every):
            print('!! совпадений %d: %s' % (len(ms), old[:70]))
            miss += 1
            continue
        # с конца, иначе смещаются границы следующих совпадений
        for m in reversed(ms):
            t = t[:m.start()] + wrap(new) + t[m.end():]
        done += len(ms)
    p.write_text(t, encoding='utf-8')
    print('заменено %d, не найдено %d' % (done, miss))
    return 1 if miss else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2]))
