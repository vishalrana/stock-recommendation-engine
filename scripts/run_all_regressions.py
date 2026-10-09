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
    "scripts/test_earnings_infrastructure.py",
    "scripts/test_earnings_decoupling.py",
    "scripts/test_hardened_pipeline.py",
    "scripts/test_production_hardening_pass.py",
    "scripts/test_final_production_baseline.py",
    "scripts/test_quant_hardening_final.py",
    "scripts/test_earnings_provider_isolation.py",
    "scripts/test_target_before_stop_reach.py",
    "scripts/test_production_canonical_hardening.py",
    "scripts/test_canonical_quant_golden.py",
    "scripts/test_earnings_and_survivorship.py",
    "scripts/test_entry_location_regression.py",
    "scripts/test_historical_metrics_provenance.py",
    "scripts/test_position_sizer_and_tier.py",
    "scripts/test_production_audit.py",
    "scripts/test_quant_spec_alignment.py",
    "scripts/test_strategy_deduplication.py",
    "tests/quant_reference/test_quant_reference_suite.py",
    "tests/quant_reference/test_property_invariants.py",
    "tests/quant_reference/test_fail_closed_and_canonical_registries.py",
    "tests/test_end_to_end_pipeline.py",
    "scripts/test_backtest_pipeline.py",
    "scripts/test_quant_correctness_fixes.py",
]


def run_all():
    import glob
    print("=" * 70)
    print("RUNNING MASTER REPO REGRESSION SUITE (SELF-AUDITING)")
    print("=" * 70)
    
    # 1. Audit uniqueness
    if len(TEST_SUITES) != len(set(TEST_SUITES)):
        print("[!] FATAL AUDIT ERROR: Duplicate test suite entries detected in TEST_SUITES.")
        return False

    # 2. Audit existence of all registered tests
    for p in TEST_SUITES:
        fp = os.path.join(PROJECT_ROOT, p)
        if not os.path.exists(fp):
            print(f"[!] FATAL AUDIT ERROR: Registered test file does not exist: {p}")
            return False

    # 3. Audit test discovery: verify no orphaned test_*.py files on disk
    scripts_tests = {os.path.relpath(f, PROJECT_ROOT).replace("\\", "/") for f in glob.glob(os.path.join(PROJECT_ROOT, "scripts", "test_*.py"))}
    tests_dir_tests = {os.path.relpath(f, PROJECT_ROOT).replace("\\", "/") for f in glob.glob(os.path.join(PROJECT_ROOT, "tests", "**", "test_*.py"), recursive=True)}
    all_discovered = scripts_tests | tests_dir_tests
    registered_set = set(TEST_SUITES)

    unregistered = all_discovered - registered_set
    if unregistered:
        print(f"[!] FATAL AUDIT ERROR: Found unregistered test suite(s) on disk: {sorted(unregistered)}")
        return False

    print(f"[*] Self-audit passed: All {len(TEST_SUITES)} unique test suites accounted for with zero orphans or duplicates.")
    print("=" * 70)

    passed = 0
    failed = 0
    results = []

    for test_path in TEST_SUITES:
        full_path = os.path.join(PROJECT_ROOT, test_path)

        t0 = time.time()
        res = subprocess.run(
            [PYTHON_EXE, full_path],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        elapsed = time.time() - t0

        if res.returncode == 0:
            print(f"[+] PASSED: {test_path:<50} ({elapsed:.2f}s)", flush=True)
            passed += 1
            results.append((test_path, "PASS", elapsed, ""))
        else:
            print(f"[!] FAILED: {test_path:<50} ({elapsed:.2f}s)", flush=True)
            print("--- Output ---", flush=True)
            print(res.stdout[-400:] if res.stdout else "", flush=True)
            print(res.stderr[-400:] if res.stderr else "", flush=True)
            print("--------------", flush=True)
            failed += 1
            results.append((test_path, "FAIL", elapsed, res.stderr or res.stdout))

    print("=" * 70, flush=True)
    print(f"REGRESSION RUN COMPLETE: {passed} PASSED, {failed} FAILED across {len(results)} suites", flush=True)
    print("=" * 70, flush=True)
    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
