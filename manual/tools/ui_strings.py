# -*- coding: utf-8 -*-
"""Строки, реально видимые пользователю: то, что собирает test_i18n."""
import ast, os, pathlib, re, sys
sys.path.insert(0, os.path.abspath('grid_isolines/tests'))
sys.path.insert(0, os.path.abspath('grid_isolines'))
import importlib.util
spec = importlib.util.spec_from_file_location('t18', 'grid_isolines/tests/test_i18n.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
keys = sorted(m.collect_keys())
import json
json.dump(keys, open('/tmp/claude-0/ui_keys.json','w',encoding='utf-8'), ensure_ascii=False, indent=0)
print(len(keys))
