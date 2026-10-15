# -*- coding: utf-8 -*-
"""并行音频下载器：从已抓正文但缺音频的目录补齐 audio_hq.mp3
用法: python audio_downloader.py [parallel]
并行数默认 4。读取各节目目录下 meta.json 的 mp3 字段。"""
import subprocess, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

PROXY = 'http://127.0.0.1:10808'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
HEADERS = ['-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
           '-H', 'Accept-Language: zh-CN,zh;q=0.9,en;q=0.8']
AUDIO_ROOT = r'D:\09coding\voa\audio'

def download_one(args):
    url, dest = args
    # 前 3 次直连 CDN（快 22 倍），后 2 次回退代理
    for attempt in range(5):
        try:
            cmd = ['curl.exe', '-s', '-L', '-A', UA]
            if attempt >= 3:
                cmd += ['-x', PROXY]
            cmd += HEADERS + ['--max-time', '300', '-o', dest, '-w', '%{http_code}|%{size_download}', url]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=320)
            parts = (r.stdout or '').split('|')
            if len(parts) == 2 and parts[0] == '200' and int(parts[1]) > 50000:
                return True, dest
        except Exception:
            pass
        time.sleep(3 * (attempt + 1))
    # 清理不完整文件
    if os.path.exists(dest) and os.path.getsize(dest) < 50000:
        try:
            os.remove(dest)
        except Exception:
            pass
    return False, dest

def main():
    parallel = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    tasks = []
    for prog in os.listdir(AUDIO_ROOT):
        prog_dir = os.path.join(AUDIO_ROOT, prog)
        if not os.path.isdir(prog_dir):
            continue
        for d in os.listdir(prog_dir):
            folder = os.path.join(prog_dir, d)
            meta_path = os.path.join(folder, 'meta.json')
            dest = os.path.join(folder, 'audio_hq.mp3')
            if not os.path.exists(meta_path) or os.path.exists(dest):
                continue
            try:
                meta = json.load(open(meta_path, encoding='utf-8'))
            except Exception:
                continue
            url = meta.get('mp3')
            if url:
                tasks.append((url, dest))
    print('待下载缺音频篇目: %d, 并行=%d' % (len(tasks), parallel), flush=True)
    if not tasks:
        return
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        futs = {ex.submit(download_one, t): t for t in tasks}
        for i, fut in enumerate(as_completed(futs), 1):
            success, dest = fut.result()
            ok += 1 if success else 0
            fail += 0 if success else 1
            print('[%d/%d] %s %s' % (i, len(tasks), 'OK' if success else 'FAIL', os.path.basename(os.path.dirname(dest))[:50]), flush=True)
    print('DONE ok=%d fail=%d' % (ok, fail), flush=True)

if __name__ == '__main__':
    main()