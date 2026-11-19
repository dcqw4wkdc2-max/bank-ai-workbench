#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serial_proxy.py — 内网工作台自愈串行网关（解决坐席端并发崩溃）

背景：客户端 v1.5.0 后端单进程阻塞式，实测并发 5 会把进程打崩（错误卡 EC-004）。
本网关做三件事：
  1) 串行：全局锁，一次只放一个请求进后端 → 并发变排队
  2) 自愈：遇到"连接被拒/被重置"，等后端起来（最多 40 秒）再重试一次
  3) 可观测：GET /__proxy_stats 返回已服务数、最大排队时间、重试次数

用法：
  python serial_proxy.py                 # 8121 → 8101（坐席/管理）
  python serial_proxy.py --port 8122 --target-port 8103   # 客户区也可套一层

坐席/管理端改用 http://127.0.0.1:8121/login（页面与接口都由网关透传）。
"""
import argparse, http.server, json, socketserver, threading, time, urllib.error, urllib.request

LOCK = threading.Lock()
STATS = {"served": 0, "queued": 0, "retried": 0, "max_wait_sec": 0.0, "errors": 0}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    target = "http://127.0.0.1:8101"

    def log_message(self, fmt, *args):
        pass

    def _reply(self, code, payload, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        if headers:
            for k, v in headers.items():
                if k.lower() in ("content-type", "set-cookie", "location", "cache-control"):
                    self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except Exception:
            pass

    def _wait_backend(self, limit=40):
        end = time.time() + limit
        probe = self.target + "/internal/api/v1/health"
        while time.time() < end:
            try:
                with urllib.request.urlopen(probe, timeout=3) as r:
                    if r.status == 200:
                        return True
            except Exception:
                pass
            time.sleep(2)
        return False

    def _forward(self):
        if self.path.startswith("/__proxy_stats"):
            return self._reply(200, json.dumps(STATS).encode(), {"Content-Type": "application/json"})
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        hdrs = {k: v for k, v in self.headers.items()
                if k.lower() not in ("host", "connection", "accept-encoding", "content-length")}
        t0 = time.time()
        STATS["queued"] += 1
        with LOCK:
            STATS["queued"] -= 1
            STATS["max_wait_sec"] = round(max(STATS["max_wait_sec"], time.time() - t0), 2)
            for attempt in (1, 2):
                req = urllib.request.Request(self.target + self.path, data=body,
                                             headers=hdrs, method=self.command)
                try:
                    with urllib.request.urlopen(req, timeout=180) as r:
                        STATS["served"] += 1
                        return self._reply(r.status, r.read(), r.headers)
                except urllib.error.HTTPError as e:
                    STATS["served"] += 1
                    return self._reply(e.code, e.read(), e.headers)
                except Exception as e:
                    if attempt == 1 and self._wait_backend():
                        STATS["retried"] += 1
                        continue
                    STATS["errors"] += 1
                    msg = ("网关：后端连接失败（%s）。已尝试等待重启后重试一次。"
                           % str(e)[:60]).encode()
                    return self._reply(502, msg)
        return self._reply(500, b"unreachable")

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _forward


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8121)
    ap.add_argument("--target-port", type=int, default=8101)
    a = ap.parse_args()
    Handler.target = f"http://127.0.0.1:{a.target_port}"
    print(f"[自愈串行网关] 127.0.0.1:{a.port} → {Handler.target}（串行 + 崩溃等待重试）")
    Server(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
