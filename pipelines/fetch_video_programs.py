# -*- coding: utf-8 -*-
"""抓 VOA Learning English 视频节目页，列出视频节目及其 z 编号"""
import subprocess, re, json
from urllib.parse import urljoin
from bs4 import BeautifulSoup

BASE = 'https://learningenglish.voanews.com'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'

def curl(url, timeout=50):
    p = subprocess.run(['curl.exe', '-s', '-L', '-x', 'http://127.0.0.1:7890', '-A', UA,
                        '--max-time', str(timeout), url], capture_output=True)
    return p.returncode, p.stdout

def fetch(url):
    for i in range(4):
        rc, html = curl(url)
        if rc == 0 and html:
            return html
        time_import = __import__('time')
        time_import.sleep(6)
    return None

def main():
    html = fetch(BASE + '/programs')
    if not html:
        print('PROGRAMS FAIL')
        return
    soup = BeautifulSoup(html.decode('utf-8', 'replace'), 'html.parser')
    print('== title ==', (soup.title.get_text(strip=True) if soup.title else ''))
    found = []
    for a in soup.select('a[href]'):
        href = a.get('href', '')
        txt = a.get_text(strip=True)
        if not txt:
            continue
        m = re.search(r'/z/(\d+)', href)
        if m and len(txt) > 1:
            found.append((txt, m.group(1), urljoin(BASE, href)))
    seen = set()
    for txt, z, full in found:
        if (txt, z) in seen:
            continue
        seen.add((txt, z))
        print('z/%s  %s' % (z, txt[:70]))
    # 保存
    with open(r'D:\09coding\voa\.temp\scripts\voa_programs_page.json', 'w', encoding='utf-8') as f:
        json.dump([{'title': t, 'z': z, 'url': u} for t, z, u in found], f, ensure_ascii=False, indent=1)
    print('total entries:', len(seen))

if __name__ == '__main__':
    main()