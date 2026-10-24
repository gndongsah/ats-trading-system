from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def steps():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return {
        step["name"]: step
        for step in workflow["jobs"]["tests"]["steps"]
        if "name" in step
    }


def test_full_suite_step_fails_on_any_skip():
    step = steps()["Full test suite"]
    command = step["run"].split()
    assert command[:3] == ["python", "-m", "pytest"]
    assert "--fail-on-skip" in command
    assert not step.get("continue-on-error")
    assert "||" not in step["run"]


def test_postgres_step_fails_on_any_skip_and_keeps_text_guard():
    step = steps()["PostgreSQL integration tests"]
    assert "--fail-on-skip" in step["run"]
    assert "set -o pipefail" in step["run"]
    assert "skipped|SKIPPED" in step["run"]
    assert not step.get("continue-on-error")


def test_ci_provides_a_disposable_test_database_to_the_suite():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["tests"]
    assert str(job["env"]["ATS_TEST_DATABASE_URL"]).endswith("/ats_test")
    assert job["services"]["postgres"]["env"]["POSTGRES_DB"] == "ats_test"


def test_ci_runs_replay_acceptance_and_retains_report_on_failure():
    report = steps()["Replay simulation acceptance report"]
    assert (
        report["run"]
        == "python -m src.replay_acceptance --output replay-acceptance.json"
    )
    assert report["if"] == "always()" and not report.get("continue-on-error")
    artifact = steps()["Upload replay acceptance report"]
    assert artifact["if"] == "always()"
    assert artifact["with"]["path"] == "replay-acceptance.json"
