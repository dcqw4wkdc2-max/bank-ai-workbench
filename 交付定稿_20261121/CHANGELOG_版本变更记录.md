# 版本变更记录（CHANGELOG）

基线：原作者开源仓库 `github.com/1708420870/bank-ai-workbench` v1.5.0（公开）。
本组在其上以补丁形式修复与增强，当前线上版本 **v1.5.4**。

---

## v1.5.4 — 登录架构与客户体验会话（2026-11-21）

- 新增 `POST /feedback/api/v1/guest_session`：服务端签发 **2 小时**访客令牌，绑定 `demo` 租户。
- 工单提交与查询**必须携带有效令牌**；原先"填任意手机号即视为已认证"的口子关闭，无/伪/过期一律 **401**。
- 对外入口 `19094 /` 改为正式风格**入口页**：客户体验 / 坐席工作台 / 管理控制台 三模块卡片。
- 内网登录页恢复为标准空白登录（无演示账号提示、无一键登录）；原有认证、单会话、连错 5 次锁定 300 秒全部保留。
- 页面响应统一 `Cache-Control: no-store`。
- 新增 `tests/test_guest_session.py` 4 项；`pytest -q` = **123 passed**。

## v1.5.3 — 规则优先于模型动作码（T10）

- `inner/rules.py` 第 ⑧ 条 + `inner/service.py` ⑩.5：转人工且命中强制类理由时，动作码收敛为 `HUMAN_REVIEW`，记 `ACTION_OVERRIDDEN_BY_RULE`。
- 新增 `tests/test_t10_rule_precedence.py` 4 项（含"放行场景不许被覆盖"反向断言）。

## v1.5.2 — 外发目录、多轮意图、上下文最小化

- `common/egress.py` 新增 `scan_tokenmap()`；`dmz/app.py` 增加 ①.5 内容复检与 `/dmz/api/v1/egress/attachment` 外发目录（命中 403 + 隔离 + 审计）。
- 客户通道多轮兜底：短句（嗯/对/继续）继承上一轮意图；跨工单按 `openid_hash` 取历史（`inner/db.py::fb_recent_by_openid`）。
- 新增 `tests/test_t03_egress_tokenmap.py`、`tests/test_t04_context_min.py`、`tests/test_fb_multi_turn.py`。

## v1.5.1 — 隐私判据、故障闭环、通道分流、模型端点兼容

- `inner/privacy.py` 新增 `judge()`：18 位证件号 → BLOCKED；note 内完整卡号 → BLOCKED；回拨＋手机号 → 转人工；
  调用点放在 `mask()` 之前（顺序反了永远判不到）。
- `inner/service.py`：`case.fault` 生效（timeout/bad_json → 失败关闭并转人工），不再出现"0.4 秒照常返回"。
- `inner/app.py`：客户通道按意图分流话术。
- `inner/agent.py`：补 `reasoning_effort: "none"`（OpenAI 兼容端点必需）、温度 0.4→0.2、max_tokens 600→320、提示词区分咨询与索赔。
- 新增 `tests/test_privacy_judge.py`、`tests/test_case_fault.py`、`tests/test_fb_reply_intent.py`。

## v1.5.0 — 原作者基线

三区架构（内网 / DMZ / 外网）、HMAC 签名与 TTL、出域字段白名单、MCP 工具目录 11 允许 6 拒绝、规则门禁与回滚、三区审计。
`pytest` 基线 90 passed；12 例验收基线 **8/12**（C 组 0/4）。

---

## 版本对照

| 指标 | v1.5.0（原作者） | v1.5.4（本组） |
|---|---|---|
| pytest | 90 passed | **123 passed** |
| 12 例验收 | 8/12（C 组 0/4） | **12/12** |
| 任务书 T01–T20 | 13 通过 / 4 部分 / 2 不符合 / 1 不适用 | **20 通过** |
| 坐席并发 | 5 并发打崩进程 | 5 / 10 并发 **100%** |
| 客户侧响应 | 10–18 秒 | **1–5 秒** |
| 外发目录（T03） | 无 | 有（403 + 隔离 + 审计） |
| 网络隔离（T12） | 逻辑模拟 | compose 三网络实证 |
| 客户身份 | 任意手机号即视为已认证 | 服务端签发访客令牌（2h，401 拦截） |

---

## 未纳入本组的改动

- 原作者仓库 `main` 分支未改动，所有修改提交在自己的 fork 分支并通过 PR #1 提交（30 文件，+4005/−57）。
- 生产级三项（真实核心系统对接、等保测评、mTLS 生产证书）不在范围内，未做。
