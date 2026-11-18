"""银行客服 AI 工作台 · 自动复测脚本（P0/P1 重点项）
用法：python wb_retest.py [session]
前置：Chrome 打开任意页；Kimi WebBridge 守护进程在 10086 运行。
输出：控制台 + retest_result.json
"""
import json, sys, time, urllib.request

BASE = "https://wbapp-sc0lv5jbitw0ejwalvqcul.app.workbuddy.host"
API = "http://127.0.0.1:10086/command"
S = sys.argv[1] if len(sys.argv) > 1 else "rt"

def cmd(action, **args):
    body = json.dumps({"action": action, "args": args, "session": S}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": str(e)}

def ev(code):
    r = cmd("evaluate", code=code)
    try:
        return r["data"]["value"]
    except Exception:
        return None

def go(url):
    return cmd("navigate", url=url, newTab=False)

def fill(selector, value):
    return cmd("fill", selector=selector, value=value)

def click(selector):
    return cmd("click", selector=selector)

def text():
    v = ev("document.body.innerText")
    return v or ""

results = []

def record(tid, name, ok, detail):
    results.append({"id": tid, "name": name, "pass": bool(ok), "detail": detail[:300]})
    print(("PASS " if ok else "FAIL ") + tid + " " + name + " | " + detail[:160].replace("\n", " "))

def submit_feedback(content, biz="账户"):
    go(BASE + "/")
    time.sleep(3)
    ev(f"""(() => {{
      const b=[...document.querySelectorAll('button')].find(x=>x.innerText.trim()==='{biz}'); b&&b.click();
      const t=document.querySelector('textarea'); if(!t) return 'no textarea';
      const s=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;
      s.call(t,{json.dumps(content)}); t.dispatchEvent(new Event('input',{{bubbles:true}}));
      const subs=[...document.querySelectorAll('button')].filter(x=>x.innerText.trim()==='提交反馈');
      subs[subs.length-1].click(); return 'submitted';
    }})()""")

# ---------- T1 空内容校验（应拦截） ----------
go(BASE + "/"); time.sleep(3)
click("textarea")
ev("""(() => {const subs=[...document.querySelectorAll('button')].filter(x=>x.innerText.trim()==='提交反馈'); subs[subs.length-1].click(); return 1;})()""")
time.sleep(2)
t = text()
record("T01", "空内容提交应拦截", ("请填写" in t) or ("不能为空" in t) or ("请输入" in t), t[-120:])

# ---------- T2 超长校验（应提示） ----------
go(BASE + "/"); time.sleep(3)
ev("""(() => {const t=document.querySelector('textarea'); const s=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set; s.call(t,'测'.repeat(501)); t.dispatchEvent(new Event('input',{bubbles:true})); return t.value.length;})()""")
time.sleep(1)
ev("""(() => {const subs=[...document.querySelectorAll('button')].filter(x=>x.innerText.trim()==='提交反馈'); subs[subs.length-1].click(); return 1;})()""")
t_all = ""
for _ in range(10):
    time.sleep(1)
    t_all += "\n" + text()
    if ("提交失败" in t_all) or ("1-500" in t_all) or ("最多" in t_all):
        break
t = t_all
has_hint = ("提交失败" in t) or ("1-500" in t) or ("最多" in t) or ("上限" in t)
record("T02", "501字应提示上限", has_hint, t[-140:])

# ---------- T3 令牌复原（P0-01） ----------
submit_feedback("我是张示例，身份证110101199001011231，卡号6222000000000012，想问上月账单里一笔39.9元是什么扣款。")
time.sleep(18)
t = text()
import re
leak = re.findall(r"[A-Z]+_\d{3}", t)
record("T03", "客户页不得出现内部令牌(P0-01)", len(leak) == 0, ("命中:" + ",".join(sorted(set(leak))[:6])) if leak else "未发现令牌")

# ---------- T4 会话内发送（P0-02） ----------
before = ev("document.querySelectorAll('[class*=msg],[class*=bubble]').length") or 0
fill("textarea", "转人工")
time.sleep(1)
ev("""(() => {const b=[...document.querySelectorAll('button')].find(x=>x.innerText.trim()==='发送'); b&&b.click(); return 1;})()""")
time.sleep(8)
after = ev("document.querySelectorAll('[class*=msg],[class*=bubble]').length") or 0
t2 = text()
sent = (after > before) or ("转人工" in t2.split("智能客服")[-1] if "智能客服" in t2 else False)
record("T04", "会话内发送应有反应(P0-02)", sent, f"消息节点 {before}->{after}")

# ---------- T5 /staff 入口 ----------
go(BASE + "/staff?sharecode=M5FJV3GRVH"); time.sleep(3)
t = text()
record("T05", "/staff 入口应用可用", ("Not Found" not in t) and len(t.strip()) > 0, t[:100])

json.dump(results, open("retest_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
passed = sum(1 for r in results if r["pass"])
print(f"\n合计 {len(results)} 项，通过 {passed} 项，未通过 {len(results)-passed} 项 → retest_result.json")
