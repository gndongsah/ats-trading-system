"""ATS-009C4 Webull SANDBOX credential provider.

Scope: Webull sandbox read-only validation only. There is no live-broker
credential handling here. The service name is sandbox-specific.

Credentials are accepted only as a complete, well-formed pair from a single
source: the OS keyring first, then (optionally) environment variables. A partial
or malformed pair in any source fails closed; sources are never mixed. Secret
values are excluded from repr/str, cannot be pickled, and are never placed in
exception messages or chains.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from src.webull_sandbox_readonly import (
    APP_KEY_ENV,
    APP_SECRET_ENV,
    ConfigurationError,
)

KEYRING_SERVICE = "ATS-Webull-Sandbox"
CREDENTIAL_NAMES = (APP_KEY_ENV, APP_SECRET_ENV)
SOURCE_KEYRING = "keyring"
SOURCE_ENVIRONMENT = "environment"
STATUS_CONFIGURED = "CONFIGURED"
STATUS_MISSING = "MISSING"


class CredentialError(ConfigurationError):
    """Credential problem; messages carry names and sources, never values."""


@dataclass(frozen=True)
class SandboxCredentials:
    source: str
    _app_key: str = field(repr=False)
    _app_secret: str = field(repr=False)

    def reveal(self) -> tuple[str, str]:
        """Return (app_key, app_secret) for SDK construction only."""
        return self._app_key, self._app_secret

    def __str__(self) -> str:
        return f"SandboxCredentials(source={self.source!r})"

    def __reduce__(self) -> Any:
        raise TypeError("SandboxCredentials cannot be serialized")


def _well_formed(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value.isascii()
        and value.isprintable()
        and not any(ch.isspace() for ch in value)
    )


def _evaluate(source: str, values: Mapping[str, object]) -> SandboxCredentials | None:
    """None when both absent; credentials when both valid; error otherwise."""
    present = [name for name in CREDENTIAL_NAMES if values.get(name) not in (None, "")]
    if not present:
        return None
    if len(present) != len(CREDENTIAL_NAMES):
        missing = ", ".join(n for n in CREDENTIAL_NAMES if n not in present)
        raise CredentialError(f"{source} credentials are partial; missing: {missing}")
    bad = [n for n in CREDENTIAL_NAMES if not _well_formed(values[n])]
    if bad:
        raise CredentialError(f"{source} credentials are malformed: " + ", ".join(bad))
    return SandboxCredentials(
        source, str(values[APP_KEY_ENV]), str(values[APP_SECRET_ENV])
    )


def _read_keyring(keyring_module: Any) -> dict[str, object] | None:
    """Values from the keyring, or None when the backend is unusable."""
    try:
        return {
            name: keyring_module.get_password(KEYRING_SERVICE, name)
            for name in CREDENTIAL_NAMES
        }
    except Exception:  # noqa: BLE001 - backend text may be sensitive; not chained.
        return None


def load_sandbox_credentials(
    *,
    environ: Mapping[str, str] | None = None,
    keyring_module: Any | None = None,
    allow_environment_fallback: bool = True,
) -> SandboxCredentials:
    if keyring_module is None:
        try:
            import keyring as keyring_module  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001
            keyring_module = None

    keyring_values = _read_keyring(keyring_module) if keyring_module is not None else None
    if keyring_values is not None:
        found = _evaluate(SOURCE_KEYRING, keyring_values)
        if found is not None:
            return found

    if allow_environment_fallback:
        if environ is None:
            import os

            environ = os.environ
        found = _evaluate(SOURCE_ENVIRONMENT, environ)
        if found is not None:
            return found

    detail = "keyring" + (" or environment" if allow_environment_fallback else "")
    if keyring_values is None:
        detail += " (keyring backend unavailable)"
    raise CredentialError(
        f"sandbox credentials not configured in {detail}: " + ", ".join(CREDENTIAL_NAMES)
    )


def credential_status(
    *,
    environ: Mapping[str, str] | None = None,
    keyring_module: Any | None = None,
    allow_environment_fallback: bool = True,
) -> str:
    """CONFIGURED only for a complete, valid pair; otherwise MISSING."""
    try:
        load_sandbox_credentials(
            environ=environ,
            keyring_module=keyring_module,
            allow_environment_fallback=allow_environment_fallback,
        )
    except CredentialError:
        return STATUS_MISSING
    return STATUS_CONFIGURED


def credential_source(
    *,
    environ: Mapping[str, str] | None = None,
    keyring_module: Any | None = None,
    allow_environment_fallback: bool = True,
) -> str:
    """Label of the source load_sandbox_credentials would use, else MISSING."""
    try:
        return load_sandbox_credentials(
            environ=environ,
            keyring_module=keyring_module,
            allow_environment_fallback=allow_environment_fallback,
        ).source
    except CredentialError:
        return STATUS_MISSING


def compare_sources(
    *,
    environ: Mapping[str, str] | None = None,
    keyring_module: Any | None = None,
) -> str:
    """MATCH, MISMATCH, or UNAVAILABLE when either source lacks a valid pair.

    Comparison is constant-time and in-process; no digest or length is exposed.
    """
    if keyring_module is None:
        try:
            import keyring as keyring_module  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001
            return "UNAVAILABLE"
    if environ is None:
        import os

        environ = os.environ
    values = _read_keyring(keyring_module)
    try:
        from_keyring = _evaluate(SOURCE_KEYRING, values) if values is not None else None
        from_environment = _evaluate(SOURCE_ENVIRONMENT, environ)
    except CredentialError:
        return "UNAVAILABLE"
    if from_keyring is None or from_environment is None:
        return "UNAVAILABLE"
    same = all(
        hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
        for left, right in zip(from_keyring.reveal(), from_environment.reveal(), strict=True)
    )
    return "MATCH" if same else "MISMATCH"
