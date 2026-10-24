"""Repeatable offline acceptance command; requires dev dependencies and PostgreSQL.

Runs only the fixed synthetic acceptance suite. Never connects to a broker.
Captured pytest output and database credentials are not included in the report.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

EXPECTED_CASES = frozenset(
    {
        "test_three_days_preserve_yesterday_after_today_correction",
        "test_delayed_arrival_denies_then_recovers_without_backdating",
        "test_stale_tick_denies_then_fresh_tick_recovers",
        "test_next_day_missing_update_denies_until_new_bars_arrive",
    }
)
ROOT = Path(__file__).resolve().parents[1]


def parse_results(path: Path, returncode: int) -> dict:
    """A missing, skipped, duplicated or unexecuted case can never pass."""
    root = ET.parse(path).getroot()
    cases = []
    for case in root.iter("testcase"):
        status = "PASS"
        if case.find("skipped") is not None:
            status = "SKIP"
        elif case.find("failure") is not None or case.find("error") is not None:
            status = "FAIL"
        properties = case.find("properties")
        evidence = None
        if properties is not None:
            values = [p.get("value") for p in properties if p.get("name") == "evidence"]
            if len(values) == 1:
                evidence = json.loads(values[0])
        cases.append({"name": case.get("name"), "status": status, "evidence": evidence})
    names = [case["name"] for case in cases]
    complete = len(names) == len(EXPECTED_CASES) and set(names) == EXPECTED_CASES
    passed = (
        returncode == 0
        and complete
        and all(
            c["status"] == "PASS"
            and isinstance(c["evidence"], dict)
            and c["evidence"].get("source") == "SIMULATION-ONLY"
            and c["evidence"].get("steps")
            for c in cases
        )
    )
    return {
        "schema_version": 1,
        "source": "SIMULATION-ONLY",
        "status": "PASS" if passed else "FAIL",
        "execution_enabled": False,
        "expected_cases": len(EXPECTED_CASES),
        "passed": sum(c["status"] == "PASS" for c in cases),
        "failed": sum(c["status"] == "FAIL" for c in cases),
        "skipped": sum(c["status"] == "SKIP" for c in cases),
        "complete": complete,
        "test_exit_code": returncode,
        "cases": cases,
    }


def run() -> dict:
    failure = {
        "schema_version": 1,
        "source": "SIMULATION-ONLY",
        "status": "FAIL",
        "execution_enabled": False,
    }
    try:
        url = make_url(os.environ.get("ATS_TEST_DATABASE_URL", ""))
        if url.get_backend_name() != "postgresql" or url.database != "ats_test":
            return failure | {"error": "DEDICATED_POSTGRES_ATS_TEST_REQUIRED"}
    except (ArgumentError, ValueError, TypeError):
        return failure | {"error": "DEDICATED_POSTGRES_ATS_TEST_REQUIRED"}
    with tempfile.TemporaryDirectory(prefix="ats-replay-acceptance-") as temp:
        report_path = Path(temp) / "results.xml"
        env = os.environ.copy()
        # Caller pytest flags must not select, skip, stop or alter this fixed suite.
        env.pop("PYTEST_ADDOPTS", None)
        env.pop("PYTEST_PLUGINS", None)
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/test_replay_acceptance.py",
                    "-q",
                    "--fail-on-skip",
                    "-o",
                    "addopts=",
                    "-o",
                    "junit_family=legacy",
                    f"--junitxml={report_path}",
                ],
                cwd=ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=300,
                check=False,
            )
            if not report_path.exists():
                return failure | {
                    "error": "ACCEPTANCE_REPORT_MISSING",
                    "test_exit_code": result.returncode,
                }
            return parse_results(report_path, result.returncode)
        except subprocess.TimeoutExpired:
            return failure | {"error": "ACCEPTANCE_TIMEOUT"}
        except (OSError, ValueError, TypeError, ET.ParseError):
            return failure | {"error": "ACCEPTANCE_RUN_OR_REPORT_INVALID"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write the JSON report atomically")
    args = parser.parse_args(argv)
    report = run()
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=args.output.parent, delete=False
            ) as handle:
                handle.write(text)
                temporary = Path(handle.name)
            try:
                temporary.replace(args.output)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError:
            print(
                json.dumps(
                    {
                        "status": "FAIL",
                        "error": "REPORT_WRITE_FAILED",
                        "execution_enabled": False,
                    }
                )
            )
            return 1
    print(text, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
