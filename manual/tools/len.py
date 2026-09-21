# -*- coding: utf-8 -*-
import json, re, statistics
keys = json.load(open('/tmp/claude-0/ui_keys.json', encoding='utf-8'))
RU = re.compile(r'[А-Яа-яЁё]')
lens, long = [], []
for k in keys:
    if not RU.search(k) or k.lstrip().startswith('<'):
        continue
    t = re.sub(r'\*\*|`|\n+', ' ', k)
    for s in re.split(r'(?<=[.!?])\s+', t):
        s = s.strip()
        w = [x for x in re.findall(r'[^\s]+', s) if RU.search(x)]
        if len(w) < 2:
            continue
        lens.append(len(w))
        if len(w) > 28:
            long.append((len(w), s))
print('предложений %d, средняя %.1f' % (len(lens), statistics.mean(lens)))
print('>28 слов: %d, >34: %d' % (len(long), sum(1 for n,_ in long if n > 34)))
for n, s in sorted(long, reverse=True)[:25]:
    print('%3d  %s' % (n, s[:220]))
