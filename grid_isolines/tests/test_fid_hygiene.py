# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Служебное поле fid не должно ехать по цепочке изолиний.

gdal:contour пишет во временный GeoPackage, и QGIS показывает служебный
ключ fid обычным полем слоя. Пока изолинию никто не разрезал, значения
оставались уникальными и запись результата проходила. Разрез линией
разлома делает из одной изолинии несколько кусков с одним и тем же fid, и
сохранение падает на UNIQUE constraint failed: OUTPUT.fid.

Беда была скрытой: она ждала первого шага, который размножает объекты.
Поэтому сторож смотрит не на разрез, а на само место, где поле входит в
цепочку - сразу после контуринга.

Проверка идёт по исходнику разбором AST, без запуска QGIS.
"""
import ast
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "isolines.py")


def _tree():
    with open(SRC, encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _func(name):
    for node in ast.walk(_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("функция %s пропала из isolines.py" % name)


def test_drop_fid_helper_exists():
    fn = _func("_drop_fid")
    src = ast.dump(fn)
    assert "deletecolumn" in src, "_drop_fid перестал удалять колонку"
    assert "fid" in src, "_drop_fid перестал знать про fid"


def test_contour_lines_drops_fid_right_after_gdal():
    """Снятие поля стоит сразу после контуринга, а не где-то позже.

    Позже уже поздно: между контурингом и записью стоят шаги, которые
    размножают объекты, и каждый из них воспроизводит поломку.
    """
    fn = _func("_contour_lines")
    order = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "_drop_fid":
            order.append(("drop", node.lineno))
        for arg in node.args:
            if isinstance(arg, ast.Constant) and arg.value == "gdal:contour":
                order.append(("contour", node.lineno))
    kinds = [k for k, _ in order]
    assert "contour" in kinds, "вызов gdal:contour пропал"
    assert "drop" in kinds, "_contour_lines перестал снимать fid"
    line_contour = min(ln for k, ln in order if k == "contour")
    line_drop = min(ln for k, ln in order if k == "drop")
    assert line_drop > line_contour, "снятие fid стоит раньше контуринга"
    assert line_drop - line_contour <= 4, (
        "снятие fid уехало от контуринга на %d строк: между ними успели "
        "вклиниться шаги" % (line_drop - line_contour))


def test_degenerate_fragments_are_cleaned_after_split():
    """Обрывки от разреза чистятся там, где рождаются, и перед разбором.

    Разрез линией разлома оставляет куски нулевой длины, когда линия
    проходит точно через вершину изолинии. Processing считает такую
    геометрию некорректной и прерывает расчёт целиком. Падало оно позже,
    на продлении открытых концов, поэтому сторож смотрит на оба места:
    сразу после разреза и сразу после разбора мультичастей.
    """
    marks = []
    for name in ("_split_by_faults", "isolines_and_polygons"):
        marks += _steps(_func(name))
    kinds = [k for k, _ in marks]
    assert kinds.count("clean") >= 2, "чисток стало меньше двух"
    for step in ("native:splitwithlines", "native:multiparttosingleparts"):
        assert step in kinds, "шаг %s пропал" % step
        line = min(ln for k, ln in marks if k == step)
        after = [ln for k, ln in marks if k == "clean" and ln > line]
        assert after and min(after) - line <= 6, (
            "после %s чистка не стоит вплотную" % step)


def _steps(fn):
    """Вызовы чистки и шагов Processing внутри функции, с номерами строк."""
    marks = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "_clean_lines":
            marks.append(("clean", node.lineno))
        for arg in node.args:
            if isinstance(arg, ast.Constant) and arg.value in (
                    "native:splitwithlines", "native:multiparttosingleparts"):
                marks.append((arg.value, node.lineno))
    return marks


def test_own_steps_do_not_go_through_processing():
    """Свои шаги обходят объекты, а не зовут processing.run.

    Любой алгоритм Processing сам проверяет геометрию на входе и сорвался
    бы ровно на том, что призван вычистить. Проверяется общий каркас и
    все три шага, которые на нём стоят.
    """
    for name in ("_rewrite_lines", "_clean_lines", "_snap_ends_to_faults",
                 "_extend_free_ends"):
        fn = _func(name)
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)):
                assert node.func.attr != "run", (
                    "%s ушёл в processing.run" % name)


def test_line_steps_share_one_frame():
    """Три шага цепочки стоят на общем каркасе, а не копируют его.

    Каркас был скопирован трижды, и каждая копия жила своей жизнью.
    Ровно на этом сломались три правки подряд: переход на свой обход
    уронил полигонизацию, а овершут сломался дважды сам. Ошибка в копии
    не видна из соседней копии.
    """
    for name in ("_clean_lines", "_snap_ends_to_faults", "_extend_free_ends"):
        src = ast.dump(_func(name))
        assert "_rewrite_lines" in src, "%s больше не на общем каркасе" % name
        assert "QgsVectorLayer" not in src, (
            "%s снова заводит слой сам" % name)


def test_corridor_is_cut_and_ends_snap_to_the_fault_line():
    """Коридор режется буфером, а концы притягивает своя функция.

    Штатная притяжка Processing тянет конец к ближайшей точке опорного
    слоя без разбора, и за концом разлома этой точкой оказывается
    концевая вершина, одна на все окрестные концы. Управлять этим у
    алгоритма нечем, поэтому притяжка своя. Сторож против возврата.
    """
    fn = _func("_cut_fault_corridor")
    src = ast.dump(fn)
    for step in ("native:buffer", "native:difference"):
        assert step in src, "в коридоре пропал шаг %s" % step
    assert "native:snapgeometries" not in src, (
        "притяжка вернулась к штатной, веер у концов разлома возвращается")
    assert "_snap_ends_to_faults" in src, "своя притяжка пропала"
    caps = [n for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and n.value == "END_CAP_STYLE"]
    assert caps, "торцы буфера перестали задаваться"


def test_faults_reach_the_lines_only_branch():
    """Разлом режет изолинии и тогда, когда пояса не строятся.

    Разрыв принадлежит линиям, а не полигонам. Пока разломы доходили лишь
    до ветки с поясами, снятая галочка «Контурные полигоны» молча
    возвращала изолинии, сшитые поперёк разлома.
    """
    fn = _func("isolines_from_raster")
    names = [n.func.id for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "_split_by_faults" in names, (
        "линейная ветка перестала резать изолинии разломом")


def test_overshoot_does_not_jump_over_the_fault():
    """Овершут идёт своим обходом и знает про разломы.

    Штатный native:extendlines продлевает все открытые концы без разбора,
    включая притянутые к разлому. Такой хвостик выносится на чужое крыло,
    достаёт до соседней изолинии и замыкает лишнюю грань пояса. Сторож
    против возврата.
    """
    fn = _func("_extend_free_ends")
    src = ast.dump(fn)
    assert "_on_fault" in src, "овершут перестал проверять разлом"
    fn2 = _func("isolines_and_polygons")
    src2 = ast.dump(fn2)
    assert "native:extendlines" not in src2, (
        "штатное продление вернулось, хвостики снова перепрыгнут разлом")
    assert "_extend_free_ends" in src2, "свой овершут пропал"
    args = [n for n in ast.walk(fn2)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_extend_free_ends"]
    assert args, "вызов овершута пропал"
    names = [a.id for a in args[0].args if isinstance(a, ast.Name)]
    assert "faults" in names, "разломы до овершута не доходят"


def test_layers_are_resolved_in_one_place():
    """mapLayerFromString зовётся только из _as_layer.

    В цепочке изолиний перемешаны два вида звеньев: алгоритмы Processing
    возвращают строку-идентификатор, а свои шаги (чистка обрывков,
    притяжка, овершут) отдают готовый memory-слой. Прямой вызов
    mapLayerFromString на объекте слоя бросает TypeError, и полигонизация
    так и упала, когда овершут перестал быть шагом Processing: правка была
    верной, а звено ниже по цепочке о ней не знало.

    Поэтому разрешение слоя живёт в одном месте, и каждое новое звено
    получает поддержку обоих видов даром.
    """
    with open(SRC, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    outside = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for call in ast.walk(node):
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "mapLayerFromString"
                    and node.name != "_as_layer"):
                outside.append("%s:%d" % (node.name, call.lineno))
    assert not outside, (
        "разрешение слоя мимо _as_layer: %s" % ", ".join(outside))


def test_corridor_end_cap_is_flat():
    """Торец коридора плоский: за концом разлома не режем.

    Круглый торец оставлял в поясах клин. Изолинии, обрезанные за концом
    линии, получали свободные концы посреди просвета, овершут продлевал
    их навстречу друг другу, и они замыкали лишнюю грань.
    """
    fn = _func("_cut_fault_corridor")
    caps = []
    for call in ast.walk(fn):
        if not isinstance(call, ast.Call):
            continue
        for kw in call.args:
            if not isinstance(kw, ast.Dict):
                continue
            for k, v in zip(kw.keys, kw.values):
                if (isinstance(k, ast.Constant) and k.value == "END_CAP_STYLE"
                        and isinstance(v, ast.Constant)):
                    caps.append(v.value)
    assert caps, "торец буфера перестал задаваться"
    assert all(c == 1 for c in caps), (
        "торец коридора не плоский: %s" % caps)


def test_overshoot_shortens_at_the_fault_but_never_stops():
    """У разлома хвостик короткий, но он есть.

    Отмена была ошибкой: конец, притянутый на линию, образует с ней
    T-стык, а такой стык GEOS часто не нодирует - ровно то, ради чего
    овершут и заведён. Грань не замыкалась, соседние пояса сливались, и
    под частью изолиний границы полигонов не было вовсе.
    """
    fn = _func("_extend_free_ends")
    src = ast.dump(fn)
    assert "short" in src, "короткий хвостик у разлома пропал"
    assert "_on_fault" in src, "овершут перестал различать разлом"
    body = _func("isolines_and_polygons")
    calls = [n for n in ast.walk(body)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_extend_free_ends"]
    assert calls, "вызов овершута пропал"
    assert "hold_r" in [kw.arg for kw in calls[0].keywords], (
        "радиус распознавания разлома до овершута не доходит")


def test_corridor_axis_is_trimmed_but_snapping_uses_the_real_fault():
    """Коридор строится по укороченной линии, притяжка - по настоящей.

    Если укоротить и то и другое, концы у торца полосы окажутся за концом
    оси и снова повиснут. Если не укорачивать ничего, торец режет
    изолинии за концом разлома с тем же итогом.
    """
    fn = _func("_cut_fault_corridor")
    src = ast.dump(fn)
    assert "_trimmed_fault_layer" in src, "ось коридора перестала укорачиваться"
    buf_inputs = []
    for call in ast.walk(fn):
        if not isinstance(call, ast.Call):
            continue
        for arg in call.args:
            if not isinstance(arg, ast.Dict):
                continue
            for k, v in zip(arg.keys, arg.values):
                if isinstance(k, ast.Constant) and k.value == "INPUT":
                    buf_inputs.append(getattr(v, "id", None))
    assert "axis" in buf_inputs, "буфер строится не по укороченной оси"
    snap = [n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_snap_ends_to_faults"]
    assert snap, "притяжка пропала"
    names = [a.id for a in snap[0].args if isinstance(a, ast.Name)]
    assert "faults" in names, "притяжка идёт к укороченной оси, а не к разлому"
