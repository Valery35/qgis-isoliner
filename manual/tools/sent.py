# -*- coding: utf-8 -*-
"""Замена фрагмента внутри строковых литералов пакета.

Простая замена по тексту файла не годится: литерал справки склеен из
десятка строк, и фраза почти всегда разорвана переносом. Поэтому правка
идёт по значению узла ast.Constant, а переписывается узел целиком.
Ключ в i18n.py правится тем же проходом, разъехаться они не могут.
"""
import ast, json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from fix import apply  # noqa: E402

PKG = pathlib.Path('grid_isolines')


def literals():
    out = set()
    for path in sorted(PKG.glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                out.add(n.value)
    return out


def main(batch):
    pairs = json.load(open(batch, encoding='utf-8'))
    lits = literals()
    todo, used = {}, set()
    for old, new in pairs:
        for lit in lits:
            if old in lit:
                todo[lit] = todo.get(lit, lit).replace(old, new)
                used.add(old)
    miss = [o for o, _ in pairs if o not in used]
    for m in miss:
        print("!! 0 совпадений:", m[:90])
    if miss:
        return 1
    seen = set()
    for path in sorted(PKG.glob('*.py')):
        seen |= apply(path, todo)
    print("пар: %d, литералов переписано: %d" % (len(pairs), len(seen)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
