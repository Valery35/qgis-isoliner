# -*- coding: utf-8 -*-
import json, re, sys
keys = json.load(open('/tmp/claude-0/ui_keys.json', encoding='utf-8'))
RU = re.compile(r'[А-Яа-яЁё]')
rx = re.compile(r'[а-яё][)»\w]*:\s+[а-яё]')
out = []
for k in keys:
    if not RU.search(k) or k.lstrip().startswith('<'):
        continue
    for m in rx.finditer(k):
        # предложение вокруг
        s = k.rfind('. ', 0, m.start()); s = 0 if s < 0 else s + 2
        s2 = k.rfind('\n', 0, m.start()); s = max(s, s2 + 1)
        e = k.find('. ', m.end()); e = len(k) if e < 0 else e + 1
        e2 = k.find('\n', m.end())
        if e2 >= 0: e = min(e, e2)
        out.append(k[s:e].strip())
seen, uniq = set(), []
for s in out:
    if s not in seen:
        seen.add(s); uniq.append(s)
print(len(out), len(uniq))
json.dump(uniq, open('/tmp/claude-0/colon_sent.json','w',encoding='utf-8'), ensure_ascii=False, indent=0)
a, b = (int(x) for x in sys.argv[1:3])
for i, s in enumerate(uniq[a:b], a):
    print('%3d| %s' % (i, s))
