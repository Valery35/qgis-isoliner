# -*- coding: utf-8 -*-
"""Замена строковых литералов пакета целиком, по узлам AST.

Правка идёт по значению узла ast.Constant: у неявной склейки узел один и
несёт точные границы, поэтому обрыв склейки посередине невозможен.
Литерал в исходнике и такой же литерал-ключ в i18n.py правятся одним
проходом, разъехаться они не могут.

Два места, на которых это уже ломалось.
1. col_offset у ast считается в БАЙТАХ utf-8. На кириллице это не номер
   символа, и конец литерала уезжает вперёд, съедая следующий код.
2. Крайний пробел несёт смысл, когда сообщение приклеивается к другому.
   Разбиение по split(" ") его теряло.
Файл переписывается за один проход, иначе разбор мегабайтного модуля на
каждый литерал съедает минуты.
"""
import ast, json, pathlib, sys

PKG = pathlib.Path('grid_isolines')
WIDTH = 74


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def one_line(text):
    return '"' + esc(text) + '"'


def triple(text):
    """Литерал в тройных кавычках, без экранирования переносов."""
    assert '\\' not in text and '"""' not in text and not text.endswith('"')
    return '"""' + text + '"""'


def wrap(text, indent):
    words = text.split(" ")
    chunks = [w + " " for w in words[:-1]] + [words[-1]]
    out, cur = [], ""
    for c in chunks:
        if cur and len(cur) + len(c) > WIDTH:
            out.append(cur); cur = c
        else:
            cur += c
    out.append(cur)
    assert "".join(out) == text
    pad = " " * indent
    return ("\n" + pad).join(one_line(s) for s in out)


def offsets(src):
    """Начало каждой строки в символах и её байтовая карта."""
    lines = src.split("\n")
    starts, acc = [], 0
    for s in lines:
        starts.append(acc); acc += len(s) + 1
    return lines, starts


def apply(path, todo):
    src = path.read_text(encoding='utf-8')
    lines, starts = offsets(src)

    def pos(lineno, col):
        return starts[lineno - 1] + len(
            lines[lineno - 1].encode("utf-8")[:col].decode("utf-8"))

    tree = ast.parse(src)
    docs = set()
    for n in ast.walk(tree):
        body = getattr(n, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            docs.add(id(body[0].value))
    spots = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and n.value in todo:
            a, b = pos(n.lineno, n.col_offset), pos(n.end_lineno,
                                                    n.end_col_offset)
            new = todo[n.value]
            if id(n) in docs:
                # Строки документации переносами не склеиваются: на нулевом
                # отступе каждая строка стала бы отдельной инструкцией, а
                # докстрока - только первой из них. Один раз так и вышло.
                txt = triple(new)
            elif n.lineno == n.end_lineno or src[:a].rstrip()[-1:] not in "(,":
                txt = one_line(new)
            else:
                txt = wrap(new, n.col_offset)
            spots.append((a, b, txt, n.value))
    if not spots:
        return set()
    for a, b, txt, _ in sorted(spots, reverse=True):
        src = src[:a] + txt + src[b:]
    path.write_text(src, encoding='utf-8')
    got = set()
    for n in ast.walk(ast.parse(src)):        # страж: текст записан верно
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            got.add(n.value)
    done = set()
    for _, _, _, old in spots:
        assert todo[old] in got, "литерал записан не тем текстом"
        done.add(old)
    return done


def main(batch):
    todo = {o: n for o, n in json.load(open(batch, encoding='utf-8'))}
    seen = set()
    for path in sorted(PKG.glob('*.py')):
        seen |= apply(path, todo)
    miss = [o for o in todo if o not in seen]
    print("литералов заменено: %d из %d" % (len(seen), len(todo)))
    for m in miss[:10]:
        print("  не найдено:", m[:70])
    return 1 if miss else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
