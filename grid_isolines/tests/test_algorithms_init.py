"""Headless-проверка, что все алгоритмы инициализируются без QGIS.

QGIS в окружении тестов нет, поэтому qgis.* подменяется лёгкой заглушкой.
Тест создаёт каждый алгоритм из ALGORITHMS и вызывает initAlgorithm() и
методы-метаданные. Этого достаточно, чтобы поймать обращения к
несуществующим методам/атрибутам (например, забытый self.tr) - то есть
ровно тот класс ошибок, что роняет загрузку плагина в QGIS.
"""
import os
import sys
import types
import importlib


def _missing(name):
    """Имя, которого заглушка подменять НЕ должна.

    Константы имён параметров у нас пишутся заглавными (INPUT, DIPFIELD).
    Если такой атрибут не объявлен в классе, обращение обязано упасть, а
    не превратиться в заглушку: иначе алгоритм с забытой константой
    проходит тест и падает уже в QGIS. Ровно так и случилось с DIPFIELD в
    4.05 - правка объявила параметры, но не объявила имена.
    """
    return (name.isupper() and not name.startswith("_")
            and name not in ("PI", "E"))


class _Stub(metaclass=type("_M", (type,), {
        "__getattr__": lambda cls, name: (_ for _ in ()).throw(
            AttributeError(name)) if _missing(name) else cls()})):
    """Универсальная заглушка: вызывается, подписывается, поддерживает |."""
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Stub()

    def __getattr__(self, name):
        if _missing(name):
            raise AttributeError(name)
        return _Stub()

    def __or__(self, other):
        return _Stub()
    __ror__ = __or__

    def __int__(self):
        return 0


def _install_qgis_stubs():
    for mod in ("qgis", "qgis.core", "qgis.gui",
                "qgis.PyQt", "qgis.PyQt.QtCore",
                "qgis.PyQt.QtGui", "qgis.PyQt.QtWidgets", "processing",
                "osgeo", "osgeo.gdal", "osgeo.osr"):
        m = types.ModuleType(mod)
        m.__getattr__ = lambda name: _Stub          # любой символ -> заглушка
        sys.modules[mod] = m


def test_all_algorithms_init():
    here = os.path.dirname(os.path.abspath(__file__))
    pkg_dir = os.path.dirname(here)
    parent = os.path.dirname(pkg_dir)
    pkg = "grid_isolines"
    # пакет-обёртка без запуска __init__.py (он тянет QGIS)
    m = types.ModuleType(pkg)
    m.__path__ = [pkg_dir]
    sys.modules[pkg] = m
    if parent not in sys.path:
        sys.path.insert(0, parent)
    _install_qgis_stubs()

    algorithms = importlib.import_module(pkg + ".algorithms")
    algorithms._tr = lambda s: s          # translate-заглушка возвращает строку
    assert algorithms.ALGORITHMS, "список ALGORITHMS пуст"
    assert len(algorithms.ALGORITHMS) == 67, (
        "ожидалось 67 алгоритмов, а их %d" % len(algorithms.ALGORITHMS))
    for cls in algorithms.ALGORITHMS:
        a = cls()
        a.initAlgorithm()                 # тут и падало бы 'no attribute tr'
        for meth in ("name", "displayName", "group", "groupId",
                     "shortHelpString", "createInstance"):
            getattr(a, meth)()
    print("OK: инициализировано алгоритмов: %d" % len(algorithms.ALGORITHMS))


def test_diagnostics_live_in_their_own_group():
    """Диагностика вынесена из рабочей цепочки топографии.

    Подгрупп в Processing нет, дерево у провайдера плоское, поэтому ветка
    делается именем группы: оно сортируется сразу за топографией. Побочный
    и желанный эффект - демо-генератор снова последний в рабочей группе.
    """
    import inspect
    from grid_isolines import algorithms as A

    diag = (A.ContourSplitAlgorithm, A.ContourResidualAlgorithm,
            A.TerracingCheckAlgorithm, A.TerraceSmoothAlgorithm)
    for cls in diag:
        src = inspect.getsource(cls)
        assert "GROUP_TOPODIAG_ID" in src, cls.__name__
        assert "return GROUP_TOPO_ID" not in src, cls.__name__

    # рабочая топография осталась при своей группе
    src = inspect.getsource(A.TopoDemoReliefAlgorithm)
    assert "GROUP_TOPO_ID" in src and "GROUP_TOPODIAG" not in src


def test_group_name_sorts_after_topography():
    from grid_isolines import algorithms as A
    assert A.GROUP_TOPODIAG > A.GROUP_TOPO, (A.GROUP_TOPO, A.GROUP_TOPODIAG)
    assert A.GROUP_TOPODIAG_ID != A.GROUP_TOPO_ID


def test_group_order_is_deterministic():
    """Слои группы встают по списку, а не в порядке загрузки.

    Порядок загрузки выходных слоёв в Processing не определён, и пачка
    приезжала в дерево каждый раз иначе. Для 4.01 это не косметика: он
    берёт порядок поверхностей из дерева слоёв, а значит случайный
    порядок дал бы случайный разрез.
    """
    from grid_isolines import algorithms as A

    order = ["B_top", "B_bottom", "AB_top", "AB_bottom",
             "KpII_top", "KpII_bottom", "dissolution"]
    shuffled = ["KpII_bottom", "dissolution", "B_bottom", "AB_top",
                "B_top", "KpII_top", "AB_bottom"]
    got = [shuffled[i] for i in A.group_order_indices(shuffled, order)]
    assert got == order, got
    again = [got[i] for i in A.group_order_indices(got, order)]
    assert again == order, again


def test_unlisted_layers_go_after_the_listed_ones():
    """Слой не из списка встаёт следом, а не теряется и не лезет вверх."""
    from grid_isolines import algorithms as A

    order = ["B_top", "B_bottom"]
    names = ["чужой", "B_bottom", "B_top", "ещё один"]
    got = [names[i] for i in A.group_order_indices(names, order)]
    assert got[:2] == ["B_top", "B_bottom"], got
    assert sorted(got[2:]) == ["ещё один", "чужой"], got


def test_style_not_overwritten_by_grouping():
    """Стиль и сворачивание узла не должны спорить за пост-процессор.

    У слоя пост-процессор один: _topo_group_layer со сворачиванием ставит
    свой и затирает стилевой. Это уже стоило одной ложной диагностики
    (списали на data-defined цвет в QML), поэтому теперь сторож.
    """
    import re
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "algorithms.py"), encoding="utf-8").read()
    lines = src.split("\n")
    styled = {}
    for i, l in enumerate(lines):
        m = re.search(r"_attach_(?:style|break_style|categories)\(context, (\w+)", l)
        if m:
            styled.setdefault(m.group(1), []).append(i)
    bad = []
    for i, l in enumerate(lines):
        m = re.search(r"_topo_group_layer\(context, (\w+)", l)
        if not m or m.group(1) not in styled:
            continue
        if "collapse=False" in "\n".join(lines[i:i + 3]):
            continue
        if [n for n in styled[m.group(1)] if abs(n - i) < 30]:
            bad.append((i + 1, m.group(1)))
    assert not bad, "группировка затрёт стиль: %s" % bad


def test_process_uses_only_known_params():
    """Всё, к чему обращается _process, объявлено и заведено выше.

    Правка, легшая наполовину, оставила в 6.03 чтение слоя линий без
    самого параметра: тесты этого не видели, потому что headless не
    доходит до формы, а живой прогон падал бы на первом же запуске.
    Сторож смотрит на код: имя, встреченное в _process, обязано
    встречаться и вне его.
    """
    import inspect
    import re

    from grid_isolines import algorithms as A

    bad = []
    for cls in A.ALGORITHMS:
        src = inspect.getsource(cls)
        k = src.find("def _process")
        if k < 0:
            continue
        head, body = src[:k], src[k:]
        for name in sorted(set(re.findall(r"self\.([A-Z][A-Z0-9_]{2,})",
                                          body))):
            if name not in head:
                bad.append("%s.%s" % (cls.__name__, name))
    assert not bad, "используется, но нигде не заведено: %s" % bad


def test_process_bodies_have_no_late_definitions():
    """Имя не читается раньше самого раннего присваивания.

    Именно так упал 6.03: список переносимых свойств вычислялся ниже
    цикла, который его читал. Компиляция такое пропускает, headless-тесты
    формы не касаются, и ловится оно только живым прогоном.

    Аргументы, вложенные функции и генераторы из проверки исключены:
    замыкание законно читает то, что определено ниже, потому что
    вызывается позже, а у генератора своя область имён.
    """
    import ast
    import inspect
    import textwrap

    from grid_isolines import algorithms as A

    bad = []
    for cls in A.ALGORITHMS:
        src = inspect.getsource(cls)
        k = src.find("def _process")
        if k < 0:
            continue
        try:
            fn = ast.parse(textwrap.dedent(src[k:])).body[0]
        except SyntaxError:
            continue
        args = {a.arg for a in fn.args.args}
        inner = set()
        for node in ast.walk(fn):
            if node is not fn and isinstance(
                    node, (ast.FunctionDef, ast.Lambda, ast.ListComp,
                           ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                for sub in ast.walk(node):
                    inner.add(id(sub))
        assigned = {}
        for node in ast.walk(fn):
            if id(node) in inner:
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                cur = assigned.get(node.id)
                if cur is None or node.lineno < cur:
                    assigned[node.id] = node.lineno
        for node in ast.walk(fn):
            if not isinstance(node, ast.Name) or id(node) in inner:
                continue
            if not isinstance(node.ctx, ast.Load) or node.id in args:
                continue
            ln = assigned.get(node.id)
            if ln is not None and node.lineno < ln:
                bad.append("%s: %s" % (cls.__name__, node.id))
    assert not bad, "имя читается раньше присваивания: %s" % sorted(set(bad))


def test_core_calls_unpack_right_number_of_values():
    """Вызовы ядра разбираются по фактическому числу возвращаемых значений.

    manning_q возвращает одно число, а в подвале чертежа его разбирали в
    пару: компиляция такое пропускает, headless-тесты формы не касаются, и
    падало оно только на живом прогоне. Сторож сверяет число целей
    присваивания с числом значений в return самой функции.
    """
    import ast
    import inspect
    import os

    from grid_isolines import hydro_section, section_core, drillhole_core

    mods = {"hydro_section": hydro_section, "section_core": section_core,
            "drillhole_core": drillhole_core, "_sc": section_core,
            "_dh": drillhole_core}
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tree = ast.parse(open(os.path.join(root, "algorithms.py"),
                          encoding="utf-8").read())
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)):
            continue
        f = node.value.func
        if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)):
            continue
        mod = mods.get(f.value.id)
        fn = getattr(mod, f.attr, None) if mod is not None else None
        if fn is None or not callable(fn):
            continue
        try:
            fsrc = inspect.getsource(fn)
        except (OSError, TypeError):
            continue
        want = len(node.targets[0].elts) \
            if isinstance(node.targets[0], ast.Tuple) else 1
        got = set()
        for r in ast.walk(ast.parse(fsrc)):
            if isinstance(r, ast.Return) and r.value is not None:
                got.add(len(r.value.elts)
                        if isinstance(r.value, ast.Tuple) else 1)
        if got and want not in got:
            bad.append("%s строка %d: берут %d, отдаёт %s"
                       % (f.attr, node.lineno, want, sorted(got)))
    assert not bad, bad


def test_parameters_are_not_reassigned_inside_loops():
    """Переменная, прочитанная из параметра, не присваивается внутри цикла.

    Цикл отметок земли завёл переменную step и затёр параметр шага по
    отметке: кривые всех створов после первого строились с шагом в
    десятки метров и выходили нулями. Нормализация параметра сразу после
    чтения законна и остаётся, запрещено присваивание того же имени
    внутри for и while: оно повторяется на каждом обороте и живёт после
    цикла.
    """
    import ast
    import inspect
    import textwrap

    from grid_isolines import algorithms as A

    bad = []
    for cls in A.ALGORITHMS:
        src = inspect.getsource(cls)
        k = src.find("def _process")
        if k < 0:
            continue
        try:
            fn = ast.parse(textwrap.dedent(src[k:])).body[0]
        except SyntaxError:
            continue
        pnames = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) \
                    and isinstance(node.value, ast.Call) \
                    and isinstance(node.value.func, ast.Attribute) \
                    and node.value.func.attr.startswith("parameterAs"):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        pnames.add(t.id)
        loops = [n for n in ast.walk(fn)
                 if isinstance(n, (ast.For, ast.While))]
        for loop in loops:
            for node in ast.walk(loop):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AugAssign):
                    targets = [node.target]
                v = getattr(node, "value", None)
                fresh = isinstance(v, ast.Call) \
                    and isinstance(v.func, ast.Attribute) \
                    and v.func.attr.startswith("parameterAs")
                if fresh:
                    continue  # повторное чтение параметра в цикле законно
                for t in targets:
                    for nm in ast.walk(t):
                        if isinstance(nm, ast.Name) and nm.id in pnames:
                            bad.append("%s: %s строка %d"
                                       % (cls.__name__, nm.id, nm.lineno))
    assert not bad, "параметр затирается в цикле: %s" % sorted(set(bad))


def test_metadata_reads_with_strict_parser():
    """metadata.txt читается тем же парсером, что и каталог плагинов.

    Каталог разбирает метаданные питоновским configparser с включённой
    интерполяцией, для которой одиночный процент - служебный символ.
    Строка журнала с процентом площади дважды отбивала архив уже на
    странице загрузки, поэтому проверка стоит здесь, а не в голове.
    """
    import configparser
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "metadata.txt")
    cp = configparser.ConfigParser(
        interpolation=configparser.BasicInterpolation())
    cp.read(path, encoding="utf-8")
    assert cp.get("general", "version")
    assert cp.get("general", "changelog")


if __name__ == "__main__":
    _fns = [(n, f) for n, f in sorted(globals().items())
            if n.startswith("test_") and callable(f)]
    _bad = 0
    for _name, _fn in _fns:
        try:
            _fn()
        except Exception as _exc:  # noqa: BLE001
            _bad += 1
            print("FAIL %s: %s" % (_name, _exc))
    print("%d тестов, ошибок %d" % (len(_fns), _bad))
    raise SystemExit(1 if _bad else 0)
