from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import AnyHttpUrl, Field, field_validator, model_validator

from src.domain.models import EvidenceStatus, ImmutableModel, _require_utc


class SourceDocument(ImmutableModel):
    id: UUID = Field(default_factory=uuid4)
    source_id: str = Field(min_length=1, max_length=255)
    version: int = Field(default=1, ge=1)
    source_type: str = Field(min_length=1, max_length=64)
    source_url: AnyHttpUrl | None = None
    publisher: str = Field(min_length=1, max_length=255)
    subject: str = Field(min_length=1, max_length=255)
    published_at: datetime
    observed_at: datetime
    available_at: datetime | None = None
    ingested_at: datetime | None = None
    correction_of_version: int | None = Field(default=None, ge=1)
    content_ref: str | None = Field(default=None, max_length=2048)
    content_hash: str | None = Field(default=None, min_length=64, max_length=64)
    version_metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("source_id", "source_type", "publisher", "subject")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("published_at", "observed_at", "available_at", "ingested_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value is not None else None

    @field_validator("content_hash")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is not None and any(
            character not in "0123456789abcdefABCDEF" for character in value
        ):
            raise ValueError("content_hash must be a SHA-256 hex digest")
        return value.lower() if value is not None else None

    @field_validator("content_ref")
    @classmethod
    def normalize_content_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def point_in_time_clocks_are_coherent(self) -> "SourceDocument":
        if self.observed_at < self.published_at:
            raise ValueError("source cannot be observed before publication")
        if self.effective_available_at < self.published_at:
            raise ValueError("source cannot be available before publication")
        if self.effective_ingested_at < self.effective_available_at:
            raise ValueError("source cannot be ingested before availability")
        if (
            self.correction_of_version is not None
            and self.correction_of_version >= self.version
        ):
            raise ValueError("correction must reference an earlier source version")
        return self

    @property
    def effective_available_at(self) -> datetime:
        """Return explicit availability or the legacy observation timestamp."""

        return self.available_at or self.observed_at

    @property
    def effective_ingested_at(self) -> datetime:
        """Return explicit ingestion or the earliest legacy point-in-time clock."""

        return self.ingested_at or self.effective_available_at


class EvidenceItem(ImmutableModel):
    id: UUID = Field(default_factory=uuid4)
    version: int = Field(default=1, ge=1)
    source_document_version_id: UUID
    subject: str = Field(min_length=1, max_length=255)
    factor: str = Field(min_length=1, max_length=128)
    inflection_stage: int | None = Field(default=None, ge=1, le=9)
    excerpt: str | None = Field(default=None, max_length=10000)
    metric_name: str | None = Field(default=None, max_length=128)
    metric_value: Decimal | None = Field(default=None, allow_inf_nan=False)
    metric_unit: str | None = Field(default=None, max_length=64)
    confidence: Decimal = Field(ge=Decimal(0), le=Decimal(1))
    verification_status: EvidenceStatus = EvidenceStatus.UNVERIFIED
    verifier_identity: str | None = Field(default=None, max_length=255)
    verified_at: datetime | None = None
    verification_method: str | None = Field(default=None, max_length=128)
    verification_policy_version: str | None = Field(default=None, max_length=64)
    verification_notes: str | None = None
    observed_at: datetime
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(
        "subject",
        "factor",
        "metric_name",
        "metric_unit",
        "verifier_identity",
        "verification_method",
        "verification_policy_version",
    )
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("metric_value", "confidence", mode="before")
    @classmethod
    def reject_boolean_metrics(cls, value: object) -> object:
        if isinstance(value, bool):
            raise TypeError("numeric evidence values must not be boolean")
        return value

    @field_validator("observed_at", "recorded_at", "verified_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_support_shape(self) -> "EvidenceItem":
        has_metric = any(
            value is not None
            for value in (self.metric_name, self.metric_value, self.metric_unit)
        )
        if has_metric and not all(
            value is not None
            for value in (self.metric_name, self.metric_value, self.metric_unit)
        ):
            raise ValueError("structured metrics require a name, value, and unit")
        if self.verification_status is EvidenceStatus.VERIFIED and any(
            value is None
            for value in (
                self.verifier_identity,
                self.verified_at,
                self.verification_method,
                self.verification_policy_version,
            )
        ):
            raise ValueError(
                "verified evidence requires verifier identity, timestamp, method, "
                "and policy version"
            )
        if (
            self.verification_status is EvidenceStatus.VERIFIED
            and self.verified_at is not None
            and self.verified_at < self.observed_at
        ):
            raise ValueError("evidence cannot be verified before it is observed")
        return self
