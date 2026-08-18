# -*- coding: utf-8 -*-
"""
Проверка оболочек в GeoPackage: замкнуты ли тела.

    python check_solids.py файл.gpkg [слой] [--solids]

Разбирает геометрию побайтово: только sqlite3 и struct, ни GDAL, ни QGIS
не нужны.

Замкнутость проверяется по рёбрам, а не по составу колец. Прежний счёт
(крышка сверху, крышка снизу, боковая стенка) годился, пока объект
состоял из крупных колец, и перестаёт работать, как только грани
приходят треугольниками: у треугольной грани нет ни верха, ни низа,
ни стенки.

Правило простое и не зависит от способа сборки: в замкнутой оболочке
каждое ребро принадлежит ровно двум граням. Ребро с одной гранью это
дыра, ребро с тремя и более это склейка или задвоенная поверхность.

Слои различаются полем `shell`: ноль у пояса поверхностей, единица
у тела. Пояс по правилам тела не судится: у поверхности рёбра и обязаны
быть висячими.

Если поля `shell` нет, вид задаётся ключом `--solids`. Без него слой
считается поясами и рёбра не считаются вовсе. Угадывать вид по геометрии
нельзя: пояс-скат несёт две отметки, по вершинам он неотличим от тела,
и всякая догадка ошибается в одну из двух сторон. Ложная тревога на
поверхности терпима, а вот незамкнутое тело, принятое за пояс, прошло бы
проверку молча, а это ровно то, что ищем.
"""

import collections
import os
import sqlite3
import struct
import sys

TOL = 1e-6          # допуск на равенство отметок
SNAP = 1e-4         # округление вершин: швы в данных бывают неточными


def rings(blob):
    """Кольца геометрии из блоба GeoPackage: список списков (x, y, z)."""
    flags = blob[3]
    env_len = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(flags >> 1) & 0x07]
    wkb = blob[8 + env_len:]
    fmt = "<" if wkb[0] == 1 else ">"
    pos = 1
    gtype = struct.unpack_from(fmt + "I", wkb, pos)[0]
    pos += 4
    base = gtype % 1000
    out = []
    polys = 1
    if base in (6, 15, 16):     # MULTIPOLYGON, POLYHEDRALSURFACE, TIN
        polys = struct.unpack_from(fmt + "I", wkb, pos)[0]
        pos += 4
    for _ in range(polys):
        if base in (6, 15, 16):
            pos += 1
            pt = struct.unpack_from(fmt + "I", wkb, pos)[0]
            pos += 4
        else:
            pt = gtype
        dim = 3 if (pt // 1000) in (1, 3) else 2
        n_rings = struct.unpack_from(fmt + "I", wkb, pos)[0]
        pos += 4
        for _r in range(n_rings):
            n_pts = struct.unpack_from(fmt + "I", wkb, pos)[0]
            pos += 4
            pts = []
            for _p in range(n_pts):
                v = struct.unpack_from(fmt + "d" * dim, wkb, pos)
                pos += 8 * dim
                pts.append((v[0], v[1], v[2] if dim == 3 else 0.0))
            out.append(pts)
    return out


def _key(p):
    """Вершина как ключ: с округлением, иначе швы не сойдутся."""
    return (round(p[0] / SNAP), round(p[1] / SNAP), round(p[2] / SNAP))


def edge_report(faces):
    """Счёт рёбер оболочки.

    Возвращает (граней, рёбер, висячих, кратных). Висячее ребро входит
    в одну грань, кратное в три и более.
    """
    edges = collections.Counter()
    n_faces = 0
    for ring in faces:
        pts = list(ring)
        if len(pts) >= 2 and _key(pts[0]) == _key(pts[-1]):
            pts = pts[:-1]
        if len(pts) < 3:
            continue
        n_faces += 1
        keys = [_key(p) for p in pts]
        for i in range(len(keys)):
            a, b = keys[i], keys[(i + 1) % len(keys)]
            if a == b:
                continue
            edges[(a, b) if a < b else (b, a)] += 1
    loose = sum(1 for n in edges.values() if n == 1)
    many = sum(1 for n in edges.values() if n > 2)
    return n_faces, len(edges), loose, many


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    args = [a for a in argv[1:] if not a.startswith("--")]
    keys = set(a for a in argv[1:] if a.startswith("--"))
    bad_keys = keys - {"--solids"}
    if bad_keys or not args:
        print(__doc__)
        if bad_keys:
            print("неизвестный ключ:", " ".join(sorted(bad_keys)))
        return 2
    as_solids = "--solids" in keys
    path = args[0]
    if not os.path.isfile(path):
        print("нет файла:", path)
        return 2
    db = sqlite3.connect(path)
    cur = db.cursor()
    if len(args) > 1:
        table = args[1]
    else:
        cur.execute("SELECT table_name FROM gpkg_contents "
                    "WHERE data_type='features'")
        rows = cur.fetchall()
        if not rows:
            print("в пакете нет векторных слоёв")
            return 2
        table = rows[0][0]

    cur.execute('PRAGMA table_info("%s")' % table)
    names = [r[1] for r in cur.fetchall()]
    has_shell = "shell" in names

    cols = "fid, geom" + (", shell" if has_shell else "")
    cur.execute('SELECT %s FROM "%s"' % (cols, table))

    total = belts = solids = closed = faces_total = 0
    bad, sizes, belt_sizes = [], [], []
    for row in cur.fetchall():
        fid, blob = row[0], row[1]
        shell = row[2] if has_shell else None
        total += 1
        faces = rings(blob)
        if shell is None:
            shell = 1 if as_solids else 0
        if not int(shell or 0):
            belts += 1
            belt_sizes.extend(len(r) for r in faces)
            continue
        solids += 1
        sizes.extend(len(r) for r in faces)
        n_faces, _n_edges, loose, many = edge_report(faces)
        faces_total += n_faces
        if n_faces and not loose and not many:
            closed += 1
        else:
            bad.append((fid, n_faces, loose, many))

    print("файл:   %s" % os.path.basename(path))
    print("слой:   %s,  объектов: %d" % (table, total))
    print("вид определён %s"
          % ("по полю shell" if has_shell
             else ("ключом --solids" if as_solids
                   else "по умолчанию: слой считается поясами")))
    print()
    print("поясов (поверхности):     %d" % belts)
    print("тел (оболочки):           %d" % solids)
    if not solids and not has_shell and not as_solids:
        print()
        print("рёбра не считались: слой принят за пояса. Если это тела,")
        print("добавьте поле shell или запустите с ключом --solids")
    if solids:
        print("из них замкнуты:          %d" % closed)
        print("граней в телах:           %d" % faces_total)
    for what, arr in (("в грани тела", sizes), ("в кольце пояса", belt_sizes)):
        if not arr:
            continue
        arr.sort()
        print()
        print("вершин %s: медиана %d, максимум %d"
              % (what, arr[len(arr) // 2], arr[-1]))
        if arr[-1] > 500:
            print("   кольца крупнее пятисот вершин это неупрощённый")
            print("   растровый контур, стоит прореживать по изолиниям")
    if bad:
        print()
        print("незамкнутые, первые 10 "
              "(fid, граней, висячих рёбер, кратных):")
        for r in bad[:10]:
            print("   %-8s %-8d %-8d %d" % r)
        print()
        print("висячее ребро входит в одну грань, это дыра в оболочке;")
        print("кратное входит в три и более, это склейка или дубль")
    if bad:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except BrokenPipeError:      # вывод оборвали через head, это не ошибка
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
