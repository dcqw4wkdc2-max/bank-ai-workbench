# data-classification.md — 数据分级、脱敏与复原

## 1. 分级（按任务书 7.1 落地口径）
| 级别 | 内容 | 允许去往 | 本 PoC 实现 |
|---|---|---|---|
| L3 原始客户数据 | 姓名、卡号、证件号、手机号、交易明细 | 仅内网 8101 | inner.db，出域前必令牌化 |
| L2 脱敏上下文 | 令牌化文本 + 规则摘要 | DMZ / 外网模型（L2 端点） | sanitized_context |
| L1 聚合指标 | 计数、比率、分布 | 外网任意 | `/observe`、`/stats` |

## 2. 令牌规则（实测）
| 字段 | 令牌形态 | 样例 |
|---|---|---|
| 姓名 | `NAME_nnn` | NAME_001 |
| 卡号 | `CARD_nnn` | CARD_001 |
| 手机 | `PHONE_nnn` | PHONE_001 |
| 金额 | `<金额>.AMOUNT_nnn` | 39.9.AMOUNT_001 |
| 商户 | `MERCHANT_nnn` | MERCHANT_001 |

## 3. 出域白名单（A01 实测快照，共 8 字段）
`request_id / schema_version / trace_id / intent_hint / sanitized_context / approved_rule_context / allowed_output / issued_at`
**不含**姓名、卡号、手机、交易明细、客户 ID、商户原值。

## 4. 复原前置条件（实测）
三元匹配必须同时成立：`request_id` ≡ 当前会话 ≡ 申请人工号。
- 会话不匹配 → 403「会话不匹配，拒绝复原」
- 换人工号 → 403「不得以他人身份申请复原」
- 裁决为 HUMAN_HANDOFF 的请求 → 不进入复原（只给建议摘要）

## 5. 客户侧现状（重要）
8103 客户通道无复原能力 → 客户会看到 `NAME_001`。前端补丁（fix4/fix5）已把令牌替换为中性表述，实测令牌 0 命中；这是**降级**，不是复原。
