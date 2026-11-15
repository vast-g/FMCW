@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   边坡监测 Agent - 评测中心
echo ============================================================
echo.
echo   [1] 环境预检          (几秒, 不调模型)
echo   [2] 裁判自检          (几秒, 2 次 API 调用)
echo   [3] 小样本试跑        (5 条, 不调裁判)
echo   [4] 试跑 20 条        (含裁判指标)
echo   [5] 全量评测          (212 条, 约 1.5-2.5 小时)
echo   [6] 重新生成报告
echo   [7] 挖掘 Bad Case 并更新回归集
echo   [8] 设为基线
echo   [9] 回归门禁
echo   [0] 离线管线自测      (不调模型, 验证评测代码本身)
echo.
set /p mode="请选择 (0-9): "

if "%mode%"=="1" python -m slope_eval check
if "%mode%"=="2" python -m slope_eval judge-test
if "%mode%"=="3" python -m slope_eval run --limit 5 --no-judge --reset-trace --verbose
if "%mode%"=="4" python -m slope_eval run --limit 20
if "%mode%"=="5" python -m slope_eval run --full
if "%mode%"=="6" python -m slope_eval report
if "%mode%"=="7" python -m slope_eval mine
if "%mode%"=="8" python -m slope_eval baseline
if "%mode%"=="9" python -m slope_eval gate
if "%mode%"=="0" python tools\selftest_pipeline.py

echo.
echo ============================================================
echo   完成。产物在 reports\ 与 bad_cases\ 目录
echo ============================================================
pause
