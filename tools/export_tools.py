#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Описания инструментов-объектов Isoliner в tools.json.

Формат тот же, что у Isoliner3D (контракт grid_isolines/toolspec.py,
формат 1): его читает общий крейт на Rust. Описание строится из класса
QGIS разбором algorithms.py (tools/classparams.py), а не пишется второй
раз, поэтому подписи в tools.json те же, что в окне. Список
инструментов - grid_isolines/tools/__init__.py, RUNS.

Запуск: python tools/export_tools.py [путь]   (без пути - в stdout)
"""

import ast
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import classparams  # noqa: E402

FORMAT = 1


def _version():
    path = os.path.join(ROOT, "grid_isolines", "metadata.txt")
    for line in open(path, encoding="utf-8"):
        if line.startswith("version="):
            return line.split("=", 1)[1].strip()
    return "?"


def _both(text, table):
    """Строка на двух языках: русский оригинал и английский перевод."""
    return {"ru": text, "en": table.get(text, text)}


def _n_strings(module):
    """Строки N_() модуля расчёта и модулей пакета tools, которые он берёт."""
    out = []
    seen = set()
    todo = [module]
    while todo:
        mod = todo.pop(0)
        if mod.__name__ in seen:
            continue
        seen.add(mod.__name__)
        tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") == "N_" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                if node.args[0].value not in out:
                    out.append(node.args[0].value)
            if isinstance(node, ast.ImportFrom) and node.level == 1 \
                    and node.module and node.module != "toolspec" \
                    and not node.module.startswith("."):
                pkg = mod.__name__.rsplit(".", 1)[0]
                if pkg.endswith(".tools"):
                    try:
                        todo.append(importlib.import_module(
                            pkg + "." + node.module))
                    except ImportError:
                        pass
    return out


def spec(tool_name):
    """Tool контракта для инструмента RUNS: описание из класса и run."""
    from grid_isolines import toolspec
    from grid_isolines.tools import RUNS
    for name, mod_name, cls_name in RUNS:
        if name == tool_name:
            break
    else:
        raise KeyError(tool_name)
    mod = importlib.import_module("grid_isolines.tools." + mod_name)
    code, title = classparams.title(cls_name)
    if classparams.tool_name(cls_name) != name:
        raise classparams.SpecError("%s: name() is not %s"
                                    % (cls_name, name))
    params = [toolspec.Param(**p) for p in classparams.params(
        cls_name, getattr(mod, "SPEC_OVERRIDES", None))]
    return toolspec.Tool(code, name, title, code.split(".")[0],
                         classparams.help_text(cls_name), params, mod.run)


def build():
    """Описание всех инструментов-объектов словарём для JSON."""
    from grid_isolines.i18n import TRANSLATIONS as tr
    from grid_isolines.tools import RUNS
    tools = []
    for name, mod_name, _cls in RUNS:
        t = spec(name)
        d = t.to_dict()
        # перевод заведён на название с кодом, как его показывает панель
        full = tr.get(t.display_name(), t.display_name())
        d["title"] = {"ru": t.title,
                      "en": full.split(" ", 1)[1] if full.startswith(
                          t.code + " ") else tr.get(t.title, t.title)}
        d["help"] = _both(t.help, tr)
        for p, pd in zip(t.params, d["params"]):
            pd["label"] = _both(p.label, tr)
            pd["hint"] = _both(p.hint, tr)
            if p.options:
                pd["options"] = [_both(o, tr) for o in p.options]
        spec_texts = {t.title, t.help}
        for p in t.params:
            spec_texts |= {p.label, p.hint} | set(p.options)
        mod = importlib.import_module("grid_isolines.tools." + mod_name)
        d["messages"] = [_both(m, tr) for m in _n_strings(mod)
                         if m not in spec_texts]
        tools.append(d)
    return {"format": FORMAT, "module": "isoliner",
            "version": _version(), "tools": tools}


def main(argv):
    data = build()
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    if len(argv) > 1:
        with open(argv[1], "w", encoding="utf-8") as fh:
            fh.write(text)
        sys.stderr.write("tools: %d -> %s\n" % (len(data["tools"]), argv[1]))
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main(sys.argv)
