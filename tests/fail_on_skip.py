"""Opt-in pytest plugin that turns any skipped test into a failed run.

Enabled only by ``--fail-on-skip`` (used by CI) so ordinary local runs without
PostgreSQL keep their explicit skips. It records collection-time skips
(module-level ``pytest.skip``, skipped collectors) and runtime skips (marks,
fixtures, ``pytest.skip``/``importorskip`` in a test), and never masks an existing
non-zero exit status. xfail outcomes are not skips and are unaffected.
"""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.getgroup("ats").addoption(
        "--fail-on-skip",
        action="store_true",
        default=False,
        help="Fail the run if any test or collector is skipped.",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--fail-on-skip", default=False):
        config.pluginmanager.register(_SkipTracker(), "ats-fail-on-skip-tracker")


class _SkipTracker:
    def __init__(self) -> None:
        self.skips: list[str] = []

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.skipped:
            self.skips.append(f"{report.nodeid} (collection)")

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.skipped and not hasattr(report, "wasxfail"):
            self.skips.append(f"{report.nodeid} ({report.when})")

    def pytest_terminal_summary(
        self, terminalreporter: pytest.TerminalReporter
    ) -> None:
        if self.skips:
            terminalreporter.section("fail-on-skip")
            terminalreporter.write_line(
                f"{len(self.skips)} skipped item(s) are not permitted:"
            )
            for entry in self.skips:
                terminalreporter.write_line(f"  SKIPPED {entry}")

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if self.skips and exitstatus in (
            pytest.ExitCode.OK,
            pytest.ExitCode.NO_TESTS_COLLECTED,
        ):
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
