"""Operator CLI for ATS-036C PAPER campaign control."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from src.database import create_database_engine
from src.forward_test_campaign_ops import (
    CampaignArtifactRepository,
    CampaignOperationsService,
)
from src.forward_test_scheduler import CampaignScheduler


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include timezone")
    return parsed


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _service(args) -> CampaignOperationsService:
    engine = create_database_engine(args.database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return CampaignOperationsService(
        session_factory=factory,
        artifacts=CampaignArtifactRepository(args.artifact_dir),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ATS-036C immutable PAPER campaign operations"
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("artifacts/forward-test"),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    next_start = sub.add_parser("next-start")
    next_start.add_argument("--after", type=_datetime, required=True)

    initialize = sub.add_parser("init")
    initialize.add_argument("--start-at", type=_datetime, required=True)
    initialize.add_argument("--created-at", type=_datetime)
    initialize.add_argument("--operational-policy-version", required=True)

    status = sub.add_parser("status")
    status.add_argument("--campaign-id", type=UUID, required=True)

    plan = sub.add_parser("plan-day")
    plan.add_argument("--campaign-id", type=UUID, required=True)
    plan.add_argument("--trading-day", type=_date, required=True)

    scorecard = sub.add_parser("scorecard")
    scorecard.add_argument("--campaign-id", type=UUID, required=True)
    scorecard.add_argument("--trading-day", type=_date, required=True)
    scorecard.add_argument("--generated-at", type=_datetime, required=True)

    weekly = sub.add_parser("weekly-report")
    weekly.add_argument("--campaign-id", type=UUID, required=True)
    weekly.add_argument("--week-start", type=_date, required=True)
    weekly.add_argument("--generated-at", type=_datetime, required=True)

    readiness = sub.add_parser("readiness")
    readiness.add_argument("--campaign-id", type=UUID, required=True)
    readiness.add_argument("--evaluated-at", type=_datetime, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    scheduler = CampaignScheduler()

    if args.command == "next-start":
        print(
            json.dumps(
                {
                    "next_start_at": scheduler.next_start_at(args.after).isoformat(),
                    "schedule_policy": scheduler.policy.model_dump(mode="json"),
                },
                default=str,
            )
        )
        return 0

    service = _service(args)
    if args.command == "init":
        result = service.initialize_campaign(
            start_at=args.start_at,
            created_at=args.created_at,
            operational_policy_version=args.operational_policy_version,
        )
    elif args.command == "status":
        result = service.load_campaign(args.campaign_id)
    elif args.command == "plan-day":
        campaign = service.load_campaign(args.campaign_id)
        result = scheduler.jobs_for_day(
            campaign=campaign,
            trading_day=args.trading_day,
        )
    elif args.command == "scorecard":
        result = service.persist_daily_scorecard(
            campaign_id=args.campaign_id,
            trading_day=args.trading_day,
            generated_at=args.generated_at,
        )
    elif args.command == "weekly-report":
        result = service.persist_weekly_report(
            campaign_id=args.campaign_id,
            week_start=args.week_start,
            generated_at=args.generated_at,
        )
    else:
        result = service.persist_readiness(
            campaign_id=args.campaign_id,
            evaluated_at=args.evaluated_at,
        )

    if isinstance(result, tuple):
        payload = [item.model_dump(mode="json") for item in result]
    else:
        payload = result.model_dump(mode="json")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
