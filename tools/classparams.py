# -*- coding: utf-8 -*-
"""Описание инструмента для tools.json из класса QGIS, без QGIS.

Разбор algorithms.py по AST: displayName даёт код и название,
shortHelpString - справку, initAlgorithm - поля. Поля описываются
видами контракта toolspec. Выход-файл (HTML) в контракт не входит и
пропускается: в RustGIS цифры отчёта идут в report.

Подписи - русские литералы self.tr(...), английские берутся из
i18n.TRANSLATIONS. Подсказки - строки setHelp(_trh(...)) у переменной
параметра, если они есть.
"""

import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "grid_isolines", "algorithms.py")

_TREE = None


class SpecError(Exception):
    pass


def _tree():
    global _TREE
    if _TREE is None:
        with open(SRC, encoding="utf-8") as fh:
            _TREE = ast.parse(fh.read())
    return _TREE


def _module_consts():
    out = {}
    for n in _tree().body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = n.value.value
    return out


def class_node(name):
    for n in _tree().body:
        if isinstance(n, ast.ClassDef) and n.name == name:
            return n
    raise SpecError("class %s not found" % name)


def _class_consts(cls):
    out = {}
    for st in cls.body:
        if not isinstance(st, ast.Assign):
            continue
        for t in st.targets:
            if isinstance(t, ast.Name) and isinstance(st.value, ast.Constant):
                out[t.id] = st.value.value
            elif isinstance(t, ast.Tuple) and isinstance(st.value, ast.Tuple):
                for a, b in zip(t.elts, st.value.elts):
                    if isinstance(a, ast.Name) and isinstance(b, ast.Constant):
                        out[a.id] = b.value
    return out


def _method(cls, name):
    for f in cls.body:
        if isinstance(f, ast.FunctionDef) and f.name == name:
            return f
    return None


def _text(node):
    """Строка из литерала или вызова перевода self.tr("..."), _trh("...")."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and node.args:
        f = node.func
        nm = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
        if nm in ("tr", "_tr", "_trh", "tr_help"):
            return _text(node.args[0])
    return None


def _kw(call, name):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def _value(node, consts):
    """Значение по умолчанию: литерал, _dv(self, KEY, fallback), список."""
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _value(node.operand, consts)
        return -v if isinstance(v, (int, float)) else None
    if isinstance(node, ast.Call):
        f = node.func
        nm = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
        if nm in ("_dv", "_dv_layer") and len(node.args) >= 3:
            return _value(node.args[2], consts)
        if nm in ("_dv_layer",):
            return None
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_value(e, consts) for e in node.elts]
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id]
    return None


def _attr_tail(node):
    """Последнее имя в цепочке атрибутов: ...Type.Double -> Double."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


_SOURCE_KIND = {"TypeVectorPoint": "points", "TypeVectorLine": "lines",
                "TypeVectorPolygon": "features",
                "TypeVectorAnyGeometry": "features", "TypeVector": "table"}
_SINK_GEOM = {"TypeVectorPoint": "point", "TypeVectorLine": "line",
              "TypeVectorPolygon": "source", "TypeVector": "none"}


def _helps(init):
    """Подсказки: переменная параметра -> текст setHelp."""
    out = {}
    for node in ast.walk(init):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setHelp" and node.args
                and isinstance(node.func.value, ast.Name)):
            t = _text(node.args[0])
            if t:
                out[node.func.value.id] = t
    return out


def params(cls_name, overrides=None):
    """Поля инструмента в виде словарей Param.to_dict (русские строки).

    overrides - {ключ: {поле: значение}}: то, чего из класса не видно
    (например, вид слоя, когда класс принимает любую геометрию)."""
    cls = class_node(cls_name)
    consts = dict(_module_consts())
    consts.update(_class_consts(cls))
    init = _method(cls, "initAlgorithm")
    if init is None:
        raise SpecError("%s has no initAlgorithm" % cls_name)
    helps = _helps(init)
    var_of = {}
    for node in ast.walk(init):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    var_of[id(node.value)] = t.id
    out = []
    calls = [node for node in ast.walk(init)
             if isinstance(node, ast.Call) and isinstance(node.func,
                                                          ast.Attribute)
             and node.func.attr == "addParameter"]
    calls.sort(key=lambda n: (n.lineno, n.col_offset))
    for node in calls:
        arg = node.args[0]
        advanced = False
        if isinstance(arg, ast.Call) and getattr(arg.func, "id", "") == \
                "_advanced":
            advanced = True
            arg = arg.args[0]
        if isinstance(arg, ast.Name):
            # параметр заведён в переменную раньше
            for n2 in ast.walk(init):
                if (isinstance(n2, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == arg.id
                        for t in n2.targets)):
                    var = arg.id
                    arg = n2.value
                    if isinstance(arg, ast.Call) and getattr(
                            arg.func, "id", "") == "_advanced":
                        advanced = True
                        arg = arg.args[0]
                    break
            else:
                raise SpecError("%s: parameter %s not resolved"
                                % (cls_name, arg.id))
        else:
            var = var_of.get(id(arg))
        out.append(_param(cls_name, arg, consts, advanced,
                          helps.get(var, "") if var else ""))
    out = [p for p in out if p is not None]
    if overrides:
        for p in out:
            p.update(overrides.get(p["key"], {}))
    for p in out:
        if p["kind"] == "_extent":
            raise SpecError("%s: field %s is an extent, give an override"
                            % (cls_name, p["key"]))
    return out


def _param(cls_name, call, consts, advanced, hint):
    f = call.func
    qtype = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
    kind_name = qtype.replace("QgsProcessingParameter", "")
    a = call.args
    keynode = a[0]
    if isinstance(keynode, ast.Attribute):
        key = consts.get(keynode.attr, keynode.attr)
    else:
        key = _value(keynode, consts)
    label = _text(a[1]) if len(a) > 1 else _text(_kw(call, "description"))
    if label is None:
        raise SpecError("%s.%s: label is not a literal" % (cls_name, key))
    optional = _value(_kw(call, "optional"), consts) is True
    p = {"key": key, "label": label, "hint": hint}
    if advanced:
        p["advanced"] = True
    if kind_name == "FeatureSource":
        types = a[2] if len(a) > 2 else _kw(call, "types")
        tails = [_attr_tail(e) for e in getattr(types, "elts", [])]
        kinds = set(_SOURCE_KIND.get(t, "features") for t in tails) or \
            {"table"}
        p["kind"] = kinds.pop() if len(kinds) == 1 else "features"
    elif kind_name == "Field":
        p["kind"] = "field"
        parent = _kw(call, "parentLayerParameterName")
        if parent is None and len(a) > 3:
            parent = a[3]
        if isinstance(parent, ast.Attribute):
            p["parent"] = consts.get(parent.attr, parent.attr)
        if _attr_tail(_kw(call, "type")) == "Numeric":
            p["numeric"] = True
        if _value(_kw(call, "allowMultiple"), consts) is True:
            p["multiple"] = True
        d = _value(_kw(call, "defaultValue"), consts)
        p["default"] = d if d is not None else ""
    elif kind_name == "Enum":
        p["kind"] = "enum"
        opts = _kw(call, "options")
        if opts is None and len(a) > 2:
            opts = a[2]
        p["options"] = [_text(e) for e in getattr(opts, "elts", [])]
        if None in p["options"]:
            raise SpecError("%s.%s: option is not a literal"
                            % (cls_name, key))
        p["default"] = _value(_kw(call, "defaultValue"), consts)
        if _value(_kw(call, "allowMultiple"), consts) is True:
            p["multiple"] = True
    elif kind_name == "Number":
        t = _attr_tail(a[2]) if len(a) > 2 else _attr_tail(_kw(call, "type"))
        p["kind"] = "integer" if t == "Integer" else "number"
        p["default"] = _value(_kw(call, "defaultValue"), consts)
        for qn, nm in (("minValue", "minimum"), ("maxValue", "maximum")):
            v = _value(_kw(call, qn), consts)
            if v is not None:
                p[nm] = v
    elif kind_name == "Boolean":
        p["kind"] = "bool"
        p["default"] = bool(_value(_kw(call, "defaultValue"), consts))
    elif kind_name == "String":
        p["kind"] = "string"
        d = _value(_kw(call, "defaultValue"), consts)
        p["default"] = d if d is not None else ""
    elif kind_name == "FeatureSink":
        t = _attr_tail(a[2]) if len(a) > 2 else _attr_tail(_kw(call, "type"))
        p["kind"] = "sink"
        p["geometry"] = _SINK_GEOM.get(t, "none")
    elif kind_name == "Extent":
        p["kind"] = "_extent"
    elif kind_name == "FileDestination":
        return None
    else:
        raise SpecError("%s.%s: %s is not in the toolspec contract"
                        % (cls_name, key, qtype))
    if optional:
        p["optional"] = True
    return p


def title(cls_name):
    """(код, название) из displayName."""
    m = _method(class_node(cls_name), "displayName")
    for node in ast.walk(m):
        t = _text(node)
        if t and t[:1].isdigit() and " " in t:
            code, rest = t.split(" ", 1)
            return code, rest
    raise SpecError("%s: displayName" % cls_name)


def help_text(cls_name):
    m = _method(class_node(cls_name), "shortHelpString")
    for node in ast.walk(m):
        if isinstance(node, ast.Call):
            f = node.func
            nm = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if nm in ("_trh", "tr_help", "tr", "_tr"):
                t = _text(node)
                if t:
                    return t
    return ""


def class_dict(cls_name, attr):
    """Словарь-литерал класса (FIELD_LABELS, FIELD_DECIMALS) или {}."""
    for st in class_node(cls_name).body:
        if isinstance(st, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == attr for t in st.targets):
            return ast.literal_eval(st.value)
    return {}


def tool_name(cls_name):
    m = _method(class_node(cls_name), "name")
    for node in ast.walk(m):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
    raise SpecError("%s: name()" % cls_name)
