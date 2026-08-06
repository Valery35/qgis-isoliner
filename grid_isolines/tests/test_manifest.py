# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Манифест модели: роли слоёв, разбор, слияние, догадки по именам."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import manifest as mf  # noqa: E402


def test_round_trip_keeps_roles():
    """Запись и разбор возвращают то же самое."""
    roles = {"id1": mf.ROLE_RELIEF, "id2": mf.ROLE_COLLAR}
    assert mf.parse(mf.dump(roles)) == roles


def test_parse_ignores_junk():
    """Пустые строки, комментарии и мусор без разделителя не ломают разбор."""
    txt = "\n# заметка\nid1=relief\n\nбез разделителя\nid2 = collar \n"
    assert mf.parse(txt) == {"id1": "relief", "id2": "collar"}


def test_merge_keeps_foreign_roles():
    """Чужая роль из другого модуля не теряется при слиянии.

    Манифест общий: вычищать незнакомое значит ломать соседям работу.
    """
    old = {"a": mf.ROLE_RELIEF, "z": "роль-другого-модуля"}
    out = mf.merge(old, {"a": mf.ROLE_DATUM})
    assert out["a"] == mf.ROLE_DATUM
    assert out["z"] == "роль-другого-модуля"


def test_gauge_wins_over_section_for_a_profile():
    """Створ гидрологов зовут профилем, и подсказка разреза не должна его брать."""
    assert mf.guess_role("Створ Профиль 1") == mf.ROLE_GAUGE
    assert mf.guess_role("Разрез по линии 2") == mf.ROLE_SECTION


def test_unknown_name_gives_nothing():
    """Имя, которое ни о чём не говорит, роли не получает.

    Догадка это подсказка, а не решение за пользователя.
    """
    assert mf.guess_role("слой 1") is None
    assert mf.guess_role("") is None


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print("ok: %s" % name)
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print("FAIL: %s - %s" % (name, exc))
    print("\n%d тестов, ошибок %d" % (len(fns), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(_run())
