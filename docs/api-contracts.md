# api-contracts.md — 接口契约（实测版）

版本：v1.5.0　｜　实测时间：2026-11-19　｜　来源：三区 `openapi.json` 实拉取

## 1. 路由总量
| 区 | 基址 | 路由数 | 鉴权 |
|---|---|---|---|
| 内网 | http://127.0.0.1:8101 | 60 | Cookie `wb_sid`（JWT，Max-Age 28800） |
| DMZ | http://127.0.0.1:8102 | 7 | 无（仅本机可达） |
| 外网 | http://127.0.0.1:8103 | 11 | 客户侧 openid 参数 |

## 2. 鉴权
`POST /internal/api/v1/auth/login {username, password, portal}` → `portal ∈ {client, admin}`；返回 `Set-Cookie: wb_sid=...`
- 坐席账号进 admin → 403「该账号无权登录此入口」
- 管理员进 client → `portal_mismatch`

## 3. 核心接口（实测样例）
### 3.1 提交案例并裁决
`POST /internal/api/v1/cases {case_id, emp_id}`
```json
{"request_id":"REQ-20261119-1833AD","session_id":"SES-…","status":"RESTORED",
 "decision":"SHOW_SUGGESTION","intent":"DISPUTE_PROCESS",
 "rule_ids":["R-DISPUTE-01","R-DISPUTE-02"],"reason_codes":[],
 "outbound_snapshot":{ "...":"8 字段白名单" }, "restored_text":"…"}
```
### 3.2 复原
`POST /internal/api/v1/restore {request_id, session_id, emp_id, text}`
- 200 → `{"text":"【建议摘要】…","fields":[]}`
- 403 → 会话/身份不匹配
### 3.3 MCP 受控调用
`POST /internal/api/v1/mcp/proxy {tool, args}`；成功 200，越权 403「策略拒绝：<tool> 不在受控工具目录内」
### 3.4 规则变更
`POST /changes/{cid}/gate` → `{passed, checks[5]}`；`/review {decision:APPROVE|REJECT}`；`/publish`；`/rollback {reason}`
### 3.5 客户反馈
`POST http://…:8103/feedback/api/v1/tickets {openid, category, body}`；`category ∈ {ACCOUNT, LOAN, CREDIT_CARD, OTHER}`
### 3.6 故障注入
`POST http://…:8103/outer/api/v1/_fault {mode: ok|timeout|bad_json}`

## 4. 错误码约定
| 码 | 语义 |
|---|---|
| 400 | 参数语义错（未知业务类型 / 未知故障模式 / openid 格式） |
| 401 | 未登录或会话失效 |
| 403 | 越权（角色/入口/工具/复原三元不匹配） |
| 404 | 资源不存在（工单、验收报告未生成） |
| 409 | 状态机不允许（未审批就发布、未发布就回滚） |
| 422 | 字段校验失败（FastAPI 标准结构） |

## 5. 变更纪律
字段变更必须先改本契约再改实现（任务书 11.3）。本版由实测生成，非设计稿。
