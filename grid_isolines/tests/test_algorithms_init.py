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
    assert len(algorithms.ALGORITHMS) == 57, (
        "ожидалось 57 алгоритмов, а их %d" % len(algorithms.ALGORITHMS))
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
