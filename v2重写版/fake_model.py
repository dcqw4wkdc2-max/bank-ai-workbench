#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""假模型端点（仅用于验证 v2 的 fail-closed 分支，不产生任何费用）

用法：python fake_model.py --port 18500 --mode ok
      mode: ok（返回合法 JSON） / slow（睡 15 秒模拟超时） / bad（返回非 JSON）
"""
import argparse, json, time
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    mode = "ok"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if self.mode == "slow":
            time.sleep(15)
            body = json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()
        elif self.mode == "bad":
            body = json.dumps({"choices": [{"message": {"content": "抱歉我无法回答这个问题"}}]}).encode()
        else:
            content = json.dumps({
                "intent_summary": "客户希望了解消费争议的受理渠道与所需材料",
                "recommended_next_step": "REQUEST_DISPUTE_FORM",
                "missing_information": ["交易时间", "争议原因"],
                "risk_flags": [],
                "source_rule_ids": ["R-DISPUTE-01", "R-DISPUTE-02"],
            }, ensure_ascii=False)
            body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18500)
    ap.add_argument("--mode", default="ok")
    a = ap.parse_args()
    H.mode = a.mode
    print(f"[fake model] {a.mode} on {a.port}")
    HTTPServer(("127.0.0.1", a.port), H).serve_forever()
