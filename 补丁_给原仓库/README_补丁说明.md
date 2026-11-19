# 补丁：fault 生效 + 隐私三级判据 + 客户通道风险分流（v1.5.1）

目标：`1708420870/bank-ai-workbench`（v1.5.0，2026-11-19 拉取）
改动：**3 个源文件 + 2 个新测试文件**，共 226 行 diff
作者：第4组 杨慧敏　｜　时间：2026-11-19

---

## 一、改了什么（三处）

### 1. `inner/privacy.py` — 新增 `PrivacyService.judge()`

隐私判据放进隐私服务（单一职责），`service.py` 只调用、不自己写正则：

| 级别 | 条件 | 处置 |
|---|---|---|
| T1 | 命中 18 位证件号 | `BLOCKED` / `PRIVACY_ID_NUMBER`（禁复原 + 转人工） |
| T1' | **补充说明（note）里**出现完整卡号 | `BLOCKED` / `PRIVACY_RAW_CARD_IN_NOTE` |
| T2 | 要求回拨/回访且给了完整手机号 | `HUMAN_HANDOFF` / `CALLBACK_REQUIRES_AUTH` |

**关键取舍：卡号只判 note，不判正文。** A 组案例正文本来就有合成卡号和手机号（来自业务库、按权限取得），判正文会把 A 组全部误杀。

### 2. `inner/service.py` — 两个插入点

- `case.fault` 生效：`CASE_CREATED` 之后读 `case["fault"]`，是 `timeout` 就置 `TIMEOUT`、`bad_json` 就置 `REJECTED`，都 `HUMAN_HANDOFF`，留审计 `FAULT_INJECTED`。
- 隐私判据：`raw_text` 构造之后、`privacy.mask()` 之前调用 `judge()`，命中即置状态并转人工，留审计 `PRIVACY_JUDGED`。顺序不能反——先脱敏再判，令牌化已经把证件号盖掉，永远判不到。

### 3. `inner/app.py` — 客户通道高风险词补齐

原来只认 `骗/盗/洗钱/资金损失/被扣款/冻结`。客户说「**陌生消费**」「**未授权**」「**不是本人**」「**被刷**」「**盗刷**」「退款」「挂失」时，会被当成普通咨询自动答复。

补齐后实测：

| 客户输入 | 补前 | 补后 |
|---|---|---|
| 卡在身边出现 3280 元陌生消费，要求退款 | `AI_ANSWERING` + 通用自助话术 | **`PENDING_HUMAN`** + 安抚 + 反诈提示 |
| 我的卡好像被盗刷了 | 同上 | **`PENDING_HUMAN`** |
| 想对一笔 268 元消费提争议，要什么材料 | `AI_ANSWERING` | `AI_ANSWERING`（不变，低风险正常答复） |

### 4. 新增测试（7 个用例）

- `tests/test_privacy_judge.py`：判据纯函数级 5 例（证件号阻断 / note 卡号阻断 / 回拨转人工 / A 组不误伤 / 空输入）
- `tests/test_case_fault.py`：C03→TIMEOUT、C04→REJECTED、A01 不受影响（自带会话，避开 SINGLE_SESSION 互相顶号）

---

## 二、验证结果（都是本机实跑）

| 项 | 打补丁前 | 打补丁后 |
|---|---|---|
| 你的 pytest | 90 passed | **98 passed**（90 原有 + 7 新增 + 1 参数化） |
| 12 例验收 | **8/12**（C 组 0/4） | **12/12** |
| C01 带完整证件号 | RESTORED | **BLOCKED / PRIVACY_ID_NUMBER** |
| C02 回拨回访 | RESTORED | **HUMAN_HANDOFF / CALLBACK_REQUIRES_AUTH** |
| C03 fault=timeout | RESTORED | **TIMEOUT / UPSTREAM_UNAVAILABLE** |
| C04 fault=bad_json | RESTORED | **REJECTED / SCHEMA_INVALID** |
| A 组 4 例 | 正常 | 正常（不受影响） |
| 并发 5 / 10 | — | **100% / 100%**（P90 2.36s / 5.25s，进程不退出） |
| 干净仓库验证 | — | 新 clone → `git apply --check` 通过 → apply → **98 passed** |

---

## 三、两处更正（我之前说错了，这里改过来）

1. **工单号时区不是源码的锅。** 你源码里 `_now()` 就是 `datetime.now(timezone.utc).astimezone()`、`fb_id` 也用本地时区，本来就是对的。我之前看到的 UTC 时分，是**跑在容器里 TZ=UTC** 造成的，不是代码问题。**这一项不用改。**
2. **并发不是架构问题。** 你用的是**三进程分离**（这点比原客户端好），实测 5/10 并发 100%。原客户端 5 并发就崩，是因为它把三区塞进了一个进程。**这一项也不用改**，只要别退回单进程。

所以"6 处改动"实际落到源码上只有 **3 处真正需要改**（fault、隐私判据、客户通道风险词），另外三项（通道结构、并发、时区）你这版已经是对的。

---

## 四、试了但回退的一处（留作下一步）

我试着让**低风险客户咨询也给更具体的话术**（比如"提争议"直接给材料清单，而不是通用自助话术）：做法是加确定性意图提示当"地板"，模型给通用动作码时用关键词覆盖。

结果：客户侧答复变成**空**（`replies: []`，工单停在 `AI_ANSWERING`）——因为丢掉了模型话术后，模板与 `_fb_deliver` 的裁决链路没对齐。为避免引入新缺陷，**已回退**，行为保持你原来的设计。

要做的话，正确改法是在 `_fb_compose_reply` 里让 `step` 与 `reply` 一起走模板分支（不要先清空 `customer_reply` 再复用模型路径），或者改 DMZ 裁决允许空话术走模板。估计半天。

---

## 五、怎么打

```bash
git checkout -b fix/case-fault-and-privacy
git apply 0001-optimal-fault-privacy-customer-risk.patch
python -m pytest -q                                        # 期望 98 passed
# 起三区后：
python tools/run_cases.py --base http://127.0.0.1:8101      # 期望 12/12
python tools/wb_loadtest.py --base http://127.0.0.1:8101 --path case --n 20 --c 10
```

`run_cases.py`、`wb_loadtest.py` 在我们的仓库 `tools/` 下（只用标准库，`--base`、`--basic` 两个参数）。

---

## 六、还得提醒一次：这个仓库是公开的

我扫过全库：

- 没有真 key（`common/config.py` 里是 `your-secret-key-here` 占位符）✅
- 没有真实客户 PII（13800138001 是合成测试号）✅
- **但**默认口令 `abc123456 / abc122333 / Tech@2026` 在 **9 个文件**明码；内网模型地址 `106.55.2.189:8000` 在 **5 个文件**；含"微光""任务书"的内部方案 **10 个文件**

任务书写明"未经微光工作室授权，请勿用于其它商业用途"。建议转私有，至少把默认口令和内网地址从 README / docs 里摘掉。我们自己的演示环境用的也是同一套口令，我会换掉。
