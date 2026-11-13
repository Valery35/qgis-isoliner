# -*- coding: utf-8 -*-
"""Сверка прежнего и нового класса инструмента в QGIS без окна.

Прежний пакет - снимок grid_isolines из git (git archive HEAD), он
подключается под именем grid_isolines_old. Новый - рабочее дерево.
Профиль QGIS временный, настройки автора не трогаются.

Запуск из python-qgis.bat: python tools/qgis_check/run_XXX.py <папка
снимка, в которой лежит grid_isolines_old>
"""

import json
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

_APP = None


def start(old_parent):
    """Поднять QGIS без окна и вернуть (old, new) модули algorithms."""
    global _APP
    from qgis.core import QgsApplication
    from qgis.PyQt.QtCore import QCoreApplication
    QCoreApplication.setOrganizationName("isoliner-check")
    QCoreApplication.setApplicationName("isoliner-check")
    prof = tempfile.mkdtemp(prefix="isoliner_check_profile_")
    _APP = QgsApplication([], False, prof)
    _APP.initQgis()
    sys.path.insert(0, ROOT)
    sys.path.insert(0, old_parent)
    from grid_isolines import i18n as i18n_new
    from grid_isolines_old import i18n as i18n_old
    i18n_new.set_language("ru", "ru")
    i18n_old.set_language("ru", "ru")
    import grid_isolines.algorithms as new
    import grid_isolines_old.algorithms as old
    return old, new


def _feedback():
    from qgis.core import QgsProcessingFeedback

    class FB(QgsProcessingFeedback):
        def __init__(self):
            QgsProcessingFeedback.__init__(self)
            self.lines = []

        def pushInfo(self, s):
            self.lines.append(["info", s])

        def pushWarning(self, s):
            self.lines.append(["warn", s])

        def reportError(self, s, fatal=False):
            self.lines.append(["error", s])

        def pushDebugInfo(self, s):
            pass

        def pushCommandInfo(self, s):
            self.lines.append(["cmd", s])

        def pushConsoleInfo(self, s):
            pass
    return FB()


def _val(v):
    if v is None:
        return None
    try:
        if v.isNull():
            return None
    except AttributeError:
        pass
    if isinstance(v, float) and not math.isfinite(v):
        return repr(v)
    return v


def run(mod, cls_name, params, sinks, files=()):
    """Прогон класса cls_name модуля mod. sinks - ключи выходов-слоёв,
    files - ключи выходов-файлов (HTML), их текст тоже сравнивается.

    Возвращает {"ok", "journal", "outputs": {ключ: {"fields", "rows"}}}."""
    from qgis.core import (QgsProcessingContext, QgsProject,
                           QgsProcessingUtils, QgsSettings)
    # память прошлых значений (_dv, _save_values) не переносится между
    # случаями: профиль временный, чистится перед каждым прогоном
    QgsSettings().clear()
    alg = getattr(mod, cls_name)()
    alg.initAlgorithm({})
    ctx = QgsProcessingContext()
    ctx.setProject(QgsProject.instance())
    fb = _feedback()
    p = dict(params)
    for k in sinks:
        p.setdefault(k, "TEMPORARY_OUTPUT")
    tmp = tempfile.mkdtemp(prefix="isoliner_check_files_")
    for k in files:
        p[k] = os.path.join(tmp, k + ".html")
    res, ok = alg.run(p, ctx, fb)
    outs = {}
    import re
    for k in files:
        path = p[k]
        text = open(path, encoding="utf-8").read() \
            if os.path.exists(path) else None
        if text is not None:
            # plotly даёт каждому графику случайный id: его не сравниваем
            text = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
                          r"[0-9a-f]{12}", "UUID", text)
            # снимок и дерево расходятся номером версии в подвале отчёта
            text = re.sub(r"Isoliner v\d+\.\d+\.\d+", "Isoliner vX", text)
        outs[k] = text
    for k in sinks:
        dest = (res or {}).get(k)
        lyr = QgsProcessingUtils.mapLayerFromString(dest, ctx) \
            if isinstance(dest, str) else None
        if lyr is None:
            outs[k] = None
            continue
        rows = []
        for f in lyr.getFeatures():
            g = f.geometry()
            rows.append([g.asWkt(6) if g is not None else None]
                        + [_val(a) for a in f.attributes()])
        outs[k] = {"fields": [x.name() for x in lyr.fields()],
                   "rows": rows}
    # строку версии не сравниваем: снимок и дерево могут разниться номером
    journal = [ln for ln in fb.lines if not ln[1].startswith("Isoliner ")]
    return {"ok": bool(ok), "journal": journal, "outputs": outs}


def memory_layer(geom, crs, fields, rows, name="pts"):
    """Слой в памяти: fields - [(имя, тип QVariant)], rows - [(wkt, attrs)]."""
    from qgis.core import (QgsVectorLayer, QgsFeature, QgsGeometry,
                           QgsField, QgsProject)
    lyr = QgsVectorLayer("%s?crs=%s" % (geom, crs), name, "memory")
    pr = lyr.dataProvider()
    pr.addAttributes([QgsField(n, t) for n, t in fields])
    lyr.updateFields()
    feats = []
    for wkt, attrs in rows:
        f = QgsFeature(lyr.fields())
        if wkt:
            f.setGeometry(QgsGeometry.fromWkt(wkt))
        f.setAttributes(list(attrs))
        feats.append(f)
    pr.addFeatures(feats)
    QgsProject.instance().addMapLayer(lyr)
    return lyr


def compare(cases, old, new, cls_name, sinks, out_path, files=()):
    """Прогнать случаи обоими классами и сравнить. Печатает итог."""
    report = []
    bad = 0
    for title, params in cases:
        a = run(old, cls_name, params, sinks, files)
        b = run(new, cls_name, params, sinks, files)
        same = json.dumps(a, sort_keys=True, default=str) == \
            json.dumps(b, sort_keys=True, default=str)
        nrows = sum(len(o["rows"]) for o in b["outputs"].values()
                    if isinstance(o, dict))
        report.append({"case": title, "same": same, "ok": b["ok"],
                       "rows": nrows, "journal_lines": len(b["journal"]),
                       "old": None if same else a,
                       "new": None if same else b})
        bad += 0 if same else 1
        print("%-48s %s  ok=%s rows=%d journal=%d"
              % (title, "SAME" if same else "DIFF", b["ok"], nrows,
                 len(b["journal"])))
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, default=str)
    print("cases %d, differences %d" % (len(cases), bad))
    return bad
