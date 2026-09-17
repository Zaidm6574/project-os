#!/usr/bin/env python3
"""os_nightly — the unattended heartbeat of Project OS (launchd: ai.projectos.nightly).

Runs once a day and does three cheap, local, zero-dependency checks:
  1. brain_scale gauge   — is the shared brain approaching the flat-index ceiling?
  2. lock reaping        — clear stale bb_lock files older than STALE_AFTER_SEC
  3. staleness sweep     — Draft packets untouched > 7 days, plans stuck "running" > 7 days

Entries land in blackboard/22-automation-log.md below the marker line (newest
first, capped at 30); everything above the marker — template docs and manual
notes — is preserved verbatim.

Exit code: 0 = nightly ran and logged (gauge severity lives in the log entry and
the notification, NOT the exit code); 1 = the nightly itself failed (log not
written). When anything needs action (gauge WATCH/CUTOVER, stuck plans, stale
packets, unharvested runs) it fires ONE macOS notification naming the single
most overdue action — silent on all-clear days, so the channel stays trusted.
Manual run:  python3 scripts/os_nightly.py

Install (macOS launchd — save as ~/Library/LaunchAgents/ai.projectos.nightly.plist,
then `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.projectos.nightly.plist`):

  <?xml version="1.0" encoding="UTF-8"?>
  <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
  <plist version="1.0"><dict>
    <key>Label</key><string>ai.projectos.nightly</string>
    <key>ProgramArguments</key><array>
      <string>/usr/bin/python3</string>
      <string>/ABSOLUTE/PATH/TO/project-os/scripts/os_nightly.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>23</integer></dict>
  </dict></plist>

Linux: a cron entry works the same — `23 8 * * * /usr/bin/python3 /ABS/PATH/scripts/os_nightly.py`.
"""
import os, sys, json, glob, time, datetime, subprocess, stat, tempfile, re

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
MARKER = "<!-- os-nightly entries below — manual notes above this line are preserved -->"
ENTRY_START = "<!-- os-nightly:entry:start -->"
ENTRY_END = "<!-- os-nightly:entry:end -->"


def run_gauge():
    """Return (summary, severity_code), or (failure_summary, None) on a crash."""
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "brain_scale.py")],
                       capture_output=True, text=True, timeout=120)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0 and not out:
        # a crash must read as a crash, not "no output" (nonzero exits WITH
        # output are legitimate severities: 1 WATCH, 2 CUTOVER, 3 n/a);
        # the LAST stderr line carries the actual error for a traceback
        detail = err.splitlines()[-1].strip() if err else "no stderr"
        return f"brain_scale CRASHED (exit {r.returncode}): {detail}", None
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
            if not isinstance(p, dict):
                out.append(os.path.basename(fp) + " (unreadable)")
                continue
            steps = p.get("steps")
            if not isinstance(steps, list):
                steps = []
            if p.get("status") == "running" and os.path.getmtime(fp) < cutoff:
                done = sum(1 for s in steps if isinstance(s, dict) and s.get("done"))
                out.append(f"{p.get('id')} ({done}/{len(steps)} steps)")
        except (OSError, ValueError):
            out.append(os.path.basename(fp) + " (unreadable)")
    return sorted(out)


def _commit_if_owned(token, temp_path):
    """Fence ownership and replace the log while the lock guard is held."""
    lock_file = bb_lock.lock_path(LOG)
    with bb_lock._guard(lock_file):
        info = bb_lock.read_lock(lock_file)
        if info is None or info.get("token") != token:
            return False
        os.utime(lock_file, None)
        os.replace(temp_path, LOG)
        os.utime(lock_file, None)
        return True


def _read_text_preserving_eol(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _detect_eol(text):
    match = re.search(r"\r\n|\n|\r", text)
    return match.group(0) if match else "\n"


def _normalize_eol(text, eol):
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", eol)


def _consume_one_eol(text, position):
    if text.startswith("\r\n", position):
        return position + 2
    if position < len(text) and text[position] in "\r\n":
        return position + 1
    return position


def _drop_managed_separator(text):
    position = 0
    for _ in range(2):
        next_position = _consume_one_eol(text, position)
        if next_position == position:
            break
        position = next_position
    return text[position:]


def _blank_line_suffix(text, eol):
    if not text or text.endswith(eol + eol):
        return ""
    if text.endswith(eol):
        return eol
    return eol + eol


def _append_raw_slice(left, right, eol):
    if not right:
        return left
    return left + _blank_line_suffix(left, eol) + right


def _partition_log_content(text, eol):
    """Separate sentinel-owned entries from raw manual prose."""
    manual_parts = []
    entries = []
    position = 0
    while True:
        start = text.find(ENTRY_START, position)
        stray_end = text.find(ENTRY_END, position)
        if stray_end >= 0 and (start < 0 or stray_end < start):
            raise ValueError("unmatched os-nightly entry end sentinel")
        if start < 0:
            manual_parts.append(text[position:])
            break
        manual_parts.append(text[position:start])
        content_start = start + len(ENTRY_START)
        end = text.find(ENTRY_END, content_start)
        nested_start = text.find(ENTRY_START, content_start)
        if end < 0 or (nested_start >= 0 and nested_start < end):
            raise ValueError("unmatched os-nightly entry start sentinel")
        block_end = end + len(ENTRY_END)
        for _ in range(2):
            next_end = _consume_one_eol(text, block_end)
            if next_end == block_end:
                break
            block_end = next_end
        block = _normalize_eol(text[start:block_end], eol).rstrip("\r\n")
        entries.append(block + eol + eol)
        position = block_end
    return "".join(manual_parts), entries


def write_entry(lines):
    """Insert one sentinel-owned entry; return False on any fencing failure."""
    token = bb_lock.acquire(LOG, agent="nightly", wait=15)
    if not token:
        print("FAILED: could not lock automation log", file=sys.stderr)
        return False
    # Older bb_lock implementations returned a Boolean success value rather
    # than a fencing token. Keep their already-held critical section usable;
    # current tokenized locks still require renewals and ownership verification.
    legacy_boolean_lock = token is True
    temp_path = None
    committed = False
    release_ok = False
    try:
        if not legacy_boolean_lock and not bb_lock.renew(LOG, token):
            print("FAILED: automation-log lease lost before read", file=sys.stderr)
            return False
        head = HEADER
        old_entries = []
        eol = "\n"
        if os.path.exists(LOG):
            text = _read_text_preserving_eol(LOG)
            eol = _detect_eol(text)
            marker_count = text.count(MARKER)
            if marker_count > 1:
                print(
                    "FAILED: automation log marker must appear exactly once",
                    file=sys.stderr,
                )
                return False
            if marker_count == 1:
                head, below = text.split(MARKER, 1)
                manual_below, old_entries = _partition_log_content(
                    _drop_managed_separator(below), eol)
                head = _append_raw_slice(head, manual_below, eol)
            else:
                # Without a managed marker, every existing byte is legacy/manual.
                head = text
        entry = (
            ENTRY_START + eol
            + "## " + datetime.datetime.now().isoformat(timespec="minutes") + eol
            + eol.join("- " + _normalize_eol(line, eol) for line in lines) + eol
            + ENTRY_END + eol + eol
        )
        if not legacy_boolean_lock and not bb_lock.renew(LOG, token):
            print("FAILED: automation-log lease lost before staging", file=sys.stderr)
            return False
        parent = os.path.dirname(os.path.abspath(LOG))
        os.makedirs(parent, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".os-nightly-", dir=parent)
        if os.path.exists(LOG):
            os.chmod(temp_path, stat.S_IMODE(os.stat(LOG).st_mode))
        if legacy_boolean_lock:
            writer = os.fdopen(fd, "w", encoding="utf-8", newline="")
        else:
            os.close(fd)
            writer = open(temp_path, "w", encoding="utf-8", newline="")
        with writer as f:
            f.write(
                head + _blank_line_suffix(head, eol) + MARKER + eol + eol
                + entry + "".join(old_entries[:MAX_ENTRIES - 1])
            )
            f.flush()
            os.fsync(f.fileno())
        if not legacy_boolean_lock and not bb_lock.renew(LOG, token):
            print("FAILED: automation-log lease lost before commit; log unchanged",
                  file=sys.stderr)
            return False
        if legacy_boolean_lock:
            os.replace(temp_path, LOG)
        elif not _commit_if_owned(token, temp_path):
            print("FAILED: automation-log lease lost before commit; log unchanged",
                  file=sys.stderr)
            return False
        temp_path = None
        committed = True
    except (OSError, ValueError) as exc:
        print(f"FAILED: could not update automation log: {exc}", file=sys.stderr)
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
        try:
            released = bb_lock.release(
                LOG, agent="nightly", token=token)
            # The historic Boolean lock API returned None on a successful
            # release. Only an explicit False means the release was refused.
            release_ok = released is not False
        except Exception as exc:
            print(f"FAILED: could not release automation-log lock: {exc}",
                  file=sys.stderr)
            release_ok = False
        if not release_ok:
            print("FAILED: could not release automation-log lock with its fencing token",
                  file=sys.stderr)
    return committed and release_ok


def notify(headline):
    """Best-effort macOS notification — never allowed to break the heartbeat."""
    try:
        subprocess.run(
            ["osascript", "-e",
             'display notification "%s" with title "Project OS nightly"'
             % headline.replace('"', "'")],
            capture_output=True, timeout=10)
    except Exception:
        pass


def main():
    bb_dir = os.path.dirname(LOG)
    if not os.path.isdir(bb_dir):
        print(
            f"FAILED: no blackboard directory at {bb_dir} — this workspace is "
            "not initialized. Run `./install.sh <target>` (or copy "
            "blackboard-template/ to blackboard/) and re-run the nightly.",
            file=sys.stderr,
        )
        sys.exit(2)
    lines = []
    gauge_failed = False
    try:
        gauge, code = run_gauge()
        gauge_failed = code is None
    except Exception as e:  # keep the heartbeat alive even if the gauge breaks
        gauge, code = f"brain_scale FAILED: {e}", None
        gauge_failed = True
    lines.append(f"gauge: {gauge}")
    lines.append(f"locks reaped: {reap_locks()}")
    sp = stale_packets()
    lines.append(f"stale Draft packets (>{STALE_DAYS}d): "
                 + (", ".join(sp) if sp else "none"))
    st = stuck_plans()
    lines.append(f"plans stuck running (>{STALE_DAYS}d): "
                 + (", ".join(st) if st else "none"))
    u = []
    try:
        import harvest
        u = harvest.unharvested()
        lines.append("unharvested done runs: " + (", ".join(u) if u else "none"))
    except Exception as e:
        lines.append(f"harvest check failed: {e}")
    ok = write_entry(lines)
    for l in lines:
        print(l)
    # ONE actionable headline, highest-priority first; silence when all clear.
    issues = []
    if gauge_failed:
        issues.append("brain_scale failed — inspect the nightly automation log")
    elif code == 2:
        issues.append("Brain past CUTOVER — run scripts/brain_archive.py")
    elif code == 1:
        issues.append("Brain gauge WATCH — review brain_scale output")
    elif code == 3:
        issues.append("Shared brain missing — gauge cannot run")
    elif code != 0:
        # The launcher's stderr destination is deployment-specific; the old
        # ~/.project-os path was not true for project-local installs.
        issues.append("brain_scale crashed — inspect the nightly stderr log")
    if st:
        issues.append(f"{len(st)} plan(s) stuck running >{STALE_DAYS}d")
    if sp:
        issues.append(f"{len(sp)} Draft packet(s) stale >{STALE_DAYS}d")
    if u:
        issues.append(f"{len(u)} done run(s) never harvested")
    if not ok:
        issues.insert(0, "nightly could not write the automation log")
    if issues:
        more = f"  (+{len(issues) - 1} more in 22-automation-log)" if len(issues) > 1 else ""
        notify(issues[0] + more)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
