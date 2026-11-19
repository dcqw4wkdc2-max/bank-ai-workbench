@echo off
chcp 65001 >nul
title SIWX 微信导出（必须在真控制台里跑，不能重定向）
echo ================================================
echo  一旦本窗口出现进度条，说明正常；跑完约 15-40 秒。
echo  产物目录：E:\SIWX_current
echo  注意：千万不要把输出重定向到文件（如 ^> log.txt），
echo        rich 库在 GBK 控制台下会崩，表现为 UnicodeEncodeError U+2726。
echo ================================================
cd /d E:\Tools\SIWX
if exist E:\SIWX_current rmdir /s /q E:\SIWX_current
stories-in-wx-v5.0.4-windows-x64.exe decrypt --no-cache --db-dir "E:\weflow\xwechat_files\wxid_saqrhqbq50tr22_c588\db_storage" --out E:\SIWX_current
if errorlevel 1 (
  echo.
  echo [失败] 看一下上面的报错；若是 U+2726 编码错误，说明输出被重定向了。
) else (
  echo.
  echo [完成] 产物在 E:\SIWX_current
)
echo 按任意键关闭…
pause >nul
