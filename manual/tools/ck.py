# -*- coding: utf-8 -*-
import json, re, sys, collections
keys = json.load(open('/tmp/claude-0/ui_keys.json', encoding='utf-8'))
pats = sys.argv[1:]
for p in pats:
    rx = re.compile(p, re.I)
    n = 0
    for k in keys:
        for mm in rx.finditer(k):
            n += 1
            print('%-22s …%s…' % (p, k[max(0,mm.start()-70):mm.end()+70].replace('\n',' ')))
    print('-- %s : %d' % (p, n))
