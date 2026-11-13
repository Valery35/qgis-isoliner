# -*- coding: utf-8 -*-
"""Описание инструмента для tools.json из класса QGIS, без QGIS.

Разбор algorithms.py по AST: displayName даёт код и название,
shortHelpString - справку, initAlgorithm - поля. Поля описываются
видами контракта toolspec. Выход-файл (HTML) в контракт не входит и
пропускается: в RustGIS цифры отчёта идут в report.

initAlgorithm обходится по операторам в порядке текста. Понимаются
параметр прямо в addParameter, параметр в переменной (p = ...;
_advanced(p); self.addParameter(p)), обёртка _advanced(...) и цикл по
кортежу литералов (for key, label, dv in ((...), (...)):). Подписи -
русские литералы self.tr(...), английские берутся из i18n.TRANSLATIONS.
Подсказки - строки setHelp(_trh(...)) у переменной параметра.

То, чего из класса не видно или что в RustGIS иначе (охват), задаёт
модуль расчёта словарём SPEC_OVERRIDES.
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
    out = {"__nodes__": {}}
    for st in cls.body:
        if not isinstance(st, ast.Assign):
            continue
        for t in st.targets:
            if isinstance(t, ast.Name):
                # списки подписей класса: FIT_LABELS = [_tr("..."), ...]
                out["__nodes__"][t.id] = st.value
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


def _fname(call):
    f = call.func
    return f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")


def _text(node, env=None):
    """Строка из литерала или вызова перевода self.tr("..."), _trh("...").

    env - имена переменных цикла и их узлы: for key, label in (...)."""
    if isinstance(node, ast.Name) and env and node.id in env:
        return _text(env[node.id], env)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and node.args:
        if _fname(node) in ("tr", "_tr", "_trh", "tr_help"):
            return _text(node.args[0], env)
    return None


def _kw(call, name):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def _value(node, consts, env=None):
    """Значение: литерал, _dv(self, KEY, fallback), список, константа."""
    if node is None:
        return None
    if isinstance(node, ast.Name) and env and node.id in env:
        return _value(env[node.id], consts, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _value(node.operand, consts, env)
        return -v if isinstance(v, (int, float)) else None
    if isinstance(node, ast.Call):
        if _fname(node) == "_dv" and len(node.args) >= 3:
            return _value(node.args[2], consts, env)
        return None
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_value(e, consts, env) for e in node.elts]
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
            and node.value.id == "self" and node.attr in consts:
        return consts[node.attr]
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
              "TypeVectorPolygon": "source", "TypeVectorAnyGeometry": "source",
              "TypeVector": "none"}


class _State(object):
    def __init__(self):
        self.vars = {}      # имя -> (узел вызова, env)
        self.adv = set()    # переменные, обёрнутые _advanced(имя)
        self.helps = {}     # переменная -> подсказка
        self.found = []     # (узел вызова, env, advanced, подсказка)


def _strip_adv(node):
    if isinstance(node, ast.Call) and _fname(node) == "_advanced" \
            and node.args:
        return node.args[0], True
    return node, False


def _walk(stmts, env, st):
    for s in stmts:
        if isinstance(s, ast.For) and isinstance(s.iter, (ast.Tuple,
                                                          ast.List)):
            names = s.target.elts if isinstance(s.target, ast.Tuple) \
                else [s.target]
            for elt in s.iter.elts:
                vals = elt.elts if isinstance(elt, ast.Tuple) else [elt]
                e2 = dict(env)
                for n, v in zip(names, vals):
                    if isinstance(n, ast.Name):
                        e2[n.id] = v
                _walk(s.body, e2, st)
            continue
        if isinstance(s, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
            for part in ("body", "orelse", "finalbody"):
                _walk(getattr(s, part, []) or [], env, st)
            for h in getattr(s, "handlers", []) or []:
                _walk(h.body, env, st)
            continue
        if isinstance(s, ast.Assign) and isinstance(s.value, ast.Call):
            for t in s.targets:
                if isinstance(t, ast.Name):
                    node, adv = _strip_adv(s.value)
                    st.vars[t.id] = (node, dict(env))
                    st.adv.discard(t.id)
                    if adv:
                        st.adv.add(t.id)
            continue
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Call)):
            continue
        call = s.value
        name = _fname(call)
        if name == "_advanced" and call.args and isinstance(call.args[0],
                                                             ast.Name):
            st.adv.add(call.args[0].id)
        elif name == "setHelp" and isinstance(call.func, ast.Attribute) \
                and isinstance(call.func.value, ast.Name) and call.args:
            t = _text(call.args[0], env)
            var = call.func.value.id
            if t and var in st.vars:
                # переменную p заводят заново под каждый параметр, поэтому
                # подсказка привязывается к узлу параметра, а не к имени
                st.helps[id(st.vars[var][0])] = t
        elif name == "addParameter" and call.args:
            arg, adv = _strip_adv(call.args[0])
            if isinstance(arg, ast.Name):
                if arg.id not in st.vars:
                    raise SpecError("parameter variable %s not resolved"
                                    % arg.id)
                node, e = st.vars[arg.id]
                st.found.append((node, e, adv or arg.id in st.adv,
                                 arg.id))
            else:
                st.found.append((arg, dict(env), adv, None))


def params(cls_name, overrides=None, strict=True):
    """Поля инструмента в виде словарей Param.to_dict (русские строки).

    overrides - {ключ: {поле: значение}}: то, чего из класса не видно,
    или что в RustGIS устроено иначе (охват - слоем)."""
    cls = class_node(cls_name)
    consts = dict(_module_consts())
    consts.update(_class_consts(cls))
    init = _method(cls, "initAlgorithm")
    if init is None:
        raise SpecError("%s has no initAlgorithm" % cls_name)
    st = _State()
    _walk(init.body, {}, st)
    out = []
    for node, env, adv, var in st.found:
        # подсказка ставится после addParameter, поэтому берётся в конце
        p = _param(cls_name, node, consts, env, adv,
                   st.helps.get(id(node), ""))
        if p is not None:
            out.append(p)
    if overrides:
        # {"_drop": True} убирает поле из описания: то, что есть только в
        # окне QGIS (наложение модели на HTML, правка чужой таблицы)
        out = [p for p in out if not overrides.get(p["key"], {}).get("_drop")]
        for p in out:
            p.update(overrides.get(p["key"], {}))
    for p in out:
        if strict and p["kind"] == "_extent":
            raise SpecError("%s: field %s is an extent, give an override"
                            % (cls_name, p["key"]))
    return out


def _param(cls_name, call, consts, env, advanced, hint):
    qtype = _fname(call)
    kind_name = qtype.replace("QgsProcessingParameter", "")
    a = call.args
    keynode = a[0]
    if isinstance(keynode, ast.Attribute):
        key = consts.get(keynode.attr, keynode.attr)
    else:
        key = _value(keynode, consts, env)
    label = _text(a[1], env) if len(a) > 1 else \
        _text(_kw(call, "description"), env)
    if label is None:
        raise SpecError("%s.%s: label is not a literal" % (cls_name, key))
    optional = _value(_kw(call, "optional"), consts, env) is True
    p = {"key": key, "label": label, "hint": hint}
    if advanced:
        p["advanced"] = True

    def kw(name):
        return _value(_kw(call, name), consts, env)

    if kind_name == "FeatureSource":
        types = a[2] if len(a) > 2 else _kw(call, "types")
        tails = [_attr_tail(e) for e in getattr(types, "elts", [])]
        kinds = set(_SOURCE_KIND.get(t, "features") for t in tails) or \
            {"table"}
        p["kind"] = kinds.pop() if len(kinds) == 1 else "features"
    elif kind_name == "VectorLayer":
        p["kind"] = "table"
    elif kind_name == "Field":
        p["kind"] = "field"
        parent = _kw(call, "parentLayerParameterName")
        if parent is None and len(a) > 3:
            parent = a[3]
        if isinstance(parent, ast.Attribute):
            p["parent"] = consts.get(parent.attr, parent.attr)
        if _attr_tail(_kw(call, "type")) == "Numeric":
            p["numeric"] = True
        if kw("allowMultiple") is True:
            p["multiple"] = True
        d = kw("defaultValue")
        p["default"] = d if d is not None else ("" if not p.get("multiple")
                                                else [])
    elif kind_name == "Enum":
        p["kind"] = "enum"
        opts = _kw(call, "options")
        if opts is None and len(a) > 2:
            opts = a[2]
        if isinstance(opts, ast.ListComp):
            # options=[_tr(x) for x in self.FIT_LABELS]: список класса
            it = opts.generators[0].iter
            name = it.attr if isinstance(it, ast.Attribute) else \
                getattr(it, "id", None)
            opts = consts.get("__nodes__", {}).get(name)
        p["options"] = [_text(e, env) for e in getattr(opts, "elts", [])]
        if None in p["options"] or not p["options"]:
            raise SpecError("%s.%s: option is not a literal"
                            % (cls_name, key))
        p["default"] = kw("defaultValue")
        if kw("allowMultiple") is True:
            p["multiple"] = True
    elif kind_name == "Number":
        t = _attr_tail(a[2]) if len(a) > 2 else _attr_tail(_kw(call, "type"))
        p["kind"] = "integer" if t == "Integer" else "number"
        p["default"] = kw("defaultValue")
        for qn, nm in (("minValue", "minimum"), ("maxValue", "maximum")):
            v = kw(qn)
            if v is not None:
                p[nm] = v
    elif kind_name == "Boolean":
        p["kind"] = "bool"
        p["default"] = bool(kw("defaultValue"))
    elif kind_name == "String":
        p["kind"] = "string"
        d = kw("defaultValue")
        p["default"] = d if d is not None else ""
    elif kind_name == "FeatureSink":
        t = _attr_tail(a[2]) if len(a) > 2 else _attr_tail(_kw(call, "type"))
        p["kind"] = "sink"
        p["geometry"] = _SINK_GEOM.get(t, "none")
    elif kind_name == "Extent":
        p["kind"] = "_extent"
    elif kind_name in ("FileDestination", "FolderDestination"):
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
        if isinstance(node, ast.Call) and _fname(node) in (
                "_trh", "tr_help", "tr", "_tr"):
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
