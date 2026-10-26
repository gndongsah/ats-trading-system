"""ATS-012 unified historical replay and proposal gating."""

from collections.abc import Callable
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Generic, Literal, TypeVar

from pydantic import Field, StrictInt, field_validator

from src.domain.evidence import SourceDocument
from src.domain.models import _require_utc
from src.market_data import (
    FrozenModel,
    MarketDataStore,
    ReplayRequirement,
    ReplaySnapshot,
    ReplayUnavailable,
)
from src.persistence.evidence_repository import EvidenceRepository

T = TypeVar("T")


class ResearchReplayRequirement(FrozenModel):
    """One required research-evidence domain for a historical decision."""

    subject: str = Field(min_length=1, max_length=255)
    source_type: Literal["regulatory_filing", "news"]
    max_publication_age: timedelta = Field(ge=timedelta(0))
    min_sources: StrictInt = Field(default=1, ge=1)

    @field_validator("subject")
    @classmethod
    def normalize_subject(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("research subject must not be blank")
        return normalized


class ResearchReplaySelection(FrozenModel):
    requirement: ResearchReplayRequirement
    sources: tuple[SourceDocument, ...]


class HistoricalReplayUnavailable(ValueError):
    """A required historical input was missing or stale."""


class HistoricalReplayContext(FrozenModel):
    """Immutable then-known decision context for one historical timestamp."""

    as_of: datetime
    market: ReplaySnapshot
    research: tuple[ResearchReplaySelection, ...]

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @property
    def digest(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()


class HistoricalReplayService:
    """Compose persisted market and research evidence as of one timestamp."""

    def __init__(self, session) -> None:
        self._market = MarketDataStore(session)
        self._evidence = EvidenceRepository(session)

    def replay(
        self,
        *,
        as_of: datetime,
        market_requirements: tuple[ReplayRequirement, ...],
        research_requirements: tuple[ResearchReplayRequirement, ...],
    ) -> HistoricalReplayContext:
        as_of = _require_utc(as_of)
        if not market_requirements:
            raise ValueError("explicit market replay requirements are required")
        if not research_requirements:
            raise ValueError("explicit research replay requirements are required")

        research_keys = [
            (requirement.subject.casefold(), requirement.source_type)
            for requirement in research_requirements
        ]
        if len(set(research_keys)) != len(research_keys):
            raise ValueError("duplicate research replay requirement")

        try:
            market = self._market.replay(
                as_of=as_of,
                requirements=market_requirements,
            )
        except ReplayUnavailable as exc:
            raise HistoricalReplayUnavailable(f"MARKET:{exc}") from exc

        selections: list[ResearchReplaySelection] = []
        for requirement in sorted(
            research_requirements,
            key=lambda item: (item.subject.casefold(), item.source_type),
        ):
            then_known = self._evidence.list_sources_as_of(
                as_of=as_of,
                subject=requirement.subject,
                source_types=(requirement.source_type,),
            )
            if not then_known:
                raise HistoricalReplayUnavailable(
                    f"MISSING_RESEARCH:{requirement.subject}:{requirement.source_type}"
                )

            fresh = tuple(
                source
                for source in then_known
                if timedelta(0)
                <= as_of - source.published_at
                <= requirement.max_publication_age
            )
            if len(fresh) < requirement.min_sources:
                raise HistoricalReplayUnavailable(
                    f"STALE_RESEARCH:{requirement.subject}:{requirement.source_type}"
                )
            selections.append(
                ResearchReplaySelection(
                    requirement=requirement,
                    sources=fresh[: requirement.min_sources],
                )
            )

        return HistoricalReplayContext(
            as_of=as_of,
            market=market,
            research=tuple(selections),
        )


class HistoricalProposalGate(Generic[T]):
    """Invoke proposal creation only after unified historical replay succeeds."""

    def __init__(self, replay_service: HistoricalReplayService) -> None:
        self._replay_service = replay_service

    def create_proposal(
        self,
        *,
        as_of: datetime,
        market_requirements: tuple[ReplayRequirement, ...],
        research_requirements: tuple[ResearchReplayRequirement, ...],
        proposal_factory: Callable[[HistoricalReplayContext], T],
    ) -> T:
        context = self._replay_service.replay(
            as_of=as_of,
            market_requirements=market_requirements,
            research_requirements=research_requirements,
        )
        return proposal_factory(context)
