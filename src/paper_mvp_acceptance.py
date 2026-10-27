"""Repeatable PAPER MVP end-to-end acceptance command.

Runs one fixed synthetic trading-day acceptance case. It never connects to a broker
and never enables LIVE execution.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

EXPECTED_CASE = "test_complete_paper_day_requires_only_human_approval"
ROOT = Path(__file__).resolve().parents[1]


def parse_results(path: Path, returncode: int) -> dict:
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    if len(cases) != 1:
        return {
            "schema_version": 1,
            "source": "SIMULATION-ONLY",
            "status": "FAIL",
            "live_execution_enabled": False,
            "error": "UNEXPECTED_CASE_COUNT",
        }

    case = cases[0]
    status = "PASS"
    if case.find("skipped") is not None:
        status = "SKIP"
    elif case.find("failure") is not None or case.find("error") is not None:
        status = "FAIL"

    evidence = None
    properties = case.find("properties")
    if properties is not None:
        values = [
            item.get("value")
            for item in properties
            if item.get("name") == "evidence"
        ]
        if len(values) == 1:
            evidence = json.loads(values[0])

    passed = (
        returncode == 0
        and case.get("name") == EXPECTED_CASE
        and status == "PASS"
        and isinstance(evidence, dict)
        and evidence.get("source") == "SIMULATION-ONLY"
        and evidence.get("manual_step_types") == ["HUMAN_APPROVAL"]
        and evidence.get("live_execution_used") is False
        and evidence.get("autonomy_policy_used") is False
        and evidence.get("paper_fills") == 2
        and evidence.get("journal_revisions") == 2
    )
    return {
        "schema_version": 1,
        "source": "SIMULATION-ONLY",
        "status": "PASS" if passed else "FAIL",
        "live_execution_enabled": False,
        "test_exit_code": returncode,
        "case": {
            "name": case.get("name"),
            "status": status,
            "evidence": evidence,
        },
    }


def run() -> dict:
    failure = {
        "schema_version": 1,
        "source": "SIMULATION-ONLY",
        "status": "FAIL",
        "live_execution_enabled": False,
    }
    with tempfile.TemporaryDirectory(prefix="ats-paper-mvp-acceptance-") as temp:
        report_path = Path(temp) / "results.xml"
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/test_paper_mvp_acceptance.py",
                    "-q",
                    "--fail-on-skip",
                    "-o",
                    "addopts=",
                    "-o",
                    "junit_family=legacy",
                    f"--junitxml={report_path}",
                ],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=180,
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
        except (OSError, ValueError, TypeError, ET.ParseError, json.JSONDecodeError):
            return failure | {"error": "ACCEPTANCE_RUN_OR_REPORT_INVALID"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = run()
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
