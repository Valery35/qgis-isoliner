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


class _Stub(metaclass=type("_M", (type,), {
        "__getattr__": lambda cls, name: cls()})):
    """Универсальная заглушка: вызывается, подписывается, поддерживает |."""
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Stub()

    def __getattr__(self, name):
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
    assert len(algorithms.ALGORITHMS) == 6, (
        "ожидалось 6 алгоритмов, а их %d" % len(algorithms.ALGORITHMS))
    for cls in algorithms.ALGORITHMS:
        a = cls()
        a.initAlgorithm()                 # тут и падало бы 'no attribute tr'
        for meth in ("name", "displayName", "group", "groupId",
                     "shortHelpString", "createInstance"):
            getattr(a, meth)()
    print("OK: инициализировано алгоритмов: %d" % len(algorithms.ALGORITHMS))


if __name__ == "__main__":
    test_all_algorithms_init()
