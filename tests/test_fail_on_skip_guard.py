import pytest

PLUGIN = ["-p", "tests.fail_on_skip", "--fail-on-skip", "-p", "no:cacheprovider"]


def run(pytester, source: str, *flags: str, name: str = "test_sample.py"):
    pytester.makepyfile(**{name.removesuffix(".py"): source})
    return pytester.runpytest(*PLUGIN, *flags)


def test_runtime_skip_fails_the_run(pytester):
    result = run(
        pytester,
        "import pytest\n\ndef test_a():\n    pytest.skip('no database')\n",
    )
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["*SKIPPED*test_a (call)*"])


def test_skipif_mark_fails_the_run(pytester):
    result = run(
        pytester,
        "import pytest\n\n@pytest.mark.skipif(True, reason='x')\ndef test_a():\n    pass\n",
    )
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_fixture_setup_skip_fails_the_run(pytester):
    result = run(
        pytester,
        "import pytest\n\n@pytest.fixture\ndef db():\n    pytest.skip('no db')\n\n"
        "def test_a(db):\n    pass\n",
    )
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["*SKIPPED*test_a (setup)*"])


def test_collection_time_module_skip_fails_the_run(pytester):
    result = run(
        pytester,
        "import pytest\n\npytest.skip('module gated', allow_module_level=True)\n\n"
        "def test_a():\n    pass\n",
    )
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["*SKIPPED*(collection)*"])


def test_importorskip_of_missing_dependency_fails_the_run(pytester):
    result = run(
        pytester,
        "import pytest\n\npytest.importorskip('definitely_missing_ats_module')\n\n"
        "def test_a():\n    pass\n",
    )
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_skips_are_allowed_without_the_flag(pytester):
    pytester.makepyfile(
        test_sample="import pytest\n\ndef test_a():\n    pytest.skip('local')\n"
    )
    result = pytester.runpytest(
        "-p", "tests.fail_on_skip", "-rs", "-p", "no:cacheprovider"
    )
    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*SKIPPED*local*"])


def test_clean_run_passes_with_the_flag(pytester):
    result = run(pytester, "def test_a():\n    assert True\n")
    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1)


def test_existing_failures_are_preserved_and_skips_still_reported(pytester):
    result = run(
        pytester,
        "import pytest\n\ndef test_bad():\n    assert False\n\n"
        "def test_skipped():\n    pytest.skip('x')\n",
    )
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.assert_outcomes(failed=1, skipped=1)


def test_other_nonzero_exit_codes_are_not_masked(pytester):
    result = run(
        pytester,
        "import pytest\n\ndef test_skipped():\n    pytest.skip('x')\n",
        "-x",
        "--no-such-option-ats",
    )
    assert result.ret == pytest.ExitCode.USAGE_ERROR


def test_xfail_is_not_treated_as_a_skip(pytester):
    result = run(
        pytester,
        "import pytest\n\n@pytest.mark.xfail(reason='known')\ndef test_a():\n    assert False\n",
    )
    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(xfailed=1)
