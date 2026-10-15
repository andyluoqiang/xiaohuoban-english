# -*- coding: utf-8 -*-
"""抓取 VOA Learning English 首页/节目索引，列出全部节目（series）及 z 编号与难度入口"""
import requests, re, json
from urllib.parse import urljoin
from bs4 import BeautifulSoup

PROXY = {'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'}
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
BASE = 'https://learningenglish.voanews.com'

def get(url):
    for i in range(3):
        try:
            r = requests.get(url, headers=HEADERS, proxies=PROXY, timeout=35)
            if r.status_code == 200:
                return r
        except Exception as e:
            print('get fail', url, str(e)[:80])
    return None

def main():
    r = get(BASE + '/')
    if r is None:
        print('home fetch fail'); return
    soup = BeautifulSoup(r.text, 'html.parser')
    print('home status ok, length', len(r.text))
    # 找出所有频道/节目链接
    found = {}
    for a in soup.select('a[href]'):
        href = a.get('href', '')
        txt = (a.get_text(strip=True) or '')
        if not href or not txt:
            continue
        # 节目链接形如 /z/XXXX 或 /programs/xxx 或 /p/xxxx
        m = re.search(r'/z/(\d+)', href)
        if m:
            zid = m.group(1)
            found.setdefault(zid, txt)
            print('z%s -> %s' % (zid, txt[:40]))
    print('--- 全部 z 编号 ---')
    for zid in sorted(found):
        print(zid, found[zid])
    # 保存
    import json
    with open(r'D:\09coding\voa\.temp\scripts\voa_home_channels.json', 'w', encoding='utf-8') as f:
        json.dump({k: v for k, v in found.items()}, f, ensure_ascii=False, indent=1)

if __name__ == '__main__':
    main()