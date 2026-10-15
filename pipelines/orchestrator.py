# -*- coding: utf-8 -*-
"""编排器：等清单出现后串行抓全部节目正文+音频（z955 等已完成节目自动跳过）。
进度: D:\09coding\voa\.temp\orch_state.txt
2026-10-15 更新：接入 7 个新节目（Ukraine/Podcast/VOA60/Early Literacy/National Parks/
Presidents/U.S.History），支持 p 系列清单（p%s.json）。"""
import os, sys, time, json, subprocess

SCRIPTS = r'D:\09coding\voa\.temp\scripts'
ZLIST = r'D:\09coding\voa\.temp\zlist'
FSTATE = r'D:\09coding\voa\.temp\fstate'
ORCH = r'D:\09coding\voa\.temp\orch_state.txt'
PY = sys.executable

# 顺序：先核心教学节目，再内容类；含 2026-10-15 新增节目
ORDER = ['7773', '1689', '3613', '7467', '5644', '6765', '6766', '6353', '5849',
         '986', '987', '1579', '1581', '3521']

def wstate(msg):
    with open(ORCH, 'w', encoding='utf-8') as f:
        f.write(msg)

def finished(z):
    p = os.path.join(FSTATE, 'z%s.txt' % z)
    if not os.path.exists(p):
        return False
    s = open(p, encoding='utf-8').read()
    return s.startswith('FINISHED')

def has_list(z):
    for pref in ('z', 'p'):
        p = os.path.join(ZLIST, '%s%s.json' % (pref, z))
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding='utf-8'))
            return len(d.get('articles', [])) > 0
        except Exception:
            continue
    return False

def run_list(z):
    wstate('listing z=%s' % z)
    subprocess.run([PY, os.path.join(SCRIPTS, 'robust_list_z.py'), z])

def run_articles(z):
    wstate('fetching articles z=%s' % z)
    try:
        subprocess.run([PY, os.path.join(SCRIPTS, 'fetch_articles.py'), z])
    except Exception as e:
        wstate('err z=%s %s' % (z, str(e)[:120]))

def main():
    os.makedirs(FSTATE, exist_ok=True)
    pending = [z for z in ORDER if not finished(z)]
    wstate('pending=%s' % ','.join(pending))
    wait_rounds = 0
    while pending:
        z = pending[0]
        if not has_list(z):
            wstate('waiting list z=%s (master)' % z)
            time.sleep(90)
            wait_rounds += 1
            # 若等待过久仍未有清单，自建清单
            if wait_rounds >= 8:
                wstate('self-list z=%s' % z)
                run_list(z)
                wait_rounds = 0
            continue
        wait_rounds = 0
        run_articles(z)
        pending = [x for x in ORDER if not finished(x)]
        wstate('pending=%s' % ','.join(pending))
        if pending:
            time.sleep(10)
    wstate('ALL_PROGRAMS_DONE')

if __name__ == '__main__':
    main()