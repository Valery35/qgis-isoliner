# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Прореживание контуров в 1.04. Контур из грида несёт вершину почти на
# каждом пересечении ячейки: в присланном тестовом файле медиана кольца
# 96 вершин, максимум 22 607. Прореживание с допуском в долю ячейки на
# вид незаметно, а вес и время разбора падают на порядок.
#
# Главное, что здесь охраняется, - стыковка. Линии и границы поясов
# выходят из ОДНОГО набора линий, и прореживать их надо один раз, в общем
# ядре. Если резать готовые полигоны по отдельности, общая граница
# соседних поясов разойдётся, и между ними появятся щели и нахлёсты.
#
# QGIS в контейнере нет, поэтому проверяем то, что проверяется статикой.
#     python grid_isolines/tests/test_contour_thin.py
import os
import sys

PKG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _read(name):
    with open(os.path.join(PKG, name), encoding="utf-8") as f:
        return f.read()


def _func(code, name, stop="\ndef "):
    body = code[code.index("def %s(" % name):]
    k = body.index(stop, 10)
    return body[:k]


def test_thinning_lives_in_the_shared_core():
    """Прореживание стоит в общем ядре линий и границ поясов."""
    code = _read("isolines.py")
    core = _func(code, "_contour_lines")
    assert "_thin_smooth_in_corridor(" in core
    assert code.count("simplify_in_corridor(") == 1, (
        "прореживание должно быть единственным: второй вызов разведёт "
        "линии и границы поясов")
    assert "native:simplifygeometries" not in code, (
        "прореживание Processing не знает соседних линий и даёт пересечения")
    assert "native:smoothgeometry" not in code


def test_belts_are_not_thinned_separately():
    """Пояса своего прореживания не имеют, иначе стыки разойдутся."""
    code = _read("isolines.py")
    for name in ("_polygonize_belts", "_belts_to_layer"):
        body = _func(code, name)
        assert "simplify" not in body.lower(), name


def test_both_entry_points_pass_the_tolerance():
    code = _read("isolines.py")
    for name in ("isolines_from_raster", "isolines_and_polygons"):
        body = _func(code, name)
        assert "thin" in body, name
        assert "_contour_lines(" in body, name


def test_thinning_runs_before_smoothing():
    """Сначала убрать лишние вершины, потом скруглять.

    Chaikin вершины добавляет, поэтому обратный порядок сначала удваивал
    бы контур, а потом прореживал уже скруглённое.
    """
    body = _func(_read("isolines.py"), "_thin_smooth_in_corridor")
    assert (body.index("simplify_in_corridor(pts")
            < body.index("chaikin_in_corridor(pts"))


def test_tolerance_is_a_share_of_the_cell():
    """Допуск считается от размера ячейки, а не задаётся в метрах.

    Один и тот же параметр тогда годится и для метрового грида, и для
    градусного, и для шахтного плана в сантиметрах.
    """
    body = _func(_read("isolines.py"), "_thin_smooth_in_corridor")
    assert "tol = float(thin) * sampler.cell" in body
    gs = _func(_read("isolines.py"), "_grid_sampler")
    assert "return None" in gs, "неудача чтения растра не должна ронять расчёт"


def test_zero_tolerance_skips_the_step():
    """Ноль означает прежнее поведение, без прореживания и скругления."""
    core = _func(_read("isolines.py"), "_contour_lines")
    assert "if (thin and thin > 0) or (line_iter and line_iter > 0):" in core
    body = _func(_read("isolines.py"), "_thin_smooth_in_corridor")
    assert "if tol > 0:" in body and "if iters:" in body


def test_parameter_is_declared_with_a_quarter_cell_default():
    code = _read("algorithms.py")
    k = code.index("alg.THIN,")
    block = code[k - 200:k + 400]
    assert "Прореживание контуров" in block
    assert "alg.THIN, 0.25" in block, "умолчание - четверть ячейки"
    assert "minValue=0.0" in block


def test_parameter_reaches_both_branches():
    """Значение доходит и до ветки с поясами, и до ветки одних линий."""
    code = _read("algorithms.py")
    assert code.count("thin=thin") == 2
    assert "thin = self.parameterAsDouble(parameters, self.THIN, context)" \
        in code


def _run():
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok:", name)
            except AssertionError as e:
                fails += 1
                print("СБОЙ:", name, e)
    if fails:
        sys.exit(1)
    print("all contour thinning tests passed")


if __name__ == "__main__":
    _run()
