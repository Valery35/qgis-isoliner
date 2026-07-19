# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Проверка файлов стилей QML:
#     python grid_isolines/tests/test_styles.py
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
STYLES = os.path.join(os.path.dirname(HERE), "styles")

# Поля, которые пишут в слои наши инструменты. Ссылка на такое имя через
# @ означает переменную, а не поле, и молча даёт NULL.
FIELDS = ("dn_sign", "is_index", "up_left", "lowconf", "drop_min", "drop_mean",
          "ELEV", "ELEV_MIN", "ELEV_MAX", "hold", "resid", "ccolor", "hole_id")


def test_dh_intervals_color_from_ccolor_field():
    """Цвет интервала обязан читаться из поля ccolor, которое пишет 4.02.

    На живой машине выяснилось, что одного ключа мало: у SimpleLine
    статический цвет зовётся line_color, а имя data-defined ключа между
    сборками надёжно не подтвердилось. Поэтому в стиле стоят ОБА ключа
    (outline_color и line_color) с одним выражением: лишний игнорируется,
    нужный красит. Логики в QML нет, только чтение поля - готовый hex
    пишет инструмент.
    """
    p = os.path.join(STYLES, "dh_intervals.qml")
    tree = ET.parse(p)
    found = set()
    for opt in tree.iter("Option"):
        if opt.get("name") in ("outline_color", "line_color") \
                and opt.get("type") == "Map":
            kids = {o.get("name"): o.get("value") for o in opt}
            expr = kids.get("expression") or ""
            if "ccolor" in expr:
                assert kids.get("active") == "true", kids
                assert "@ccolor" not in expr, expr
                found.add(opt.get("name"))
    assert found == {"outline_color", "line_color"}, found


def test_dh_intervals_black_underlay_is_locked():
    """Чёрный контур колонки это нижний слой символа под замком.

    Категоризатор красит клон символа через setColor, а setColor не трогает
    слои с locked="1". Поэтому подложка обязана быть под замком, иначе она
    перекрасится в цвет кода и контур исчезнет.
    """
    p = os.path.join(STYLES, "dh_intervals.qml")
    tree = ET.parse(p)
    layers = list(tree.getroot().iter("layer"))
    assert len(layers) == 2, "ожидались подложка и цветная линия"
    under, top = layers[0], layers[1]
    assert under.get("locked") == "1", "подложка должна быть под замком"
    assert top.get("locked") == "0", "цветная линия должна перекрашиваться"
    uopts = {o.get("name"): o.get("value") for o in under.iter("Option")}
    topts = {o.get("name"): o.get("value") for o in top.iter("Option")}
    assert uopts.get("line_color") == "0,0,0,255"
    assert float(uopts.get("line_width")) > float(topts.get("line_width"))


def test_dh_bands_black_outline_survives_recolor():
    """Заливка полосы перекрашивается категоризатором, контур остаётся
    чёрным: у SimpleFill setColor меняет только цвет заливки."""
    p = os.path.join(STYLES, "dh_bands.qml")
    tree = ET.parse(p)
    opts = {o.get("name"): o.get("value")
            for o in tree.getroot().iter("Option")}
    assert opts.get("outline_color") == "0,0,0,255"
    assert opts.get("outline_style") == "solid"


def _qml_files():
    return [os.path.join(STYLES, f) for f in sorted(os.listdir(STYLES))
            if f.endswith(".qml")]


def test_all_styles_are_valid_xml():
    for p in _qml_files():
        ET.parse(p)


def test_no_field_is_referenced_as_variable():
    """Главный урок дня.

    В QGIS «собака» перед именем означает переменную, а не поле слоя.
    Выражение @dn_sign молча давало NULL, смещение падало на постоянное
    запасное значение, и бергштрихи всегда смотрели в одну сторону. Хуже
    того, переключение знака в коде не меняло ничего, потому что поле не
    участвовало вовсе.
    """
    bad = []
    for p in _qml_files():
        with open(p, encoding="utf-8") as fh:
            src = fh.read()
        for f in FIELDS:
            if re.search(r"@%s\b" % re.escape(f), src):
                bad.append("%s: @%s" % (os.path.basename(p), f))
    assert not bad, "поля указаны как переменные: %s" % ", ".join(bad)


def test_depression_style_uses_dn_sign_field():
    p = os.path.join(STYLES, "iso_depression.qml")
    with open(p, encoding="utf-8") as fh:
        src = fh.read()
    assert "dn_sign" in src, "стиль депрессии обязан опираться на dn_sign"
    m = re.search(r'name="expression" type="QString" value="([^"]*dn_sign[^"]*)"',
                  src)
    assert m, "выражение со стороной склона не найдено"
    expr = m.group(1)
    assert "@dn_sign" not in expr, expr
    assert "dn_sign" in expr


def test_data_defined_offset_is_active():
    """Смещение должно быть включено, иначе поле снова ни на что не влияет."""
    p = os.path.join(STYLES, "iso_depression.qml")
    tree = ET.parse(p)
    found = False
    for opt in tree.iter("Option"):
        if opt.get("name") == "offset" and opt.get("type") == "Map":
            kids = {o.get("name"): o.get("value") for o in opt}
            if "expression" in kids and "dn_sign" in (kids["expression"] or ""):
                assert kids.get("active") == "true", kids
                found = True
    assert found, "не найдено включённое смещение по dn_sign"


def test_contour_styles_allow_upside_down_labels():
    """Без этого топографические подписи не работают в принципе.

    Найдено на живой машине: разворот линий не давал никакого эффекта,
    потому что в настройках подписей стояло «никогда не показывать
    перевёрнутые подписи», и QGIS сам доворачивал текст ради читаемости.
    Топографическая подпись по определению бывает перевёрнутой: на склоне,
    обращённом на юг, цифра читается вверх ногами. Значение 2 означает
    «показывать всегда».
    """
    for name in ("iso_structure.qml", "iso_depression.qml"):
        tree = ET.parse(os.path.join(STYLES, name))
        vals = [e.get("upsidedownLabels") for e in tree.getroot().iter("rendering")]
        assert vals, "%s: настройки отрисовки подписей не найдены" % name
        assert all(v == "2" for v in vals), "%s: %s" % (name, vals)


def test_both_contour_styles_carry_labeling():
    """Иначе флажок топографических подписей молчит на одном из стилей."""
    for name in ("iso_structure.qml", "iso_depression.qml"):
        root = ET.parse(os.path.join(STYLES, name)).getroot()
        cats = root.get("styleCategories") or ""
        assert "Labeling" in cats, "%s: %s" % (name, cats)
        assert list(root.iter("labeling")), "%s: нет блока подписей" % name


def test_label_expressions_use_qgis_concatenation():
    """Оператор склейки строк в QGIS это ||, а не &.

    Выражение с & не разбирается вовсе: подпись подсвечивается красным и не
    рисуется. Держалось незамеченным, потому что у депрессионного стиля
    подписей не было, а на карте работали настройки слоя пользователя.
    """
    bad = []
    for p in _qml_files():
        root = ET.parse(p).getroot()
        for e in root.iter("text-style"):
            if e.get("isExpression") != "1":
                continue
            expr = e.get("fieldName") or ""
            if "&" in expr.replace("&&", ""):
                bad.append("%s: %s" % (os.path.basename(p), expr))
    assert not bad, "недопустимый оператор склейки: %s" % "; ".join(bad)


def test_contour_label_expression_is_parseable_shape():
    """Грубая проверка формы выражения: поле в кавычках и склейка через ||."""
    for name in ("iso_structure.qml", "iso_depression.qml"):
        root = ET.parse(os.path.join(STYLES, name)).getroot()
        exprs = [e.get("fieldName") for e in root.iter("text-style")
                 if e.get("isExpression") == "1"]
        assert exprs, name
        for x in exprs:
            assert '"ELEV"' in x, (name, x)
            # Приставки единиц в подписи изолиний нет намеренно: на топокарте
            # у горизонтали подписывают только отметку.
            assert "м" not in x, (name, x)


def test_contour_labels_sit_on_the_line():
    """Подпись горизонтали ставится НА линию, как на топокарте.

    Флаг размещения 1 означает «на линии», 2 «над линией». Над линией
    подпись отрывается от горизонтали и читается хуже, а разрыв линии под
    подписью и есть привычный топографический приём.
    """
    for name in ("iso_structure.qml", "iso_depression.qml"):
        root = ET.parse(os.path.join(STYLES, name)).getroot()
        flags = [e.get("placementFlags") for e in root.iter("placement")]
        assert flags, name
        assert all(f == "1" for f in flags), "%s: %s" % (name, flags)


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print("FAIL %s: %s" % (name, exc))
    print("%d тестов, ошибок %d" % (len(fns), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run())
