# -*- coding: utf-8 -*-
"""Генератор текстового дерева инструментов для руководства.

Читает algorithms.py разбором AST, без запуска QGIS. Порядок групп - как в
панели Обработки, порядок внутри группы - по номеру инструмента.
"""
import ast, os, re, sys

path = sys.argv[1] if len(sys.argv) > 1 else "algorithms.py"
lang = sys.argv[2] if len(sys.argv) > 2 else "ru"
src = open(path, encoding="utf-8").read()
tree = ast.parse(src)

gnames = dict(re.findall(r'(GROUP\w*) = _tr\("([^"]+)"\)', src))
order = ["GROUP", "GROUP_TOPO", "GROUP_TOPODIAG", "GROUP2", "GROUP3", "GROUP5"]

def first_str(fn):
    for c in ast.walk(fn):
        if isinstance(c, ast.Constant) and isinstance(c.value, str):
            return c.value
    return None

items = {}
for n in ast.walk(tree):
    if not isinstance(n, ast.ClassDef):
        continue
    disp = grp = None
    for f in n.body:
        if isinstance(f, ast.FunctionDef) and f.name == "displayName":
            disp = first_str(f)
        if isinstance(f, ast.FunctionDef) and f.name == "group":
            for c in ast.walk(f):
                if isinstance(c, ast.Name) and c.id in gnames:
                    grp = c.id
    if disp and grp:
        items.setdefault(grp, []).append(disp)

if lang == "en":
    # словарь лежит рядом с разбираемым файлом, а не в текущем каталоге:
    # сборщик зовёт генератор из каталога руководств, и относительный путь
    # ронял его молча - список инструментов из-за этого застыл на месяц
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
    import i18n as I
    trans = I.TRANSLATIONS

    def tr(s):
        return trans.get(s, s)
else:
    def tr(s):
        return s

out = []
total = 0
for g in order:
    if g not in items:
        continue
    lines = sorted(items[g], key=lambda s: s.split()[0])
    total += len(lines)
    out.append("**%s**\n" % tr(gnames[g]))
    for d in lines:
        num, _, rest = tr(d).partition(" ")
        out.append("- `%s` %s" % (num, rest))
    out.append("")
print("\n".join(out))
print("_%s: %d_" % ("Всего инструментов" if lang == "ru" else "Tools in total",
                    total))
