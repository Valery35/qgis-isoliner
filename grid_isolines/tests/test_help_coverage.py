# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Настроечный параметр обязан быть описан в справке инструмента.

Пробелы копились молча. Разломы прожили несколько версий вообще без
единой строки объяснения, а у 3.06 на восемнадцать параметров приходилось
меньше семисот знаков справки. Пользователь видит ручку и не знает ни что
она делает, ни что будет при умолчании.

Что проверяется. Для каждого параметра берутся значимые слова его
подписи, и хотя бы одно из них обязано встретиться в тексте справки.
Сравнение по корню в шесть букв: русское словоизменение иначе не поймать
без словаря, а тащить словарь ради сторожа не стоит.

Что НЕ проверяется. Входы, выходы и прочая обвязка описания не требуют:
их назначение видно из имени. Список таких подписей ведётся вручную
ниже - он короткий и осмысленный, в отличие от списка исключений по
инструментам.

Порог объёма справки намеренно мягкий. Цель сторожа не в том, чтобы
заставить писать много, а в том, чтобы новая ручка не появилась немой.

Сторож грубый и это осознанно. Совпадения по корню хватает, чтобы ручка
считалась описанной, поэтому «Коэффициент затухания» пройдёт мимо, если в
справке уже есть «Коэффициент релаксации». Он ловит немоту, а не качество
текста. Качество проверяет человек.
"""
import ast
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "algorithms.py")

# Обвязка определяется ТИПОМ параметра, а не словами в подписи. Слой,
# поле, охват, система координат и место сохранения не нуждаются в
# объяснении: их назначение задано самим типом. Список слов вместо этого
# приходилось вести руками, он разрастался и всё равно промахивался -
# «Входная ЦМР» в него попадала, а «Озёра и урез воды» нет, хотя обе
# суть входные слои.
PLUMBING_TYPES = (
    "QgsProcessingParameterFeatureSource",
    "QgsProcessingParameterMultipleLayers",
    "QgsProcessingParameterRasterLayer",
    "QgsProcessingParameterVectorLayer",
    "QgsProcessingParameterMapLayer",
    "QgsProcessingParameterField",
    "QgsProcessingParameterBand",
    "QgsProcessingParameterExtent",
    "QgsProcessingParameterCrs",
    "QgsProcessingParameterFile",
    "QgsProcessingParameterFileDestination",
    "QgsProcessingParameterFolderDestination",
    "QgsProcessingParameterRasterDestination",
    "QgsProcessingParameterVectorDestination",
    "QgsProcessingParameterFeatureSink",
)

# Инструменты, у которых справка описывает предмет целиком и разбор по
# ручкам излишен: демо-генераторы объясняют, ЧТО они строят, а параметры
# там однотипные размеры и зерно ГСЧ.
WHOLE_SUBJECT = ("7.05",)


# Долг закрыт полностью: настроечных параметров без описания не осталось.
# Список сохранён пустым намеренно. Он нужен, если однажды придётся внести
# большую партию параметров разом и описать их не сразу: тогда новые
# записи кладутся сюда и обязаны отсюда уходить. Пока он пуст, сторож
# работает строго - ни одной немой ручки.
KNOWN_GAPS = frozenset()



def _lit(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and node.args:
        return _lit(node.args[0])
    if isinstance(node, ast.BinOp):
        left, right = _lit(node.left), _lit(node.right)
        if left is not None or right is not None:
            return (left or "") + (right or "")
    return None


def _shared_blocks(tree):
    """Общие куски справки, подставляемые функциями вроде _fill_help().

    Пара «Заполнить понижения» плюс «Epsilon уклона» описана один раз и
    подключается к пяти инструментам вызовом функции. Читая только
    строковые литералы внутри shortHelpString, сторож такой блок не видит
    и считает параметры неописанными. Поэтому имена таких функций
    разворачиваются в текст их константы.
    """
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
            val = _lit(node.value)
            if name and isinstance(val, str):
                consts[name] = val
    blocks = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for st in node.body:
            if not isinstance(st, ast.Return):
                continue
            call = st.value
            if (isinstance(call, ast.Call)
                    and getattr(call.func, "id", None) == "_tr"
                    and call.args
                    and isinstance(call.args[0], ast.Name)
                    and call.args[0].id in consts):
                blocks[node.name] = consts[call.args[0].id]
    return blocks


def _tools():
    """Список (префикс, имя, справка, подписи параметров)."""
    with open(SRC, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    blocks = _shared_blocks(tree)
    out = []
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        disp = None
        help_text = ""
        params = []
        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            if fn.name == "displayName":
                for st in ast.walk(fn):
                    val = _lit(st.value) if isinstance(st, ast.Return) else None
                    if val and re.match(r"\d\.\d\d ", val):
                        disp = val
            elif fn.name == "shortHelpString":
                help_text = "".join(
                    s.value for s in ast.walk(fn)
                    if isinstance(s, ast.Constant) and isinstance(s.value, str))
                for call in ast.walk(fn):
                    name = (getattr(getattr(call, "func", None), "id", None)
                            if isinstance(call, ast.Call) else None)
                    if name in blocks:
                        help_text += blocks[name]
            elif fn.name == "initAlgorithm":
                for call in ast.walk(fn):
                    if not isinstance(call, ast.Call):
                        continue
                    name = (getattr(call.func, "id", None)
                            or getattr(call.func, "attr", None) or "")
                    if not name.startswith("QgsProcessingParameter"):
                        continue
                    for arg in call.args:
                        val = _lit(arg)
                        if val and " " in val and not val.isupper():
                            params.append((name, val))
                            break
        if disp:
            out.append((disp[:4], disp, help_text, params))
    return out


def _is_plumbing(kind):
    return kind in PLUMBING_TYPES


def _words(label):
    head = re.split(r"[,(]", label)[0]
    return [w.lower() for w in re.findall(r"[А-Яа-яЁёA-Za-z]{5,}", head)]


def _gaps():
    """Настроечные параметры, не упомянутые в справке своего инструмента."""
    found = []
    for num, _disp, help_text, params in _tools():
        if num in WHOLE_SUBJECT:
            continue
        low = help_text.lower()
        for kind, label in params:
            if _is_plumbing(kind):
                continue
            words = _words(label)
            if not words:
                continue
            if not any(w[:6] in low for w in words):
                found.append("%s: %s" % (num, label))
    return set(found)


def test_every_knob_is_documented():
    """Ни одного настроечного параметра без описания в справке."""
    fresh = sorted(_gaps() - KNOWN_GAPS)
    assert not fresh, (
        "параметры без описания в справке:\n  " + "\n  ".join(fresh))


def test_known_gaps_list_only_shrinks():
    """Закрытый пробел обязан уходить из списка известного долга.

    Иначе список превращается в свалку и перестаёт что-либо значить.
    """
    stale = sorted(KNOWN_GAPS - _gaps())
    assert not stale, (
        "эти пробелы закрыты, уберите их из KNOWN_GAPS:\n  "
        + "\n  ".join(stale))


def test_help_is_not_empty():
    """У каждого инструмента справка есть и она не в одну строку."""
    thin = ["%s (%d знаков)" % (disp[:44], len(h))
            for _, disp, h, _ in _tools() if len(h) < 200]
    assert not thin, "справка почти отсутствует:\n  " + "\n  ".join(thin)


def test_faults_are_described_where_they_are_accepted():
    """Разломы описаны в справке каждого инструмента, который их принимает.

    Отдельная проверка: разломы прожили несколько версий вообще без
    объяснения, а решений там много неочевидных. Инструменты берутся по
    объявленному входу FAULTS, а не по подписи: демо-генераторы разлом
    ВЫДАЮТ, и требовать от них того же разбора незачем.
    """
    with open(SRC, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    bad = []
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        takes = any(isinstance(st, ast.Assign)
                    and any(getattr(tg, "id", "") == "FAULTS"
                            for tg in st.targets)
                    for st in cls.body)
        if not takes:
            continue
        for fn in cls.body:
            if isinstance(fn, ast.FunctionDef) and fn.name == "shortHelpString":
                txt = "".join(s.value for s in ast.walk(fn)
                              if isinstance(s, ast.Constant)
                              and isinstance(s.value, str))
                if "разлом" not in txt.lower():
                    bad.append(cls.name)
    assert not bad, "разломы приняты, но не описаны: %s" % ", ".join(bad)
