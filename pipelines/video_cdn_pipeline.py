# -*- coding: utf-8 -*-
"""视频 CDN 上线流水线：
8 个视频节目 → 逐集抓页面 → 提取四档 VOA CDN URL（240/480/720/1080p）+ 时长 + 正文
→ 正文 JSON 上传 VPS → manifest 条目 upsert（q=多档 CDN 源；a=VPS 兜底路径，
兜底 240p 下载上传由独立流水线另行负责）→ 每 5 集同步 manifest。断点续传。
不下载视频文件本身：H5 播放时直连 VOA CDN（实测快 17 倍）。
"""
import subprocess, json, re, os, sys, time, datetime
import msvcrt
from contextlib import contextmanager

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
HEADERS = ['-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
           '-H', 'Accept-Language: zh-CN,zh;q=0.9,en;q=0.8',
           '-H', 'Sec-Fetch-Dest: document', '-H', 'Sec-Fetch-Mode: navigate', '-H', 'Sec-Fetch-Site: none']
ZLIST = r'D:\09coding\voa\.temp\zlist'
SITE = r'D:\09coding\voa\site'
ART_ROOT = os.path.join(SITE, 'articles')
MP_PATH = os.path.join(SITE, 'api', 'manifest.json')
STATE_PATH = r'D:\09coding\voa\.temp\video_cdn_state.json'
HOST, USER, PWD = '23.94.61.235', 'root', os.environ.get('VPS_PWD', '')
REMOTE_APP = '/var/www/xiaohuoban/app'
SYNC_EVERY = 5

# 清单号 -> (节目名, 难度分组, slug)
PROGS = {
    'z4691': ('English @ the Movies', 'Intermediate', 'english-at-the-movies'),
    'z4716': ('Everyday Grammar TV', 'Intermediate', 'everyday-grammar-tv'),
    'z6042': ('How to Pronounce', 'Beginning', 'how-to-pronounce'),
    'p8326': ("Let's Learn English with Anna", 'Beginning', 'lets-learn-english-with-anna'),
    'p5644': ("Let's Learn English - Level 1", 'Beginning', 'lets-learn-english-level-1'),
    'p6765': ("Let's Learn English - Level 2", 'Beginning', 'lets-learn-english-level-2'),
    'p6764': ("Let's Teach English", 'Advanced', 'lets-teach-english'),
    'p6840': ('News Literacy', 'Advanced', 'news-literacy'),
    'z3619': ('English in a Minute', 'Intermediate', 'english-in-a-minute'),
    'z3620': ('News Words', 'Beginning', 'news-words'),
    'z3613': ('VOA60: Watch & Learn', 'Intermediate', 'voa60-watch-learn'),
    'z7467': ('Early Literacy', 'Advanced', 'early-literacy'),
    'p6766': ("America's Presidents", 'Advanced', 'america-s-presidents'),
    'p6353': ('U.S. History', 'Advanced', 'us-s-history'),
}


def log(m):
    print('[vcdn] %s' % m, flush=True)


def slugify(s):
    s = s.lower()
    s = re.sub(r'&', ' and ', s)
    s = re.sub(r'[^a-z0-9]+', '-', s)
    return re.sub(r'-+', '-', s).strip('-')


def fetch_html(url, tries=5):
    for i in range(tries):
        p = subprocess.run(['curl.exe', '-s', '-L', '-x', 'http://127.0.0.1:10808', '-A', UA] + HEADERS +
                           ['--max-time', '40', url], capture_output=True)
        if p.returncode == 0 and p.stdout and len(p.stdout) > 5000:
            return p.stdout.decode('utf-8', 'replace')
        time.sleep(min(4 * (i + 1), 16))
    return None


def load_json(p, d):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return d


@contextmanager
def manifest_lock(timeout=30):
    """与音频流水线/视频兜底流水线共用的 manifest 文件锁"""
    lf = open(MP_PATH + '.lock', 'a+')
    ok = False
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            lf.seek(0)
            msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)
            ok = True
            break
        except OSError:
            time.sleep(0.5)
    try:
        yield ok
    finally:
        if ok:
            try:
                lf.seek(0)
                msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
        lf.close()


def parse_iso_dur(s):
    """PT2M30S -> 2:30"""
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', s or '')
    if not m:
        return ''
    h, mi, se = (int(x) if x else 0 for x in m.groups())
    tot = h * 3600 + mi * 60 + se
    if tot <= 0:
        return ''
    return '%d:%02d' % (tot // 60, tot % 60)


def _clean_wsw_bd(bd):
    """★ 清洗正文容器（BeautifulSoup）：移除播放器/分享/quiz/作者/下载/脚本等非正文噪声，
    保留原文样式标签。与音频端 fetch_articles.clean_wsw 同策略，另补 share/quiz/column 残留。"""
    from bs4 import BeautifulSoup as _BS
    for t in bd(['script', 'style', 'iframe', 'noscript', 'form', 'svg', 'canvas', 'video', 'audio', 'object', 'embed']):
        t.decompose()
    # 多媒体/播放器/分享/作者/下载/quiz/测验等块
    for t in bd.select('[class*="c-mmp"], [class*="media"], [class*="multimedia"], [class*="share"],'
                       '[class*="author"], [class*="download"], [id*="player"], [class*="player"],'
                       '[class*="caption"], [class*="credit"], [class*="byline"], [class*="wsw__embed"],'
                       '[class*="quiz"], [class*="sticky"], [class*="widget"], [data-sp_api]'):
        t.decompose()
    # 下载/外链按钮（Direct link、kbps MP3、Pop-out player、Embed、Print、Share 等）
    for a in bd.find_all('a', href=True):
        txt = a.get_text(' ', strip=True)
        hr = a['href']
        if re.search(r'\.mp3|\.mp4|\?download|/download|pop-out|popup|javascript:', hr, re.I) \
                or re.search(r'Direct|Pop-out|Embed|Print|kbps|MP3|MP4|Share', txt, re.I):
            a.decompose()
    # 含 kbps/Pop-out/Direct/Share/copied 的文本节点所在块
    for s in bd.find_all(string=re.compile(r'Direct link|Pop-out player|\d+\s*kbps|Embed|Share|The code has been copied', re.I)):
        try:
            blk = s.find_parent(['p', 'div', 'li', 'span'])
        except Exception:
            blk = None
        if blk:
            blk.decompose()
    return bd


def _is_noise_tag(tag):
    """判断某标签是否非正文噪声（播放器/分享/quiz/作者/下载等）。"""
    cls = ' '.join(tag.get('class') or []) + ' ' + (tag.get('id') or '')
    low = cls.lower()
    if tag.name in ('script', 'style', 'iframe', 'noscript', 'form', 'svg', 'canvas',
                    'video', 'audio', 'object', 'embed', 'link', 'meta', 'head'):
        return True
    if tag.name == 'a':
        txt = tag.get_text(' ', strip=True)
        hr = tag.get('href') or ''
        if re.search(r'\.mp3|\.mp4|\?download|/download|pop-out|popup|javascript:', hr, re.I) \
                or re.search(r'Direct|Pop-out|Embed|Print|kbps|MP3|MP4|Share', txt, re.I):
            return True
        return False
    noise_cls = ('c-mmp', 'media', 'multimedia', 'share', 'author', 'download', 'player',
                 'caption', 'credit', 'byline', 'wsw__embed', 'quiz', 'sticky', 'widget',
                 'column', 'simple-menu', 'ico', 'strip', 'not-apply-to-sticky', 'c-spinner',
                 'clear', 'js-sources')
    return any(n in low for n in noise_cls)


def _is_noise_text(s):
    """判定文本节点是否是正文（长度足够且非空白/分享残留）。"""
    t = s.strip()
    if not t:
        return False
    if len(t) < 2:
        return False
    if re.search(r'(?i)^(Link|The code has been copied|Direct link|Pop-out player|Embed|Share)', t):
        return False
    return True


def extract_wsw(html):
    """★ 从页面/片段提取正文（纯文本 + 干净 HTML 原文）。
    处理两类结构：
      A) 经典页：<div class="wsw">...</div> 单容器
      B) 新版页：正文 <p>/<h2> 平铺在 [document] 层（顶部夹播放器/分享噪声）
    策略：在 body 顶层遍历，跳过噪声节点，收集正文文本节点及其容器（p/li/h2/blockquote/figure 保留）。
    修复：旧实现只匹配 div.wsw 单容器，导致新版页（正文平铺）被误判为播放器而丢失。"""
    from bs4 import BeautifulSoup as _BS
    try:
        soup = _BS(html.decode('utf-8', 'replace'), 'html.parser')
    except Exception:
        soup = _BS(html, 'html.parser')
    # 优先：标准 div.wsw 容器
    bd = soup.select_one('div[class*="wsw"]')
    if bd and bd.find('p'):
        bd = _clean_wsw_bd(bd)
        text = bd.get_text('\n', strip=True)
        text = re.sub(r'\n{2,}', '\n', text).strip()
        if len(text) > 20:
            return text, str(bd).strip()
    # 兜底：正文平铺在顶层（Presidents 新版页）
    root = soup.find('body') or soup
    # 收集正文块：遍历顶层子元素，跳过噪声，收集可读文本节点
    parts = []
    saw_para = False
    for c in root.children:
        if getattr(c, 'name', None) is None:
            continue  # 跳过纯空白
        if _is_noise_tag(c):
            continue
        # 非噪声块内的可读文本
        if c.name == 'p':
            t = c.get_text(' ', strip=True)
            if _is_noise_text(t):
                parts.append(str(c))
                saw_para = True
        elif c.name in ('h1', 'h2', 'h3', 'h4'):
            t = c.get_text(' ', strip=True)
            if t:
                parts.append(str(c))
                saw_para = True
        elif c.name in ('blockquote', 'ul', 'ol'):
            t = c.get_text(' ', strip=True)
            if t:
                parts.append(str(c))
                saw_para = True
        elif c.name == 'figure':
            t = c.get_text(' ', strip=True)
            if t:
                parts.append(str(c))
                saw_para = True
        else:
            # div 等：若含可读正文文本（且非纯噪声容器），递归提取内部 p
            if c.get_text(' ', strip=True):
                sub = c.find_all(['p', 'h2', 'h3', 'blockquote'])
                for s in sub:
                    t = s.get_text(' ', strip=True)
                    if _is_noise_text(t):
                        parts.append(str(s))
                        saw_para = True
    if not parts:
        return '', ''
    body_html = '\n'.join(parts).strip()
    text = re.sub(r'<[^>]+>', '\n', body_html)
    text = re.sub(r'\n{2,}', '\n', text).strip()
    return text, body_html


def q_urls(html):
    """★ 从页面 data-sources 属性解析各档 CDN 视频 URL，返回 {清晰度数字: URL}。
    新格式 240/480/720/1080p（1080p URL 后缀优先，页面 DataInfo 曾误标 810p）；
    老格式 270(mobile)/360(默认)/720(hq)/1080(fullhd)。
    URL 统一去掉 ?cb= 签名参数。无 data-sources 时用正则兜底。"""
    import html as _html
    out = {}
    m = re.search(r'data-sources="([^"]*)"', html)
    if m:
        try:
            arr = json.loads(_html.unescape(m.group(1)))
            for it in arr:
                if not isinstance(it, dict):
                    continue
                src = it.get('Src') or it.get('AmpSrc') or ''
                if not src:
                    continue
                u = re.sub(r'[?&]cb=[^&]*$', '', src)
                low = u.lower()
                k = None
                if low.endswith('_1080p.mp4') or low.endswith('_fullhd.mp4'):
                    k = '1080'
                elif low.endswith('_720p.mp4') or low.endswith('_hq.mp4'):
                    k = '720'
                elif low.endswith('_480p.mp4'):
                    k = '480'
                elif low.endswith('_240p.mp4'):
                    k = '240'
                elif low.endswith('_mobile.mp4'):
                    k = '270'
                elif low.endswith('.mp4'):
                    k = '360'
                if k and k not in out:
                    out[k] = u
        except Exception:
            pass
    if not out:
        # 正则兜底：新格式四档 + 老格式三档
        pats = [('240', r'_240p\.mp4'), ('480', r'_480p\.mp4'), ('720', r'_720p\.mp4'),
                ('1080', r'_1080p\.mp4'), ('270', r'_mobile\.mp4'), ('720', r'_hq\.mp4')]
        for k, pat in pats:
            if k in out:
                continue
            mm = re.search(r'(https?://[^"\'\s<>]+?)' + pat + r'(?:\?[^"\'\s<>]*)?', html)
            if mm:
                out[k] = re.sub(r'[?&]cb=[^&]*$', '', mm.group(0))
        if not out:
            # 默认 360p 档（无清晰度后缀）
            mm = re.search(r'(https?://voa-video-ns\.[^"\'\s<>]+?\.mp4)(?:\?[^"\'\s<>]*)?', html)
            if mm:
                out['360'] = re.sub(r'[?&]cb=[^&]*$', '', mm.group(1))
    return out


def parse_page(html):
    """提取多档 CDN URL、时长、日期、摘要、正文（文本+HTML 原文）"""
    out = {'q': {}, 'dur': '', 'date': '', 'summary': '', 'body': '', 'body_html': ''}
    out['q'] = q_urls(html)
    m = re.search(r'"duration"\s*:\s*"([^"]+)"', html)
    if m:
        out['dur'] = parse_iso_dur(m.group(1))
    m = re.search(r'<time[^>]*datetime="([^"]+)"', html)
    out['date'] = m.group(1)[:10] if m else ''
    m = re.search(r'<meta name="description" content="([^"]*)"', html)
    out['summary'] = m.group(1)[:300] if m else ''
    out['body'], out['body_html'] = extract_wsw(html)
    return out


class Sftp:
    def __init__(self):
        self.client = self.sftp = None

    def ensure(self):
        import paramiko
        try:
            if self.sftp:
                self.sftp.stat(REMOTE_APP)
                return True
        except Exception:
            pass
        for i in range(6):
            try:
                c = paramiko.SSHClient()
                c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                c.connect(HOST, username=USER, password=PWD, timeout=25,
                          banner_timeout=25, auth_timeout=25)
                self.client, self.sftp = c, c.open_sftp()
                return True
            except Exception as e:
                log('sftp retry %d %s' % (i + 1, str(e)[:60]))
                time.sleep(6)
        return False

    def mkdir_p(self, rd):
        cur = ''
        for p in rd.strip('/').split('/'):
            cur += '/' + p
            try:
                self.sftp.stat(cur)
            except Exception:
                self.sftp.mkdir(cur)

    def upload(self, local, remote):
        for a in range(8):
            if not self.ensure():
                time.sleep(8)
                continue
            try:
                self.mkdir_p(os.path.dirname(remote))
                self.sftp.put(local, remote)
                if self.sftp.stat(remote).st_size == os.path.getsize(local):
                    return True
            except Exception as e:
                log('up retry %d %s' % (a + 1, str(e)[:50]))
                try:
                    self.client.close()
                except Exception:
                    pass
                self.client = self.sftp = None
                time.sleep(6)
        return False


def sync_manifest(pool, pending):
    """锁内 upsert：重载磁盘最新基底 → 对每个节目合并 pending 条目 → 写回；锁外上传。
    pending: [(level, prog, entry), ...]"""
    with manifest_lock() as lok:
        if not lok:
            log('MANIFEST_LOCK_TIMEOUT')
            return False
        fresh = load_json(MP_PATH, None)
        if fresh is None:
            log('MANIFEST_LOAD_FAIL')
            return False
        added = updated = 0
        by_prog = {}
        for level, prog, entry in pending:
            by_prog.setdefault((level, prog), []).append(entry)
        try:
            for (level, prog), entries in by_prog.items():
                # 自动创建缺失的分组（如 video 下新增的节目）
                arr = fresh['categories']['video'].setdefault(level, {}).setdefault(prog, [])
                have = {e['id']: e for e in arr}
                for entry in entries:
                    eid = entry['id']
                    if eid in have:
                        # upsert：更新 q/dur，保留已有 a/sz/c 等字段
                        old = have[eid]
                        old['q'] = entry.get('q', old.get('q'))
                        if entry.get('dur'):
                            old['dur'] = entry['dur']
                        if not old.get('j') and entry.get('j'):
                            old['j'] = entry['j']
                        updated += 1
                    else:
                        arr.append(entry)
                        have[eid] = entry
                        added += 1
                arr.sort(key=lambda x: x['id'])
            fresh['version'] = datetime.datetime.now().strftime('%Y.%m.%d-%H%M')
            json.dump(fresh, open(MP_PATH, 'w', encoding='utf-8'),
                      ensure_ascii=False, separators=(',', ':'))
        except Exception as e:
            log('MANIFEST_STRUCT_FAIL %r' % (e,))
            return False
    if pool.upload(MP_PATH, REMOTE_APP + '/api/manifest.json'):
        log('MANIFEST_SYNCED %s (+%d 更新%d)' % (fresh['version'], added, updated))
        return True
    log('MANIFEST_UPLOAD_FAIL（稍后重试）')
    return False


def main():
    pool = Sftp()
    state = load_json(STATE_PATH, {'eps': {}})
    pending = []
    total = 0
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)

    # ★ 启动对账：state 已 done 且有 q 的集，manifest 对应条目缺 q 则重建补回（自愈）
    recon = 0
    for url, v in state['eps'].items():
        if not (v.get('done') and v.get('q') and v.get('eid')):
            continue
        zid = v.get('prog')
        if zid not in PROGS:
            continue
        prog_name, level, slug = PROGS[zid]
        e = {'id': v['eid'], 'q': v['q'], 'dur': v.get('dur') or '--:--', 'v': True,
             't': v.get('t', ''), 'd': v.get('d', ''), 'a': 'video/%s/%s.mp4' % (slug, v['eid'])}
        if v.get('j'):
            e['j'] = v['j']
        pending.append((level, prog_name, e))
        recon += 1
    if pending:
        log('启动对账：%d 条待校验/补回' % recon)
        if sync_manifest(pool, pending):
            pending = []

    for zid, (prog_name, level, slug) in PROGS.items():
        lpath = os.path.join(ZLIST, ('z%s.json' % zid[1:] if zid[0] == 'z' else 'p%s.json' % zid[1:]))
        arts = load_json(lpath, {}).get('articles', [])
        log('%s [%s] 共 %d 集，已完成 %d' % (prog_name, level, len(arts),
            sum(1 for k, v in state['eps'].items() if v.get('prog') == zid and v.get('done'))))
        for it in arts:
            url = it.get('url') or ''
            if not url:
                continue
            if url in state['eps'] and state['eps'][url].get('done'):
                continue
            list_title = (it.get('title') or '').strip()
            html = fetch_html(url)
            if not html:
                log('PAGE_FAIL %s' % url[-40:])
                continue
            info = parse_page(html)
            title = list_title or 'untitled'
            date_s = info['date'] or 'nodate'
            eid = '%s-%s' % (date_s, slugify(title))
            if not info['q']:
                log('NO_VIDEO %s' % title[:40])
                state['eps'][url] = {'done': True, 'no_video': True, 'prog': zid}
                json.dump(state, open(STATE_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
                continue
            # 正文 JSON（有正文才上传）
            j_ok = True
            if info['body']:
                jd = {'id': eid, 'title': title, 'date': info['date'], 'summary': info['summary'],
                      'program': prog_name, 'difficulty': level, 'type': 'video',
                      'words': len(info['body'].split()), 'content_md': info['body'],
                      'content_html': info['body_html'] or '', 'md': True}
                j_local = os.path.join(ART_ROOT, slug, eid + '.json')
                os.makedirs(os.path.dirname(j_local), exist_ok=True)
                json.dump(jd, open(j_local, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
                j_ok = pool.upload(j_local, '%s/articles/%s/%s.json' % (REMOTE_APP, slug, eid))
                if not j_ok:
                    log('UPLOAD_FAIL_JSON %s' % eid)
            j_path = 'articles/%s/%s.json' % (slug, eid) if info['body'] else ''
            entry = {'id': eid, 't': title, 'd': date_s, 'dur': info['dur'] or '--:--',
                     'v': True, 'q': info['q'],
                     'a': 'video/%s/%s.mp4' % (slug, eid)}   # VPS 兜底路径（由兜底流水线后续上传）
            if j_path:
                entry['j'] = j_path
            pending.append((level, prog_name, entry))
            state['eps'][url] = {'done': True, 'prog': zid, 'eid': eid, 'q': info['q'],
                                 't': title, 'd': date_s, 'dur': info['dur'] or '--:--', 'j': j_path}
            json.dump(state, open(STATE_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            total += 1
            log('OK %s [%s] %s' % (prog_name[:12], '/'.join(sorted(info['q'])), eid[:56]))
            if len(pending) >= SYNC_EVERY:
                if sync_manifest(pool, pending):
                    pending = []
        # 每个节目收尾同步
        if pending:
            if sync_manifest(pool, pending):
                pending = []
    # 最终收尾
    if pending:
        for _ in range(3):
            if sync_manifest(pool, pending):
                pending = []
                break
            time.sleep(10)
    log('DONE 共处理 %d 集' % total)


if __name__ == '__main__':
    main()
