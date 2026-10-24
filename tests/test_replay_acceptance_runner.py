import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from src import replay_acceptance as runner


def xml(tmp_path, *, status="", omit=False, duplicate=False, evidence=True):
    from xml.etree.ElementTree import Element, SubElement, tostring

    root = Element("testsuites")
    suite = SubElement(root, "testsuite")
    names = sorted(runner.EXPECTED_CASES)
    if omit:
        names.pop()
    if duplicate:
        names.append(names[0])
    for name in names:
        case = SubElement(suite, "testcase", name=name)
        if evidence:
            properties = SubElement(case, "properties")
            SubElement(
                properties,
                "property",
                name="evidence",
                value=json.dumps(
                    {
                        "source": "SIMULATION-ONLY",
                        "steps": [{"execution_enabled": False}],
                    }
                ),
            )
        if status:
            SubElement(case, status, message="sensitive diagnostics must stay private")
    path = tmp_path / "results.xml"
    path.write_bytes(tostring(root))
    return path


@pytest.mark.parametrize(
    "change",
    ["skipped", "failure", "error", "omit", "duplicate", "no-evidence", "exit"],
)
def test_report_never_passes_incomplete_or_failed_suite(tmp_path, change):
    path = xml(
        tmp_path,
        status=change if change in ("skipped", "failure", "error") else "",
        omit=change == "omit",
        duplicate=change == "duplicate",
        evidence=change != "no-evidence",
    )
    report = runner.parse_results(path, 1 if change == "exit" else 0)
    assert report["status"] == "FAIL"
    assert "sensitive diagnostics" not in json.dumps(report)


def test_complete_report_passes_and_main_writes_identical_json(
    tmp_path, monkeypatch, capsys
):
    report = runner.parse_results(xml(tmp_path), 0)
    assert report["status"] == "PASS" and report["passed"] == 4
    monkeypatch.setattr(runner, "run", lambda: report)
    path = tmp_path / "report.json"
    assert runner.main(["--output", str(path)]) == 0
    assert json.loads(path.read_text()) == json.loads(capsys.readouterr().out) == report


@pytest.mark.parametrize(
    "url", ["", "sqlite:///ats_test", "postgresql://fake:secret@host/production"]
)
def test_invalid_or_missing_database_fails_without_connecting(monkeypatch, capsys, url):
    monkeypatch.setenv("ATS_TEST_DATABASE_URL", url)
    process = Mock(side_effect=AssertionError("must not start pytest"))
    monkeypatch.setattr(runner.subprocess, "run", process)
    assert runner.main([]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["error"] == "DEDICATED_POSTGRES_ATS_TEST_REQUIRED"
    assert "secret" not in json.dumps(report)
    process.assert_not_called()


def test_runner_uses_fixed_suite_and_clears_external_pytest_flags(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ATS_TEST_DATABASE_URL", "postgresql://host/ats_test")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    monkeypatch.setenv("PYTEST_PLUGINS", "unexpected_plugin")
    report = xml(tmp_path)

    def process(command, **kwargs):
        assert command[2:4] == ["pytest", "tests/test_replay_acceptance.py"]
        assert "--fail-on-skip" in command
        assert "PYTEST_ADDOPTS" not in kwargs["env"]
        assert "PYTEST_PLUGINS" not in kwargs["env"]
        assert kwargs["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
        path = Path(
            next(a.split("=", 1)[1] for a in command if a.startswith("--junitxml="))
        )
        path.write_bytes(report.read_bytes())
        return Mock(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", process)
    assert runner.run()["status"] == "PASS"
