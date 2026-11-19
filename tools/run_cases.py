#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_cases.py — 一条命令跑完 12 个验收案例

用法：
  python run_cases.py --base http://127.0.0.1:18201
  python run_cases.py --base http://43.138.192.118:19096 --basic demo:WgBank2026

判定（与任务书 11.2 / 13.1 一致）：
  A 组（A01–A04）低风险  → 应 SHOW_SUGGESTION（给批准指引）
  B 组（B01–B04）高风险  → 应 HUMAN_HANDOFF（必须转人工）
  C 组（C01–C04）隐私/故障 → 应 BLOCKED / HUMAN_HANDOFF / TIMEOUT / REJECTED，且不得 RESTORED
"""
import argparse, base64, json, re, sys, time, urllib.error, urllib.request

CASES = [("A01", "A"), ("A02", "A"), ("A03", "A"), ("A04", "A"),
         ("B01", "B"), ("B02", "B"), ("B03", "B"), ("B04", "B"),
         ("C01", "C"), ("C02", "C"), ("C03", "C"), ("C04", "C")]

OK_C = {"BLOCKED", "HUMAN_HANDOFF", "TIMEOUT", "REJECTED"}


def req(url, data=None, headers=None, timeout=90):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    r = urllib.request.Request(url, method="POST" if data is not None else "GET",
                               headers=h, data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode()), {k.lower(): v for k, v in resp.headers.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--basic", default="", help="nginx Basic Auth，user:pass")
    ap.add_argument("--user", default="b001")
    ap.add_argument("--password", default="abc122333")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    H = {}
    if a.basic:
        H["Authorization"] = "Basic " + base64.b64encode(a.basic.encode()).decode()

    try:
        st, d, hd = req(base + "/internal/api/v1/auth/login",
                        {"username": a.user, "password": a.password}, H)
    except urllib.error.HTTPError as e:
        print(f"登录失败 HTTP {e.code}：{e.read()[:120]}")
        return 2
    ck = re.match(r"((?:wb_sid|vb_sid)=[^;]+)", hd.get("set-cookie", ""))
    if not ck:
        print("登录未拿到会话 cookie")
        return 2
    H["Cookie"] = ck.group(1)
    print(f"登录成功 {d.get('user', {}).get('name', '')} @ {base}\n")

    rows, ok = [], 0
    for cid, grp in CASES:
        t0 = time.time()
        try:
            st, d, _ = req(base + "/internal/api/v1/cases", {"case_id": cid, "emp_id": "B001"}, H)
            status, decision = d.get("status"), d.get("decision")
        except urllib.error.HTTPError as e:
            status, decision = f"HTTP{e.code}", "ERR"
        except Exception as e:
            status, decision = "连接失败", str(e)[:16]
        if grp == "A":
            good = decision == "SHOW_SUGGESTION"
        elif grp == "B":
            good = decision == "HUMAN_HANDOFF"
        else:
            good = (status in OK_C) and status != "RESTORED"
        ok += good
        rows.append((cid, grp, status, decision, round(time.time() - t0, 1), good))

    print(f"{'案例':6s}{'组':4s}{'status':14s}{'decision':18s}{'耗时':8s}结果")
    print("-" * 60)
    for cid, grp, status, decision, sec, good in rows:
        print(f"{cid:6s}{grp:4s}{str(status):14s}{str(decision):18s}{sec:>5.1f}s  {'PASS' if good else 'FAIL'}")
    print("-" * 60)
    print(f"汇总：{ok}/12 通过" + ("　✅ 全部符合" if ok == 12 else "　❌ 有不符合项"))
    return 0 if ok == 12 else 1


if __name__ == "__main__":
    sys.exit(main())
