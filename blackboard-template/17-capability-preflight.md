# Capability Preflight

```text
Preflight status:
Available models:
Available tools:
OSVec status:
GraphOS status:
Browser access:
Constraints:
Recommended route:
```

Status labels: Verified, Optional, Not configured, Not auto-detected, Unavailable, Not used, Claimed but unverified.

For a local optional tool check, run:

```bash
python3 scripts/check_optional_tools.py --target .
```

Or during install:

```bash
./install.sh /path/to/project --check-tools
```

Activation guard: if `memory/build_graph.py`, `memory/osvec_adapter.py`, or legacy `memory/turbovec_adapter.py` exists, the local graph/vector helper path is available even when external Graphify/TurboVec commands are missing. Build the graph with `python3 memory/build_graph.py --root blackboard` or a run folder, and verify OSVec with `python3 memory/osvec_adapter.py selftest` or the legacy adapter selftest before calling memory unavailable.

## Reality Check

The table below is a starter/manual snapshot. If an automated optional tool check is appended later, treat the newest automated report as fresher evidence.

| Capability | Status | Evidence | Limitation / Next Step |
|---|---|---|---|
| Model routing | Not auto-detected | Configure inside the AI tool | Record whether sub-agents inherit the parent model in `11-model-routing.md`. |
| Sub-agent/swarms | Not configured |  |  |
| OSVec | Not configured |  |  |
| GraphOS | Not configured |  |  |
| Browser/UI QA | Not configured |  |  |
| Container isolation | Not configured |  |  |
| Network egress controls | Not configured |  |  |
