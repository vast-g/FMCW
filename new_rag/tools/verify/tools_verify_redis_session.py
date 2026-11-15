"""Run session-storage contracts and optionally real Redis Lua checks.

The old minimal RESP fake could not execute Lua and expected automatic fallback.
Use mocked failure tests for offline runs, and SLOPE_TEST_REDIS=1 for real Redis.
All Agent tests use mocked models and avoid production MySQL writes.
"""
from pathlib import Path
import os
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
os.chdir(ROOT)
os.environ.setdefault('SLOPE_HEADLESS','1')
if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(errors='replace')

if __name__=='__main__':
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py')
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    checked=result.testsRun-len(result.skipped)
    passed=checked-len(result.failures)-len(result.errors)
    print(f'通过 {passed} / {checked}')
    if result.skipped:
        print(f'另有 {len(result.skipped)} 项真实 Redis 验证未启用；设置 SLOPE_TEST_REDIS=1 后运行。')
    raise SystemExit(0 if result.wasSuccessful() else 1)
