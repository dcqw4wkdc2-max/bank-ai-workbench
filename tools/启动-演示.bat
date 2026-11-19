@echo off
chcp 65001 >nul
title 银行客服AI工作台 - 演示启动（关掉本窗口等于停掉看门狗）
cd /d "%~dp0"

echo [1/3] 启动客户端…
start "BankApp" /min "%~dp0BankAIWorkbench.exe"

echo [2/3] 等待服务就绪（约 20 秒）…
set /a n=0
:wait
ping -n 3 127.0.0.1 >nul
curl -s -o nul --max-time 2 http://127.0.0.1:8101/internal/api/v1/health >nul 2>&1
if not errorlevel 1 goto ready
set /a n+=1
if %n% lss 12 goto wait

:ready
echo [3/3] 启动串行网关 + 看门狗（8121 → 8101）…
where python >nul 2>&1
if errorlevel 1 (
  echo     未检测到 python，跳过网关，仅用下面的看门狗守护客户端。
  goto watchdog
)
start "BankGateway" /min python "%~dp0_gateway\serial_proxy.py" --port 8121 --target-port 8101 --auto-start "%~dp0BankAIWorkbench.exe" --workdir "%~dp0"
echo     网关已起：坐席可访问 http://127.0.0.1:8121/login （也可继续用 8101）

:watchdog
echo.
echo ====== 演示要点（本窗口不要关）======
echo  坐席（直连）  http://127.0.0.1:8101/login   b001 / abc122333
echo  坐席（网关）  http://127.0.0.1:8121/login   多人同时点时用这个
echo  管理端        http://127.0.0.1:8101/admin   a001 / abc123456
echo  客户页        http://127.0.0.1:8103/
echo  只开一个坐席窗口；不要演示 C 组与故障注入。
echo ======================================
:loop
tasklist /FI "IMAGENAME eq BankAIWorkbench.exe" | find /I "BankAIWorkbench.exe" >nul
if errorlevel 1 (
  echo [%time%] 客户端不在了，正在拉起…
  start "BankApp" /min "%~dp0BankAIWorkbench.exe"
  ping -n 26 127.0.0.1 >nul
)
ping -n 10 127.0.0.1 >nul
goto loop
