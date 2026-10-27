"""TypeSafe/Jev adapter for ATS-020.

No credential is stored in source control. The adapter reads TYPESAFE_API_KEY at runtime.
"""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from urllib import error as urllib_error
from urllib import request as urllib_request

from pydantic import BaseModel, ConfigDict, Field


TYPESAFE_SYSTEMONE_URL = "https://api.typesafe.ai/v1/systemone"


class JevBooleanAnswer(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str = Field(min_length=1)
    probability_true: float = Field(ge=0.0, le=1.0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


@dataclass(frozen=True)
class TypeSafeJevClient:
    api_key: str
    model: str = "jev-latest"
    endpoint: str = TYPESAFE_SYSTEMONE_URL
    timeout_seconds: float = 15.0

    @classmethod
    def from_environment(cls) -> "TypeSafeJevClient":
        api_key = os.getenv("TYPESAFE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("TYPESAFE_API_KEY is required for live Jev calls")
        model = os.getenv("TYPESAFE_JEV_MODEL", "jev-latest").strip() or "jev-latest"
        return cls(api_key=api_key, model=model)

    def ask_boolean(
        self,
        *,
        state: str | dict[str, object],
        question_name: str,
        instructions: str,
        transport: Callable[[urllib_request.Request, float], bytes] | None = None,
    ) -> JevBooleanAnswer:
        payload = {
            "state": state,
            "model": self.model,
            "questions": {
                question_name: {
                    "instructions": instructions,
                    "type": "noul",
                }
            },
        }
        req = urllib_request.Request(
            self.endpoint,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        if transport is None:
            transport = _urlopen_transport
        raw = transport(req, self.timeout_seconds)
        return parse_boolean_response(raw, question_name=question_name)


def _urlopen_transport(req: urllib_request.Request, timeout_seconds: float) -> bytes:
    try:
        with urllib_request.urlopen(req, timeout=timeout_seconds) as response:
            return response.read()
    except urllib_error.HTTPError as exc:
        raise RuntimeError(f"TypeSafe API returned HTTP {exc.code}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError("TypeSafe API request failed") from exc


def parse_boolean_response(raw: bytes, *, question_name: str) -> JevBooleanAnswer:
    data = json.loads(raw.decode("utf-8"))
    try:
        answer = data["answers"][question_name]
        probability = float(answer["noul"])
        usage = data["usage"]
        model = str(data["model"])
        input_tokens = int(usage["input_tokens"])
        output_tokens = int(usage["output_tokens"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid TypeSafe System One response") from exc

    return JevBooleanAnswer(
        model=model,
        probability_true=probability,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
