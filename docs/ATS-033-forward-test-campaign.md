# ATS-033 Forward-Test Campaign Boundary

ATS-033 moves the accepted ATS-031/032 PAPER forward-test infrastructure into a controlled campaign identity.

## First slice

`src/forward_test_campaign.py` adds:

- an immutable `ForwardTestCampaignManifest`;
- PAPER-only enforcement;
- an 8–12 week campaign window;
- frozen playbook version and SHA-256 digest;
- frozen setup scope and operational-policy version;
- a deterministic campaign manifest digest;
- a fail-closed preflight check for playbook identity drift.

Focused tests cover valid campaign construction, invalid duration, invalid/duplicate setup scope, non-PAPER reconstruction, playbook drift, digest stability and UTC requirements.

## Safety boundary

This slice does not enable LIVE execution, does not alter Layer 3 authorization, and does not treat ATS-009 broker-backed loss-lock evidence as satisfied.

## Follow-on work inside ATS-033

The next slice should persist the campaign manifest, bind each forward-test run to a campaign identifier/digest, and provide a command/service boundary that refuses to start or continue a campaign when preflight fails.
