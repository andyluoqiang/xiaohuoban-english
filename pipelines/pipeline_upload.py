# -*- coding: utf-8 -*-
"""流水线：扫描已抓取节目 → ffmpeg 压缩音频(64k单声道) → 生成文章JSON
→ SFTP 上传 VPS → 节目完成后更新 manifest 并上线。
循环运行，与抓取编排器并行工作，互不阻塞。
"""
import subprocess, json, os, re, sys, time, datetime
import msvcrt
from contextlib import contextmanager

FFMPEG = r'D:\08install\ffmpeg\ffmpeg.exe'
FFPROBE = r'D:\08install\ffmpeg\ffprobe.exe'
SRC_ROOT = r'D:\09coding\voa\audio'
SITE = r'D:\09coding\voa\site'
MP_PATH = os.path.join(SITE, 'api', 'manifest.json')
STATE_PATH = r'D:\09coding\voa\.temp\pipeline_state.json'
FSTATE = r'D:\09coding\voa\.temp\fstate'
HOST, USER, PWD = '23.94.61.235', 'root', os.environ.get('VPS_PWD', '')
REMOTE_APP = '/var/www/xiaohuoban/app'

# zid -> (节目名, 难度, slug)  ★ 难度按用户内容单 2026-10-15
PROGS = {
    '955': ('Health & Lifestyle', 'Intermediate', 'health-lifestyle'),
    '986': ('Arts & Culture', 'Intermediate', 'arts-culture'),
    '987': ('Words and Their Stories', 'Advanced', 'words-and-their-stories'),
    '1579': ('Science & Technology', 'Intermediate', 'science-technology'),
    '1581': ('American Stories', 'Advanced', 'american-stories'),
    '3521': ('As It Is', 'Intermediate', 'as-it-is'),
    '4456': ('Everyday Grammar', 'Advanced', 'everyday-grammar'),
    '5535': ('Ask a Teacher', 'Beginning', 'ask-a-teacher'),
    '7468': ('Education Tips', 'Advanced', 'education-tips'),
    '7773': ('Teach Us about Ukraine', 'Intermediate', 'teach-us-about-ukraine'),
    '1689': ('VOA Learning English Podcast', 'Advanced', 'voa-learning-english-podcast'),
    '5849': ("America's National Parks", 'Advanced', 'america-s-national-parks'),
    # 视频+音频节目（音频流同样处理）
    '5644': ("Let's Learn English - Level 1", 'Beginning', 'lets-learn-english-level-1'),
    '6765': ("Let's Learn English - Level 2", 'Beginning', 'lets-learn-english-level-2'),
    '7467': ('Early Literacy', 'Advanced', 'early-literacy'),
    '6766': ("America's Presidents", 'Advanced', 'america-s-presidents'),
    '6353': ('U.S. History', 'Advanced', 'us-s-history'),
}


def log(msg):
    print('[pipe] %s' % msg, flush=True)


def slugify(s):
    s = s.lower()
    s = re.sub(r'&', ' and ', s)
    s = re.sub(r'[^a-z0-9]+', '-', s)
    return re.sub(r'-+', '-', s).strip('-')


def mp_duration(path):
    try:
        r = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'csv=p=0', path], capture_output=True, text=True, timeout=30)
        sec = int(float(r.stdout.strip()))
        return '%d:%02d' % (sec // 60, sec % 60)
    except Exception:
        return '0:00'


def compress(src, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 20000:
        return True
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        r = subprocess.run([FFMPEG, '-y', '-i', src, '-ac', '1', '-ar', '44100',
                            '-b:a', '48k', dest], capture_output=True, timeout=180)
        return r.returncode == 0 and os.path.getsize(dest) > 20000
    except Exception:
        return False


# 噪声行（音频页头尾的下载/嵌入/分享区块，独占一行才剔除）
NOISE_LINE = re.compile(
    r'^\s*(Direct\s*link|'
    r'\d+\s*kbps\s*\|\s*MP3|'
    r'Pop-out\s*player|'
    r'Embed|share|'
    r'The\s*code\s*has\s*been\s*copied\s*to\s*your\s*clipboard\.?|'
    r'The\s*URL\s*has\s*been\s*copied\s*to\s*your\s*clipboard\.?|'
    r'No\s*media\s*source\s*currently\s*available|'
    r'Download\s+Lesson\s+\d+)\s*$', re.I)
# 独立成行的分隔线长串（连续下划线/横线等 ≥6 个），渲染为 md 分隔线
SEP_LINE = re.compile(r'^[\s_\u2010-\u2015\u00ad\u2500\u2501={6,}]{6,}$')
ENTITY = re.compile(r'&(?:quot|amp|lt|gt|nbsp|apos|#x27|#39|#160|#x2019|#x2013|#x2014);')


def build_content_md(article_txt):
    """从 article.txt 提取正文 markdown（去掉头部元信息和 ## Article 标题），
    并剔除噪声行（Direct link/kbps/Pop-out 等）与超长下划线串（防横向滚动），
    还原字面 HTML 实体（前端会正确转义）。"""
    txt = open(article_txt, encoding='utf-8').read()
    m = re.search(r'## Article\s*\n(.*)$', txt, re.S)
    if not m:
        return ''
    body = m.group(1).strip()
    lines = []
    for ln in body.split('\n'):
        if NOISE_LINE.match(ln):
            continue
        lines.append('---' if SEP_LINE.match(ln) else ln)
    body = '\n'.join(lines)
    body = re.sub(r'_{4,}', ' ', body)
    body = body.replace('\u00ad', '')
    if ENTITY.search(body):
        import html as H
        body = H.unescape(body)
    return body


def build_content_html(article_dir):
    """★ 从 article.html 读正文 HTML 原文（保留样式）；不存在则返回 ''"""
    p = os.path.join(article_dir, 'article.html')
    if os.path.exists(p):
        h = open(p, encoding='utf-8').read().strip()
        return h
    return ''


class SftpPool:
    def __init__(self):
        self.client = None
        self.sftp = None

    def _connect(self):
        import paramiko
        for i in range(6):
            try:
                c = paramiko.SSHClient()
                c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                c.connect(HOST, username=USER, password=PWD, timeout=25,
                          banner_timeout=25, auth_timeout=25)
                self.client, self.sftp = c, c.open_sftp()
                return True
            except Exception as e:
                log('sftp retry %d: %s' % (i + 1, str(e)[:70]))
                time.sleep(6)
        return False

    def ensure(self):
        try:
            if self.sftp:
                self.sftp.stat(REMOTE_APP)
                return True
        except Exception:
            pass
        return self._connect()

    def mkdir_p(self, remote_dir):
        parts = remote_dir.strip('/').split('/')
        cur = ''
        for p in parts:
            cur += '/' + p
            try:
                self.sftp.stat(cur)
            except Exception:
                self.sftp.mkdir(cur)

    def upload(self, local, remote):
        for attempt in range(8):
            if not self.ensure():
                time.sleep(8)
                continue
            try:
                self.mkdir_p(os.path.dirname(remote))
                self.sftp.put(local, remote)
                if self.sftp.stat(remote).st_size == os.path.getsize(local):
                    return True
            except Exception as e:
                log('upload retry %d %s: %s' % (attempt + 1, os.path.basename(remote), str(e)[:60]))
                try:
                    self.client.close()
                except Exception:
                    pass
                self.client = self.sftp = None
                time.sleep(6)
        return False

    def sync_manifest(self):
        """★ 原子上线 manifest：先传 .tmp 校验完整后，服务器 mv 原子替换。
        避免 sftp 直传大文件中断留下半截 JSON，导致线上读取/解析失败。"""
        remote = REMOTE_APP + '/api/manifest.json'
        tmp = remote + '.tmp'
        local = MP_PATH
        if not self.ensure():
            return False
        for attempt in range(8):
            try:
                self.mkdir_p('/var/www/xiaohuoban/app/api')
                self.sftp.put(local, tmp)
                if self.sftp.stat(tmp).st_size != os.path.getsize(local):
                    raise IOError('tmp size mismatch')
                _, out, err = self.client.exec_command('mv -f "%s" "%s"' % (tmp, remote))
                out.channel.recv_exit_status()
                return self.sftp.stat(remote).st_size == os.path.getsize(local)
            except Exception as e:
                log('SYNC retry %d: %s' % (attempt + 1, str(e)[:70]))
                try:
                    self.client.close()
                except Exception:
                    pass
                self.client = self.sftp = None
                time.sleep(8)
                if not self.ensure():
                    time.sleep(8)
        return False


def load_json(path, default):
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return default


@contextmanager
def manifest_lock(timeout=30):
    """★ Windows 文件锁：音频/视频两个流水线并发 read-modify-write
    同一个 manifest.json，即使写前重载仍有毫秒级窗口（已实际撞上丢条目）。
    用 .lock 文件 + msvcrt 锁住整个 load→merge→dump 区间，彻底原子化。"""
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


def eps_sort_key(x):
    """★ 排序：标题含 'Lesson N' 的节目（如 Let's Learn English）按 N 数字升序，
    避免按发布日期字符串排序把 Lesson 1 排到最后；其余退化按 id。"""
    t = x.get('t', '') or ''
    mm = re.search(r'Lesson\s+(\d+)', t)
    if mm:
        return (0, int(mm.group(1)), x.get('id', ''))
    if re.match(r'^Introducing', t):
        return (-1, 0, x.get('id', ''))
    return (1, 0, x.get('id', ''))


def merge_base(manifest):
    """★ 竞态修复：写回前从磁盘重载最新 manifest 做基底（保留视频流水线等其他
    写入者的更新），只覆盖本流水线负责的音频节目数组，再返回合并结果。
    两个流水线写的节目数组不相交，可彻底避免互相覆盖丢条目。
    注意：音频数组是同一 list 引用，后续 append 依然生效。"""
    fresh = load_json(MP_PATH, None)
    if fresh is None:
        log('MERGE_DIAG load_fail -> 用内存副本')
        return manifest
    # ★ 音频数组以权威 state（pipeline_state.json 的 done 条目）重建 ∪ 磁盘已有，
    # 自愈外部覆盖（如修复脚本/视频流水线改动造成音频条目丢失）；视频等其它部分保留磁盘最新。
    state = load_json(STATE_PATH, {'eps': {}})
    merged = 0
    for _zid, (prog_name, level, slug) in PROGS.items():
        try:
            arr = fresh['categories']['audio'][level][prog_name]
            have = {e['id']: e for e in arr}
            for eid, v in state['eps'].items():
                if v.get('done') and v.get('a') and v['a'].startswith('audio/%s/' % slug) and eid not in have:
                    have[eid] = {'id': eid, 't': v.get('t', ''), 'd': v.get('d', ''),
                                 'dur': v.get('dur', '0:00'), 'a': v['a'], 'j': v.get('j', ''),
                                 'sz': v.get('sz', 0)}
                    if v.get('c'):
                        have[eid]['c'] = v['c']   # ★ 保留 CDN 直链字段
            fresh['categories']['audio'][level][prog_name] = sorted(have.values(), key=eps_sort_key)
            merged += len(have)
        except Exception:
            pass
    try:
        n_v = len(fresh['categories']['video']['Beginning'].get('How to Pronounce', []))
    except Exception:
        n_v = -1
    log('MERGE_DIAG ok 磁盘HtP=%d 音频条目=%d' % (n_v, merged))
    return fresh


def main():
    state = load_json(STATE_PATH, {'eps': {}, 'manifest_done': {}})
    pool = SftpPool()
    stability = {}   # prog -> (n_eps, ts) 用于音频数稳定性检测
    dirty = False    # 本轮有新集上传，需同步 manifest

    while True:
        manifest = load_json(MP_PATH, None)
        if manifest is None:
            log('manifest 不可读，跳过本轮')
            time.sleep(30)
            continue
        dirty = False

        for zid, (prog_name, level, slug) in PROGS.items():
            prog_dir = os.path.join(SRC_ROOT, prog_name)
            if not os.path.isdir(prog_dir):
                continue
            if state['manifest_done'].get(prog_name):
                continue
            # 已在线上的 id（如 Ask a Teacher 45 集优化版）不覆盖
            try:
                existing_ids = {e['id'] for e in
                                manifest['categories']['audio'][level][prog_name]}
            except Exception:
                existing_ids = set()

            eps = []
            for d in os.listdir(prog_dir):
                folder = os.path.join(prog_dir, d)
                meta_p = os.path.join(folder, 'meta.json')
                if os.path.isdir(folder) and os.path.exists(meta_p):
                    eps.append(folder)
            if not eps:
                continue

            # 完成判定：fstate 有 FINISHED/PARTIAL，或篇数+音频数连续 45s 稳定
            fs_p = os.path.join(FSTATE, 'z%s.txt' % zid)
            fs_txt = open(fs_p, encoding='utf-8').read() if os.path.exists(fs_p) else ''
            finished = ('FINISHED' in fs_txt) or ('PARTIAL' in fs_txt)
            n_audio = sum(1 for f in eps if os.path.exists(os.path.join(f, 'audio_hq.mp3')))
            key = (len(eps), n_audio)
            if not finished:
                prev, ts = stability.get(prog_name, (None, 0))
                if prev == key and time.time() - ts >= 45:
                    finished = True   # 两轮扫描无变化，视为稳定
                elif prev != key:
                    stability[prog_name] = (key, time.time())

            new_eps = []
            processed_n = 0
            for folder in eps:
                meta = load_json(os.path.join(folder, 'meta.json'), {})
                title = meta.get('title', '')
                date_s = (meta.get('date') or '')[:10] or 'nodate'
                eid = ('%s-%s' % (date_s, slugify(title)))
                if eid in existing_ids:
                    processed_n += 1
                    continue
                if eid in state['eps'] and state['eps'][eid].get('done'):
                    processed_n += 1
                    continue
                src_mp3 = os.path.join(folder, 'audio_hq.mp3')
                if not os.path.exists(src_mp3):
                    if not finished:
                        continue          # 还在下载，等齐
                    state['eps'][eid] = {'done': False, 'no_audio': True}
                    continue              # 完成后仍无音频：跳过（日志见汇总）
                new_eps.append((eid, folder, meta, src_mp3))

            # 上传新篇
            prog_arr_ref = manifest['categories']['audio'][level][prog_name]
            ups_round = 0
            for eid, folder, meta, src_mp3 in new_eps:
                t0 = time.time()
                # ★ CDN 方案（2026-10-15 用户指示）：VPS 不再上传音频文件；
                # manifest 条目带 c（VOA 音频 CDN 直链），H5 优先 CDN 播放，仅本地保留全码率
                j_dest = os.path.join(SITE, 'articles', slug, eid + '.json')
                content_md = build_content_md(os.path.join(folder, 'article.txt'))
                jdata = {'id': eid, 'title': meta.get('title', ''), 'date': meta.get('date', ''),
                         'summary': meta.get('summary', ''), 'program': prog_name,
                         'difficulty': level, 'type': 'audio', 'words': len(content_md.split()),
                         'content_md': content_md,
                         'content_html': build_content_html(folder), 'md': True}
                os.makedirs(os.path.dirname(j_dest), exist_ok=True)
                json.dump(jdata, open(j_dest, 'w', encoding='utf-8'),
                          ensure_ascii=False, indent=1)
                rj = '%s/articles/%s/%s.json' % (REMOTE_APP, slug, eid)
                # 音频 CDN URL：meta 里 sources(新格式) 或 mp3(旧格式)
                srcs = meta.get('sources') or {}
                cdn = ''
                for k in ('128kbps', 'HQ', 'default', '64kbps'):
                    if k in srcs and srcs[k]:
                        cdn = srcs[k]
                        break
                if not cdn:
                    cdn = meta.get('mp3') or ''
                dur_s = mp_duration(src_mp3)
                if pool.upload(j_dest, rj):
                    state['eps'][eid] = {'done': True, 't': meta.get('title', ''),
                                         'd': (meta.get('date') or '')[:10],
                                         'dur': dur_s, 'c': cdn, 'sz': os.path.getsize(src_mp3),
                                         'a': 'audio/%s/%s.mp3' % (slug, eid),
                                         'j': 'articles/%s/%s.json' % (slug, eid)}
                    # 增量合入 manifest（本地立即生效）
                    entry = {'id': eid, 't': state['eps'][eid]['t'],
                             'd': state['eps'][eid]['d'], 'dur': dur_s,
                             'a': state['eps'][eid]['a'],
                             'j': state['eps'][eid]['j'], 'sz': state['eps'][eid]['sz']}
                    if cdn:
                        entry['c'] = cdn   # ★ 音频 CDN 直链
                    prog_arr_ref.append(entry)
                    dirty = True
                    json.dump(state, open(STATE_PATH, 'w', encoding='utf-8'),
                              ensure_ascii=False, indent=1)
                    processed_n += 1
                    ups_round += 1
                    log('UPS %s/%s %s [%.0fs] cdn=%s' % (slug, eid, dur_s,
                                                         time.time() - t0, 'Y' if cdn else 'N'))
                    # 每传 5 篇即同步线上 manifest，手机尽快可见
                    if ups_round % 5 == 0:
                        with manifest_lock() as lok:
                            if lok:
                                manifest = merge_base(manifest)
                                manifest['version'] = datetime.datetime.now().strftime('%Y.%m.%d-%H%M')
                                json.dump(manifest, open(MP_PATH, 'w', encoding='utf-8'),
                                          ensure_ascii=False, separators=(',', ':'))
                            else:
                                log('LOCK_TIMEOUT 本轮跳过同步')
                        if pool.upload(MP_PATH, REMOTE_APP + '/api/manifest.json'):
                            log('MANIFEST_SYNCED_MID %s (HL=%d)' %
                                (manifest['version'], len(prog_arr_ref)))
                else:
                    log('UPLOAD_FAIL %s' % eid)

            # 节目完成判定：所有有音频的篇已处理 → 排序、置完成标记
            ready_total = sum(1 for f in eps
                              if os.path.exists(os.path.join(f, 'audio_hq.mp3')))
            no_audio_n = sum(1 for e, v in state['eps'].items()
                             if not v.get('done') and v.get('no_audio'))
            if finished and processed_n + no_audio_n >= len(eps):
                # 排序并去重（现有 + 新传）
                seen = set()
                list_arr = []
                for e in sorted(prog_arr_ref, key=eps_sort_key):
                    if e['id'] not in seen:
                        seen.add(e['id'])
                        list_arr.append(e)
                manifest['categories']['audio'][level][prog_name] = list_arr
                dirty = True
                state['manifest_done'][prog_name] = True
                json.dump(state, open(STATE_PATH, 'w', encoding='utf-8'),
                          ensure_ascii=False, indent=1)
                log('PROGRAM_COMPLETE %s: %d 集 (+无音频跳过 %d)' %
                    (prog_name, len(list_arr), no_audio_n))
            elif new_eps or finished:
                log('%s: eps=%d audio=%d done=%d finished=%s' %
                    (prog_name, len(eps), ready_total,
                     sum(1 for e, v in state['eps'].items() if v.get('done')), finished))

        # 每轮末：如有增量，立即写入本地并上传线上 manifest
        if dirty:
            with manifest_lock() as lok:
                if lok:
                    manifest = merge_base(manifest)
                    manifest['version'] = datetime.datetime.now().strftime('%Y.%m.%d-%H%M')
                    now = datetime.datetime.now()
                    manifest['updatedAt'] = '%s+08:00' % now.strftime('%Y-%m-%dT%H:%M:%S')
                    json.dump(manifest, open(MP_PATH, 'w', encoding='utf-8'),
                              ensure_ascii=False, separators=(',', ':'))
                else:
                    log('LOCK_TIMEOUT 轮末跳过同步')
                    dirty = False
            if dirty and pool.sync_manifest():
                log('MANIFEST_SYNCED %s' % manifest['version'])
            elif dirty:
                log('MANIFEST_UPLOAD_FAIL（下轮重试）')

        time.sleep(30)


if __name__ == '__main__':
    main()