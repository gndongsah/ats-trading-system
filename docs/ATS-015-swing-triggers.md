# ATS-015 — Initial Swing Triggers

Status: final Sprint 5 Layer 2 implementation.

## Goal

Convert accepted ATS-013 regime state plus ATS-014 Setup Score into deterministic swing trigger / invalidation candidates without creating orders or bypassing Layer 3.

## Swing prerequisite

The swing strategy page requires Setup Score >=70 before the swing setups are actionable.

ATS-015 therefore rejects swing actionability when:

- `SetupScoreResult.score < 70`; or
- ATS-014 has already marked the setup `NO_TRADE`.

A generic ATS-014 50–69 `UNPROVEN_SIZE` band is not sufficient for these swing triggers.

The trigger timestamp must exactly match the ATS-014 Setup Score timestamp. ATS-014 already binds that score to the ATS-013 regime timestamp.

## 1. Breakout retest

Playbook rule:

- stock clears a flat base;
- breakout occurs on rising volume;
- enter when price retests the breakout level and holds;
- stop 3–5% below the base.

ATS-015 uses the flat-base breakout level as the trigger level.

The candidate supplies the base low and a stop percentage. Only 3% through 5%, inclusive, is accepted. The invalidation level is calculated exactly from the base low and chosen percentage.

Missing breakout, volume or retest confirmation returns `NO_SETUP` with an explicit failure reason.

## 2. Pullback to the 21-day EMA

Playbook rule:

- steady uptrend;
- rising 21-day EMA;
- first or second tap;
- stop below the swing low.

ATS-015 uses the 21-day EMA level as the trigger level.

The playbook does not specify how far below the swing low the stop must sit. ATS-015 therefore requires an explicit invalidation level and validates that it is strictly below the swing low. No hidden offset is invented.

A third-or-later tap returns `NO_SETUP`.

## 3. Breakdown short

Playbook rule:

- stock below a falling 50-day moving average;
- bounce fails at resistance;
- bounce volume contracts;
- trigger when the swing low breaks;
- stop above the failed bounce.

ATS-015 uses the swing-low level as the short trigger level.

The playbook does not specify an offset above the failed bounce. The caller supplies the invalidation level and ATS-015 validates that it is strictly above the failed-bounce high.

Any missing structural confirmation returns `NO_SETUP`.

## Result contract

`SwingTriggerResult` is immutable and records:

- as-of timestamp;
- trigger type;
- long/short side;
- trigger level;
- invalidation level;
- actionability;
- deterministic failure reasons;
- complete ATS-014 Setup Score result;
- trigger-rule version;
- playbook version.

Long triggers require invalidation below trigger. Short triggers require invalidation above trigger.

## Scope boundary

ATS-015 does not:

- calculate quantity;
- calculate portfolio heat;
- authorize Layer 3 risk;
- create a TradePlan or OrderIntent;
- submit anything to a broker;
- implement day-trading triggers;
- invent unquantified technical tolerances.

The output is a Layer 2 candidate only. It must still pass Layer 3 before any execution workflow can proceed.
