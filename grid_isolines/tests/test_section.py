# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты разреза по линии. Чистая геометрия/выборка, без QGIS:
#     python grid_isolines/tests/test_section.py
#
# Помощники выборки и разбиения импортируются из grid_isolines.section_core.
# Раньше они дублировались здесь, потому что жили в algorithms.py и тянули
# QGIS. Теперь ядро чистое, копии не нужны.
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))          # сама папка плагина
sys.path.insert(0, os.path.join(_HERE, "..", ".."))  # родитель, для пакета


# Помощники берём из чистого ядра плагина, а не из копии: ядро не тянет QGIS,
# поэтому тест проверяет тот самый код, который поедет заказчику.
from grid_isolines.section_core import (  # noqa: E402
    sample_grid_points as _sample_grid_points,
    valid_runs as _valid_runs,
)


def _linear_grid(a, b, c, nx=20, ny=15, cell=5.0, x0=1000.0, y0=2000.0):
    """f(x,y)=a*x+b*y+c в центрах ячеек. gt с верхним левым углом."""
    gt = (x0, cell, 0.0, y0 + ny * cell, 0.0, -cell)
    arr = np.empty((ny, nx))
    for r in range(ny):
        for col in range(nx):
            x = gt[0] + (col + 0.5) * gt[1]
            y = gt[3] + (r + 0.5) * gt[5]
            arr[r, col] = a * x + b * y + c
    return arr, gt


def test_bilinear_exact_on_linear_field():
    a, b, c = 0.3, -0.2, 50.0
    arr, gt = _linear_grid(a, b, c)
    xs = np.array([1023.0, 1041.7, 1060.0])
    ys = np.array([2033.0, 2050.5, 2061.0])
    z = _sample_grid_points(arr, gt, xs, ys, bilinear=True)
    exp = a * xs + b * ys + c
    assert np.allclose(z, exp, atol=1e-9)        # билинейно точно для линейной


def test_nearest_returns_cell_value():
    arr, gt = _linear_grid(1.0, 0.0, 0.0)
    # центр ячейки (col=3,row=2): x=gt0+3.5*cell
    x = np.array([gt[0] + 3.5 * gt[1]]); y = np.array([gt[3] + 2.5 * gt[5]])
    z = _sample_grid_points(arr, gt, x, y, bilinear=False)
    assert abs(float(z[0]) - arr[2, 3]) < 1e-9


def test_sampling_outside_is_nan():
    arr, gt = _linear_grid(1.0, 1.0, 0.0)
    xs = np.array([gt[0] - 100.0]); ys = np.array([gt[3] + 100.0])
    z = _sample_grid_points(arr, gt, xs, ys, bilinear=True)
    assert np.isnan(z[0])


def test_valid_runs_splits_on_gaps():
    mask = np.array([1, 1, 1, 0, 0, 1, 1, 0, 1, 1, 1], dtype=bool)
    assert _valid_runs(mask) == [(0, 2), (5, 6), (8, 10)]


def test_valid_runs_drops_singletons():
    mask = np.array([1, 0, 1, 1, 0, 1], dtype=bool)   # одиночки 0 и 5 отброшены
    assert _valid_runs(mask) == [(2, 3)]


def test_bed_polygon_ring_closes():
    """Кольцо полосы пласта: верх вперёд + низ назад + замыкание."""
    d = np.array([0.0, 10.0, 20.0, 30.0])
    ztop = np.array([100.0, 101.0, 99.0, 100.0])
    zbot = np.array([90.0, 91.0, 89.0, 90.0])
    idx = list(range(4)); ridx = list(range(3, -1, -1))
    ring = [(d[i], ztop[i]) for i in idx] + [(d[i], zbot[i]) for i in ridx]
    ring.append(ring[0])
    assert ring[0] == ring[-1]                   # замкнуто
    assert len(ring) == 4 + 4 + 1
    tmean = float(np.mean(ztop - zbot))
    assert abs(tmean - 10.0) < 1e-9              # средняя мощность


def _beds_from_levels(values):
    """Логика 3.3: отметки сортируются по убыванию, соседние пары - пласты.
    Возвращает список (top, bot) сверху вниз. NULL (None) отбрасываются."""
    vals = sorted((float(v) for v in values if v is not None), reverse=True)
    return [(vals[k], vals[k + 1]) for k in range(len(vals) - 1)]


def test_levels_sorted_to_bed_pairs():
    beds = _beds_from_levels([90.0, 100.0, 75.0])     # порядок выбора любой
    assert beds == [(100.0, 90.0), (90.0, 75.0)]      # сверху вниз, 2 пласта


def test_levels_with_null_skipped():
    beds = _beds_from_levels([100.0, None, 80.0])
    assert beds == [(100.0, 80.0)]                    # один пласт, NULL выкинут


def test_levels_single_value_no_bed():
    assert _beds_from_levels([100.0, None]) == []     # пласт не построить


def _class_zones(valid, cls):
    """Логика 3.4 (категориальный): смежные валидные точки одного класса в зону.
    Возвращает список (i0, i1, class)."""
    out = []; i = 0; n = len(valid)
    while i < n:
        if valid[i]:
            j = i
            while j + 1 < n and valid[j + 1] and cls[j + 1] == cls[i]:
                j += 1
            if j > i:
                out.append((i, j, cls[i]))
            i = j + 1
        else:
            i += 1
    return out


def test_categorical_zones_merge_runs():
    valid = [True, True, True, True, True, True]
    cls = [1, 1, 2, 2, 2, 1]
    # смежные одинаковые сливаются; одиночный класс-1 в конце (1 точка) отброшен
    assert _class_zones(valid, cls) == [(0, 1, 1), (2, 4, 2)]


def test_categorical_zones_break_on_gap():
    valid = [True, True, False, True, True]
    cls = [1, 1, 1, 1, 1]
    assert _class_zones(valid, cls) == [(0, 1, 1), (3, 4, 1)]   # разрыв делит


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("ok:", fn.__name__)
    print("\nALL %d TESTS PASSED" % len(fns))





# --- Инженерная СК чертежа разреза (4.0.2) ------------------------------------
# Регресс: слои чертежа создавались с пустой СК, подхватывали СК проекта и на
# реальных данных (местные СК, координаты в сотнях тысяч) уходили за кадр -
# «объекты есть, на карте пусто». Чертёж должен получать инженерную (локальную)
# СК, которую QGIS не перепроецирует.

import ast as _ast
import unittest as _unittest
import re as _re


def _extract_draw_wkt():
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "algorithms.py"), encoding="utf-8").read()
    m = _re.search(r"_SECTION_DRAW_WKT\s*=\s*\((.*?)\)\n", src, _re.S)
    assert m, "_SECTION_DRAW_WKT не найдена"
    # склеить строковые литералы
    parts = _re.findall(r"'([^']*)'", m.group(1))
    return "".join(parts)


class TestSectionDrawCrs(_unittest.TestCase):

    def test_wkt_is_local_not_geographic(self):
        try:
            from osgeo import osr
        except Exception:
            self.skipTest("osgeo недоступен")
        srs = osr.SpatialReference()
        rc = srs.SetFromUserInput(_extract_draw_wkt())
        self.assertEqual(rc, 0, "инженерная WKT не разобралась")
        self.assertFalse(srs.IsGeographic(), "чертёж не должен быть географическим")
        self.assertFalse(srs.IsProjected(), "чертёж не должен быть проектным")
        self.assertTrue(srs.IsLocal(), "чертёж должен быть локальной/инженерной СК")

    def test_section_group_has_no_empty_crs(self):
        """В классах разреза стоки чертежа не должны создаваться с пустой
        QgsCoordinateReferenceSystem() - только через _section_draw_crs()."""
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "algorithms.py"), encoding="utf-8").read()
        tree = _ast.parse(src)
        section_classes = {
            "SectionAlgorithm", "DrillholesOnSectionAlgorithm",
            "CompositionOnSectionAlgorithm", "SectionGridIntersectAlgorithm",
            "SectionVectorIntersectAlgorithm", "SectionTinIntersectAlgorithm",
            "SectionProjectAlgorithm", "ShaftUnwrapAlgorithm",
        }
        offenders = []
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ClassDef) and node.name in section_classes:
                body = _ast.get_source_segment(src, node)
                # пустой вызов конструктора без аргументов
                if _re.search(r"QgsCoordinateReferenceSystem\(\s*\)", body):
                    offenders.append(node.name)
        self.assertFalse(
            offenders, "пустая СК в классах разреза: %s" % offenders)




class TestSurfaceTreeOrder(_unittest.TestCase):
    """4.0.x: порядок поверхностей берётся из дерева слоёв проекта, слои вне
    дерева стабильно уходят в конец. Логика идиомы (без QGIS)."""

    @staticmethod
    def _resort(layer_ids, tree_ids):
        order = {lid: i for i, lid in enumerate(tree_ids)}
        tail = len(order)
        return sorted(layer_ids, key=lambda i: order.get(i, tail))

    def test_reorders_to_tree(self):
        tree = ["B_top", "B_bottom", "AB_top", "AB_bottom"]
        picked = ["AB_bottom", "B_top", "B_bottom", "AB_top"]
        self.assertEqual(self._resort(picked, tree), tree)

    def test_external_layers_go_last_stably(self):
        tree = ["B_top", "B_bottom"]
        picked = ["X1", "B_bottom", "X2", "B_top"]
        res = self._resort(picked, tree)
        self.assertEqual(res[:2], ["B_top", "B_bottom"])
        # сторонние сохраняют относительный порядок
        self.assertEqual(res[2:], ["X1", "X2"])

    def test_algorithm_declares_tree_order_param(self):
        import re as _re2
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "algorithms.py"),
            encoding="utf-8").read()
        self.assertIn('TREE_ORDER = "TREE_ORDER"', src)
        self.assertIn("findLayers()", src)


class TreeOrderWiring(_unittest.TestCase):
    """Статические проверки по исходнику: порядок слоёв задаётся явно там, где
    он несёт смысл, и стопка поверхностей демо перечислена сверху вниз."""

    def setUp(self):
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "algorithms.py")
        with open(path, encoding="utf-8") as fh:
            self.src = fh.read()

    def test_demo_and_section_request_explicit_order(self):
        self.assertIn("order=True", self.src)
        self.assertIn("GRP_SECTION_DEMO, ordered, order=True", self.src)
        self.assertIn("GRP_SECTION, ordered, force=True, order=True", self.src)

    def test_demo_surface_keys_go_top_down(self):
        blk = self.src.split("ordered_keys = (self.LINE")[1].split(")")[0]
        pos = [blk.index("self.SURF%d" % k) for k in range(1, 7)]
        self.assertEqual(pos, sorted(pos), "поверхности не по порядку 1...6")

    def test_demo_rasters_below_vectors(self):
        blk = self.src.split("ordered_keys = (self.LINE")[1].split(")")[0]
        self.assertLess(blk.index("self.ZONE"), blk.index("self.SURF1"))
        self.assertLess(blk.index("self.TIN"), blk.index("self.BED1"))

    def test_demo_makes_three_lines(self):
        for nm in ("Разрез 1", "Разрез 2", "Разрез 3"):
            self.assertIn('self.tr("%s")' % nm, self.src)

    def test_sort_helper_is_idempotent_guarded(self):
        # перестановка не должна происходить, если порядок уже верный
        self.assertIn("if all(a is b for a, b in zip(children, want)):",
                      self.src)

    def test_sort_inserts_clones_before_removing_originals(self):
        """Регресс 4.0.3: обратный порядок стирал слои из проекта.

        Реестровый мост удаляет слой, если при удалении узла слоя больше нет
        нигде в дереве. Значит вставка копий обязана идти до удаления
        оригиналов, иначе группа остаётся пустой.
        """
        blk = self.src.split("def _sort_group_by_order")[1]
        blk = blk.split("def _set_group")[0]
        i_ins = blk.index("grp.insertChildNodes(0, clones)")
        i_rem = blk.index("grp.removeChildNode(n)")
        self.assertLess(i_ins, i_rem,
                        "копии вставляются после удаления оригиналов")

    def test_sort_mutes_registry_bridge(self):
        blk = self.src.split("def _sort_group_by_order")[1]
        blk = blk.split("def _set_group")[0]
        self.assertIn("layerTreeRegistryBridge()", blk)
        self.assertIn("setEnabled(False)", blk)
        self.assertIn("setEnabled(True)", blk)
        self.assertIn("finally:", blk)


# Запуск: сперва функциональные тесты, затем классы unittest. Раньше вызов
# unittest.main() стоял в середине файла, и классы, объявленные ниже, не
# выполнялись вообще. Единая точка входа в конце файла закрывает эту дыру.


class BatchWiring(_unittest.TestCase):
    """Статические проверки проводки пакетного режима в 4.01."""

    def setUp(self):
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "algorithms.py")
        with open(path, encoding="utf-8") as fh:
            self.src = fh.read()
        tree = _ast.parse(self.src)
        self.cls = [n for n in tree.body if isinstance(n, _ast.ClassDef)
                    and n.name == "SectionAlgorithm"][0]

    def _proc(self):
        return [n for n in self.cls.body if isinstance(n, _ast.FunctionDef)
                and n.name == "_process"][0]

    def test_batch_parameters_declared(self):
        init = [n for n in self.cls.body if isinstance(n, _ast.FunctionDef)
                and n.name == "initAlgorithm"][0]
        body = _ast.get_source_segment(self.src, init)
        for p in ("self.BATCH", "self.NAMEFLD", "self.LAYOUT",
                  "self.NCOLS", "self.GAP"):
            self.assertIn(p, body, p)

    def test_sec_fields_in_every_output(self):
        body = _ast.get_source_segment(self.src, self.cls)
        self.assertEqual(body.count('QgsField("sec", QVariant.String)'), 4)
        self.assertEqual(body.count('QgsField("sec_id", QVariant.Int)'), 4)

    def test_definition_carries_layout_offset(self):
        body = _ast.get_source_segment(self.src, self.cls)
        self.assertIn('QgsField("ox", QVariant.Double)', body)
        self.assertIn('QgsField("oy", QVariant.Double)', body)

    def test_common_vex_used_not_per_section(self):
        body = _ast.get_source_segment(self.src, self._proc())
        self.assertIn("_sc.common_vex(samples, vmode, vscale)", body)
        self.assertIn("_sc.build_section(None, surfs, vex=vex", body)

    def test_fence_3d_is_not_offset(self):
        """Забор стоит в реальных координатах, раскладка его не двигает."""
        body = _ast.get_source_segment(self.src, self._proc())
        blk = body.split("# 3D: вертикальная стенка")[1].split("nbed += 1")[0]
        self.assertNotIn("+ ox", blk)
        self.assertNotIn("+ oy", blk)

    def test_drawing_is_offset(self):
        body = _ast.get_source_segment(self.src, self._proc())
        blk = body.split("if sink2d is not None:")[1].split("# 3D:")[0]
        self.assertIn("+ ox", blk)
        self.assertIn("+ oy", blk)

    def test_three_scale_modes_everywhere(self):
        # Носителей выбора масштаба три: 4.01, 4.03 и 4.09. Модельный 4.02
        # берёт vex из определения разреза и своего выбора не имеет.
        self.assertEqual(
            self.src.count('self.tr("отношение масштабов Г:В (1:N)")'), 3)


class ChildToolsBatch(_unittest.TestCase):
    """Дочерние инструменты разреза читают все определения слоя, а не первое,
    и переносят имя разреза в атрибуты."""

    CONVERTED = ("SectionGridIntersectAlgorithm",
                 "SectionVectorIntersectAlgorithm",
                 "SectionTinIntersectAlgorithm")

    def setUp(self):
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "algorithms.py")
        with open(path, encoding="utf-8") as fh:
            self.src = fh.read()
        self.tree = _ast.parse(self.src)

    def _cls(self, name):
        found = [n for n in self.tree.body
                 if isinstance(n, _ast.ClassDef) and n.name == name]
        self.assertTrue(found, "класс %s не найден" % name)
        return _ast.get_source_segment(self.src, found[0])

    def test_reader_returns_all_definitions(self):
        self.assertIn("def _read_section_defs(", self.src)
        for key in ('"sec"', '"sec_id"', '"ox"', '"oy"', '"vex"'):
            self.assertIn(key, self.src.split("def _read_section_defs(")[1]
                          .split("def ")[0], key)

    def test_reader_is_tolerant_to_old_layers(self):
        """Слои прежних версий без новых полей должны читаться как раньше."""
        blk = self.src.split("def _def_num(")[1].split("def _read_section_defs")[0]
        self.assertIn("if name not in names:", blk)
        self.assertIn("return fallback", blk)

    def test_converted_tools_loop_over_definitions(self):
        for name in self.CONVERTED:
            body = self._cls(name)
            self.assertIn("_defs_or_raise(", body, name)
            self.assertNotIn("_read_section_def(", body, name)

    def test_converted_tools_tag_outputs(self):
        for name in self.CONVERTED:
            body = self._cls(name)
            self.assertIn('QgsField("sec", QVariant.String)', body, name)
            self.assertIn('QgsField("sec_id", QVariant.Int)', body, name)

    def test_converted_tools_apply_layout_offset(self):
        for name in self.CONVERTED:
            body = self._cls(name)
            self.assertIn('dd["ox"]', body, name)
            self.assertIn('dd["oy"]', body, name)

    def test_mixed_vex_is_reported(self):
        blk = self.src.split("def _log_defs(")[1].split("\ndef ")[0]
        self.assertIn("pushWarning", blk)

    # --- 4.06: слой из одного объекта не должен выглядеть зависшим -------

    def _code_only(self, body):
        """Тело класса без комментариев: проверяем код, а не пояснения."""
        out = []
        for line in body.split("\n"):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            out.append(line.split("  #")[0])
        return "\n".join(out)

    def test_tin_intersect_can_be_stopped(self):
        """Отмена и ход работы висят на частях, а не на объектах.

        У слоя изоповерхности объект один на уровень, а треугольников в
        нём десятки тысяч. Пока проверка стояла в цикле по объектам, она
        срабатывала два-три раза за всю работу: кнопку отмены нажать было
        нельзя, полосы хода не было, и обработка выглядела зависшей.
        """
        body = self._code_only(self._cls("SectionTinIntersectAlgorithm"))
        self.assertGreaterEqual(body.count("isCanceled()"), 2,
                                "проверки отмены мало для слоя из одного объекта")
        self.assertIn("setProgress(", body)

    def test_tin_intersect_reads_parts_in_place(self):
        """Части читаются по месту, без построения объекта на треугольник.

        asGeometryCollection() создавал объект Python на каждый
        треугольник, ring.points() - список точек на каждое кольцо. У
        вокселей из 2.04 частей до полумиллиона.
        """
        body = self._code_only(self._cls("SectionTinIntersectAlgorithm"))
        self.assertNotIn("asGeometryCollection(", body)
        self.assertNotIn("ring.points()", body)
        self.assertTrue("geometryN(" in body or "numGeometries()" in body,
                        "части должны читаться через geometryN/numGeometries")
        for acc in ("xAt(", "yAt(", "zAt("):
            self.assertIn(acc, body, acc)

    def test_tin_intersect_merges_segments_into_chains(self):
        """Отрезки сливаются в цепочки, а не пишутся по одному.

        Резка даёт отрезок на треугольник, и у оболочки их десятки тысяч:
        слоем из отрезков ни подписать, ни выбрать. Цепочки собираются по
        источнику - мешать треугольники разных оболочек нельзя, соседние
        тела сошлись бы в одну линию.
        """
        body = self._code_only(self._cls("SectionTinIntersectAlgorithm"))
        self.assertIn("chain_segments(", body)
        self.assertIn("for sname, extra, got in groups:", body)
        # запись по одному отрезку исчезла
        self.assertNotIn("for dd, d0, z0, d1, z1, sname in segs:", body)

    def test_tin_intersect_carries_source_fields(self):
        """Поля источника переносятся, значит резать надо по объектам."""
        body = self._code_only(self._cls("SectionTinIntersectAlgorithm"))
        self.assertIn("want_fields", body)
        self.assertIn("_field_text(", body)
        # треугольники собираются на каждый объект, а не на весь слой
        self.assertIn("tris = []", body)
        self.assertIn("n_tri += _emit(tris, sname, extra)", body)


if __name__ == "__main__":
    _run_all()
    _unittest.main()
