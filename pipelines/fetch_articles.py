# -*- coding: utf-8 -*-
"""按 z 清单抓取文章正文 + HQ 音频（curl 走 7890，断点续传）。
存储: D:\09coding\voa\audio\{Program}\{date} {title}\{article.txt, meta.json, audio_hq.mp3}
进度: D:\09coding\voa\.temp\fstate\z{Z}.txt
用法: python fetch_articles.py <zid>"""
import subprocess, json, re, os, sys, time, threading
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from queue import Queue

BASE = 'https://learningenglish.voanews.com'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
HEADERS = ['-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
           '-H', 'Accept-Language: zh-CN,zh;q=0.9,en;q=0.8',
           '-H', 'Sec-Fetch-Dest: document',
           '-H', 'Sec-Fetch-Mode: navigate',
           '-H', 'Sec-Fetch-Site: none']
AUDIO_ROOT = r'D:\09coding\voa\audio'
ZLIST = r'D:\09coding\voa\.temp\zlist'
FSTATE = r'D:\09coding\voa\.temp\fstate'

PROGS = {
    '955': ('Health & Lifestyle', 'Intermediate'),
    '986': ('Arts & Culture', 'Intermediate'),
    '987': ('Words and Their Stories', 'Advanced'),
    '1579': ('Science & Technology', 'Intermediate'),
    '1581': ('American Stories', 'Advanced'),
    '3521': ('As It Is', 'Intermediate'),
    '3619': ('English in a Minute', 'Intermediate'),
    '3620': ('News Words', 'Beginning'),
    '4456': ('Everyday Grammar', 'Advanced'),
    '4691': ('English @ the Movies', 'Intermediate'),
    '4716': ('Everyday Grammar TV', 'Intermediate'),
    '5535': ('Ask a Teacher', 'Beginning'),
    '6047': ('How to Pronounce', 'Beginning'),
    '7468': ('Education Tips', 'Advanced'),
    '7773': ('Teach Us about Ukraine', 'Intermediate'),
    '1689': ('VOA Learning English Podcast', 'Advanced'),
    '3613': ('VOA60: Watch & Learn', 'Intermediate'),
    '7467': ('Early Literacy', 'Advanced'),
    '5644': ("Let's Learn English - Level 1", 'Beginning'),
    '6765': ("Let's Learn English - Level 2", 'Beginning'),
    '6766': ("America's Presidents", 'Advanced'),
    '6353': ('U.S. History', 'Advanced'),
    '5849': ("America's National Parks", 'Advanced'),
}

def curl(url, timeout=60, binary=False, direct=False):
    cmd = ['curl.exe', '-s', '-L', '-A', UA]
    if not direct:
        cmd += ['-x', 'http://127.0.0.1:10808']
    cmd += HEADERS + ['--max-time', str(timeout), url]
    p = subprocess.run(cmd, capture_output=True)
    return p.returncode, p.stdout

def fetch(url, tries=6, timeout=60):
    for i in range(tries):
        rc, body = curl(url, timeout)
        if rc == 0 and body:
            return body
        time.sleep(min(4 * (i + 1), 24))
    return None

def download(url, dest, tries=5):
    for i in range(tries):
        # 音频走 CDN 直连（快 22 倍），失败时回退代理
        rc, body = curl(url, timeout=300, binary=True, direct=(i < 3))
        if rc == 0 and body and len(body) > 5000:
            with open(dest, 'wb') as f:
                f.write(body)
            return len(body)
        time.sleep(min(4 * (i + 1), 20))
    return 0

# ===== 后台并行音频下载队列 =====
AUDIO_Q = Queue()
AUDIO_STATS = {'ok': 0, 'fail': 0}
AUDIO_LOCK = threading.Lock()


def audio_worker():
    while True:
        url, dest, title = AUDIO_Q.get()
        if url is None:
            AUDIO_Q.task_done()
            break
        if os.path.exists(dest) and os.path.getsize(dest) > 5000:
            with AUDIO_LOCK:
                AUDIO_STATS['ok'] += 1
            AUDIO_Q.task_done()
            continue
        sz = download(url, dest)
        with AUDIO_LOCK:
            if sz:
                AUDIO_STATS['ok'] += 1
            else:
                AUDIO_STATS['fail'] += 1
                print('AUDIO_FAIL %s' % title[:50], flush=True)
        AUDIO_Q.task_done()

def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]', '', s).strip().strip(' .')[:120]

def clean_wsw(bd):
    """★ 清理正文容器：移除脚本/样式/媒体播放器/下载链接等非正文内容，保留原文样式标签"""
    for t in bd(['script', 'style', 'iframe', 'noscript']):
        t.decompose()
    # 多媒体/播放器/分享/作者信息等块
    for t in bd.select('[class*="c-mmp"], [class*="media"], [class*="multimedia"], [class*="share"],'
                       '[class*="author"], [class*="download"], [id*="player"], [class*="player"],'
                       '[class*="caption"], [class*="credit"], [class*="byline"]'):
        t.decompose()
    # 下载/外链按钮（Direct link、128/64 kbps MP3、Pop-out player、Embed、Print 等）
    for a in bd.find_all('a', href=True):
        txt = a.get_text(' ', strip=True)
        hr = a['href']
        if re.search(r'\.mp3|\.mp4|\?download|/download|pop-out|popup|javascript:', hr, re.I) \
                or re.search(r'Direct|Pop-out|Embed|Print|kbps|MP3|MP4', txt, re.I):
            a.decompose()
    # 含 kbps/Pop-out/Direct 的文本节点所在块
    for s in bd.find_all(string=re.compile(r'Direct link|Pop-out player|\d+\s*kbps|Embed', re.I)):
        blk = s.find_parent(['p', 'div', 'li', 'span'])
        if blk:
            blk.decompose()
    return bd


def audio_sources(audio):
    """★ 从 audio 标签 data-sources 解析多码率音频源：{'128kbps': url, '64kbps': url, ...}
    注意：DataInfo 可能是 '128 kbps'（带空格），统一规范化为 '128kbps' 以便 _pick 匹配。"""
    out = {}
    if audio is None:
        return out
    ds = audio.get('data-sources') or ''
    if ds:
        import html as hm
        ds = hm.unescape(ds)
        for m in re.finditer(r'"Src"\s*:\s*"([^"]+)"[^}]*?"DataInfo"\s*:\s*"([^"]+)"', ds):
            u, di = m.group(1), m.group(2)
            di = re.sub(r'\s+', '', di)   # '128 kbps' -> '128kbps'
            out[di] = u
    if not out and audio.get('src'):
        out['default'] = audio['src']
    return out


def parse_article(html):
    soup = BeautifulSoup(html.decode('utf-8', 'replace'), 'html.parser')
    title_el = soup.find('h1')
    title = title_el.get_text(strip=True) if title_el else (soup.title.get_text(strip=True) if soup.title else 'untitled')
    time_el = soup.find('time', attrs={'datetime': True})
    pubdate = time_el.get('datetime') if time_el else ''
    meta_desc = soup.find('meta', attrs={'name': 'description'})
    summary = meta_desc.get('content', '') if meta_desc else ''
    audio = soup.find('audio')
    sources = audio_sources(audio)
    # 正文：文本版 + HTML 原文版（保留 <b>/<i>/<strong>/<em>/<a>/列表等样式）
    body = body_html = ''
    bd = soup.select_one('div.wsw')
    if bd:
        bd = clean_wsw(bd)
        body = bd.get_text('\n', strip=True)
        body_html = str(bd)
    return {'title': title, 'date': pubdate, 'summary': summary,
            'sources': sources, 'body': body, 'body_html': body_html}

def main():
    z = sys.argv[1]
    # 启动 8 个后台音频下载线程（直连 CDN，总带宽可达 1.4MB/s+）
    for _ in range(8):
        t = threading.Thread(target=audio_worker, daemon=True)
        t.start()
    prog_name, level = PROGS[z]
    # 支持 z/p 两种清单前缀（p 系列节目清单存为 p%s.json）
    src = os.path.join(ZLIST, 'z%s.json' % z)
    if not os.path.exists(src):
        src = os.path.join(ZLIST, 'p%s.json' % z)
    d = json.load(open(src, encoding='utf-8'))
    articles = d['articles']
    os.makedirs(FSTATE, exist_ok=True)
    prog_dir = os.path.join(AUDIO_ROOT, safe_name(prog_name))
    os.makedirs(prog_dir, exist_ok=True)
    idx_path = os.path.join(prog_dir, '_done_index.json')
    done = {}
    if os.path.exists(idx_path):
        try:
            done = json.load(open(idx_path, encoding='utf-8'))
        except Exception:
            done = {}
    ok = audio_ok = fail = 0
    state = os.path.join(FSTATE, 'z%s.txt' % z)
    with open(state, 'w', encoding='utf-8') as f:
        f.write('z=%s %s total=%d done_prev=%d starting' % (z, prog_name, len(articles), len(done)))
    for n, it in enumerate(articles, 1):
        url = it['url']
        if url in done:
            ok += 1
            continue
        html = fetch(url)
        if html is None:
            fail += 1
            print('FAIL page %s' % url, flush=True)
            continue
        try:
            a = parse_article(html)
        except Exception as e:
            fail += 1
            print('FAIL parse %s %s' % (url, str(e)[:60]), flush=True)
            continue
        dstr = (a['date'] or '')[:10] or 'nodate'
        folder = os.path.join(prog_dir, '%s %s' % (dstr, safe_name(a['title'])))
        os.makedirs(folder, exist_ok=True)
        L = ['# ' + a['title'], '',
             'Program: %s' % prog_name, 'Difficulty: %s' % level,
             'Published: %s' % a['date'], 'Source: %s' % url]
        if a['summary']:
            L += ['', '## Summary', a['summary']]
        L += ['', '## Article', a['body'] or '(no body)']
        with open(os.path.join(folder, 'article.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(L))
        # ★ 正文 HTML 原文（保留样式），供 H5 渲染
        if a['body_html']:
            with open(os.path.join(folder, 'article.html'), 'w', encoding='utf-8') as f:
                f.write(a['body_html'])
        with open(os.path.join(folder, 'meta.json'), 'w', encoding='utf-8') as f:
            json.dump({'url': url, 'title': a['title'], 'date': a['date'],
                       'summary': a['summary'], 'sources': a['sources'],
                       'mp3': a['sources'].get('128kbps') or a['sources'].get('default'),
                       'program': prog_name, 'level': level},
                      f, ensure_ascii=False, indent=1)
        # ★ 多码率本地全量下载：HQ(128k) + LQ(64k)，入后台队列并行
        srcs = a['sources']
        def _pick(pref):
            for k in pref:
                if k in srcs:
                    return srcs[k]
            return ''
        hq = _pick(['128kbps', 'HQ', 'default'])
        lq = _pick(['64kbps', 'LQ'])
        if hq:
            AUDIO_Q.put((hq, os.path.join(folder, 'audio_hq.mp3'), a['title']))
        if lq and lq != hq:
            AUDIO_Q.put((lq, os.path.join(folder, 'audio_lq.mp3'), a['title'] + ' [64k]'))
        ok += 1
        done[url] = folder
        with open(idx_path, 'w', encoding='utf-8') as f:
            json.dump(done, f, ensure_ascii=False)
        with open(state, 'w', encoding='utf-8') as f:
            f.write('z=%s %s %d/%d ok=%d audio=%d(fail=%d) fail=%d' % (z, prog_name, n, len(articles), ok, AUDIO_STATS['ok'], AUDIO_STATS['fail'], fail))
        print('z=%s [%d/%d] %s' % (z, n, len(articles), a['title'][:45]), flush=True)
    # 等待后台音频队列完成
    AUDIO_Q.join()
    for _ in range(8):
        AUDIO_Q.put((None, None, None))
    audio_ok = AUDIO_STATS['ok']
    audio_fail_cnt = AUDIO_STATS['fail']
    if fail == 0:
        tag = 'FINISHED'
    else:
        tag = 'PARTIAL'
    with open(state, 'w', encoding='utf-8') as f:
        f.write('%s z=%s %s total=%d ok=%d audio=%d fail=%d audio_fail=%d' % (tag, z, prog_name, len(articles), ok, audio_ok, fail, audio_fail_cnt))
    print('%s z=%s ok=%d audio=%d fail=%d audio_fail=%d' % (tag, z, ok, audio_ok, fail, audio_fail_cnt))

if __name__ == '__main__':
    main()