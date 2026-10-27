# ATS-018 — Layer 0/1 structured research evidence

Status: Sprint 7 implementation slice.

## Goal

Create a typed boundary between AI-assisted research and the deterministic ATS core.

The research model may extract claims and suggest factor contributions, but it cannot verify its own evidence, compute alternative scoring weights, authorize risk, or submit orders.

## Safety boundary

Architecture Baseline v1.1 requires the AI research sandbox to consume persisted evidence and return typed judgments/evidence references. The trusted core remains responsible for deterministic scoring and risk.

`ResearchEvidenceDraft` therefore contains only:

- persisted `source_document_version_id`;
- subject;
- configured factor name;
- optional inflection stage;
- excerpt and/or complete structured metric;
- model confidence.

It has no verification-status or verifier-provenance fields.

`materialize_unverified_research_evidence` always emits `EvidenceStatus.UNVERIFIED`. A model confidence of 1.0 is still unverified evidence and receives no positive score credit.

## Layer 0 evidence gate

ATS-018 extends the existing evidence eligibility boundary to Market Opportunity scoring. The same fail-closed rules used by Discovery now apply to Layer 0:

- no evidence -> zero allowed contribution;
- missing/untraceable source -> zero;
- publication, availability, ingestion, observation or verification after the decision timestamp -> zero;
- subject/factor mismatch -> zero;
- unverified/rejected evidence -> zero;
- missing verifier provenance -> zero;
- excerpt/structured metric absent -> zero.

The final score is still computed only by the configured weights in `playbook/playbook_v3.yaml`.

## Layer 1 thesis-breaker

The Playbook states that a Discovery candidate without a written thesis-breaker is a story, not a scored candidate.

`score_layer0_layer1_research` therefore fails closed when `thesis_breaker` is blank before returning a combined Layer 0/1 result.

## Deliberate non-goals

This story does not:

- call an external LLM provider;
- let an LLM verify evidence;
- fetch live web content;
- create a TradePlan or OrderIntent;
- change Layer 3 risk policy;
- enable broker execution.

Provider-specific prompting/orchestration can be added later behind this typed contract without changing the deterministic evidence and scoring boundary.
