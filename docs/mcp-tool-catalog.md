# mcp-tool-catalog.md — MCP 受控工具目录

取数：`GET http://127.0.0.1:8102/dmz/api/v1/mcp/catalog`（实测 2026-11-19）

## 1. 允许（11）
| 工具 | 用途 | 实测 |
|---|---|---|
| get_aggregate_metrics | 聚合指标 | 200 |
| get_approved_rule_summary | 已批规则摘要 | 200 |
| get_change_status | 变更状态 | 200 |
| get_masked_sample_case | 脱敏样例 | 200 |
| get_rule_metadata | 规则元数据 | 200 |
| list_rule_catalog | 规则目录 | 200（实测返回 R-DISPUTE-01 等） |
| list_synthetic_cases | 合成案例 | 200 |
| run_sandbox_test | 沙箱回归 | 200（12 例 PASS / 0 失败 / isolation=sandbox） |
| submit_rule_change_request | 提交规则草稿 | 200（developer 可用，supervisor 403） |
| submit_skill_draft | 提交 Skill 草稿 | 未逐一调用 |
| submit_workflow_draft | 提交 Workflow 草稿 | 未逐一调用 |

## 2. 拒绝（6，注册表内不存在）
get_raw_customer_record ｜ query_customer_account ｜ execute_sql ｜ export_full_customer_dataset ｜ update_production_rule ｜ execute_refund_or_freeze

## 3. 越权实测
| 调用 | 结果 |
|---|---|
| `get_raw_customer_record` | 403 策略拒绝：不在受控工具目录内 |
| `query_customer_account` | 403 |
| `execute_sql` | 403 |
| `update_production_rule`（developer） | 403 |
| `submit_rule_change_request`（supervisor） | 403「角色 supervisor 无权调用」 |

审计：DMZ 记 `MCP_DENIED / not_in_allowlist / severity=high`。

## 4. 角色权限矩阵（实测）
| 角色 | 只读工具 | 提交草稿 | 生产写 | 任意 SQL |
|---|---|---|---|---|
| developer (T001) | ✅ | ✅ | ❌ 403 | ❌ 403 |
| supervisor (A001) | ✅ | ❌ 403 | ❌ 403 | ❌ 403 |
| agent (B001) | 未开放 | ❌ | ❌ | ❌ |
