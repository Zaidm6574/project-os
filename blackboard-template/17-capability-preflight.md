# Capability Preflight

```text
Preflight status:
Available models:
Available tools:
Vector Memory status:
Knowledge Graph status:
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

## Reality Check

| Capability | Status | Evidence | Limitation / Next Step |
|---|---|---|---|
| Model routing | Not auto-detected | Configure inside the AI tool | Record whether sub-agents inherit the parent model in `11-model-routing.md`. |
| Sub-agent/swarms | Not configured |  |  |
| Vector Memory | Not configured |  |  |
| Knowledge Graph | Not configured |  |  |
| Browser/UI QA | Not configured |  |  |
| Container isolation | Not configured |  |  |
| Network egress controls | Not configured |  |  |
