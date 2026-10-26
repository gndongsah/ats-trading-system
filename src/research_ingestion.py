"""ATS-011 normalized filings/news adapter for persisted point-in-time evidence."""

import json
from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from src.domain.evidence import SourceDocument
from src.domain.models import _require_utc


class ResearchDocumentInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1, max_length=255)
    version: int = Field(default=1, ge=1)
    source_type: Literal["regulatory_filing", "news"]
    source_url: AnyHttpUrl | None = None
    publisher: str = Field(min_length=1, max_length=255)
    subject: str = Field(min_length=1, max_length=255)
    published_at: datetime
    available_at: datetime
    correction_of_version: int | None = Field(default=None, ge=1)
    content_ref: str | None = Field(default=None, max_length=2048)
    content_hash: str | None = Field(default=None, min_length=64, max_length=64)
    version_metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("published_at", "available_at")
    @classmethod
    def clocks_are_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @field_validator("source_id", "publisher", "subject")
    @classmethod
    def required_text_is_trimmed(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class JsonResearchDocumentAdapter:
    """Normalize synthetic/regression filing and news fixtures.

    The provider payload supplies publication and first-availability time.
    Ingestion time is deliberately absent and belongs to the collector/store.
    """

    def parse(self, document: str) -> tuple[SourceDocument, ...]:
        rows = json.loads(document)
        if not isinstance(rows, list):
            raise TypeError("adapter expects a JSON array")
        parsed: list[SourceDocument] = []
        for row in rows:
            item = ResearchDocumentInput.model_validate(row)
            parsed.append(
                SourceDocument(
                    id=uuid5(
                        NAMESPACE_URL,
                        f"ats-source:{item.source_type}:{item.source_id}:{item.version}",
                    ),
                    source_id=item.source_id,
                    version=item.version,
                    source_type=item.source_type,
                    source_url=item.source_url,
                    publisher=item.publisher,
                    subject=item.subject,
                    published_at=item.published_at,
                    observed_at=item.available_at,
                    available_at=item.available_at,
                    correction_of_version=item.correction_of_version,
                    content_ref=item.content_ref,
                    content_hash=item.content_hash,
                    version_metadata=item.version_metadata,
                )
            )
        return tuple(parsed)
