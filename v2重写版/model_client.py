#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2 模型客户端（可插拔，OpenAI 兼容）

设计原则（跟任务书一致）：
- 模型只产出「建议摘要 / 建议动作 / 缺失信息」，**不产出裁决**；裁决仍由 rules 层做。
- 失败必须 fail-closed：超时 → ModelTimeout，结构非法 → ModelSchemaError，
  由调用方置 TIMEOUT / REJECTED 并转人工，绝不"猜一个答案继续"。

配置（环境变量）：
  MODEL_BASE_URL   例如 http://127.0.0.1:8000/v1
  MODEL_API_KEY
  MODEL_NAME       例如 Qwen3.5-9B-AWQ
  MODEL_TIMEOUT    秒，默认 8

未配置时 available() 为 False，系统自动用规则模板（离线可跑、零费用、零出域）。
"""
import json, os, re, time, urllib.error, urllib.request

CFG = {
    "base": (os.getenv("MODEL_BASE_URL") or "").rstrip("/"),
    "key": os.getenv("MODEL_API_KEY") or "",
    "model": os.getenv("MODEL_NAME") or "",
    "timeout": float(os.getenv("MODEL_TIMEOUT") or 8),
}

ALLOWED_OUTPUT = {"intent_summary", "recommended_next_step", "missing_information", "risk_flags", "source_rule_ids"}


class ModelTimeout(Exception):
    pass


class ModelSchemaError(Exception):
    pass


class ModelUnavailable(Exception):
    pass


def available() -> bool:
    return bool(CFG["base"] and CFG["model"])


def info() -> dict:
    return {"base": CFG["base"] or "(未配置)", "model": CFG["model"] or "(未配置)",
            "timeout": CFG["timeout"], "available": available()}


SYSTEM = (
    "你是银行客服的辅助建议器。只输出 JSON，不要解释。"
    "字段：intent_summary(≤60字)、recommended_next_step(动作码，如 REQUEST_DISPUTE_FORM / GUIDE_APP_QUERY)、"
    "missing_information(数组，最多3项)、risk_flags(数组)、source_rule_ids(数组，只能引用给定规则号)。"
    "你不做业务裁决，不给赔付口径，不承诺时限。"
)


def complete(sanitized_context: str, rules: list, intent_hint: str, timeout: float = None) -> dict:
    """返回 {"intent_summary","recommended_next_step","missing_information","risk_flags","source_rule_ids","_model"}"""
    if not available():
        raise ModelUnavailable("未配置模型端点")
    timeout = timeout or CFG["timeout"]
    payload = {
        "model": CFG["model"],
        "temperature": 0,
        "max_tokens": 400,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "意图提示：%s\n规则：%s\n脱敏上下文：%s" % (
                intent_hint,
                "; ".join("%s=%s" % (r.get("rule_id"), r.get("summary", "")) for r in rules) or "无",
                sanitized_context)},
        ],
    }
    req = urllib.request.Request(
        CFG["base"] + "/chat/completions", method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + (CFG["key"] or "none")},
        data=json.dumps(payload).encode())
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ModelUnavailable("模型端点拒绝访问（%d）" % e.code)
        raise ModelSchemaError("模型 HTTP %d" % e.code)
    except Exception as e:
        msg = str(e).lower()
        if "timed out" in msg or "timeout" in msg:
            raise ModelTimeout("模型超时（%.1fs）" % (time.time() - t0))
        raise ModelUnavailable("模型不可达：%s" % str(e)[:60])

    try:
        data = json.loads(raw)
        content = data["choices"][0]["message"]["content"]
    except Exception:
        raise ModelSchemaError("响应结构非法（缺 choices/message）")

    if "```" in content:
        content = re.sub(r"^```[a-zA-Z]*|```$", "", content.strip(), flags=re.M).strip()
    try:
        obj = json.loads(content)
    except Exception:
        raise ModelSchemaError("模型输出不是 JSON：%s" % content[:60])

    if not isinstance(obj, dict):
        raise ModelSchemaError("模型输出不是对象")
    allowed_rule_ids = {r.get("rule_id") for r in rules}
    cited = [x for x in (obj.get("source_rule_ids") or []) if x in allowed_rule_ids]
    out = {
        "intent_summary": str(obj.get("intent_summary") or "")[:160],
        "recommended_next_step": str(obj.get("recommended_next_step") or "")[:40],
        "missing_information": [str(x)[:40] for x in (obj.get("missing_information") or [])][:3],
        "risk_flags": [str(x)[:40] for x in (obj.get("risk_flags") or [])][:3],
        "source_rule_ids": cited,
        "_model": CFG["model"],
        "_latency": round(time.time() - t0, 2),
    }
    if not out["intent_summary"]:
        raise ModelSchemaError("缺少 intent_summary")
    return out


def render(model_out: dict, fallback_text: str = "") -> str:
    """把模型输出渲染成坐席端看到的建议文本（保留固定格式，便于前端解析）"""
    lines = ["【建议摘要】" + model_out["intent_summary"]]
    if model_out.get("recommended_next_step"):
        lines.append("【建议动作】" + model_out["recommended_next_step"])
    if model_out.get("missing_information"):
        lines.append("【待补充】" + "、".join(model_out["missing_information"]))
    if model_out.get("source_rule_ids"):
        lines.append("【引用规则】" + "、".join(model_out["source_rule_ids"]))
    if not lines[1:] and fallback_text:
        return fallback_text
    return "\n".join(lines)
