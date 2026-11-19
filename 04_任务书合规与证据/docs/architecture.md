# architecture.md — 银行客服 AI 工作台 架构说明（v1.5.0 PoC）

## 1. 形态
单进程三端口模拟三区（任务书 2 章 / 附录 B）。真实部署应替换为三段独立部署 + 银行批准的安全交换设施。

| 区 | 端口 | 职责 | 是否持原始数据 |
|---|---|---|---|
| 内网可信域 inner | 8101 | 员工工作台、客户数据、规则库、复原服务、审计 | 是 |
| 安全交换区 DMZ | 8102 | 字段白名单、内容复检、签名换发、MCP 受控入口 | 否（只过脱敏上下文） |
| 外网协同域 outer | 8103 | 客户反馈入口、模型调用出口、任务队列 | 否（只有令牌） |

## 2. 主链路（实测 A01）
```
坐席提交案例 → inner 取数（张示例/6222…0001）
→ 确定性脱敏（NAME_001/CARD_001/PHONE_001）
→ DMZ 出域扫描 + 签名换发
→ outer 调模型（partner-cloud/llm-b-2.1 或内网 Qwen3.5-9B-AWQ）
→ 内网规则裁决（R-DISPUTE-01/02）
→ 三元匹配复原（request_id ≡ session_id ≡ emp_id）
→ 工单落库 + 审计三区留痕
```

## 3. 组件清单（按任务书附录 A 对照）
| 任务书目录 | 本 PoC 对应 | 说明 |
|---|---|---|
| inner/staff-portal | `_internal/web/index.html` | 坐席工作台（含建议展示/采纳回执） |
| inner/inner-api | 进程内 uvicorn 路由 `/internal/api/v1/*` | 60 条路由，见 api-contracts.md |
| inner/privacy-service + restore-service | 进程内令牌化 + `POST /internal/api/v1/restore` | 复原需三元匹配 |
| inner/rule-service | `/internal/api/v1/rules`（8 条生效规则） | 含 mandatory_human 标志 |
| inner/audit-service | `/internal/api/v1/audit` | 三区事件合并查询 |
| dmz/exchange-gateway + content-inspection | 8102 的 `/dmz/api/v1/*`（7 条） | 唯一跨域通路 |
| dmz/mcp-gateway | `/dmz/api/v1/mcp/call`、`/mcp/catalog` | 11 白名单 / 6 拒绝 |
| outer/ai-workbench-api | 8103 的 `/feedback/api/v1/*`、`/outer/api/v1/*`（11 条） | 客户入口 + 模型出口 |
| mcp/tools | 11 个受控工具 | 见 mcp-tool-catalog.md |
| tests/ | 外部脚本 `tools/wb_retest.py` + 手工用例 | 后端编译，pytest 不在包内 |
| deploy/docker-compose | **缺**（交付件为编译产物，无源码无 compose） | 影响 T12 无法验证 |

## 4. 已知架构级缺口
1. 三区同进程不同线程 → 网络级隔离不成立（T12 不适用），仅"逻辑网关唯一通路"成立。
2. 客户通道（8103 → 客户页）未接复原服务 → 客户侧会看到令牌，已由前端兜底改中性话术（P0-01 降级处理）。
3. 后端为 PyInstaller 产物，无法新增降级状态机（T07/T08）。
