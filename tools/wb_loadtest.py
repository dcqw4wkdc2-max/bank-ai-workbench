#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb_loadtest.py — 银行客服 AI 工作台 压测脚本（任务书 13.1 稳定性 / 14.2 运营指标）

用法：
  python wb_loadtest.py --base http://43.138.192.118:19092 --path customer --n 60 --c 10
  python wb_loadtest.py --base http://127.0.0.1:8101 --path case --n 20 --c 5

只发合成数据，不写任何真实客户信息。
输出：JSON 结果 + 控制台摘要（成功率、P50/P90/P99、错误分布、进程存活）。
"""
import argparse, json, random, string, statistics, sys, threading, time, urllib.error, urllib.request

LOCK = threading.Lock()
RESULT = {"ok": 0, "fail": 0, "lat": [], "errors": {}, "codes": {}, "records": []}

TEXTS = [
    "上月账单有一笔 39.9 元扣款我不认识，请问是什么费用？",
    "想对一笔 268 元消费提争议，需要准备什么材料？",
    "卡一直在身边，出现 3280 元陌生消费，我要退款。",
    "工资还没到账，帮我查一下交易状态。",
]


def post_json(url, payload, timeout=120):
    req = urllib.request.Request(url, method="POST",
                                 headers={"Content-Type": "application/json"},
                                 data=json.dumps(payload).encode())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def get_json(url, timeout=30):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def new_openid():
    return "oPoc-lt-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))


def one_customer(base, idx):
    t0 = time.time()
    try:
        st, body = post_json(base + "/feedback/api/v1/tickets",
                             {"openid": new_openid(), "category": "ACCOUNT",
                              "body": random.choice(TEXTS)})
        d = json.loads(body)
        fb = d.get("fb_id")
        return st, fb, time.time() - t0, None
    except urllib.error.HTTPError as e:
        return e.code, None, time.time() - t0, e.read(200).decode("utf-8", "replace")[:120]
    except Exception as e:
        return "ERR", None, time.time() - t0, str(e)[:120]


def one_case(base, idx):
    t0 = time.time()
    try:
        ck = LOGIN["ck"]
        req = urllib.request.Request(base + "/internal/api/v1/cases", method="POST",
                                     headers={"Content-Type": "application/json", "Cookie": ck},
                                     data=json.dumps({"case_id": random.choice(["A01", "A03", "B01", "B03"]),
                                                      "emp_id": "B001"}).encode())
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read().decode())
            return r.status, d.get("request_id"), time.time() - t0, None
    except urllib.error.HTTPError as e:
        return e.code, None, time.time() - t0, e.read(200).decode("utf-8", "replace")[:120]
    except Exception as e:
        return "ERR", None, time.time() - t0, str(e)[:120]


LOGIN = {}


def login_seat(base):
    import re
    req = urllib.request.Request(base + "/internal/api/v1/auth/login", method="POST",
                                 headers={"Content-Type": "application/json"},
                                 data=json.dumps({"username": "b001", "password": "abc122333"}).encode())
    with urllib.request.urlopen(req, timeout=15) as r:
        ck = r.headers.get("set-cookie", "")
    LOGIN["ck"] = re.match(r"(wb_sid=[^;]+)", ck).group(1)


def worker(fn, base, start_idx, per_thread, barrier):
    barrier.wait()
    for k in range(per_thread):
        st, ref, lat, err = fn(base, start_idx + k)
        with LOCK:
            RESULT["codes"][str(st)] = RESULT["codes"].get(str(st), 0) + 1
            if st == 200:
                RESULT["ok"] += 1
            else:
                RESULT["fail"] += 1
                if err:
                    key = err[:60]
                    RESULT["errors"][key] = RESULT["errors"].get(key, 0) + 1
            RESULT["lat"].append(lat)
            RESULT["records"].append({"status": st, "ref": ref, "sec": round(lat, 2)})


def alive(base):
    for path in ("/feedback/api/v1/_status", "/internal/api/v1/health", "/outer/api/v1/health", "/"):
        try:
            st, _ = get_json(base + path, timeout=10)
            if st in (200, 302, 404):
                return True, path, st
        except Exception:
            continue
    return False, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--path", choices=["customer", "case"], default="customer")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--c", type=int, default=8)
    ap.add_argument("--out", default="loadtest_result.json")
    a = ap.parse_args()

    base = a.base.rstrip("/")
    fn = one_customer if a.path == "customer" else one_case
    if a.path == "case":
        login_seat(base)

    print(f"[压测] base={base} path={a.path} n={a.n} concurrency={a.c}")
    ok0, p0, st0 = alive(base)
    print(f"[压测] 起跑前存活={ok0} ({p0} {st0})")

    per = max(1, a.n // a.c)
    threads, barrier = [], threading.Barrier(a.c)
    t_start = time.time()
    for i in range(a.c):
        t = threading.Thread(target=worker, args=(fn, base, i * per, per, barrier))
        t.start(); threads.append(t)
    for t in threads:
        t.join()
    dur = time.time() - t_start

    ok1, p1, st1 = alive(base)
    lat = sorted(RESULT["lat"])

    def pct(p):
        if not lat: return 0
        i = min(len(lat) - 1, int(round(p / 100 * len(lat))))
        return round(lat[i], 2)

    out = {
        "base": base, "path": a.path, "requests": RESULT["ok"] + RESULT["fail"],
        "concurrency": a.c, "ok": RESULT["ok"], "fail": RESULT["fail"],
        "success_rate": round(RESULT["ok"] / max(1, RESULT["ok"] + RESULT["fail"]) * 100, 1),
        "duration_sec": round(dur, 1), "rps": round((RESULT["ok"] + RESULT["fail"]) / max(0.1, dur), 2),
        "latency": {"min": round(lat[0], 2) if lat else 0, "p50": pct(50), "p90": pct(90),
                    "p99": pct(99), "max": round(lat[-1], 2) if lat else 0,
                    "avg": round(statistics.mean(lat), 2) if lat else 0},
        "status_codes": RESULT["codes"], "errors": RESULT["errors"],
        "alive_before": {"ok": ok0, "probe": p0, "status": st0},
        "alive_after": {"ok": ok1, "probe": p1, "status": st1},
        "sampled_records": RESULT["records"][:10],
    }
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "sampled_records"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
