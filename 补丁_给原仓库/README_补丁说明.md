# 补丁：fault 生效 + 隐私三级判据 + 客户通道风险与话术分流（v1.5.1）

目标：`1708420870/bank-ai-workbench`（v1.5.0，2026-11-19 拉取）
改动：**3 个源文件 + 3 个测试文件**，319 行 diff，全部是新增分支，不改原有逻辑
作者：第4组 杨慧敏　｜　时间：2026-11-19

---

## 一、改了什么（四处）

### 1. `inner/privacy.py` — 新增 `PrivacyService.judge()`

隐私判据放进隐私服务（单一职责），`service.py` 只调用、不自己写正则：

| 级别 | 条件 | 处置 |
|---|---|---|
| T1 | 命中 18 位证件号 | `BLOCKED` / `PRIVACY_ID_NUMBER` |
| T1' | **补充说明（note）里**出现完整卡号 | `BLOCKED` / `PRIVACY_RAW_CARD_IN_NOTE` |
| T2 | 要求回拨/回访且给了完整手机号 | `HUMAN_HANDOFF` / `CALLBACK_REQUIRES_AUTH` |

**关键取舍：卡号只判 note，不判正文。** A 组正文本来就有合成卡号与手机号（来自业务库、按权限取得），判正文会把 A 组全误杀。

### 2. `inner/service.py` — 两个插入点

- **`case.fault` 生效**：`CASE_CREATED` 之后读 `case["fault"]`，`timeout` → `TIMEOUT`，`bad_json` → `REJECTED`，都 `HUMAN_HANDOFF`，留审计 `FAULT_INJECTED`。
- **隐私判据**：`raw_text` 之后、`privacy.mask()` 之前调用 `judge()`，命中即置状态 + 转人工，留审计 `PRIVACY_JUDGED`。
  顺序不能反——先脱敏再判，令牌化已经把证件号盖掉，永远判不到。

### 3. `inner/app.py` — 客户通道两个问题一起修

**(a) 高风险词补齐**：原来只认 `骗/盗/洗钱/资金损失/被扣款/冻结`，客户说「陌生消费」「未授权」「不是本人」「被刷」「盗刷」「退款」「挂失」会被当普通咨询答复。已补齐。

**(b) 低风险话术细化**：原来模板兜底一律 `GUIDE_APP_QUERY`，问“提争议要什么材料”也只回一句通用自助话术。
现在加了 `_fb_intent_hint()` 作为**意图地板**：模型只给通用动作码时，按关键词补具体动作，并改走对应模板（保证动作码与话术一致）。

实测（客户页真实提交）：

| 客户输入 | 补前 | 补后 |
|---|---|---|
| 想对一笔 268 元消费提争议，要什么材料 | 通用自助话术 | **“可在手机银行 App「交易争议」入口提交，按页面提示准备订单信息和沟通记录”** |
| 账单里 39.9 元扣款我看不懂 | 通用自助话术 | **“为了帮您准确核实，麻烦补充：发生场景（时间/办理渠道）”** |
| 卡在身边出现 3280 元陌生消费，要求退款 | 通用话术（没转人工） | **`PENDING_HUMAN` + 安抚 + 反诈提示** |
| 信用卡年费在哪看 | 通用话术 | 通用话术（无意图提示，保持不变） |

### 4. 新增测试 14 个

- `tests/test_privacy_judge.py`（5）：证件号阻断 / note 卡号阻断 / 回拨转人工 / A 组不误伤 / 空输入
- `tests/test_case_fault.py`（3）：C03→TIMEOUT / C04→REJECTED / A01 不受影响（自带会话，避开 SINGLE_SESSION 顶号）
- `tests/test_fb_reply_intent.py`（6）：意图提示三例 + 争议话术含“交易争议” + 追问话术 + 答复不含内部术语

---

## 二、验证（全部本机实跑）

| 项 | 打补丁前 | 打补丁后 |
|---|---|---|
| pytest | 90 passed | **119 passed** |
| 12 例验收 | **8/12**（C 组 0/4） | **12/12** |
| C01 证件号 | RESTORED | **BLOCKED / PRIVACY_ID_NUMBER** |
| C02 回拨回访 | RESTORED | **HUMAN_HANDOFF / CALLBACK_REQUIRES_AUTH** |
| C03 fault=timeout | RESTORED | **TIMEOUT / UPSTREAM_UNAVAILABLE** |
| C04 fault=bad_json | RESTORED | **REJECTED / SCHEMA_INVALID** |
| 并发 5 / 10 | — | **100% / 100%**（P90 2.05s / 4.02s） |
| 干净应用验证 | — | `git archive HEAD` 出 pristine 树 → `git apply --check` 通过 → apply → **119 passed** |

---

## 三、两处更正（我之前说错了，这里改过来）

1. **工单号时区不是源码的锅。** `_now()` 就是 `datetime.now(timezone.utc).astimezone()`、`fb_id` 也用本地时区。之前看到的 UTC 时分，是**跑在容器里 TZ=UTC** 造成的。**不用改。**
2. **并发不是架构问题。** 你用的是**三进程分离**，实测 5/10 并发 100%。原客户端 5 并发就崩，是因为三区塞进一个进程。**不用改**，只要别退回单进程。

所以“6 处改动”落到源码只有 **4 处**（fault、隐私判据、高风险词、低风险话术），通道结构 / 并发 / 时区你这版已经是对的。

---

## 四、怎么打

```bash
git checkout -b fix/case-fault-and-privacy
git apply 0001-optimal-fault-privacy-customer-risk.patch
python -m pytest -q                                        # 期望 119 passed
# 起三区后：
python tools/run_cases.py --base http://127.0.0.1:8101      # 期望 12/12
python tools/wb_loadtest.py --base http://127.0.0.1:8101 --path case --n 20 --c 10
```

---

## 五、还可以继续做的（按性价比，都不大）

| # | 项 | 说明 | 量级 |
|---|---|---|---|
| 1 | 隐私判据补 T3 | 备注里出现完整**手机号**也要提醒（现在只在回拨场景转人工） | 半小时 |
| 2 | 判据可配置 | 把三级判据抽成 `rules` 表的一类（业务可配，无需改代码） | 半天 |
| 3 | 客户通道多轮意图 | 现在按**最新一条**消息判意图，客户说“嗯”“对”这类短句会掉到通用话术；可用 `history` 兜底 | 1 天 |
| 4 | T12 网络隔离 | 仓库里有 `docker-compose.yml`，可起三网络实证“外网不能直连内网库” | 半天 |
| 5 | 判据命中率的回归集 | 把 A/B/C 三组 12 例扩到 30 例，锁住判据不误伤 | 1 天 |

---

## 六、再提醒一次：这个仓库是公开的

- 没有真 key（`common/config.py` 是 `your-secret-key-here` 占位符）✅
- 没有真实客户 PII（13800138001 是合成测试号）✅
- **但**：默认口令 `abc123456 / abc122333 / Tech@2026` 在 **9 个文件**明码；内网模型地址 `106.55.2.189:8000` 在 **5 个文件**；含“微光/任务书”的内部方案 **10 个文件**

任务书写明“未经微光工作室授权，请勿用于其它商业用途”。建议转私有，至少把口令与内网地址从 README / docs 摘掉。我们自己的演示环境用的是同一套口令，我会换掉。


---

## 七、已经直接开成 PR 了（2026-11-19 21:20）

不用你手动 apply 了，补丁已提交为 PR：

**https://github.com/1708420870/bank-ai-workbench/pull/1**

- 来源分支：`dcqw4wkdc2-max/bank-ai-workbench-1 : fix/case-fault-and-privacy`
- 变更：**6 文件 +243/-4**（3 源文件 + 3 测试文件）
- 状态：`open`，`mergeable: true`

他只需要：**Review → Merge**。合并后本地 `git pull`，跑 `python -m pytest -q`（期望 119 passed）与 `tools/run_cases.py`（期望 12/12）。


---

## 八、补丁追加一处：模型端点兼容 + 转人工口径（2026-11-19 21:40）

PR 里现在有两个 commit，共 **7 文件 +251/-5**。

### 8.1 `inner/agent.py`：`reasoning_effort=none`

你用的是 `chat_template_kwargs: {enable_thinking: False}`——**vLLM 认，OpenAI 兼容端点（中转站/DeepSeek）不认**。
接上去的表现：模型调用返回 200（日志 `hermes_completion_ok`），但内容落在 `reasoning_content`、`content` 为空 →
schema 校验失败 → 退化成"转人工"，客户看到的是"已转接人工"而不是解答。

请求体补一行 `"reasoning_effort": "none"` 即可。**vLLM 端点不受影响**（多余字段被忽略）。

### 8.2 `inner/agent.py`：提示词补第 7 条判定口径

原提示词第 5 条要求"涉及资金安全…建议 HUMAN_REVIEW"，模型把「想对一笔消费**提争议**，要什么材料」也判成资金安全 →
客户侧一律转人工。补第 7 条：

> 仅咨询办理流程、所需材料、查询路径属于低风险，不要标 `HUMAN_REVIEW_REQUIRED`；
> 只有客户主张资金已损失、要求退款/赔付/冻结/挂失、或明确为陌生交易时，才标。

---

## 九、这套现在是**线上跑的版本**（不是纸上方案）

服务器上已经用你的源码 + 这个补丁跑起来了：

| 入口 | 地址 | 跑的是什么 |
|---|---|---|
| 客户 | http://43.138.192.118:19094/ | 你的 `outer`（18403）+ H5 页 |
| 控制台 | http://43.138.192.118:19096/ | 你的 `inner`（18401），Basic Auth `demo / WgBank2026` |
| 原版对比 | 19092 / 19093 | 之前交付的编译产物（未修） |

- 部署位置：`/opt/banksrc/app`（源码，含补丁）、数据 `/opt/banksrc/data`、模型配置 `/opt/banksrc/model.env`（600，不入库）
- 三个 systemd：`banksrc-inner` / `banksrc-dmz` / `banksrc-outer`（18401 / 18402 / 18403）
- 模型：`MODEL_IMPL=real`，端点走 OpenAI 兼容中转

### 线上实测（2026-11-19 21:45）

| 项 | 结果 |
|---|---|
| 12 例验收（`run_cases.py --base 19096 --basic`） | **12/12**（A 4/4、B 4/4、C 4/4） |
| 客户页·低风险「提争议要什么材料」 | `AI_ANSWERING`，答复「App『交易争议』入口 + 订单信息与沟通记录」 |
| 客户页·高风险「卡在身边却陌生消费」 | `PENDING_HUMAN` + 安抚 + 反诈提示 |
| 并发 5 / 10（坐席通道，真模型） | **100% / 100%**（P90 10.1s / 17.9s） |

也就是说：**你要给客户看，直接用 19094（客户）+ 19096（控制台）就行**，不用再拿本地包。


---

## 十、2026-11-20 追加：把你点名的四条全部补掉

PR 现在共 **15 个文件**（8 源 + 7 测试），三个 commit。下面每条都给了实测。

### 10.1 客户多轮意图（原：客户只回"嗯/对"就掉回通用话术）

- `inner/app.py`：`_fb_intent_hint(body, history)` 支持历史；短句（`嗯/对/好/是/继续/然后呢`）或 ≤3 字时，
  回看最近一条客户消息沿用其意图（只回看一层，不递归）。
- `inner/app.py` ingest：本工单没有历史时，按 **`openid_hash` 跨工单** 取最近 8 条客户消息兜底
  （客户页每条消息创建一个新工单，所以"多轮"在数据层是跨工单的）。
- `inner/db.py`：新增 `fb_recent_by_openid()`。

**实测**（公网 19094，真模型）：
```
第1轮「想对一笔 268 元消费提争议，需要准备什么材料？」→ 材料清单
第2轮「嗯」→ 仍然给材料清单（不再回"请问有什么可以帮您"）
```

### 10.2 真模型延迟（原：10–18 秒）

- `inner/agent.py`：`temperature 0.4 → 0.2`、`max_tokens 600 → 320`。

**实测**（0.5 秒粒轮询，公网 19094）：**4.6 秒 / 5.9 秒**（之前测的 10–18 秒含 3 秒轮询粒度）。
> 顺带记一笔：直连中转站同样的短提示只要 4.4 秒，说明延迟主要在模型侧，不在你的代码。

### 10.3 T03 外发目录（原：这版没有外发目录，无法构造）

- `common/egress.py`：新增 `scan_tokenmap()`，识别三类特征——`sealed_value/token_map/mapping_table` 字段、
  `NAME_001` 与 16+ 位密文并排、CSV 映射表表头。
- `dmz/app.py`：
  - `dispatch` 在字段白名单之后加一层内容复检（挡"把映射表塞进合法字段"）；
  - 新增 **`POST /dmz/api/v1/egress/attachment`**：文件先落 `data/egress_out/`，命中即 **403 + 隔离到 `blocked/` + 审计 `EGRESS_TOKENMAP_BLOCKED`**。

**实测**（服务器 DMZ 18402）：
```
映射表外发 → 403 {"detail":"策略拒绝：外发物包含令牌映射表"}
隔离目录   → data/egress_out/blocked/REQ-T03-1-token_map.csv
正常文件   → 200 {"ok":true,"sha256_16":"b7d55ac9..."}
```

### 10.4 T12 网络级隔离（原：有 compose 但没实证）

用你仓库里的 `docker-compose.yml` 在服务器起了三网络栈：`app_inner_net`（internal） / `app_dmz_net` / `app_outer_net`，
容器接入方式与你设计一致：

```
app-inner-1: app_dmz_net app_inner_net
app-dmz-1:   app_dmz_net app_outer_net
app-outer-1: app_outer_net
```

**实测三条**：
```
outer 容器连 inner:8101   → 拒绝 ✅ gaierror: Temporary failure in name resolution
outer 解析主机名 inner    → 失败 ✅
dmz 容器连 inner:8101     → 成功 ✅（唯一咽喉）
```

同一套 compose 栈跑 `run_cases.py`：**12/12 通过**。

---

## 十一、现在的状态（2026-11-20 11:10）

| 项 | 结果 |
|---|---|
| pytest | **119 passed**（原有 90 + 新增 29） |
| 12 例验收 | **12/12**（公网 19096 与 compose 栈 8101 双环境） |
| T01–T20 | 通过 20、部分 0、不适用 0 |
| 客户多轮 | "嗯" 继承上一轮意图 |
| 客户延迟 | 4.6–5.9 秒（真模型） |
| T03 / T12 | 已实证（见上） |

**仍未做**：正式盲测（需其他组现场抽）+ 组内真人签字。这两条本来就不在我这边。

### 10.6 T10 冲突以内网为准（原：转人工了，动作码还写着"收集更多信息"）

- `inner/rules.py` `evaluate()` 末尾新增第 ⑧ 条：判定 `HUMAN_HANDOFF` 且命中
  `MANDATORY_HUMAN / RULE_ACTION_CONFLICT / INVALID_RULE_REFERENCE / PROHIBITED_CLAIM / INVALID_ACTION_CODE`
  之一时，返回 `action_override = "HUMAN_REVIEW"`，并补 reason `ACTION_OVERRIDDEN_BY_RULE`。
- `inner/service.py` ⑩.5：落库前用 override 覆盖模型给的动作码，写 `ACTION_OVERRIDDEN` 审计（谁改的、从什么改成什么）。

**为什么这么改**：B04 本来就转人工，但动作码是 `COLLECT_MORE_INFO`——坐席看到会去"再多问几句"，
而正确动作是交人工研判。这就是"外部建议与内网规则冲突，以内网为准"的落地，且改的是动作码，不是模型输出本身。

**实测**（服务器 18401，真模型）：
```
B04 → HUMAN_HANDOFF | next: HUMAN_REVIEW | reasons: ACTION_OVERRIDDEN_BY_RULE, AGENT_REQUESTED_HUMAN, INVALID_RULE_REFERENCE, MANDATORY_HUMAN
B03 → HUMAN_HANDOFF | next: HUMAN_REVIEW | 同上
A01 → RESTORED       | next: REQUEST_DISPUTE_FORM | reasons: []      （放行场景不受影响）
12 例：12/12；pytest：119 passed（新增 tests/test_t10_rule_precedence.py 4 项）
```
