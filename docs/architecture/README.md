# ATS Architecture

**Current baseline:** v1.1

This directory is the implementation-facing architecture source of truth for the Agentic Trading System.

## Documents

- [Baseline v1.1](baseline-v1.1.md) — component topology, decision flow, data model, trust boundaries, deployment, runtime sequence, and point-in-time lineage.
- [Cross-tool workflow](../WORKFLOW.md) — how ChatGPT, Notion, GitHub, and VS Code stay synchronized.

## Change-control rule

A material architecture change is not complete until all of the following are true:

1. The relevant diagram/document in this directory is updated.
2. A Notion ADR is added or updated when the decision is material.
3. The Notion backlog/sprint impact is recorded.
4. Code and tests reflect the new invariant.
5. The pull request explains the architecture impact.
6. After merge, Notion status is reconciled to the accepted GitHub tree.

The deterministic Layer 3 Risk Engine remains the only authority that can allow, resize, or deny a proposed order. Human approval is a separate authorization boundary until the autonomy policy explicitly permits otherwise.
