"""
Master Regression Runner
========================
Runs all active unit and regression test suites across the repository.
"""

import sys
import os
import time
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_EXE = sys.executable

TEST_SUITES = [
    "scripts/test_entry_location.py",
    "scripts/test_no_lookahead.py",
    "scripts/test_targets_refactor.py",
    "scripts/test_stop_architecture.py",
    "scripts/test_target_hierarchy_and_reach.py",
    "scripts/test_context.py",
    "scripts/test_context_vetoes.py",
    "scripts/test_earnings_failsafe.py",
    "scripts/test_regime_failsafe.py",
    "scripts/test_recommendation_lifecycle.py",
    "scripts/test_recommendation_simplification.py",
    "scripts/test_decommissioning_and_recommendation_isolation.py",
    "scripts/test_macd_normalization.py",
    "scripts/test_p0_fixes.py",
    "scripts/test_validator_serialization.py",
    "scripts/test_us_universe.py",
    "scripts/test_cache_safety_and_resilience.py",
]


def run_all():
    print("=" * 70)
    print("RUNNING MASTER REPO REGRESSION SUITE")
    print("=" * 70)
    
    passed = 0
    failed = 0
    results = []

    for test_path in TEST_SUITES:
        full_path = os.path.join(PROJECT_ROOT, test_path)
        if not os.path.exists(full_path):
            print(f"[-] SKIPPED: {test_path} (file not found)")
            continue

        t0 = time.time()
        res = subprocess.run(
            [PYTHON_EXE, full_path],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        elapsed = time.time() - t0

        if res.returncode == 0:
            print(f"[+] PASSED: {test_path:<50} ({elapsed:.2f}s)")
            passed += 1
            results.append((test_path, "PASS", elapsed, ""))
        else:
            print(f"[!] FAILED: {test_path:<50} ({elapsed:.2f}s)")
            print("--- Output ---")
            print(res.stdout[-400:] if res.stdout else "")
            print(res.stderr[-400:] if res.stderr else "")
            print("--------------")
            failed += 1
            results.append((test_path, "FAIL", elapsed, res.stderr or res.stdout))

    print("=" * 70)
    print(f"REGRESSION RUN COMPLETE: {passed} PASSED, {failed} FAILED across {len(results)} suites")
    print("=" * 70)
    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
