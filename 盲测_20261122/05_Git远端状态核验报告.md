# Git 远端状态核验报告（2026-11-22 14:0x）

核验方式：`git ls-remote`、`git merge-base --is-ancestor`、`git log origin/<branch>`，均为命令实测，不引用历史结论。

---

## 一、交付仓 `dcqw4wkdc2-max/bank-ai-workbench`

| 项 | 值 |
|---|---|
| 本地 HEAD | `4085c0c` |
| 远端 `origin/main` | `4085c0c` |
| 是否一致 | **一致** |
| `784d156` 是否已在远端历史 | **是**（`merge-base --is-ancestor 784d156 origin/main` 返回成功） |

远端最近 3 个提交：

```
4085c0c docs: 版本冻结清单（HEAD/文件哈希/端口/口令状态/未完成项）
784d156 security: 关闭旧版公网入口 19092/19093；轮换演示口令；12 例复跑 12/12；出整改报告
5427b39 docs: 最终验收 A-E
```

> 说明：`784d156` 当时推送因网络抖动未确认，但随后在其之上提交了 `4085c0c` 并成功推送；既然 `4085c0c` 已在远端，且 `merge-base` 判定 `784d156` 为其祖先，**可确认 784d156 已进入远端历史**。

## 二、补丁分支仓 `dcqw4wkdc2-max/bank-ai-workbench-1`

| 项 | 值 |
|---|---|
| 分支 | `fix/case-fault-and-privacy` |
| 本地 HEAD | `b53a817` |
| 远端 | `b53a817` |
| 是否一致 | **一致**（`merge-base --is-ancestor b53a817 origin/…` 成功，`ls-remote` 一并确认） |

## 三、原作者仓库

| 项 | 值 |
|---|---|
| PR | https://github.com/1708420870/bank-ai-workbench/pull/1 |
| 状态 | open（未合并） |
| 范围 | 30 文件 / +4005 −57 |

## 四、两个交付包的关系（回答"245 件 vs 247 件"的疑问）

| 包 | 件数 | 大小 | 说明 |
|---|---|---|---|
| `第4组_银行客服AI工作台_交付物_20261122.zip` | 245 | 9.70 MB | 加冻结清单**之前**打的包 |
| `第4组_银行客服AI工作台_交付物_20261122_FINAL.zip` | 247 | 9.70 MB | 多了 `版本冻结清单.md` + `version_manifest.json` |

逐条比对：**FINAL 是 A 的超集，A 没有任何 FINAL 缺失的文件**（差异只有那两份冻结清单）。
结论：内容无冲突，FINAL 为唯一交付候选；按"不删除任何旧版本"的要求，A 暂不删除。

## 五、签名/凭据说明

- 本报告不含任何 Key 或口令明文，只记录提交哈希。
- 服务器密钥文件权限：`model.env`、`secrets.env` 均 `600 root:root`。
