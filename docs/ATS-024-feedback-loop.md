# ATS-024 — Automatic fill capture and trade outcomes

ATS-024 closes the PAPER execution feedback loop by converting broker-neutral PAPER fill receipts into journal revisions and deterministic trade outcomes.

## Automatic fill capture

A filled `BrokerSubmissionReceipt` is converted into the canonical domain `Fill` using the broker submission UUID as the fill ID. This makes replay idempotent and avoids manual re-entry.

Only PAPER receipts with status `FILLED` can create journal fills.

## Journal revision

`capture_paper_fill` appends the order/fill to the existing immutable `JournalEntry` aggregate. Duplicate receipts return the existing journal unchanged. `PaperFillJournalService` delegates persistence to the existing append-only `JournalRepositoryProtocol`.

## Trade result

When captured exit quantity exactly matches captured entry quantity, ATS-024 computes:

- weighted average entry and exit prices;
- realized P&L net of captured fees;
- R-multiple using planned per-share stop risk × actual entered quantity;
- win/loss/breakeven outcome.

A partial exit does not close the trade. Exit quantity above captured entry quantity fails closed.

## MAE / MFE

MAE and MFE are derived only from persisted `PositionSnapshot.unrealized_pnl` observations and the same initial-risk denominator used for R-multiple.

- MAE = absolute worst negative unrealized P&L / initial risk;
- MFE = best positive unrealized P&L / initial risk;
- excursion source = `position_snapshots`.

No market excursion is inferred when no position snapshots exist.

## Adherence

ATS-024 records the execution facts that can be determined mechanically from fills:

- entered quantity does not exceed planned quantity;
- LONG weighted average entry is not worse than planned entry;
- SHORT weighted average entry is not worse than planned entry.

This is intentionally narrower than discretionary trade-management adherence. ATS-024 does not infer whether an early exit or other discretionary management decision was justified.

## Safety boundary

The feedback service consumes PAPER receipts only. It does not create orders, approve proposals, change risk policy, or introduce LIVE broker authority.
