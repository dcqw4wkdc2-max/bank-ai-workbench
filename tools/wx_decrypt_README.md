# 微信记录导出：一键脚本（解决 GBK 崩溃）

## 症状
运行 `stories-in-wx-*.exe decrypt` 时报：
`UnicodeEncodeError: 'gbk' codec can't encode character '\u2726'`
—— 这是 **rich 库在 GBK 控制台下画 banner** 崩的，还没开始干活就退出，所以不产出任何目录。

## 唯一正确跑法
双击 `tools/wx_decrypt.bat`（脚本内部用真控制台 + chcp 65001，且**不重定向输出**）。

三个反例，都试过、都会崩：
1. `... exe decrypt ... > log.txt 2>&1` —— 重定向后 rich 仍走 Windows 控制台渲染器，崩
2. `cmd /c chcp 65001 && exe ... > log.txt` —— 一样崩
3. `PYTHONUTF8=1 / PYTHONIOENCODING=utf-8 / NO_COLOR=1 / TERM=dumb` —— 环境变量无效，崩

## 导出完成后怎么查最新消息
产物是 SQLite 分库：`E:\SIWX_current\<wxid>\message\message_0..3.db`
- 会话与消息在 `Msg_<md5(会话username)>` 表里
- 会话 username → 显示名：`contact/contact.db` 的 `contact` 表（remark / nick_name）
- 时间字段 `create_time` 是 Unix 秒（本机时区）
- 已知坑：`msg_content` 有些是压缩的（`WCDB_CT_message_content=4`），文本消息通常是明文

常用查询（Python）：
```python
import sqlite3, hashlib, time
con = sqlite3.connect(r"E:/SIWX_current/<wxid>/message/message_3.db")
t = "Msg_" + hashlib.md5("50394324864@chatroom".encode()).hexdigest()
ids = {r[0]: r[1] for r in con.execute("SELECT rowid, user_name FROM Name2Id")}
for ct, lt, rid, mc in con.execute(f"SELECT create_time, local_type, real_sender_id, message_content FROM {t} ORDER BY create_time DESC LIMIT 20"):
    print(time.strftime("%m-%d %H:%M", time.localtime(ct)), ids.get(rid, rid), str(mc)[:100])
```

## 备注
自带的 MCP（stories-in-wx）读的是某次导出的缓存，可能滞后一两天。要看当天消息，先跑本脚本再直接查库。
