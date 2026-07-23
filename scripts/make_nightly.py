# -*- coding: utf-8 -*-
"""Сборка ночного выпуска Isoliner: архив плагина и nightly/plugins.xml.

Запуск из корня репозитория:

    python scripts/make_nightly.py

Что делает:
1. Читает grid_isolines/metadata.txt (версия, описание, ссылки).
2. Собирает dist/grid_isolines_nightly.zip, принудительно ставя в нём
   experimental=True. Флаг обязателен: без него ночная сборка перекрыла бы
   каталожную версию у всех, кто подключил оба репозитория, - эксперименталки
   же видны только при включённой галке показа экспериментальных модулей.
   Исходный metadata.txt в репозитории не меняется.
3. Пишет nightly/plugins.xml со ссылкой на постоянный релиз-тег nightly.

После скрипта остаётся два ручных шага:
- закоммитить и запушить nightly/plugins.xml;
- заменить ассет в релизе nightly файлом dist/grid_isolines.zip
  (в вебе: Releases -> nightly -> Edit -> удалить старый ассет, положить
  новый; из консоли: gh release upload nightly
  dist/grid_isolines_nightly.zip --clobber).

Имя архива ночной НЕ grid_isolines.zip нарочно: QGIS различает модули по
имени zip до первой точки, и при совпадении с каталожным именем сведения
из официального каталога затирают ночные при слиянии репозиториев.
Внутри архива корневая папка остаётся grid_isolines, ставится модуль в ту
же папку.

Каталог tests/ в ночной архив входит, как заведено для рабочих сборок.
"""
import io
import re
import xml.sax.saxutils as sx
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "grid_isolines"
DIST = ROOT / "dist"
NIGHTLY = ROOT / "nightly"

DOWNLOAD_URL = ("https://github.com/Valery35/qgis-isoliner/"
                "releases/download/nightly/grid_isolines_nightly.zip")

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def read_metadata():
    text = io.open(PLUGIN / "metadata.txt", encoding="utf-8").read()

    def field(name, default=""):
        m = re.search(r"^%s=(.*)$" % re.escape(name), text, re.M)
        return m.group(1).strip() if m else default

    return {
        "name": field("name", "Isoliner"),
        "version": field("version"),
        "description": field("description"),
        "qgis_min": field("qgisMinimumVersion", "3.16"),
        "qgis_max": field("qgisMaximumVersion", "4.99"),
        "author": field("author"),
        "homepage": field("homepage"),
        "tracker": field("tracker"),
        "repository": field("repository"),
        "tags": field("tags"),
    }, text


def build_zip(meta_text):
    DIST.mkdir(exist_ok=True)
    out = DIST / "grid_isolines_nightly.zip"
    nightly_meta = re.sub(r"^experimental=.*$", "experimental=True",
                          meta_text, count=1, flags=re.M)
    if "experimental=True" not in nightly_meta:
        raise SystemExit("В metadata.txt нет поля experimental - "
                         "добавьте его прежде, чем собирать ночную.")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(PLUGIN.rglob("*")):
            if p.is_dir():
                continue
            rel = p.relative_to(ROOT)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            if p.suffix in SKIP_SUFFIXES:
                continue
            if rel == Path("grid_isolines/metadata.txt"):
                z.writestr(str(rel), nightly_meta)
            else:
                z.write(p, str(rel))
    return out


def write_plugins_xml(meta):
    NIGHTLY.mkdir(exist_ok=True)
    e = sx.escape
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<plugins>\n"
        '  <pyqgis_plugin name="%s" version="%s">\n'
        "    <description>%s</description>\n"
        "    <version>%s</version>\n"
        "    <qgis_minimum_version>%s</qgis_minimum_version>\n"
        "    <qgis_maximum_version>%s</qgis_maximum_version>\n"
        "    <homepage>%s</homepage>\n"
        "    <tracker>%s</tracker>\n"
        "    <repository>%s</repository>\n"
        "    <tags>%s</tags>\n"
        "    <author_name>%s</author_name>\n"
        "    <file_name>grid_isolines_nightly.zip</file_name>\n"
        "    <download_url>%s</download_url>\n"
        "    <experimental>True</experimental>\n"
        "    <deprecated>False</deprecated>\n"
        "  </pyqgis_plugin>\n"
        "</plugins>\n"
    ) % (e(meta["name"]), e(meta["version"]), e(meta["description"]),
         e(meta["version"]), e(meta["qgis_min"]), e(meta["qgis_max"]),
         e(meta["homepage"]), e(meta["tracker"]), e(meta["repository"]),
         e(meta["tags"]), e(meta["author"]), DOWNLOAD_URL)
    out = NIGHTLY / "plugins.xml"
    io.open(out, "w", encoding="utf-8").write(xml)
    return out


def main():
    meta, meta_text = read_metadata()
    if not meta["version"]:
        raise SystemExit("Не найдена версия в metadata.txt")
    z = build_zip(meta_text)
    x = write_plugins_xml(meta)
    print("Ночная сборка %s %s" % (meta["name"], meta["version"]))
    print("  архив:      %s" % z.relative_to(ROOT))
    print("  реестр:     %s" % x.relative_to(ROOT))
    print("Осталось: закоммитить nightly/plugins.xml и заменить ассет "
          "в релизе nightly.")


if __name__ == "__main__":
    main()
