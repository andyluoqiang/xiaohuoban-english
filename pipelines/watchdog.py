# -*- coding: utf-8 -*-
"""网络探活守卫：learningenglish.voanews.com 恢复访问后自动启动编排器续抓；
运行期间若网络再次不可用则终止编排器并继续等待。"""
import subprocess, time, sys

URL = 'https://learningenglish.voanews.com/'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
HEADERS = ['-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
           '-H', 'Accept-Language: zh-CN,zh;q=0.9,en;q=0.8',
           '-H', 'Sec-Fetch-Dest: document',
           '-H', 'Sec-Fetch-Mode: navigate',
           '-H', 'Sec-Fetch-Site: none']
ORCHESTRATOR = r'D:\09coding\voa\.temp\scripts\orchestrator.py'


def probe():
    try:
        p = subprocess.run(
            ['curl.exe', '-s', '-o', 'NUL', '-w', '%{http_code}',
             '-L', '-x', 'http://127.0.0.1:10808', '-A', UA] + HEADERS +
            ['--max-time', '30', URL],
            capture_output=True, text=True, timeout=45)
        return p.stdout.strip() == '200'
    except Exception:
        return False


def kill_tree(pid):
    subprocess.run(['taskkill.exe', '/PID', str(pid), '/T', '/F'],
                   capture_output=True)


def main():
    wait = 0
    while True:
        if not probe():
            wait += 1
            print('[watchdog] offline round=%d retry in 10min' % wait, flush=True)
            time.sleep(600)
            continue
        print('[watchdog] ONLINE - starting orchestrator', flush=True)
        proc = subprocess.Popen([sys.executable, ORCHESTRATOR])
        while proc.poll() is None:
            time.sleep(300)
            if not probe():
                print('[watchdog] network lost - killing orchestrator', flush=True)
                kill_tree(proc.pid)
                break
        if proc.poll() is not None:
            rc = proc.returncode
            print('[watchdog] orchestrator exited rc=%s' % rc, flush=True)
            if rc == 0:
                return
        time.sleep(30)


if __name__ == '__main__':
    main()