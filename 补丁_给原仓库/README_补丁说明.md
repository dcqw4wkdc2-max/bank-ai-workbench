# 补丁 0001：让 `case.fault` 生效 + 加隐私三级判据

目标仓库：`1708420870/bank-ai-workbench`（v1.5.0）
基线：`main` @ `ee821c3` 之后的版本（2026-11-19 拉取）
涉及文件：**只有 1 个** — `inner/service.py`（+41 行，含 4 行 import）
作者：第4组 杨慧敏　｜　时间：2026-11-19

---

## 一、这两个补丁解决什么

用 12 例验收脚本（`tools/run_cases.py`）打你这版源码，基线是 **8/12**：

```
A01–A04  PASS          （低风险给建议，正常）
B01–B04  PASS          （高风险转人工，正常）
C01–C04  FAIL  ← 全部 RESTORED
```

原因两条，都在 `handle_case()` 里：

1. **`cases` 表有 `fault` 字段（C03=timeout、C04=bad_json），但 `handle_case` 从来没读它。**
   所以"故障演练案例"提交后 0.4 秒照常返回 `RESTORED`——故障注入只在 `outer/_fault` 的跨域层有效（T07 那条测试是对的），案例级的入口没接上。

2. **没有隐私判据。** `inner/privacy.py` 只做识别与令牌化，`inner/rules.py` 只有 8 条业务规则。
   C01 备注里粘贴了完整身份证号、C02 要求回拨并给了完整手机号，都按普通查询处理 → `RESTORED / SHOW_SUGGESTION`。

---

## 二、改法（两个插入点，都不改原有分支）

插入点 1：`CASE_CREATED` 审计之后（在建工单之后、取数之前）

```python
_fault = (case.get("fault") or "").strip()
if _fault in {"timeout", "bad_json"}:
    ...  # TIMEOUT / REJECTED + HUMAN_HANDOFF + 审计 FAULT_INJECTED
    return _result(request_id)
```

插入点 2：`raw_text` 构造之后、令牌化之前

```python
_hit_id = 18 位证件号
_hit_card_in_note = 补充说明里的 16–19 位卡号     # ← 只看 note，不看案例正文
_hit_phone = 完整手机号
if _hit_id:            -> BLOCKED        PRIVACY_ID_NUMBER
elif _hit_card_in_note:-> BLOCKED        PRIVACY_RAW_CARD_IN_NOTE
elif 回拨|回访 and _hit_phone: -> HUMAN_HANDOFF  CALLBACK_REQUIRES_AUTH
```

**为什么卡号只判 note**：A01/A02 案例正文里本来就有合成卡号和手机号（来自业务库、按权限取得），
判正文会把 A 组全部误杀。真正的风险是"人把原始标识粘进了业务文本"，所以只判 `note`（补充说明）。
这一点我们第一次改的时候踩过——把正文也判了，结果 T11 提示注入用例直接 `BLOCKED`、测试挂。

---

## 三、验证结果（都在本机实跑）

| 项 | 打补丁前 | 打补丁后 |
|---|---|---|
| 你的 pytest | 90 passed | **90 passed**（一条没坏） |
| 12 例验收 | 8/12 | **12/12** |
| C01 带完整证件号 | RESTORED | **BLOCKED / PRIVACY_ID_NUMBER** |
| C02 回拨回访 | RESTORED | **HUMAN_HANDOFF / CALLBACK_REQUIRES_AUTH** |
| C03 timeout 案例 | RESTORED | **TIMEOUT / UPSTREAM_UNAVAILABLE** |
| C04 bad_json 案例 | RESTORED | **REJECTED / SCHEMA_INVALID** |
| A 组 4 例 | RESTORED | RESTORED（不受影响） |

---

## 四、怎么打

```bash
git checkout -b fix/case-fault-and-privacy
git apply 0001-fault-and-privacy-judge.patch
python -m pytest -q                      # 期望 90 passed
python tools/run_cases.py --base http://127.0.0.1:8101   # 期望 12/12
```

> `run_cases.py` 在我们的仓库 `E:/20261118_银行客服AI工作台_交付包/repo/tools/run_cases.py`，
> 也可以直接拿去用（只用标准库，参数 `--base`、`--basic`）。

---

## 五、还没做的三条（你那边有源码更容易）

| # | 项 | 说明 | 量级 |
|---|---|---|---|
| 1 | 客户通道与坐席通道判定一致性 | 现在客户通道走 `outer → dmz → inner` 同一入口了，但要确认低风险咨询在客户页也给指引（我们在自己的 v2 上验证过路径可行） | 半天 |
| 2 | 并发 | 你的是**三进程分离**（这点比原客户端好），需要实测 5/10 并发；我们的压测脚本 `tools/wb_loadtest.py` 可直接用 | 2–3 天 |
| 3 | 工单号时区 | `FB20261119**0341**` 内嵌 UTC 时分，与页面差 8 小时 | 几行 |

---

## 六、另外提醒一件事（跟代码无关，但重要）

这个仓库现在是**公开**的。我扫了一遍：

- 没有真 key（`common/config.py` 里是 `your-secret-key-here` 占位符）✅
- 没有真实客户 PII（扫到的 13800138001 是合成测试号）✅
- **但**：默认口令 `abc123456 / abc122333 / Tech@2026` 在 9 个文件里明码（README、`inner/db.py`、`desktop/launcher.py`、`docs/25-分发与迁移指南.md` 等）
- 内网模型地址 `106.55.2.189:8000` 在 5 个文件里
- 含"微光""任务书"字样的内部方案 10 个文件（`docs/18-牛马仕定位说明` 等）

任务书写明"未经微光工作室授权，请勿用于其它商业用途"。建议**转私有**（Settings → Danger Zone → Change visibility），
至少把默认口令与内网地址从 README 和 docs 里摘出去。
