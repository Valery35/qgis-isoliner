# -*- coding: utf-8 -*-
"""Проверка metadata.txt перед отправкой в каталог QGIS.

Перенесено из PlanetX (tools/preflight.py: url_problems, url_answers,
tag_problems). Запуск из корня хранилища:

    python tools/preflight.py          без сети
    python tools/preflight.py --net    ещё ответы адресов и метки выпусков

Без сети: подстановка процентов в каждом поле (одиночный «%» срывает
загрузку), процент-кодировка в homepage, repository и tracker (из-за неё
каталог не принял выпуск Routeliner 0.5.3), адрес не https, сайт
Информ++ без www, первая запись changelog не с номером версии, номер
версии против первого раздела CHANGELOG.md.

С --net: адреса открываются (400 и выше или обрыв - ошибка), метки
берутся с GitHub, номер, уже выпущенный меткой на другом коммите, - ошибка
«номер не поднят», номер меньше последнего выпуска - ошибка. Метки
Isoliner вида 5.13.47, без буквы v. Номера сравниваются числами: строкой
5.13.9 оказалась бы выше 5.13.10.

Код возврата 1 означает ошибки, замечания его не меняют.
"""

import configparser
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gittool import GitError, git as real_git  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = "grid_isolines"
URL_FIELDS = ("homepage", "repository", "tracker")
TAG = re.compile(r"\d+\.\d+\.\d+")


def url_problems(general):
    """Адреса metadata.txt без сети."""
    out = []
    for field in URL_FIELDS:
        url = general.get(field, "").strip()
        if not url:
            continue
        if re.search(r"%[0-9A-Fa-f]{2}", url):
            out.append("metadata.txt: %s в процент-кодировке, каталог её "
                       "не принимает - адрес пишется буквами" % field)
        if not url.startswith("https://"):
            out.append("metadata.txt: %s не https - %s" % (field, url))
        if re.match(r"https?://informpp\.ru", url):
            out.append("metadata.txt: %s без www, сайт так не открывается"
                       % field)
    return out


def url_answers(general, opener=None):
    """Ответ сервера по адресам metadata.txt, только с ключом --net."""
    import urllib.error
    import urllib.parse
    import urllib.request
    opener = opener or urllib.request.urlopen
    out = []
    for field in URL_FIELDS:
        url = general.get(field, "").strip()
        if not url:
            continue
        parts = urllib.parse.urlsplit(url)
        wire = urllib.parse.urlunsplit(parts._replace(
            path=urllib.parse.quote(parts.path, safe="/")))
        request = urllib.request.Request(
            wire, headers={"User-Agent": "Isoliner release check"})
        try:
            with opener(request, timeout=20) as reply:
                status = reply.status
        except urllib.error.HTTPError as error:
            status = error.code
        except (urllib.error.URLError, OSError) as error:
            out.append("metadata.txt: %s не открылся - %s" % (field, error))
            continue
        if status >= 400:
            out.append("metadata.txt: %s ответил %d" % (field, status))
    return out


def number(text):
    return tuple(int(part) for part in text.split("."))


def tag_problems(version, git=None):
    """Номер версии против меток выпусков. git подменяется в тестах."""
    git = git or real_git
    out = []
    try:
        try:
            git("fetch", "--tags", "--quiet", "origin")
        except GitError as error:
            out.append(("warn", "метки с GitHub не получены - %s"
                        % str(error).splitlines()[-1]))
        tags = [t.strip() for t in git("tag", "-l").splitlines()]
        valid = [t for t in tags if TAG.fullmatch(t)]
        head = git("rev-parse", "HEAD").strip()
        if version in valid:
            at = git("rev-list", "-n", "1", version).strip()
            if at != head:
                out.append(("error", "номер %s уже выпущен меткой на другом "
                            "коммите - номер не поднят" % version))
        if valid:
            last = max(valid, key=number)
            if number(version) < number(last):
                out.append(("error", "номер %s меньше последнего выпуска %s"
                            % (version, last)))
    except GitError as error:
        out.append(("error", "метки выпусков не прочитаны - %s" % error))
    return out


def changelog_problems(general, changelog_text):
    """Первая запись changelog metadata.txt и первый раздел CHANGELOG.md
    несут номер version."""
    out = []
    version = general.get("version", "").strip()
    log = general.get("changelog", "").strip()
    first = log.split()[0] if log else ""
    if log and first != version:
        out.append("metadata.txt: changelog начинается с %s, а version=%s"
                   % (first, version))
    for line in changelog_text.split("\n"):
        if line.startswith("## "):
            match = TAG.search(line)
            if match and match.group(0) != version:
                out.append("CHANGELOG.md: первый раздел %s, а version=%s"
                           % (match.group(0), version))
            break
    return out


def read_general(path):
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    return cp["general"]


def main(argv=()):
    errors, warnings = [], []
    general = read_general(os.path.join(ROOT, PLUGIN, "metadata.txt"))
    for field in general:
        try:
            general.get(field)
        except configparser.InterpolationError as error:
            errors.append("metadata.txt: поле %s - %s" % (field, error))
    version = general.get("version", "").strip()
    changelog = os.path.join(ROOT, "CHANGELOG.md")
    text = ""
    if os.path.exists(changelog):
        with open(changelog, encoding="utf-8") as fh:
            text = fh.read()
    errors += changelog_problems(general, text)
    errors += url_problems(general)
    if "--net" in argv:
        errors += url_answers(general)
        for level, line in tag_problems(version):
            (errors if level == "error" else warnings).append(line)

    print("Проверка metadata.txt Isoliner %s" % (version or "?"))
    for title, lines in (("Ошибки", errors), ("Замечания", warnings)):
        if lines:
            print("\n%s:" % title)
            for line in lines:
                print("  " + line)
    if not errors and not warnings:
        print("\nВсё в порядке.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
