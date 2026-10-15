#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""VPS 端 HTTP CONNECT 代理（纯 socket 版）：token 校验 + 域名白名单
用法: python voa_proxy.py <port>
curl 用法: curl -x http://user:<token>@<vps_ip>:<port> <url>
"""
import socket
import select
import threading
import base64
import sys

TOKEN = 'voa2F9xQ1'
ALLOW = {
    'learningenglish.voanews.com',
    'av.voanews.com',
    'ga.voanews.com',
    'download.voanews.com',
    'media.voanews.com',
}
PORT = 8888


def log(msg):
    sys.stderr.write('[proxy] %s\n' % msg)
    sys.stderr.flush()


def handle(conn, addr):
    try:
        conn.settimeout(30)
        data = b''
        while b'\r\n\r\n' not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return
            data += chunk
            if len(data) > 65536:
                return
        head, _, _ = data.partition(b'\r\n\r\n')
        lines = head.split(b'\r\n')
        parts = lines[0].decode(errors='replace').strip().split(' ')
        if len(parts) < 3 or parts[0].upper() != 'CONNECT':
            conn.sendall(b'HTTP/1.1 405 Method Not Allowed\r\n\r\n')
            return
        hostport = parts[1]
        host, _, port = hostport.partition(':')
        try:
            port = int(port or 443)
        except ValueError:
            port = 443
        if host not in ALLOW:
            log('deny host %s' % host)
            conn.sendall(b'HTTP/1.1 403 Forbidden\r\n\r\n')
            return
        auth_ok = False
        expect = 'Basic ' + base64.b64encode(('user:' + TOKEN).encode('ascii')).decode('ascii')
        for line in lines[1:]:
            k, _, v = line.partition(b':')
            if k.strip().lower() == b'proxy-authorization':
                if v.strip().decode('ascii', 'replace') == expect:
                    auth_ok = True
        if not auth_ok:
            log('deny auth from %s' % addr[0])
            conn.sendall(b'HTTP/1.1 403 Forbidden\r\n\r\n')
            return
        try:
            up = socket.create_connection((host, port), timeout=20)
        except Exception as e:
            log('upstream fail %s %r' % (host, e))
            conn.sendall(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
            return
        conn.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        log('tunnel %s:%d from %s' % (host, port, addr[0]))
        conn.settimeout(None)
        up.settimeout(None)
        socks = [conn, up]

        def pump():
            try:
                while True:
                    r, _, _ = select.select(socks, [], [], 180)
                    if not r:
                        continue
                    for s in r:
                        try:
                            d = s.recv(65536)
                        except Exception:
                            d = b''
                        if not d:
                            return
                        other = up if s is conn else conn
                        try:
                            other.sendall(d)
                        except Exception:
                            return
            except Exception:
                pass
            finally:
                try:
                    up.close()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass

        t = threading.Thread(target=pump)
        t.daemon = True
        t.start()
        t.join()
    except Exception as e:
        log('handle err %r' % e)
        try:
            conn.close()
        except Exception:
            pass


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', PORT))
    srv.listen(64)
    log('proxy ready on 0.0.0.0:%d' % PORT)
    while True:
        c, addr = srv.accept()
        t = threading.Thread(target=handle, args=(c, addr))
        t.setDaemon(True)
        t.start()


if __name__ == '__main__':
    main()