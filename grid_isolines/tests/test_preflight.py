# -*- coding: utf-8 -*-
"""Проверка metadata.txt перед каталогом (tools/preflight.py в корне
хранилища). git и сеть подменены. Исходники проверки в плагин не входят,
в установленном модуле сторож пропускается."""

import importlib.util
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PATH = os.path.join(ROOT, "tools", "preflight.py")

pytestmark = pytest.mark.skipif(
    not os.path.exists(PATH),
    reason="tools/preflight.py не входит в установленный модуль")


def _load():
    spec = importlib.util.spec_from_file_location("iso_preflight", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(tags, head="aaa", at=None, fetch_error=None, pf=None):
    def git(*args):
        if args[0] == "fetch":
            if fetch_error:
                raise pf.GitError(fetch_error)
            return ""
        if args[0] == "tag":
            return "\n".join(tags)
        if args[0] == "rev-parse":
            return head
        if args[0] == "rev-list":
            return at or head
        raise AssertionError(args)
    return git


def test_percent_encoding_http_and_bare_site_are_errors():
    pf = _load()
    general = {"homepage": "https://www.informpp.ru/%D0%B3%D0%BB",
               "repository": "http://github.com/x/y",
               "tracker": "https://informpp.ru/x"}
    found = " | ".join(pf.url_problems(general))
    assert "homepage в процент-кодировке" in found
    assert "repository не https" in found
    assert "tracker без www" in found


def test_clean_urls_pass():
    pf = _load()
    general = {"homepage": "https://github.com/Valery35/qgis-isoliner",
               "repository": "https://www.informpp.ru/главная-страница/x"}
    assert pf.url_problems(general) == []


def test_numbers_compare_as_numbers_not_strings():
    pf = _load()
    out = pf.tag_problems("5.13.9", _git(["5.13.10", "5.13.9", "nightly",
                                          "v3.2.0"], at="aaa", pf=pf))
    assert ("error", "номер 5.13.9 меньше последнего выпуска 5.13.10") in out


def test_issued_number_on_other_commit_is_not_raised():
    pf = _load()
    out = pf.tag_problems("5.13.47", _git(["5.13.47"], head="new",
                                          at="old", pf=pf))
    assert any("номер не поднят" in t for lvl, t in out if lvl == "error")


def test_new_number_passes_and_fetch_failure_is_warning():
    pf = _load()
    out = pf.tag_problems("5.13.62", _git(["5.13.47", "5.13.61x"],
                                          fetch_error="нет сети", pf=pf))
    assert [lvl for lvl, _ in out] == ["warn"]


def test_changelog_first_entry_must_carry_version():
    pf = _load()
    general = {"version": "5.13.62", "changelog": "5.13.61\n- старое"}
    out = pf.changelog_problems(general, "# История\n## 5.13.61\n")
    assert len(out) == 2
    general = {"version": "5.13.61", "changelog": "5.13.61\n- текст"}
    assert pf.changelog_problems(general, "## 5.13.61\n") == []


@pytest.mark.parametrize("text, want", [
    ("## v5.13.62", "5.13.62"),
    ("grid_isolines-5.13.62.zip", "5.13.62"),
    ("## 5.13.62.7", None),
    ("## 5.13.62 (16.11.2026)", "5.13.62"),
])
def test_version_pattern(text, want):
    pf = _load()
    match = pf.VERSION.search(text)
    assert (match.group(1) if match else None) == want


def test_changelog_heading_with_prefix_and_build_number():
    pf = _load()
    general = {"version": "5.13.62", "changelog": "5.13.62\n- текст"}
    assert pf.changelog_problems(general, "## v5.13.61\n") != []
    assert pf.changelog_problems(general, "## 5.13.62.1\n") != []


def test_url_answers_reports_bad_status():
    pf = _load()

    class Reply:
        def __init__(self, status):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    statuses = iter([200, 404])
    general = {"homepage": "https://a.example", "tracker": "https://b.example"}
    out = pf.url_answers(general, opener=lambda r, timeout: Reply(next(statuses)))
    assert out == ["metadata.txt: tracker ответил 404"]


def test_current_metadata_passes_offline():
    pf = _load()
    assert pf.main([]) == 0
