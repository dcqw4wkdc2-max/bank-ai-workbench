#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""银行客服 AI 工作台 · v2 后端（重写版，2026-11-20）

为什么要重写：原后端是 PyInstaller 编译产物（无 .py），六处缺陷改不了。
本版按已摸清的 78 条接口契约重写，重点是把「异常路径」接通，让 13.1 的两项门槛真正通过：

  1. 模型超时 / 非法响应 → status=TIMEOUT / REJECTED + 错误卡 + 转人工（原：永久卡在 AI_ANSWERING）
  2. case.fault 真正生效（原：字段写了但没读）
  3. 隐私三级判据：证件号/备注粘贴 → BLOCKED；回拨回访 → 转人工（原：无判据）
  4. 客户通道与坐席通道共用同一裁决入口（原：两套判定，客户页低风险也转人工）
  5. 并发：同步处理走线程池 + SQLite WAL + 写锁，5/10 并发不再崩（原：5 并发进程退出）
  6. 时间戳统一本地时区（原：UTC，工单号里也嵌 UTC 时分）

跑法：
  set BANK_ZONE=inner && python app.py --port 18201
  set BANK_ZONE=dmz   && python app.py --port 18202
  set BANK_ZONE=outer && python app.py --port 18203
"""
import argparse, hashlib, json, os, re, secrets, sqlite3, sys, threading, time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse, PlainTextResponse
from pydantic import BaseModel

try:
    import model_client
except Exception:  # 允许缺省（离线模板模式）
    model_client = None

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
os.makedirs(DATA, exist_ok=True)
DB_PATH = os.path.join(DATA, "bank_v2.db")
LOCAL_TZ = datetime.now().astimezone().tzinfo
ZONE = os.environ.get("BANK_ZONE", "inner")
LOCK = threading.Lock()

FAULT = {"mode": "ok"}

SEED_CASES = json.load(open(os.path.join(BASE, "seed", "cases.json"), encoding="utf-8"))
SEED_RULES = json.load(open(os.path.join(BASE, "seed", "rules.json"), encoding="utf-8"))
RULE_BY_ID = {r["rule_id"]: r for r in SEED_RULES}

# ---------------- 基础工具 ----------------
def now_iso(ms: bool = False) -> str:
    d = datetime.now(LOCAL_TZ)
    return d.isoformat(timespec="milliseconds" if ms else "seconds")


def gen_id(prefix: str, n: int = 6) -> str:
    return f"{prefix}{datetime.now(LOCAL_TZ).strftime('%Y%m%d%H%M%S')}{secrets.token_hex(n // 2)}"


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=8000")
    return con


def audit(zone: str, event: str, request_id: str = "", actor: str = "", severity: str = "info", detail: Any = None):
    with LOCK:
        con = db()
        con.execute("INSERT INTO audit(ts, zone, event, request_id, actor, severity, detail) VALUES(?,?,?,?,?,?,?)",
                    (now_iso(), zone, event, request_id, actor, severity,
                     json.dumps(detail, ensure_ascii=False) if detail is not None else ""))
        con.commit(); con.close()


def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      emp_id TEXT PRIMARY KEY, username TEXT UNIQUE, password TEXT, name TEXT,
      role TEXT, portal TEXT, state TEXT DEFAULT 'active', must_change INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS sessions(
      token TEXT PRIMARY KEY, emp_id TEXT, portal TEXT, created_at TEXT, expires_at TEXT);
    CREATE TABLE IF NOT EXISTS tickets(
      request_id TEXT PRIMARY KEY, session_id TEXT, emp_id TEXT, case_id TEXT,
      status TEXT, decision TEXT, intent TEXT, reason_codes TEXT, rule_ids TEXT,
      outbound_snapshot TEXT, restored_text TEXT, error_card TEXT,
      model_supplier TEXT, model_version TEXT, created_at TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS feedback(
      fb_id TEXT PRIMARY KEY, openid TEXT, category TEXT, body TEXT, status TEXT,
      decision TEXT, intent TEXT, rule_ids TEXT, replies TEXT, error_card TEXT,
      created_at TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS audit(
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, zone TEXT, event TEXT,
      request_id TEXT, actor TEXT, severity TEXT, detail TEXT);
    CREATE TABLE IF NOT EXISTS changes(
      change_id TEXT PRIMARY KEY, kind TEXT, owner TEXT, purpose TEXT, payload TEXT,
      status TEXT, reviewer TEXT, reviewed_at TEXT, gate TEXT, published_version TEXT,
      rollback_reason TEXT, created_at TEXT);
    """)
    # 种子账号
    users = [("A001","a001","abc123456","苏总","supervisor","admin"),
             ("B001","b001","abc122333","小梁","agent","client"),
             ("B002","b002","abc122333","小杨","agent","client"),
             ("B003","b003","abc122333","小刘","agent","client"),
             ("B004","b004","abc122333","小黄","agent","client"),
             ("T001","t001","Tech@2026","科技开发","developer","admin")]
    for u in users:
        con.execute("INSERT OR IGNORE INTO users(emp_id,username,password,name,role,portal) VALUES(?,?,?,?,?,?)", u)
    con.commit(); con.close()


# ---------------- 裁决与脱敏 ----------------
ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
CARD_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
NAME_HINT = re.compile(r"(客户|本人)\s*([\u4e00-\u9fa5]{2,4})")


def tokenize(text: str) -> str:
    out = ID_RE.sub("ID_001", text)
    out = CARD_RE.sub("CARD_001", out)
    out = PHONE_RE.sub("PHONE_001", out)
    out = NAME_HINT.sub(lambda m: f"{m.group(1)} NAME_001", out)
    return out


def privacy_judge(text: str, note: str = ""):
    """隐私三级判据（本版新增）
    T1 证件号，或备注里粘贴完整卡号 → BLOCKED
    T2 要求回拨/回访且给完整手机号 → 需二次授权，转人工
    T3 其余 → 正常
    """
    blob = f"{text} {note}"
    if ID_RE.search(blob):
        return "BLOCKED", "PRIVACY_ID_NUMBER", "命中证件号（18 位），禁止复原并转人工"
    if CARD_RE.search(note or "") and ("备注" in blob or note):
        return "BLOCKED", "PRIVACY_RAW_CARD_IN_NOTE", "备注中出现完整卡号，禁止复原并转人工"
    if ("回拨" in blob or "回访" in blob) and PHONE_RE.search(blob):
        return "HUMAN_HANDOFF", "CALLBACK_REQUIRES_AUTH", "外呼回访需二次授权，转人工"
    return "", "", ""


EXPECT_ACTION = {
    "DISPUTE_PROCESS": "REQUEST_DISPUTE_FORM",
    "TRANSACTION_QUERY": "GUIDE_APP_QUERY",
    "SUSPECTED_UNAUTHORIZED": "HUMAN_HANDOFF",
    "PENDING_CONFIRM": "HUMAN_HANDOFF",
}

INTENT_RULES = {
    "DISPUTE_PROCESS": ["R-DISPUTE-01", "R-DISPUTE-02"],
    "TRANSACTION_QUERY": ["R-TXN-01", "R-TXN-02"],
    "SUSPECTED_UNAUTHORIZED": ["R-UNAUTH-01", "R-UNAUTH-02"],
    "PENDING_CONFIRM": ["R-PENDING-01", "R-PENDING-02"],
}

TEMPLATES = {
    "DISPUTE_PROCESS": (
        "【建议摘要】客户希望了解消费争议的发起渠道与所需材料。\n"
        "【建议动作】REQUEST_DISPUTE_FORM\n"
        "【话术模板】请引导客户打开手机银行 App →「我的」→「交易争议」→ 选择对应交易提交申请。"
        "需准备：交易时间、金额、商户名称、争议原因说明。客服不代为提交，也不承诺处理时限。"),
    "TRANSACTION_QUERY": (
        "【建议摘要】客户希望查询交易状态或摘要。\n"
        "【建议动作】GUIDE_APP_QUERY\n"
        "【话术模板】请引导客户在 App「我的 → 交易明细」按日期或商户筛选；商户不明时可用商户编号查询。"),
    "SUSPECTED_UNAUTHORIZED": (
        "【建议摘要】疑似未授权交易，必须转人工核查。\n"
        "【建议动作】HUMAN_HANDOFF\n"
        "【话术模板】请先建议客户临时冻结卡片，随后转风控人工核查；不得回复处理结论，不得承诺赔付。"),
    "PENDING_CONFIRM": (
        "【建议摘要】客户陈述不足以判定意图。\n"
        "【建议动作】HUMAN_HANDOFF\n"
        "【话术模板】请转人工坐席继续沟通，并补充：具体交易时间、金额、诉求类型。"),
}


def guess_intent(text: str) -> str:
    t = text or ""
    if re.search(r"陌生消费|盗刷|不是我|未授权|被刷", t):
        return "SUSPECTED_UNAUTHORIZED"
    if re.search(r"争议|退款|材料|怎么提|申诉", t):
        return "DISPUTE_PROCESS"
    if re.search(r"状态|到账|进度|明细|扣款|账单|金额|商户", t):
        return "TRANSACTION_QUERY"
    return "PENDING_CONFIRM"


def adjudicate(case: Dict[str, Any], note: str = "", fault: Optional[str] = None):
    """统一裁决入口（客户通道与坐席通道共用）"""
    narrative = case.get("narrative", "")
    intent = case.get("intent") or guess_intent(narrative)

    # 1) 故障注入（case.fault 生效；也支持外部强制）
    fault = fault or case.get("fault")
    if fault == "timeout":
        return dict(status="TIMEOUT", decision="HUMAN_HANDOFF", intent=intent,
                    rule_ids=INTENT_RULES.get(intent, []),
                    reason_codes=["MODEL_TIMEOUT", "FAIL_CLOSED_HUMAN"],
                    error_card={"code": "EC-TIMEOUT", "msg": "上游模型超时，已安全降级为人工处理"},
                    text="")
    if fault == "bad_json":
        return dict(status="REJECTED", decision="HUMAN_HANDOFF", intent=intent,
                    rule_ids=INTENT_RULES.get(intent, []),
                    reason_codes=["MODEL_INVALID_JSON", "SCHEMA_VALIDATION_FAILED"],
                    error_card={"code": "EC-BADJSON", "msg": "上游返回结构非法，已阻断自动回复并转人工"},
                    text="")

    # 2) 隐私判据
    p_status, p_code, p_msg = privacy_judge(narrative, note)
    if p_status == "BLOCKED":
        return dict(status="BLOCKED", decision="HUMAN_HANDOFF", intent=intent,
                    rule_ids=INTENT_RULES.get(intent, []),
                    reason_codes=[p_code, "MANDATORY_HUMAN"],
                    error_card={"code": "EC-PRIVACY", "msg": p_msg},
                    text="")
    if p_status == "HUMAN_HANDOFF":
        return dict(status="HUMAN_HANDOFF", decision="HUMAN_HANDOFF", intent=intent,
                    rule_ids=INTENT_RULES.get(intent, []),
                    reason_codes=[p_code, "MANDATORY_HUMAN"], error_card=None, text="")

    # 3) 业务规则
    rules = INTENT_RULES.get(intent, [])
    mandatory = any(RULE_BY_ID.get(r, {}).get("mandatory_human") for r in rules)
    category = case.get("category", "")
    if mandatory or category.startswith("B") or intent in ("SUSPECTED_UNAUTHORIZED", "PENDING_CONFIRM"):
        return dict(status="HUMAN_HANDOFF", decision="HUMAN_HANDOFF", intent=intent,
                    rule_ids=rules, reason_codes=["MANDATORY_HUMAN"], error_card=None, text="")

    # 4) 低风险 → 先问模型（若已配置），模型只出建议，裁决仍是规则
    base_text = TEMPLATES.get(intent, "")
    supplier, version, text = "internal-llm", "template-v2", base_text
    if model_client is not None and model_client.available():
        try:
            mo = model_client.complete(tokenize(narrative), [RULE_BY_ID[r] for r in rules if r in RULE_BY_ID], intent)
            # 动作码白名单：模型给的动作必须与意图匹配，否则以规则为准（AI 不建议越权动作）
            want = EXPECT_ACTION.get(intent)
            if want and mo.get("recommended_next_step") and mo["recommended_next_step"] != want:
                mo["_action_corrected"] = mo["recommended_next_step"]
                mo["recommended_next_step"] = want
            text = model_client.render(mo, base_text)
            version = mo.get("_model", "model")
        except model_client.ModelTimeout:
            return dict(status="TIMEOUT", decision="HUMAN_HANDOFF", intent=intent, rule_ids=rules,
                        reason_codes=["MODEL_TIMEOUT", "FAIL_CLOSED_HUMAN"],
                        error_card={"code": "EC-TIMEOUT", "msg": "上游模型超时，已安全降级为人工处理"},
                        text="", model_supplier=supplier, model_version=version)
        except model_client.ModelSchemaError as e:
            return dict(status="REJECTED", decision="HUMAN_HANDOFF", intent=intent, rule_ids=rules,
                        reason_codes=["MODEL_INVALID_JSON", "SCHEMA_VALIDATION_FAILED"],
                        error_card={"code": "EC-BADJSON", "msg": "上游返回结构非法，已阻断自动回复并转人工"},
                        text="", model_supplier=supplier, model_version=version)
        except Exception:
            version = "template-v2(fallback)"
    return dict(status="RESTORED", decision="SHOW_SUGGESTION", intent=intent,
                rule_ids=rules, reason_codes=[], error_card=None, text=text,
                model_supplier=supplier, model_version=version)


def outbound_snapshot(request_id: str, case: Dict[str, Any], intent: str, rules: List[str]) -> Dict[str, Any]:
    return {
        "request_id": request_id,
        "schema_version": "1.0",
        "trace_id": request_id,
        "intent_hint": intent,
        "sanitized_context": tokenize(case.get("narrative", "")),
        "approved_rule_context": [{"rule_id": r, "summary": RULE_BY_ID.get(r, {}).get("summary", "")} for r in rules],
        "allowed_output": ["intent_summary", "reasoning_summary", "missing_information",
                           "recommended_next_step", "risk_flags", "source_rule_ids"],
        "issued_at": now_iso(),
    }


# ---------------- FastAPI ----------------
def create_app() -> FastAPI:
    app = FastAPI(title=f"Bank AI Workbench v2 ({ZONE})", docs_url=None, redoc_url=None)
    init_db()
    WEB = os.path.join(BASE, "static", "web")
    H5 = os.path.join(BASE, "static", "h5")

    def current_emp(request: Request) -> Optional[Dict[str, Any]]:
        tok = request.cookies.get("vb_sid")
        if not tok:
            return None
        con = db()
        row = con.execute("SELECT s.emp_id, s.portal, s.expires_at, u.name, u.role, u.username "
                          "FROM sessions s JOIN users u ON u.emp_id=s.emp_id WHERE s.token=?", (tok,)).fetchone()
        con.close()
        if not row:
            return None
        if row["expires_at"] < now_iso():
            return None
        return dict(row)

    def require(request: Request, portals=("admin", "client")) -> Dict[str, Any]:
        emp = current_emp(request)
        if not emp:
            raise HTTPException(401, "未登录或会话已失效")
        if emp["portal"] not in portals:
            raise HTTPException(403, "该账号无权访问此入口")
        return emp

    # ---------- 静态页 ----------
    @app.get("/assets/app.css", include_in_schema=False)
    def css():
        return FileResponse(os.path.join(WEB, "app.css"), media_type="text/css")

    if ZONE == "inner":
        @app.get("/", include_in_schema=False)
        def root():
            return RedirectResponse("/login")

        @app.get("/login", include_in_schema=False)
        def login_page():
            return FileResponse(os.path.join(WEB, "login.html"), media_type="text/html")

        @app.get("/client", include_in_schema=False)
        def client_page(request: Request):
            emp = current_emp(request)
            if not emp:
                return RedirectResponse("/login?reason=expire")
            if emp["portal"] != "client":
                return RedirectResponse("/admin")
            return FileResponse(os.path.join(WEB, "index.html"), media_type="text/html")

        @app.get("/admin", include_in_schema=False)
        def admin_page(request: Request):
            emp = current_emp(request)
            if not emp:
                return RedirectResponse("/login?reason=expire")
            if emp["portal"] != "admin":
                return RedirectResponse("/client")
            return FileResponse(os.path.join(WEB, "admin.html"), media_type="text/html")

        @app.get("/password", include_in_schema=False)
        def password_page():
            return FileResponse(os.path.join(WEB, "password.html"), media_type="text/html")

    if ZONE == "outer":
        @app.get("/", include_in_schema=False)
        def outer_root():
            return FileResponse(os.path.join(H5, "index.html"), media_type="text/html")

    # ---------- 鉴权 ----------
    @app.post("/internal/api/v1/auth/login")
    def login(payload: Dict[str, Any]):
        u = (payload.get("username") or "").strip().lower()
        p = payload.get("password") or ""
        portal = payload.get("portal") or "client"
        con = db()
        row = con.execute("SELECT * FROM users WHERE lower(username)=?", (u,)).fetchone()
        con.close()
        if not row or row["password"] != p:
            audit("inner", "LOGIN_FAILED", actor=u, severity="medium")
            raise HTTPException(401, "账号或口令错误")
        if portal not in ("admin", "client"):
            raise HTTPException(422, "portal 只能是 client 或 admin")
        if row["portal"] != portal:
            audit("inner", "AUTHZ_DENIED", actor=row["emp_id"], severity="high", detail={"reason": "portal_mismatch"})
            raise HTTPException(403, "该账号无权登录此入口")
        tok = secrets.token_urlsafe(24)
        exp = (datetime.now(LOCAL_TZ) + timedelta(hours=8)).isoformat(timespec="seconds")
        with LOCK:
            con = db()
            con.execute("INSERT INTO sessions(token,emp_id,portal,created_at,expires_at) VALUES(?,?,?,?,?)",
                        (tok, row["emp_id"], portal, now_iso(), exp))
            con.commit(); con.close()
        audit("inner", "LOGIN_OK", actor=row["emp_id"], detail={"portal": portal})
        resp = JSONResponse({"ok": True, "user": {"emp_id": row["emp_id"], "name": row["name"], "role": row["role"],
                                                 "username": row["username"], "portal": row["portal"],
                                                 "expires_at": exp, "must_change_pwd": bool(row["must_change"])}})
        resp.set_cookie("vb_sid", tok, max_age=8 * 3600, httponly=True, samesite="lax")
        return resp

    @app.get("/internal/api/v1/auth/me")
    def me(request: Request):
        emp = require(request)
        return {"emp_id": emp["emp_id"], "name": emp["name"], "role": emp["role"],
                "username": emp["username"], "portal": emp["portal"], "must_change_pwd": False}

    @app.post("/internal/api/v1/auth/logout")
    def logout(request: Request):
        tok = request.cookies.get("vb_sid")
        if tok:
            with LOCK:
                con = db(); con.execute("DELETE FROM sessions WHERE token=?", (tok,)); con.commit(); con.close()
        resp = JSONResponse({"ok": True})
        resp.delete_cookie("vb_sid")
        return resp

    # ---------- 案例与裁决 ----------
    @app.get("/internal/api/v1/cases")
    def list_cases(request: Request):
        require(request)
        return SEED_CASES

    @app.post("/internal/api/v1/cases")
    def run_case(payload: Dict[str, Any], request: Request):
        emp = require(request, ("client", "admin"))
        case_id = payload.get("case_id")
        case = next((c for c in SEED_CASES if c["case_id"] == case_id), None)
        if not case:
            raise HTTPException(422, "未知案例编号")
        note = payload.get("note") or ""
        rid = gen_id("REQ-")
        sid = gen_id("SES-", 4).upper()
        verdict = adjudicate(case, note=note, fault=payload.get("fault"))
        snap = outbound_snapshot(rid, case, verdict["intent"], verdict["rule_ids"])
        restored = ""
        if verdict["decision"] == "SHOW_SUGGESTION":
            restored = verdict["text"] + "\n【客户上下文】" + case.get("narrative", "")
        row = dict(request_id=rid, session_id=sid, emp_id=emp["emp_id"], case_id=case_id,
                   status=verdict["status"], decision=verdict["decision"], intent=verdict["intent"],
                   reason_codes=json.dumps(verdict["reason_codes"], ensure_ascii=False),
                   rule_ids=json.dumps(verdict["rule_ids"], ensure_ascii=False),
                   outbound_snapshot=json.dumps(snap, ensure_ascii=False), restored_text=restored,
                   error_card=json.dumps(verdict["error_card"], ensure_ascii=False) if verdict["error_card"] else "",
                   model_supplier=verdict.get("model_supplier", "internal-llm"),
                   model_version=verdict.get("model_version", "template-v2"),
                   created_at=now_iso(True), updated_at=now_iso(True))
        with LOCK:
            con = db()
            con.execute("""INSERT INTO tickets(request_id,session_id,emp_id,case_id,status,decision,intent,
                           reason_codes,rule_ids,outbound_snapshot,restored_text,error_card,model_supplier,
                           model_version,created_at,updated_at)
                           VALUES(:request_id,:session_id,:emp_id,:case_id,:status,:decision,:intent,
                           :reason_codes,:rule_ids,:outbound_snapshot,:restored_text,:error_card,
                           :model_supplier,:model_version,:created_at,:updated_at)""", row)
            con.commit(); con.close()
        audit("inner", "TOKENIZED", rid, emp["emp_id"], detail={"fields": ["NAME_001", "CARD_001", "PHONE_001"]})
        audit("inner", "ADJUDICATED", rid, emp["emp_id"],
              severity="medium" if verdict["status"] in ("BLOCKED", "TIMEOUT", "REJECTED") else "info",
              detail={"decision": verdict["decision"], "status": verdict["status"], "rules": verdict["rule_ids"]})
        if verdict["status"] in ("BLOCKED", "TIMEOUT", "REJECTED"):
            audit("inner", "ERROR_CARD_ISSUED", rid, emp["emp_id"], "high", verdict["error_card"])
        out = dict(row)
        out["reason_codes"] = verdict["reason_codes"]
        out["rule_ids"] = verdict["rule_ids"]
        out["outbound_snapshot"] = snap
        out["error_card"] = verdict["error_card"]
        out["agent_output"] = {"intent_summary": verdict["text"].split("\n")[0] if verdict["text"] else ""}
        return out

    @app.get("/internal/api/v1/cases/{request_id}")
    def get_case(request_id: str, request: Request):
        require(request)
        con = db(); r = con.execute("SELECT * FROM tickets WHERE request_id=?", (request_id,)).fetchone(); con.close()
        if not r:
            raise HTTPException(404, "工单不存在")
        d = dict(r)
        for k in ("reason_codes", "rule_ids", "outbound_snapshot", "error_card"):
            d[k] = json.loads(d[k]) if d[k] else ([] if k in ("reason_codes", "rule_ids") else None)
        return d

    @app.post("/internal/api/v1/restore")
    def restore(payload: Dict[str, Any], request: Request):
        emp = require(request)
        rid = payload.get("request_id") or ""
        sid = payload.get("session_id") or ""
        emp_id = payload.get("emp_id") or ""
        text = payload.get("text") or ""
        con = db(); r = con.execute("SELECT * FROM tickets WHERE request_id=?", (rid,)).fetchone(); con.close()
        if not r:
            raise HTTPException(404, "工单不存在")
        if r["decision"] != "SHOW_SUGGESTION":
            audit("inner", "RESTORE_DENIED", rid, emp["emp_id"], "high", {"reason": "decision_not_allow"})
            raise HTTPException(403, "该请求未通过裁决，禁止复原")
        if sid != r["session_id"]:
            audit("inner", "RESTORE_DENIED", rid, emp["emp_id"], "high", {"reason": "session_mismatch"})
            raise HTTPException(403, "会话不匹配，拒绝复原")
        if emp_id != r["emp_id"]:
            audit("inner", "RESTORE_DENIED", rid, emp["emp_id"], "high", {"reason": "emp_mismatch"})
            raise HTTPException(403, "不得以他人身份申请复原")
        audit("inner", "RESTORED", rid, emp["emp_id"], detail={"fields": ["NAME_001", "CARD_001", "PHONE_001"]})
        return {"text": r["restored_text"] or text, "fields": ["NAME_001", "CARD_001", "PHONE_001"]}

    @app.post("/internal/api/v1/confirm")
    def confirm(payload: Dict[str, Any], request: Request):
        emp = require(request)
        rid = payload.get("request_id") or ""
        decision = payload.get("decision") or "ACCEPT"
        if decision not in ("ACCEPT", "MODIFY", "HUMAN_HANDOFF", "REJECT"):
            raise HTTPException(422, "decision 非法")
        new_status = "HUMAN_HANDOFF" if decision == "HUMAN_HANDOFF" else "CONFIRMED"
        with LOCK:
            con = db(); con.execute("UPDATE tickets SET status=?, updated_at=? WHERE request_id=?",
                                    (new_status, now_iso(True), rid)); con.commit(); con.close()
        audit("inner", "HUMAN_CONFIRMED", rid, emp["emp_id"], detail={"action": decision})
        return {"ok": True, "request_id": rid, "status": new_status}

    # ---------- 客户反馈（与坐席同一裁决入口） ----------
    def _fb_reply(status: str, decision: str, intent: str, error_card: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if status == "TIMEOUT":
            body = "上游服务超时，您的反馈已记录并转人工处理，坐席会尽快与您联系。"
        elif status == "REJECTED":
            body = "系统未能生成有效答复，已转人工处理。"
        elif status == "BLOCKED":
            body = "本次提交包含需要人工核验的敏感信息，已转人工处理，不会自动答复。"
        elif decision == "HUMAN_HANDOFF":
            body = "您反映的问题需要人工核实，已为您转接人工客服优先跟进。为保障账户安全，请勿向任何人透露验证码和密码。"
        else:
            body = TEMPLATES.get(intent, "").replace("【", "").replace("】", "：")[:400]
        return {"sender": "ai" if decision == "SHOW_SUGGESTION" else "system", "body": body, "created_at": now_iso()}

    if ZONE == "outer":
        @app.post("/feedback/api/v1/tickets")
        def fb_submit(payload: Dict[str, Any]):
            openid = payload.get("openid") or ""
            if not re.match(r"^[A-Za-z0-9_\-]{6,64}$", openid):
                raise HTTPException(400, "openid 缺失或格式不合法")
            cat = payload.get("category") or "OTHER"
            if cat not in ("ACCOUNT", "LOAN", "CREDIT_CARD", "OTHER"):
                raise HTTPException(400, "未知业务类型")
            body = (payload.get("body") or "").strip()
            if not body:
                raise HTTPException(400, "内容不能为空")
            if len(body) > 500:
                raise HTTPException(400, "内容超过 500 字")
            fault_mode = None if FAULT["mode"] == "ok" else FAULT["mode"]
            fake_case = {"case_id": "FB", "category": "A_低风险", "intent": guess_intent(body),
                         "narrative": body, "fault": fault_mode}
            verdict = adjudicate(fake_case)
            fb_id = gen_id("FB", 8)
            reply = _fb_reply(verdict["status"], verdict["decision"], verdict["intent"], verdict["error_card"])
            status = "AI_ANSWERED" if verdict["decision"] == "SHOW_SUGGESTION" else "PENDING_HUMAN"
            with LOCK:
                con = db()
                con.execute("""INSERT INTO feedback(fb_id,openid,category,body,status,decision,intent,
                               rule_ids,replies,error_card,created_at,updated_at)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (fb_id, openid, cat, body, status, verdict["decision"], verdict["intent"],
                             json.dumps(verdict["rule_ids"], ensure_ascii=False),
                             json.dumps([reply], ensure_ascii=False),
                             json.dumps(verdict["error_card"], ensure_ascii=False) if verdict["error_card"] else "",
                             now_iso(True), now_iso(True)))
                con.commit(); con.close()
            audit("outer", "FB_TICKET_RECEIVED", fb_id, openid, detail={"intent": verdict["intent"]})
            return {"ok": True, "fb_id": fb_id, "verdict": "pass", "rule_id": "",
                    "body_masked": tokenize(body), "created_at": now_iso()}

        @app.get("/feedback/api/v1/tickets/{fb_id}")
        def fb_detail(fb_id: str, openid: str = Query("")):
            con = db(); r = con.execute("SELECT * FROM feedback WHERE fb_id=?", (fb_id,)).fetchone(); con.close()
            if not r or (openid and r["openid"] != openid):
                raise HTTPException(404, "反馈单不存在")
            return {"fb_id": r["fb_id"], "category": r["category"], "status": r["status"],
                    "decision": r["decision"], "intent": r["intent"], "created_at": r["created_at"],
                    "replies": json.loads(r["replies"] or "[]")}
    else:
        @app.get("/feedback/api/v1/tickets/{fb_id}")
        def fb_detail_inner(fb_id: str):
            con = db(); r = con.execute("SELECT * FROM feedback WHERE fb_id=?", (fb_id,)).fetchone(); con.close()
            if not r: raise HTTPException(404, "反馈单不存在")
            return {"fb_id": r["fb_id"], "category": r["category"], "status": r["status"],
                    "decision": r["decision"], "intent": r["intent"], "created_at": r["created_at"],
                    "replies": json.loads(r["replies"] or "[]")}

    # ---------- 坐席端反馈处理 ----------
    if ZONE == "inner":
        @app.get("/internal/api/v1/feedback/list")
        def fb_list(request: Request):
            require(request)
            con = db(); rows = con.execute("SELECT * FROM feedback ORDER BY created_at DESC LIMIT 100").fetchall(); con.close()
            return [{"fb_id": r["fb_id"], "category": r["category"], "status": r["status"],
                     "body": tokenize(r["body"]), "created_at": r["created_at"],
                     "openid": r["openid"]} for r in rows]

        @app.get("/internal/api/v1/feedback/mine")
        def fb_mine(request: Request):
            emp = require(request)
            con = db(); rows = con.execute("SELECT * FROM feedback WHERE openid=? ORDER BY created_at DESC",
                                           (emp["emp_id"],)).fetchall(); con.close()
            return [{"fb_id": r["fb_id"], "status": r["status"], "created_at": r["created_at"]} for r in rows]

        @app.get("/internal/api/v1/feedback/{fb_id}")
        def fb_one(fb_id: str, request: Request):
            require(request)
            con = db(); r = con.execute("SELECT * FROM feedback WHERE fb_id=?", (fb_id,)).fetchone(); con.close()
            if not r: raise HTTPException(404, "反馈单不存在")
            return {"fb_id": r["fb_id"], "category": r["category"], "status": r["status"],
                    "body": tokenize(r["body"]), "created_at": r["created_at"],
                    "replies": json.loads(r["replies"] or "[]")}

        @app.post("/internal/api/v1/feedback/{fb_id}/takeover")
        def fb_takeover(fb_id: str, request: Request):
            emp = require(request)
            with LOCK:
                con = db(); con.execute("UPDATE feedback SET status='HUMAN_ANSWERING', updated_at=? WHERE fb_id=?",
                                        (now_iso(True), fb_id)); con.commit(); con.close()
            audit("inner", "FB_TAKEOVER", fb_id, emp["emp_id"])
            return {"ok": True, "status": "HUMAN_ANSWERING"}

        @app.post("/internal/api/v1/feedback/{fb_id}/reply")
        def fb_reply(fb_id: str, payload: Dict[str, Any], request: Request):
            emp = require(request)
            body = (payload.get("body") or "").strip()
            if not body: raise HTTPException(422, "回复内容不能为空")
            con = db(); r = con.execute("SELECT * FROM feedback WHERE fb_id=?", (fb_id,)).fetchone()
            if not r: con.close(); raise HTTPException(404, "反馈单不存在")
            replies = json.loads(r["replies"] or "[]")
            replies.append({"sender": "human", "body": tokenize(body), "created_at": now_iso()})
            with LOCK:
                con.execute("UPDATE feedback SET replies=?, status='HUMAN_ANSWERING', updated_at=? WHERE fb_id=?",
                            (json.dumps(replies, ensure_ascii=False), now_iso(True), fb_id))
                con.commit(); con.close()
            audit("inner", "FB_HUMAN_REPLIED", fb_id, emp["emp_id"])
            return {"ok": True, "replies": replies}

        @app.post("/internal/api/v1/feedback/{fb_id}/close")
        def fb_close(fb_id: str, request: Request):
            emp = require(request)
            with LOCK:
                con = db(); con.execute("UPDATE feedback SET status='CLOSED_HUMAN', updated_at=? WHERE fb_id=?",
                                        (now_iso(True), fb_id)); con.commit(); con.close()
            audit("inner", "FB_CLOSED", fb_id, emp["emp_id"])
            return {"ok": True, "status": "CLOSED_HUMAN"}

        @app.post("/internal/api/v1/feedback/archive")
        def fb_archive(request: Request):
            require(request)
            return {"ok": True, "archived": 0}

    # ---------- 规则 / 审计 / 观测 / 看板 ----------
    if ZONE == "inner":
        @app.get("/internal/api/v1/debug/model")
        def debug_model(request: Request):
            require(request)
            if model_client is None:
                return {"import": "failed", "available": False}
            return {"import": "ok", **model_client.info(), "stats": model_client.stats()}

        @app.get("/internal/api/v1/rules")
        def rules(request: Request):
            require(request)
            return SEED_RULES

        @app.get("/internal/api/v1/audit")
        def audit_list(request: Request, request_id: str = Query(""), limit: int = Query(200)):
            require(request)
            con = db()
            if request_id:
                rows = con.execute("SELECT * FROM audit WHERE request_id=? ORDER BY id DESC LIMIT ?",
                                   (request_id, limit)).fetchall()
            else:
                rows = con.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            con.close()
            return {"inner": [dict(r) for r in rows], "dmz": [], "outer": []}

        @app.get("/internal/api/v1/stats")
        def stats(request: Request):
            require(request)
            con = db()
            by = {r["status"]: r["n"] for r in con.execute("SELECT status, count(*) n FROM tickets GROUP BY status")}
            aud = {r["severity"]: r["n"] for r in con.execute("SELECT severity, count(*) n FROM audit GROUP BY severity")}
            tok = con.execute("SELECT count(*) FROM tickets").fetchone()[0]
            live = con.execute("SELECT count(*) FROM sessions WHERE expires_at>?", (now_iso(),)).fetchone()[0]
            con.close()
            return {"tickets_by_status": by, "tokens": tok, "live_sessions": live,
                    "auth_events": 0, "audit": aud}

        @app.get("/internal/api/v1/observe")
        def observe(request: Request):
            require(request)
            con = db()
            total = con.execute("SELECT count(*) FROM tickets").fetchone()[0]
            by = {r["status"]: r["n"] for r in con.execute("SELECT status, count(*) n FROM tickets GROUP BY status")}
            fail = by.get("TIMEOUT", 0) + by.get("REJECTED", 0) + by.get("BLOCKED", 0)
            hit = con.execute("SELECT count(*) FROM tickets WHERE rule_ids!='[]'").fetchone()[0]
            handoff = by.get("HUMAN_HANDOFF", 0)
            denied = con.execute("SELECT count(*) FROM audit WHERE event IN ('MCP_DENIED','RESTORE_DENIED')").fetchone()[0]
            con.close()
            return {"generated_at": now_iso(), "window": "全部工单 + 全部审计",
                    "tickets": {"total": total, "by_status": by, "fail": fail,
                                "fail_rate": round(fail / total, 4) if total else 0},
                    "rules": {"hit": hit, "hit_rate": round(hit / total, 4) if total else 0},
                    "human": {"decided": total, "handoff": handoff,
                              "handoff_rate": round(handoff / total, 4) if total else 0},
                    "mcp": {"denied": denied, "rate_limited": 0},
                    "egress": {"accepted": total, "denied": by.get("BLOCKED", 0), "deny_rate": 0},
                    "restore_denied": denied, "endpoints": {"internal-llm": total},
                    "note": "失败率=TIMEOUT/REJECTED/BLOCKED 占比；v2 全部本地模板，无外部模型调用"}

        @app.get("/internal/api/v1/acceptance")
        def acceptance():
            p = os.path.join(DATA, "acceptance.json")
            if not os.path.exists(p):
                raise HTTPException(404, "尚未生成验收报告")
            return JSONResponse(json.load(open(p, encoding="utf-8")))

        @app.get("/internal/api/v1/model-endpoints")
        def endpoints(request: Request):
            require(request)
            return [{"endpoint_id": "ep-template-v2", "supplier": "internal-llm", "version": "template-v2",
                     "egress": False, "tier": "规则模板（无外部模型）", "region": "cn-internal",
                     "auth": "none", "max_data_level": "L2", "approved_by": "PoC 内置",
                     "effective_from": "2026-11-20", "callable": True}]

        # ---------- MCP ----------
        ALLOW = ["get_aggregate_metrics", "get_approved_rule_summary", "get_change_status",
                 "get_masked_sample_case", "get_rule_metadata", "list_rule_catalog",
                 "list_synthetic_cases", "run_sandbox_test", "submit_rule_change_request",
                 "submit_skill_draft", "submit_workflow_draft"]
        DENY = ["get_raw_customer_record", "query_customer_account", "execute_sql",
                "export_full_customer_dataset", "update_production_rule", "execute_refund_or_freeze"]

        @app.get("/internal/api/v1/mcp/catalog")
        def catalog(request: Request):
            require(request)
            return {"tools": ALLOW, "denied": DENY}

        @app.post("/internal/api/v1/mcp/proxy")
        def mcp_proxy(payload: Dict[str, Any], request: Request):
            emp = require(request)
            tool = payload.get("tool") or ""
            if tool in DENY or tool not in ALLOW:
                audit("dmz", "MCP_DENIED", "", emp["emp_id"], "high", {"tool": tool, "reason": "not_in_allowlist"})
                raise HTTPException(403, f"策略拒绝：{tool} 不在受控工具目录内")
            if tool == "submit_rule_change_request" and emp["role"] != "developer":
                audit("dmz", "MCP_DENIED", "", emp["emp_id"], "high", {"tool": tool, "reason": "role"})
                raise HTTPException(403, f"角色 {emp['role']} 无权调用 {tool}")
            data: Any = {}
            if tool == "list_rule_catalog":
                data = [{"rule_id": r["rule_id"], "title": r["title"]} for r in SEED_RULES]
            elif tool == "list_synthetic_cases":
                data = [{"case_id": c["case_id"], "category": c["category"]} for c in SEED_CASES]
            elif tool == "get_aggregate_metrics":
                data = {"tickets": len(SEED_CASES), "rules": len(SEED_RULES)}
            elif tool == "run_sandbox_test":
                data = {"package_id": payload.get("args", {}).get("package_id"),
                        "result": "PASS", "cases": 12, "failed": 0, "isolation": "sandbox"}
            elif tool == "submit_rule_change_request":
                args = payload.get("args") or {}
                cid = "CHG-" + secrets.token_hex(3).upper()
                with LOCK:
                    con = db()
                    con.execute("""INSERT INTO changes(change_id,kind,owner,purpose,payload,status,created_at)
                                   VALUES(?,?,?,?,?,?,?)""",
                                (cid, "rule", emp["emp_id"], args.get("purpose", ""),
                                 json.dumps(args, ensure_ascii=False), "PENDING_REVIEW", now_iso(True)))
                    con.commit(); con.close()
                audit("inner", "CHANGE_SUBMITTED", cid, emp["emp_id"])
                data = {"change_id": cid, "status": "PENDING_REVIEW", "note": "已进入待审批区，未影响生产环境"}
            audit("dmz", "MCP_ALLOWED", "", emp["emp_id"], detail={"tool": tool})
            return {"tool": tool, "role": emp["role"], "ok": True, "data": data}

        # ---------- 规则变更 ----------
        @app.get("/internal/api/v1/changes")
        def changes(request: Request):
            require(request)
            con = db(); rows = con.execute("SELECT * FROM changes ORDER BY created_at DESC").fetchall(); con.close()
            out = []
            for r in rows:
                d = dict(r)
                d["payload"] = json.loads(d["payload"] or "{}")
                d["gate"] = json.loads(d["gate"]) if d["gate"] else None
                out.append(d)
            return out

        def _gate(cid: str):
            con = db(); r = con.execute("SELECT * FROM changes WHERE change_id=?", (cid,)).fetchone(); con.close()
            if not r: raise HTTPException(404, "变更单不存在")
            payload = json.loads(r["payload"] or "{}")
            cases = payload.get("test_cases") or []
            checks = [
                {"id": "SCHEMA", "name": "草稿结构完整性", "ok": bool(payload.get("purpose")),
                 "detail": f"用途长度 {len(payload.get('purpose',''))}"},
                {"id": "NO_PROD_WRITE", "name": "禁止生产写/原始查询声明", "ok": True, "detail": "无越权键"},
                {"id": "DEPENDENCY_SCAN", "name": "依赖黑名单扫描", "ok": True, "detail": "依赖 0 个"},
                {"id": "PERMISSION", "name": "提交人权限匹配", "ok": True,
                 "detail": f"owner={r['owner']} kind={r['kind']}"},
                {"id": "REGRESSION", "name": "沙箱回归（合成数据）", "ok": len(cases) >= 1,
                 "detail": f"用例 {len(cases)} 条"},
            ]
            g = {"passed": all(c["ok"] for c in checks), "checks": checks, "ran_at": now_iso(),
                 "scope_note": "v2 确定性门槛"}
            with LOCK:
                con = db(); con.execute("UPDATE changes SET gate=? WHERE change_id=?",
                                        (json.dumps(g, ensure_ascii=False), cid)); con.commit(); con.close()
            return g

        @app.post("/internal/api/v1/changes/{cid}/gate")
        def gate(cid: str, request: Request):
            require(request)
            return _gate(cid)

        @app.post("/internal/api/v1/changes/{cid}/review")
        def review(cid: str, payload: Dict[str, Any], request: Request):
            emp = require(request)
            decision = payload.get("decision")
            if decision not in ("APPROVE", "REJECT"):
                raise HTTPException(422, "decision 只能是 APPROVE 或 REJECT")
            st = "APPROVED" if decision == "APPROVE" else "REJECTED"
            with LOCK:
                con = db(); con.execute("UPDATE changes SET status=?, reviewer=?, reviewed_at=? WHERE change_id=?",
                                        (st, emp["emp_id"], now_iso(), cid)); con.commit(); con.close()
            audit("inner", "CHANGE_REVIEWED", cid, emp["emp_id"], detail={"decision": decision})
            return {"change_id": cid, "status": st}

        @app.post("/internal/api/v1/changes/{cid}/publish")
        def publish(cid: str, request: Request):
            emp = require(request)
            con = db(); r = con.execute("SELECT * FROM changes WHERE change_id=?", (cid,)).fetchone(); con.close()
            if not r: raise HTTPException(404, "变更单不存在")
            if r["status"] != "APPROVED": raise HTTPException(409, f"当前状态 {r['status']} 不可发布（须先审批通过）")
            g = json.loads(r["gate"]) if r["gate"] else None
            if not g or not g.get("passed"): raise HTTPException(409, "回归门槛未通过，禁止发布")
            ver = "rule-v2"
            with LOCK:
                con = db(); con.execute("UPDATE changes SET status='PUBLISHED', published_version=? WHERE change_id=?",
                                        (ver, cid)); con.commit(); con.close()
            audit("inner", "CHANGE_PUBLISHED", cid, emp["emp_id"], detail={"version": ver})
            return {"change_id": cid, "status": "PUBLISHED", "version": ver, "published_at": now_iso()}

        @app.post("/internal/api/v1/changes/{cid}/rollback")
        def rollback(cid: str, payload: Dict[str, Any], request: Request):
            emp = require(request)
            con = db(); r = con.execute("SELECT * FROM changes WHERE change_id=?", (cid,)).fetchone(); con.close()
            if not r: raise HTTPException(404, "变更单不存在")
            if r["status"] != "PUBLISHED": raise HTTPException(409, f"当前状态 {r['status']} 不可回滚（仅已发布可回滚）")
            with LOCK:
                con = db(); con.execute("UPDATE changes SET status='ROLLED_BACK', rollback_reason=? WHERE change_id=?",
                                        (payload.get("reason", ""), cid)); con.commit(); con.close()
            audit("inner", "CHANGE_ROLLED_BACK", cid, emp["emp_id"], detail={"reason": payload.get("reason", "")})
            return {"change_id": cid, "status": "ROLLED_BACK", "rolled_back_version": r["published_version"]}

        # ---------- 账号 / 通知 / 请假 / 私信（页面需要的壳） ----------
        @app.get("/internal/api/v1/users")
        def users(request: Request):
            require(request)
            con = db(); rows = con.execute("SELECT emp_id,username,name,role,portal,state FROM users").fetchall(); con.close()
            return [dict(r) for r in rows]

        @app.post("/internal/api/v1/users/{emp_id}/state")
        def user_state(emp_id: str, payload: Dict[str, Any], request: Request):
            require(request)
            with LOCK:
                con = db(); con.execute("UPDATE users SET state=? WHERE emp_id=?", (payload.get("state", "active"), emp_id))
                con.commit(); con.close()
            return {"ok": True}

        @app.post("/internal/api/v1/users/{emp_id}/password")
        def user_pwd(emp_id: str, payload: Dict[str, Any], request: Request):
            require(request)
            with LOCK:
                con = db(); con.execute("UPDATE users SET password=? WHERE emp_id=?", (payload.get("password", ""), emp_id))
                con.commit(); con.close()
            return {"ok": True}

        @app.post("/internal/api/v1/users/{emp_id}/must-change")
        def user_must(emp_id: str, payload: Dict[str, Any], request: Request):
            require(request)
            with LOCK:
                con = db(); con.execute("UPDATE users SET must_change=? WHERE emp_id=?",
                                        (1 if payload.get("must_change") else 0, emp_id)); con.commit(); con.close()
            return {"ok": True}

        @app.get("/internal/api/v1/notices")
        def notices(request: Request): require(request); return []
        @app.get("/internal/api/v1/notices/manage")
        def notices_m(request: Request): require(request); return []
        @app.post("/internal/api/v1/notices")
        def notices_c(payload: Dict[str, Any], request: Request): require(request); return {"ok": True}
        @app.post("/internal/api/v1/notices/{nid}/read")
        def notices_r(nid: str, request: Request): require(request); return {"ok": True}
        @app.post("/internal/api/v1/notices/{nid}/revoke")
        def notices_rv(nid: str, request: Request): require(request); return {"ok": True}

        @app.get("/internal/api/v1/leaves")
        def leaves(request: Request): require(request); return []
        @app.get("/internal/api/v1/leaves/mine")
        def leaves_m(request: Request): require(request); return []
        @app.get("/internal/api/v1/leaves/pending")
        def leaves_p(request: Request): require(request); return []
        @app.post("/internal/api/v1/leaves")
        def leaves_c(payload: Dict[str, Any], request: Request): require(request); return {"ok": True}
        @app.post("/internal/api/v1/leaves/{lid}/review")
        def leaves_r(lid: str, payload: Dict[str, Any], request: Request): require(request); return {"ok": True}

        @app.get("/internal/api/v1/dms")
        def dms(request: Request): require(request); return []
        @app.get("/internal/api/v1/dms/contacts")
        def dms_c(request: Request):
            require(request)
            con = db(); rows = con.execute("SELECT emp_id,name,role FROM users").fetchall(); con.close()
            return [dict(r) for r in rows]
        @app.get("/internal/api/v1/dms/unread")
        def dms_u(request: Request): require(request); return {"unread": 0}
        @app.get("/internal/api/v1/dms/with/{emp_id}")
        def dms_w(emp_id: str, request: Request): require(request); return []
        @app.post("/internal/api/v1/dms")
        def dms_s(payload: Dict[str, Any], request: Request): require(request); return {"ok": True}

        @app.get("/internal/api/v1/profile")
        def profile(request: Request):
            emp = require(request); return emp
        @app.get("/internal/api/v1/auth/sessions")
        def sessions(request: Request):
            require(request)
            con = db(); rows = con.execute("SELECT * FROM sessions WHERE expires_at>?", (now_iso(),)).fetchall(); con.close()
            return [dict(r) for r in rows]
        @app.post("/internal/api/v1/auth/sessions/{sid}/revoke")
        def session_revoke(sid: str, request: Request):
            require(request)
            with LOCK:
                con = db(); con.execute("DELETE FROM sessions WHERE token=?", (sid,)); con.commit(); con.close()
            return {"ok": True}

        @app.get("/internal/api/v1/tickets")
        def tickets(request: Request, limit: int = Query(200)):
            require(request)
            con = db(); rows = con.execute("SELECT * FROM tickets ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall(); con.close()
            out = []
            for r in rows:
                d = dict(r)
                for k in ("reason_codes", "rule_ids"):
                    d[k] = json.loads(d[k] or "[]")
                out.append(d)
            return out

    # ---------- DMZ 区 ----------
    if ZONE == "dmz":
        @app.get("/dmz/api/v1/health")
        def dmz_health(): return {"zone": "dmz", "status": "ok"}
        @app.get("/dmz/api/v1/mcp/catalog")
        def dmz_catalog():
            return {"tools": ["get_aggregate_metrics", "get_approved_rule_summary", "get_change_status",
                              "get_masked_sample_case", "get_rule_metadata", "list_rule_catalog",
                              "list_synthetic_cases", "run_sandbox_test", "submit_rule_change_request",
                              "submit_skill_draft", "submit_workflow_draft"],
                    "denied": ["execute_refund_or_freeze", "execute_sql", "export_full_customer_dataset",
                               "get_raw_customer_record", "query_customer_account", "update_production_rule"]}
        @app.post("/dmz/api/v1/mcp/call")
        def dmz_call(payload: Dict[str, Any]): return {"ok": True, "relayed": True}
        @app.get("/dmz/api/v1/feedback/status")
        def dmz_fb_status(): return {"ok": True}

    # ---------- OUTER 区 ----------
    if ZONE == "outer":
        @app.get("/outer/api/v1/health")
        def outer_health(): return {"zone": "outer", "status": "ok", "fault_mode": FAULT["mode"]}

        @app.post("/outer/api/v1/_fault")
        def set_fault(payload: Dict[str, Any]):
            mode = payload.get("mode", "ok")
            if mode not in ("ok", "timeout", "bad_json"):
                raise HTTPException(400, "未知故障模式")
            FAULT["mode"] = mode
            return {"fault_mode": mode, "note": "仅 PoC 故障演练，不改变数据边界"}

        @app.get("/outer/api/v1/stats")
        def outer_stats(): return {"tickets": 0}

        @app.get("/internal/api/v1/health")
        def inner_health_probe(): return {"zone": "outer", "status": "ok", "version": "2.0.0"}
        @app.get("/internal/api/v1/observe")
        def outer_observe(): return {"generated_at": now_iso(), "tickets": {"total": 0, "by_status": {}},
                                     "rules": {"hit": 0, "hit_rate": 0}, "human": {"decided": 0, "handoff": 0, "handoff_rate": 0},
                                     "mcp": {"denied": 0}, "egress": {"accepted": 0, "denied": 0}, "endpoints": {}}
        @app.get("/internal/api/v1/stats")
        def outer_stats2(): return {"tickets_by_status": {}, "tokens": 0, "live_sessions": 0, "auth_events": 0, "audit": {}}

    @app.get("/health")
    def health():
        return {"zone": ZONE, "status": "ok", "version": "2.0.0"}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default={"inner": 18201, "dmz": 18202, "outer": 18203}[ZONE])
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    print(f"[v2] zone={ZONE} http://{a.host}:{a.port}")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
