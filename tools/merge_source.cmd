@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo === 银行客服 AI 工作台 · 源码并入 ===
if not exist "银行客服AI工作台-源码快照-20261117-精简.zip" if not exist "银行客服AI工作台-v1.5.0-win64.zip" (
  echo [错误] 当前目录没有找到源码 zip。
  echo 请把 源码快照 或 v1.5.0 客户端 zip 放到本目录后重跑。
  pause & exit /b 1
)

if exist "银行客服AI工作台-源码快照-20261117-精简.zip" (
  echo 解压 源码快照...
  powershell -NoProfile -Command "Expand-Archive -Force '银行客服AI工作台-源码快照-20261117-精简.zip' '.'"
)
if exist "银行客服AI工作台-v1.5.0-win64.zip" (
  echo 解压 v1.5.0 客户端...
  powershell -NoProfile -Command "Expand-Archive -Force '银行客服AI工作台-v1.5.0-win64.zip' 'v1.5.0'"
)

echo.
echo 并入后请补三样：
echo   1. 启动命令（README「怎么跑」）
echo   2. 环境变量清单（只写要填什么，不写真实值）
echo   3. 依赖清单（requirements.txt 或 package.json）
echo.
echo 提交：
echo   git add . ^&^& git commit -m "feat: 源码并入（v1.5.0 快照）" ^&^& git push
pause
