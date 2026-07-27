#!/usr/bin/env python3
"""os_nightly — the unattended heartbeat of Project OS (launchd: ai.projectos.nightly).

Runs once a day and does three cheap, local, zero-dependency checks:
  1. brain_scale gauge   — is the shared brain approaching the flat-index ceiling?
  2. lock reaping        — clear stale bb_lock files older than STALE_AFTER_SEC
  3. staleness sweep     — Draft packets untouched > 7 days, plans stuck "running" > 7 days

Results are prepended to blackboard/22-automation-log.md (newest first, capped at
30 entries) so any session — human or agent — sees drift without being asked to look.

Exit code mirrors brain_scale severity: 0 OK, 1 WATCH, 2 CUTOVER, 3 n/a (brain missing).
Manual run:  python3 scripts/os_nightly.py
"""
import os, sys, json, glob, time, datetime, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
import bb_lock

LOG = os.path.join(ROOT, "blackboard", "22-automation-log.md")
PACKETS = os.path.join(ROOT, "blackboard", "packets")
PLANS = os.path.join(ROOT, "blackboard", "plans")
STALE_DAYS = 7
MAX_ENTRIES = 30

HEADER = """# 22 — Automation Log

Written by `scripts/os_nightly.py` (launchd agent `ai.projectos.nightly`, daily 08:23).
Newest first, capped at 30 entries. Any WATCH/CUTOVER or stale item here is a
standing prompt for the next session to act — see 18-project-pipeline for the
cutover milestone.

"""


def run_gauge():
    """Run brain_scale.py; return (status_word, one_line_summary, exit_code)."""
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "brain_scale.py")],
                       capture_output=True, text=True, timeout=120)
    out = (r.stdout or "").strip()
    # brain_scale prints an "OVERALL:" line; fall back to the last line
    status = next((l for l in out.splitlines() if "OVERALL" in l.upper() or "STATUS" in l.upper()),
                  out.splitlines()[-1] if out else "no output")
    return status.strip(), r.returncode


def reap_locks():
    """Reap stale locks; return count removed."""
    n = 0
    lock_dir = bb_lock.LOCK_DIR
    if os.path.isdir(lock_dir):
        for lp in glob.glob(os.path.join(lock_dir, "*.lock")):
            before = os.path.exists(lp)
            bb_lock.reap_one(lp)
            if before and not os.path.exists(lp):
                n += 1
    return n


def stale_packets():
    """Draft packets untouched for > STALE_DAYS."""
    cutoff = time.time() - STALE_DAYS * 86400
    out = []
    for fp in glob.glob(os.path.join(PACKETS, "*.md")):
        if os.path.basename(fp).lower() == "readme.md":  # docs, not a packet
            continue
        try:
            if os.path.getmtime(fp) < cutoff:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    if "Status: Draft" in f.read():
                        out.append(os.path.basename(fp))
        except OSError:
            continue
    return sorted(out)


def stuck_plans():
    """Plans in status 'running' whose file hasn't moved in > STALE_DAYS."""
    cutoff = time.time() - STALE_DAYS * 86400
    out = []
    for fp in glob.glob(os.path.join(PLANS, "*.json")):
        try:
            with open(fp, encoding="utf-8") as f:
                p = json.load(f)
            if p.get("status") == "running" and os.path.getmtime(fp) < cutoff:
                done = sum(1 for s in p.get("steps", []) if s.get("done"))
                out.append(f"{p.get('id')} ({done}/{len(p.get('steps', []))} steps)")
        except (OSError, ValueError, AttributeError):
            # 2026-07-25: non-dict JSON (e.g. `[]`) parses fine but has no
            # .get(), so a bare AttributeError must be caught alongside the
            # read/parse errors or it crashes the whole nightly heartbeat.
            out.append(os.path.basename(fp) + " (unreadable)")
    return sorted(out)


def write_entry(lines):
    """Prepend a dated entry to the log under bb_lock, keep newest MAX_ENTRIES."""
    entry = "## " + datetime.datetime.now().isoformat(timespec="minutes") + "\n" \
            + "\n".join("- " + l for l in lines) + "\n\n"
    if not bb_lock.acquire(LOG, agent="nightly", wait=15):
        print("FAILED: could not lock automation log", file=sys.stderr)
        return False
    try:
        old_entries = []
        if os.path.exists(LOG):
            with open(LOG, encoding="utf-8") as f:
                body = f.read().split("\n## ")
            old_entries = ["## " + e.rstrip() + "\n\n" for e in body[1:]]
        with open(LOG, "w", encoding="utf-8") as f:
            f.write(HEADER + entry + "".join(old_entries[:MAX_ENTRIES - 1]))
        return True
    finally:
        bb_lock.release(LOG, agent="nightly", force=True)


def main():
    # 2026-07-26: on a fresh clone `blackboard/` does not exist yet (the
    # installer creates it from blackboard-template/), so write_entry's
    # open(LOG, "w") died with a raw FileNotFoundError traceback after the
    # heartbeat had already done all of its work. Say what is missing and how
    # to fix it instead.
    bb_dir = os.path.dirname(LOG)
    if not os.path.isdir(bb_dir):
        print(f"FAILED: no blackboard directory at {bb_dir} — this workspace is "
              "not initialized. Run `./install.sh <target>` (or copy "
              "blackboard-template/ to blackboard/) and re-run the nightly.",
              file=sys.stderr)
        sys.exit(2)
    lines = []
    try:
        gauge, code = run_gauge()
    except Exception as e:  # keep the heartbeat alive even if the gauge breaks
        gauge, code = f"brain_scale FAILED: {e}", 2
    lines.append(f"gauge: {gauge}")
    lines.append(f"locks reaped: {reap_locks()}")
    sp = stale_packets()
    lines.append(f"stale Draft packets (>{STALE_DAYS}d): "
                 + (", ".join(sp) if sp else "none"))
    st = stuck_plans()
    lines.append(f"plans stuck running (>{STALE_DAYS}d): "
                 + (", ".join(st) if st else "none"))
    try:
        import harvest
        u = harvest.unharvested()
        lines.append("unharvested done runs: " + (", ".join(u) if u else "none"))
    except Exception as e:
        lines.append(f"harvest check failed: {e}")
    ok = write_entry(lines)
    for l in lines:
        print(l)
    sys.exit(code if ok else 2)


if __name__ == "__main__":
    main()
