# 22 — Automation Log

Written automatically by `scripts/os_nightly.py` — the unattended daily heartbeat
(macOS: a launchd agent; Linux: a cron entry). Do not edit by hand.

Each entry (newest first, capped at 30) reports:

- **gauge** — brain-scale status from `scripts/brain_scale.py` (OK / WATCH / CUTOVER
  against the flat-index ceiling)
- **locks reaped** — stale `bb_lock` files cleaned up
- **stale Draft packets** — worker packets untouched past the staleness window
- **plans stuck running** — plan artifacts that never reached `done`
- **unharvested done runs** — finished runs whose lessons haven't been promoted
  (`scripts/harvest.py status`)

Any WATCH/CUTOVER or stale item here is a standing prompt for the next session to
act. Read this file at kickoff instead of re-deriving drift by hand.

Install (macOS example — adjust paths):

```xml
<!-- ~/Library/LaunchAgents/ai.projectos.nightly.plist -->
<key>ProgramArguments</key>
<array>
  <string>/usr/bin/python3</string>
  <string>/ABSOLUTE/PATH/TO/project-os/scripts/os_nightly.py</string>
</array>
<key>StartCalendarInterval</key>
<dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>23</integer></dict>
```

Then: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.projectos.nightly.plist`

---

(entries appear below once the heartbeat runs)
