# ATS-019 — Critic and evidence verifier workflow

Status: Sprint 7 implementation.

ATS-019 adds a mandatory second pass between model-produced research evidence and the deterministic scoring boundary established by ATS-018.

## Critic pass

The critic receives the thesis, written thesis-breaker, researcher identity, critic identity and typed evidence references.

The critic must:

- use a distinct identity from the originating researcher;
- challenge at least one claim;
- mark each finding as supported, contradicted or insufficient;
- cite exact evidence/source versions for supported or contradicted findings;
- fail closed when contradictions, insufficient support, missing evidence, version mismatch, source mismatch or subject mismatch are detected.

A blank thesis-breaker is rejected before the critique is valid.

## Verifier pass

The critic does not automatically verify evidence.

Verification requires a separate `EvidenceVerificationAttestation` containing:

- verifier identity;
- verification timestamp;
- verification method;
- policy version;
- accepted/rejected decision;
- optional notes.

The verifier identity must differ from the originating researcher identity.

Applying an attestation creates the next immutable version of the same evidence item. The original version remains UNVERIFIED. Accepted attestations create VERIFIED versions; rejected attestations create REJECTED versions.

The existing eligibility boundary remains authoritative. Therefore only the verified version can become eligible for positive factor contribution; the original unverified version remains ineligible.

## Deliberate limits

ATS-019 does not add external-model orchestration, automatic source fetching, or any execution-path authority. It only establishes the typed second-pass and verification contracts required by Sprint 7.
