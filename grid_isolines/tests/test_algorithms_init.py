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


# Заглушки живут в sys.modules и переживают файл, если их не убрать. Пока
# уборки не было, подставной «osgeo» доставался тестам, которые идут следом
# по алфавиту: TestDstAxisOrder, TestSectionDrawCrs и проверка WKT в
# demo_relief просили настоящий osgeo, получали заглушку, пропуск по
# ImportError не срабатывал, и сторожа падали на ровном месте. По одному
# файлу они проходили, в общем прогоне - нет.
_SAVED_MODULES = None


def setup_module(module):
    """Снимок sys.modules до первого теста файла."""
    global _SAVED_MODULES
    _SAVED_MODULES = dict(sys.modules)


def teardown_module(module):
    """Вернуть sys.modules в исходное состояние."""
    if _SAVED_MODULES is None:
        return
    for name in list(sys.modules):
        if name not in _SAVED_MODULES:
            del sys.modules[name]
    sys.modules.update(_SAVED_MODULES)


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
    assert len(algorithms.ALGORITHMS) == 66, (
        "ожидалось 66 алгоритмов, а их %d" % len(algorithms.ALGORITHMS))
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
    _install_qgis_stubs()
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
    _install_qgis_stubs()
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
    _install_qgis_stubs()
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
    _install_qgis_stubs()
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
    _install_qgis_stubs()
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
    _install_qgis_stubs()
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
    _install_qgis_stubs()
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


def test_every_self_constant_exists_in_its_class():
    """Каждое self.ИМЯ в классе алгоритма существует как константа.

    Опечатка в имени константы не видна ни синтаксисом, ни импортом: она
    вылезает только на живом прогоне, посреди расчёта, и тем обиднее, чем
    дольше считалось до неё. Ровно так 1.05 сорвалась на self.FIELD при
    константе ZFIELD - после подбора модели, на самой записи результата.

    Проверяются только имена в верхнем регистре: это соглашение для
    параметров и выходов, а методы и обычные атрибуты сюда не попадают.
    """
    import ast as _ast
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "algorithms.py"), encoding="utf-8") as fh:
        tree = _ast.parse(fh.read())
    classes = {n.name: n for n in tree.body if isinstance(n, _ast.ClassDef)}

    def own(node):
        got = set()
        for st in node.body:
            if isinstance(st, _ast.Assign):
                for tgt in st.targets:
                    if isinstance(tgt, _ast.Name):
                        got.add(tgt.id)
                    elif isinstance(tgt, _ast.Tuple):
                        for el in tgt.elts:
                            if isinstance(el, _ast.Name):
                                got.add(el.id)
        return got

    def visible(node, seen=None):
        """Свои константы плюс родительские, по цепочке наследования."""
        seen = seen or set()
        if node.name in seen:
            return set()
        seen.add(node.name)
        got = own(node)
        for base in node.bases:
            name = getattr(base, "id", None)
            if name in classes:
                got |= visible(classes[name], seen)
        return got

    bad = []
    for cls in classes.values():
        declared = visible(cls)
        if not declared:
            continue
        used = set()
        for node in _ast.walk(cls):
            if (isinstance(node, _ast.Attribute)
                    and isinstance(node.value, _ast.Name)
                    and node.value.id == "self"
                    and node.attr.isupper()):
                used.add(node.attr)
        for name in sorted(used - declared):
            bad.append("%s.%s" % (cls.name, name))
    assert not bad, ("несуществующие константы: %s" % ", ".join(bad))


def test_module_functions_have_no_late_definitions():
    """В функциях модуля имя не читается раньше присваивания.

    Сторож выше проверял только тела _process в классах, и правка в
    обычной функции проскочила: присваивание target уехало при переписке
    соседней функции, а чтение осталось. Компиляция такое пропускает,
    вылезает оно на живом прогоне.

    Аргументы, вложенные функции, генераторы и импорты из проверки
    исключены: замыкание законно читает то, что определено ниже, потому
    что вызывается позже.
    """
    import ast as _ast
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "algorithms.py"), encoding="utf-8") as fh:
        tree = _ast.parse(fh.read())
    bad = []
    for fn in [n for n in tree.body if isinstance(n, _ast.FunctionDef)]:
        args = {a.arg for a in fn.args.args}
        args |= {a.arg for a in getattr(fn.args, "kwonlyargs", [])}
        if fn.args.vararg:
            args.add(fn.args.vararg.arg)
        if fn.args.kwarg:
            args.add(fn.args.kwarg.arg)
        assigned = dict()          # имя -> САМАЯ РАННЯЯ строка присваивания
        globals_ = set()
        nested = set()
        for node in _ast.walk(fn):
            if isinstance(node, (_ast.FunctionDef, _ast.Lambda,
                                 _ast.ListComp, _ast.SetComp,
                                 _ast.DictComp, _ast.GeneratorExp)):
                if node is not fn:
                    for sub in _ast.walk(node):
                        nested.add(id(sub))
                continue
            def _mark(name, line):
                # ast.walk обходит в ширину, поэтому «первое» встреченное
                # присваивание не обязательно первое по тексту. Берём
                # самую раннюю строку, иначе сторож врёт на ровном месте.
                if name not in assigned or line < assigned[name]:
                    assigned[name] = line

            if isinstance(node, _ast.Global):
                globals_.update(node.names)
            elif isinstance(node, _ast.Name) and isinstance(node.ctx,
                                                            _ast.Store):
                _mark(node.id, node.lineno)
            elif isinstance(node, (_ast.Import, _ast.ImportFrom)):
                for al in node.names:
                    nm = (al.asname or al.name).split(".")[0]
                    _mark(nm, node.lineno)
            elif isinstance(node, _ast.ExceptHandler) and node.name:
                _mark(node.name, node.lineno)
            elif isinstance(node, _ast.arg):
                _mark(node.arg, fn.lineno)
        for node in _ast.walk(fn):
            if id(node) in nested:
                continue
            if (isinstance(node, _ast.Name)
                    and isinstance(node.ctx, _ast.Load)
                    and node.id in assigned
                    and node.id not in args
                    and node.id not in globals_
                    and node.lineno < assigned[node.id]):
                bad.append("%s: %s (строка %d, присваивание на %d)"
                           % (fn.name, node.id, node.lineno,
                              assigned[node.id]))
    assert not bad, ("имя читается раньше присваивания:\n  "
                     + "\n  ".join(sorted(set(bad))))


def test_module_functions_have_no_undefined_names():
    """В функциях модуля не читаются имена, которых нигде нет.

    Это второй случай той же беды и он опаснее: не «раньше
    присваивания», а присваивания нет вовсе. Ровно так пропало
    объявление target при переписке соседней функции, и 1.05 упала с
    NameError уже после подбора модели.

    Сторож на порядок такое не ловит: имени нет в списке присвоенных, и
    сравнивать не с чем.
    """
    import ast as _ast
    import builtins
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "algorithms.py"), encoding="utf-8") as fh:
        src = fh.read()
    tree = _ast.parse(src)
    known = set(dir(builtins))
    known.update(("__file__", "__name__", "__doc__", "__package__"))
    for node in tree.body:
        if isinstance(node, _ast.Assign):
            for tgt in _ast.walk(node):
                if isinstance(tgt, _ast.Name) and isinstance(tgt.ctx,
                                                             _ast.Store):
                    known.add(tgt.id)
        elif isinstance(node, (_ast.FunctionDef, _ast.ClassDef)):
            known.add(node.name)
        elif isinstance(node, (_ast.Import, _ast.ImportFrom)):
            for al in node.names:
                known.add((al.asname or al.name).split(".")[0])

    bad = []
    for fn in [n for n in tree.body if isinstance(n, _ast.FunctionDef)]:
        local = set(known)
        for node in _ast.walk(fn):
            if isinstance(node, _ast.Name) and isinstance(node.ctx,
                                                          _ast.Store):
                local.add(node.id)
            elif isinstance(node, _ast.arg):
                local.add(node.arg)
            elif isinstance(node, (_ast.Import, _ast.ImportFrom)):
                for al in node.names:
                    local.add((al.asname or al.name).split(".")[0])
            elif isinstance(node, (_ast.FunctionDef, _ast.Lambda)):
                local.add(getattr(node, "name", "<lambda>"))
            elif isinstance(node, _ast.ExceptHandler) and node.name:
                local.add(node.name)
            elif isinstance(node, _ast.Global):
                local.update(node.names)
        for node in _ast.walk(fn):
            if (isinstance(node, _ast.Name)
                    and isinstance(node.ctx, _ast.Load)
                    and node.id not in local):
                bad.append("%s: %s (строка %d)"
                           % (fn.name, node.id, node.lineno))
    assert not bad, ("имя нигде не определено:\n  "
                     + "\n  ".join(sorted(set(bad))))


def test_declared_parameter_constants_appear_in_the_form():
    """Константа параметра объявлена - значит и в форме она есть.

    Скрипт правки упал на середине, константа TARGET_TABLE осталась, а
    сам параметр в initAlgorithm не добавился. Код при этом рабочий:
    чтение шло через getattr и молча возвращало None, поэтому 1.05
    плодила новую таблицу на каждый прогон вместо дописывания в
    существующую. Ни один сторож этого не видел.

    Проверяются только константы, чьё значение совпадает с именем: это
    соглашение для параметров Processing. Служебные константы вроде
    списков и порогов сюда не попадают.
    """
    import ast as _ast
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "algorithms.py"), encoding="utf-8") as fh:
        src = fh.read()
    tree = _ast.parse(src)
    lines = src.splitlines(True)
    # Общие функции, которые сами добавляют параметры: только их текст
    # считается законным местом объявления через alg.ИМЯ. Искать по
    # всему модулю нельзя - имя встретится в любой функции, которая
    # параметр лишь читает, и сторож ослепнет.
    adders = ""
    for fn in [n for n in tree.body if isinstance(n, _ast.FunctionDef)]:
        seg = "".join(lines[fn.lineno - 1:fn.end_lineno])
        if "addParameter" in seg:
            adders += seg
    bad = []
    for cls in [n for n in tree.body if isinstance(n, _ast.ClassDef)]:
        params = set()
        for st in cls.body:
            if not isinstance(st, _ast.Assign):
                continue
            targets, values = [], []
            for tgt in st.targets:
                if isinstance(tgt, _ast.Name):
                    targets, values = [tgt], [st.value]
                elif isinstance(tgt, _ast.Tuple) \
                        and isinstance(st.value, _ast.Tuple):
                    targets, values = list(tgt.elts), list(st.value.elts)
            for tgt, val in zip(targets, values):
                if (isinstance(tgt, _ast.Name)
                        and isinstance(val, _ast.Constant)
                        and val.value == tgt.id):
                    params.add(tgt.id)
        if not params:
            continue
        body = "".join(lines[cls.lineno - 1:cls.end_lineno])
        init = None
        for fn in cls.body:
            if isinstance(fn, _ast.FunctionDef) and fn.name == "initAlgorithm":
                init = "".join(lines[fn.lineno - 1:fn.end_lineno])
        if init is None:
            continue
        for name in sorted(params):
            # часть параметров добавляется общей функцией через alg.ИМЯ,
            # поэтому в initAlgorithm класса их нет по устройству
            # Ищем по всему телу класса, а не только в initAlgorithm:
            # часть параметров объявляется в соседних методах, и сужать
            # до одного метода значит ловить ложное.
            if ("self." + name) in body or ("alg." + name) in adders:
                continue
            bad.append("%s.%s" % (cls.name, name))
    assert not bad, ("константа параметра есть, а в форме его нет: %s"
                     % ", ".join(bad))
