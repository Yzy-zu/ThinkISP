#!/usr/bin/env python3
"""
ThinkISP Standalone Test Runner
Discovers and executes all unit tests with colorized feedback, timing, and status codes.
"""

import argparse
from io import StringIO
import os
from pathlib import Path
import sys
import time
import traceback
import unittest

# Ensure thinkisp is in sys.path
TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ANSI Color Codes
BOLD = "\033[1m"
GREEN = "\033[1;32m"
RED = "\033[1;31m"
YELLOW = "\033[1;33m"
CYAN = "\033[1;36m"
BLUE = "\033[1;34m"
MAGENTA = "\033[1;35m"
DIM = "\033[2m"
RESET = "\033[0m"


class ColorTestResult(unittest.TestResult):
    """Custom TestResult that collects metrics and prints colorized indicators."""

    def __init__(self, stream=sys.stdout, verbose=False):
        super().__init__()
        self.stream = stream
        self.verbose = verbose
        self.test_timings = {}
        self._test_start_time = 0
        self.successes = []

    def startTest(self, test):
        super().startTest(test)
        self._test_start_time = time.perf_counter()

    def addSuccess(self, test):
        super().addSuccess(test)
        elapsed = time.perf_counter() - self._test_start_time
        self.test_timings[test] = elapsed
        self.successes.append((test, elapsed))
        test_id = test.id().split(".")
        cls_name = test_id[-2]
        mth_name = test_id[-1]
        self.stream.write(f"  {GREEN}✔ PASS{RESET}  {CYAN}{cls_name}.{BOLD}{mth_name}{RESET} {DIM}({elapsed * 1000:.1f}ms){RESET}\n")
        self.stream.flush()

    def addFailure(self, test, err):
        super().addFailure(test, err)
        elapsed = time.perf_counter() - self._test_start_time
        self.test_timings[test] = elapsed
        test_id = test.id().split(".")
        cls_name = test_id[-2]
        mth_name = test_id[-1]
        self.stream.write(f"  {RED}✖ FAIL{RESET}  {BOLD}{cls_name}.{mth_name}{RESET} {DIM}({elapsed * 1000:.1f}ms){RESET}\n")
        self.stream.flush()

    def addError(self, test, err):
        super().addError(test, err)
        elapsed = time.perf_counter() - self._test_start_time
        self.test_timings[test] = elapsed
        test_id = test.id().split(".")
        cls_name = test_id[-2]
        mth_name = test_id[-1]
        self.stream.write(f"  {YELLOW}⚠ ERR {RESET}  {BOLD}{cls_name}.{mth_name}{RESET} {DIM}({elapsed * 1000:.1f}ms){RESET}\n")
        self.stream.flush()

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        test_id = test.id().split(".")
        cls_name = test_id[-2]
        mth_name = test_id[-1]
        self.stream.write(f"  {BLUE}↷ SKIP{RESET}  {DIM}{cls_name}.{mth_name} ({reason}){RESET}\n")
        self.stream.flush()


def print_banner():
    """Prints test suite header banner."""
    print(f"\n{BOLD}{CYAN}╔═══════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║             ⚡ THINKISP UNIT TEST SUITE                   ║{RESET}")
    print(f"{BOLD}{CYAN}║        Zero-Dependency Automated Quality Assurance        ║{RESET}")
    print(f"{BOLD}{CYAN}╚═══════════════════════════════════════════════════════════╝{RESET}\n")


def run_tests():
    parser = argparse.ArgumentParser(description="ThinkISP Test Runner")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose test reporting")
    parser.add_argument("-p", "--pattern", default="test_*.py", help="Test filename pattern (default: test_*.py)")
    parser.add_argument("-s", "--suite", default=None, help="Run a specific test module (e.g. test_network)")
    args = parser.parse_args()

    print_banner()

    start_total_time = time.perf_counter()

    loader = unittest.TestLoader()
    if args.suite:
        suite_name = args.suite if args.suite.endswith(".py") else f"{args.suite}.py"
        test_suite = loader.discover(start_dir=str(TESTS_DIR), pattern=suite_name)
    else:
        test_suite = loader.discover(start_dir=str(TESTS_DIR), pattern=args.pattern)

    total_tests = test_suite.countTestCases()
    print(f"{BOLD}Discovered {CYAN}{total_tests}{RESET}{BOLD} unit tests across test suite:{RESET}\n")

    result = ColorTestResult(stream=sys.stdout, verbose=args.verbose)
    test_suite.run(result)

    total_duration = time.perf_counter() - start_total_time

    # Print failures and errors details if any
    if result.failures:
        print(f"\n{BOLD}{RED}┌─── [ FAILURES ({len(result.failures)}) ] ────────────────────────────────────────{RESET}")
        for test, tb_text in result.failures:
            print(f"{RED}FAIL:{RESET} {BOLD}{test.id()}{RESET}")
            print(f"{DIM}{tb_text.strip()}{RESET}\n")
        print(f"{BOLD}{RED}└──────────────────────────────────────────────────────────{RESET}")

    if result.errors:
        print(f"\n{BOLD}{YELLOW}┌─── [ ERRORS ({len(result.errors)}) ] ──────────────────────────────────────────{RESET}")
        for test, tb_text in result.errors:
            print(f"{YELLOW}ERROR:{RESET} {BOLD}{test.id()}{RESET}")
            print(f"{DIM}{tb_text.strip()}{RESET}\n")
        print(f"{BOLD}{YELLOW}└──────────────────────────────────────────────────────────{RESET}")

    # Summary Panel
    passed_count = len(result.successes)
    failed_count = len(result.failures)
    error_count = len(result.errors)
    skipped_count = len(result.skipped)

    print(f"\n{BOLD}{CYAN}┌─── [ RESUMEN DE PRUEBAS / TEST SUMMARY ] ────────────────{RESET}")
    print(f"│  Total de Pruebas:    {BOLD}{result.testsRun}{RESET}")
    print(f"│  Exitosas (Passed):   {GREEN}✔ {passed_count}{RESET}")
    if failed_count > 0:
        print(f"│  Fallidas (Failed):   {RED}✖ {failed_count}{RESET}")
    else:
        print(f"│  Fallidas (Failed):   0")
    if error_count > 0:
        print(f"│  Errores (Errors):    {YELLOW}⚠ {error_count}{RESET}")
    else:
        print(f"│  Errores (Errors):    0")
    if skipped_count > 0:
        print(f"│  Omitidas (Skipped):  {BLUE}↷ {skipped_count}{RESET}")
    print(f"│  Tiempo de Ejecución: {CYAN}{total_duration * 1000:.2f} ms ({total_duration:.3f}s){RESET}")
    print(f"{BOLD}{CYAN}└──────────────────────────────────────────────────────────{RESET}")

    if result.wasSuccessful():
        print(f"\n{BOLD}{GREEN}🎉 ¡TODAS LAS PRUEBAS PASARON EXITOSAMENTE! (ALL TESTS PASSED){RESET}\n")
        sys.exit(0)
    else:
        print(f"\n{BOLD}{RED}❌ SE DETECTARON FALLOS EN LA SUITE DE PRUEBAS.{RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
