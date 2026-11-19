#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""带模型启动 v2（key 从 .prov 读，不落命令行、不进日志）

用法：python run_model.py --port 18201 --zone inner
"""
import argparse, json, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18201)
    ap.add_argument("--zone", default="inner")
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()

    prov_path = os.path.join(BASE, ".prov")
    if os.path.exists(prov_path):
        p = json.load(open(prov_path, encoding="utf-8"))
        os.environ.setdefault("MODEL_BASE_URL", p.get("base", ""))
        os.environ.setdefault("MODEL_API_KEY", p.get("key", ""))
        os.environ.setdefault("MODEL_NAME", p.get("model", ""))
        os.environ.setdefault("MODEL_TIMEOUT", str(p.get("timeout", 30)))
        print("[run_model] 模型端点=%s 模型=%s key=%s…（已隐藏）" % (
            os.environ["MODEL_BASE_URL"], os.environ["MODEL_NAME"],
            (os.environ.get("MODEL_API_KEY") or "")[:6]))
    else:
        print("[run_model] 未找到 .prov，按规则模板模式启动")

    os.environ["BANK_ZONE"] = a.zone
    sys.path.insert(0, BASE)
    import uvicorn
    import app as app_module
    uvicorn.run(app_module.app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
