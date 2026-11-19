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
| pytest | 90 passed | **104 passed** |
| 12 例验收 | **8/12**（C 组 0/4） | **12/12** |
| C01 证件号 | RESTORED | **BLOCKED / PRIVACY_ID_NUMBER** |
| C02 回拨回访 | RESTORED | **HUMAN_HANDOFF / CALLBACK_REQUIRES_AUTH** |
| C03 fault=timeout | RESTORED | **TIMEOUT / UPSTREAM_UNAVAILABLE** |
| C04 fault=bad_json | RESTORED | **REJECTED / SCHEMA_INVALID** |
| 并发 5 / 10 | — | **100% / 100%**（P90 2.05s / 4.02s） |
| 干净应用验证 | — | `git archive HEAD` 出 pristine 树 → `git apply --check` 通过 → apply → **104 passed** |

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
python -m pytest -q                                        # 期望 104 passed
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

他只需要：**Review → Merge**。合并后本地 `git pull`，跑 `python -m pytest -q`（期望 104 passed）与 `tools/run_cases.py`（期望 12/12）。
