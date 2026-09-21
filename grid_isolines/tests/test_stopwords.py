# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Стоп-слова публикуемых текстов.

Правило принято для всего, что уходит наружу: справки инструментов,
описание и changelog в metadata.txt, стили, шаблоны. Проверка в
tests/test_i18n.py смотрела только модули пакета и жила под
``if __name__ == "__main__"``, поэтому pytest её не собирал вовсе, а
changelog не попадал в неё и по составу файлов. Так в записи 4.85.0
оказалось «врёт», а в 4.86.0 - «честно».

Тесты пакета сюда не входят: это рабочие тексты для своих, правило про
публикуемые. Тире ищется только в русском тексте по соседству с
кириллицей, чтобы не задевать таблицы и разметку.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)

# Слово: почему запрещено
STOP_WORDS = {
    r"честн[а-яё]+": "«честный» - затычка вместо сути, писать «корректнее»",
    r"врёт|врут|врал[а-яё]*": "«врёт» - о программе так не пишем",
    r"наблюдённ[а-яё]+": "принято «наблюдаемые»",
    r"\bмерк(а|и|е|у|ой|ам|ами|ах)?\b": "«мерка» - бытовое слово, писать «измеритель» или «измерение»",
    r"членени[а-яё]*": "принято «участки», «разбивка на участки»",
    r"\bлаг(а|у|ом|е|и|ов|ам|ами|ах)?\b": "«лаг» - писать «расстояние» (между точками пары) "
                                          "или «шаг» (параметр инструмента)",
    r"\bсофт[а-яё]*": "«софт» - писать «программа», «пакет»",
    r"\bкучк[а-яё]*": "«кучка» - разговорное, писать «группа», «набор»",
    r"\bскучн[а-яё]*": "«скучный» - оценка вместо сути",
    r"главн[а-яё]* грабл[а-яё]*": "«главные грабли» - писать «главная ошибка»",
}

# Правила ниже проверяются ТОЛЬКО по строкам, видимым пользователю, а не по
# файлам целиком. Слова из них законны во внутреннем тексте: «лог-лог
# вариограмма» и «файл-лог» в комментарии, «внешние кольца и дырки» в коде
# разбора геометрии. Запрещены они в том, что человек читает в окне.
VISIBLE_ONLY = {
    r"(?<!-)\bлог(а|у|ом|е|и|ов|ам|ами|ах)?\b(?!-)":
        "«лог» - панель называется журналом, писать «журнал»",
    r"[Зз]ерно генератора":
        "принято «Зерно ГСЧ», так его зовёт общая справка всех пяти "
        "инструментов",
    r"\bдырк[а-яё]*":
        "«дырка» - писать «пропуск» про грид и «внутреннее кольцо» про "
        "полигон",
    # Пояснение нуля и пустого значения в подписи пишется знаком равенства.
    # В окне было три записи вперемешку, «(0 = авто)», «(0 - авто)» и
    # «(пусто: по данным)», причём у одного и того же зерна ГСЧ встречались
    # две из них. Читающий справку искал в форме подпись, которой там нет.
    r"\((?:0|пусто)\s*(?:-|:)\s*[А-Яа-яЁё]":
        "пояснение нуля пишется «(0 = ...)», не через тире и не двоеточием",
}

# Тире между кириллическими словами. В коде тире встречается ещё как
# символ-заполнитель, поэтому смотрим именно прозу.
DASH = re.compile(r"[А-Яа-яЁё][^\n]{0,40}—|—[^\n]{0,40}[А-Яа-яЁё]")


def _published_files():
    """Файлы, попадающие к пользователю. Тесты исключены осознанно."""
    out = []
    for name in sorted(os.listdir(PKG)):
        if name.endswith(".py") or name == "metadata.txt":
            out.append(os.path.join(PKG, name))
    styles = os.path.join(PKG, "styles")
    if os.path.isdir(styles):
        for name in sorted(os.listdir(styles)):
            if name.endswith(".qml"):
                out.append(os.path.join(styles, name))
    return out


def _hits(pattern, text):
    """Список (номер строки, строка) для каждого совпадения."""
    found = []
    for m in re.finditer(pattern, text):
        line = text.count("\n", 0, m.start()) + 1
        found.append((line, text.splitlines()[line - 1].strip()[:90]))
    return found


def test_no_stop_words_in_published_texts():
    bad = []
    for path in _published_files():
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
        for pattern, why in STOP_WORDS.items():
            for line, ctx in _hits(pattern, text):
                bad.append("%s:%d [%s] %s" % (os.path.basename(path),
                                              line, why, ctx))
    assert not bad, "стоп-слова в публикуемых текстах:\n" + "\n".join(bad)


def test_no_em_dash_in_russian_prose():
    bad = []
    for path in _published_files():
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
        for line, ctx in _hits(DASH, text):
            bad.append("%s:%d %s" % (os.path.basename(path), line, ctx))
    assert not bad, "тире в русской прозе, заменить на ' - ':\n" + "\n".join(bad)


def test_visible_only_rules():
    """Правила, действующие в окне, но не в комментариях кода.

    Скан по файлам целиком для них не годится: «лог-лог вариограмма» и
    «внешние кольца и дырки» это верный внутренний текст. Строки берутся тем
    же сборщиком, что и в test_i18n, то есть ровно те, что видит человек.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "t18_collect", os.path.join(HERE, "test_i18n.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    bad = []
    for key in sorted(mod.collect_keys()):
        for pattern, why in VISIBLE_ONLY.items():
            m = re.search(pattern, key, re.IGNORECASE)
            if m:
                bad.append("[%s] …%s…"
                           % (why, key[max(0, m.start() - 60):m.end() + 60]
                              .replace("\n", " ")))
    assert not bad, ("нарушения в строках, видимых пользователю:\n"
                     + "\n".join(bad))


def test_metadata_changelog_is_covered():
    """Сторож самого сторожа: changelog обязан попадать в проверку.

    Ошибка была не в правиле, а в охвате. Если metadata.txt однажды
    выпадет из списка файлов, тесты выше замолчат и ничего не заметят.
    """
    names = [os.path.basename(p) for p in _published_files()]
    assert "metadata.txt" in names, "metadata.txt выпал из проверки"
    with open(os.path.join(PKG, "metadata.txt"), encoding="utf-8") as fh:
        assert "changelog=" in fh.read(), "changelog не найден в metadata.txt"
