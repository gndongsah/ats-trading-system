"""Synthetic tests for the Webull sandbox credential provider (mock keyring only)."""

import copy
import pickle
import traceback

import pytest

from src.webull_sandbox_credentials import (
    KEYRING_SERVICE,
    CredentialError,
    SandboxCredentials,
    credential_status,
    load_sandbox_credentials,
)
from src.webull_sandbox_readonly import ConfigurationError

KEY = "SYNTH-KEY-123"
SECRET = "SYNTH-SECRET-456"
K, S = "WEBULL_APP_KEY", "WEBULL_APP_SECRET"


class FakeKeyring:
    def __init__(self, values=None, error=None):
        self.values = values or {}
        self.error = error
        self.calls = []

    def get_password(self, service, name):
        self.calls.append((service, name))
        if self.error:
            raise self.error
        return self.values.get(name)


def test_service_name_is_sandbox_specific():
    assert KEYRING_SERVICE == "ATS-Webull-Sandbox"


def test_keyring_pair_is_preferred_over_environment():
    kr = FakeKeyring({K: KEY, S: SECRET})
    creds = load_sandbox_credentials(
        environ={K: "env-key", S: "env-secret"}, keyring_module=kr
    )
    assert creds.source == "keyring"
    assert creds.reveal() == (KEY, SECRET)
    assert {c[0] for c in kr.calls} == {"ATS-Webull-Sandbox"}


def test_environment_fallback_when_keyring_empty():
    creds = load_sandbox_credentials(
        environ={K: KEY, S: SECRET}, keyring_module=FakeKeyring()
    )
    assert creds.source == "environment"
    assert creds.reveal() == (KEY, SECRET)


def test_environment_fallback_when_keyring_backend_fails():
    kr = FakeKeyring(error=RuntimeError("backend-secret-detail"))
    creds = load_sandbox_credentials(environ={K: KEY, S: SECRET}, keyring_module=kr)
    assert creds.source == "environment"


def test_fallback_can_be_disabled():
    with pytest.raises(CredentialError):
        load_sandbox_credentials(
            environ={K: KEY, S: SECRET},
            keyring_module=FakeKeyring(),
            allow_environment_fallback=False,
        )


@pytest.mark.parametrize("present", [K, S])
def test_partial_keyring_fails_closed_and_never_mixes_with_environment(present):
    kr = FakeKeyring({present: "kr-value"})
    with pytest.raises(CredentialError, match="partial"):
        load_sandbox_credentials(environ={K: KEY, S: SECRET}, keyring_module=kr)


@pytest.mark.parametrize("present", [K, S])
def test_partial_environment_fails_closed(present):
    with pytest.raises(CredentialError, match="partial"):
        load_sandbox_credentials(environ={present: "v"}, keyring_module=FakeKeyring())


def test_key_from_keyring_and_secret_from_environment_is_never_combined():
    with pytest.raises(CredentialError):
        load_sandbox_credentials(
            environ={S: SECRET}, keyring_module=FakeKeyring({K: KEY})
        )


@pytest.mark.parametrize(
    "bad",
    [
        " lead",
        "trail ",
        "in ner",
        "tab\tx",
        "new\nline",
        "nul\x00x",
        "ünï",
        123,
        b"bytes",
    ],
)
def test_malformed_values_fail_closed(bad):
    with pytest.raises(CredentialError, match="malformed"):
        load_sandbox_credentials(
            environ={}, keyring_module=FakeKeyring({K: bad, S: SECRET})
        )


def test_whitespace_only_value_is_rejected_not_treated_as_absent():
    with pytest.raises(CredentialError):
        load_sandbox_credentials(
            environ={}, keyring_module=FakeKeyring({K: "   ", S: SECRET})
        )


def test_missing_everywhere_fails_closed_with_names_only():
    with pytest.raises(CredentialError) as error:
        load_sandbox_credentials(environ={}, keyring_module=FakeKeyring())
    assert "WEBULL_APP_KEY" in str(error.value)
    assert isinstance(error.value, ConfigurationError)


def test_secrets_not_in_repr_str_pickle_or_copy():
    creds = SandboxCredentials("keyring", KEY, SECRET)
    for text in (repr(creds), str(creds)):
        assert KEY not in text and SECRET not in text
    with pytest.raises(TypeError):
        pickle.dumps(creds)
    with pytest.raises(TypeError):
        copy.deepcopy(creds)


@pytest.mark.parametrize(
    "keyring_values",
    [{K: "bad value", S: SECRET}, {K: KEY}, {}],
)
def test_errors_carry_no_values_or_chains(keyring_values):
    with pytest.raises(CredentialError) as error:
        load_sandbox_credentials(environ={}, keyring_module=FakeKeyring(keyring_values))
    rendered = str(error.value) + "".join(traceback.format_exception(error.value))
    for secret in (KEY, SECRET, "bad value"):
        assert secret not in rendered
    assert error.value.__cause__ is None


def test_backend_exception_text_never_surfaces():
    kr = FakeKeyring(error=RuntimeError("backend-secret-detail"))
    with pytest.raises(CredentialError) as error:
        load_sandbox_credentials(environ={}, keyring_module=kr)
    rendered = "".join(traceback.format_exception(error.value))
    assert "backend-secret-detail" not in rendered
    assert "unavailable" in str(error.value)


def test_status_reports_only_configured_or_missing():
    assert (
        credential_status(environ={}, keyring_module=FakeKeyring({K: KEY, S: SECRET}))
        == "CONFIGURED"
    )
    assert credential_status(environ={}, keyring_module=FakeKeyring()) == "MISSING"
    assert (
        credential_status(environ={}, keyring_module=FakeKeyring({K: KEY})) == "MISSING"
    )
    assert (
        credential_status(environ={}, keyring_module=FakeKeyring({K: " x", S: SECRET}))
        == "MISSING"
    )


def test_module_has_no_live_broker_or_output_capability():
    from pathlib import Path

    import src.webull_sandbox_credentials as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    for token in (
        "print(",
        "logging",
        "set_password",
        "delete_password",
        "api.webull.com",
        "place_order",
    ):
        assert token not in source
