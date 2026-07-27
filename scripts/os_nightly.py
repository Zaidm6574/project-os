#!/usr/bin/env python3
"""os_nightly — the unattended heartbeat of Project OS (launchd: ai.projectos.nightly).

Runs once a day and does three cheap, local, zero-dependency checks:
  1. brain_scale gauge   — is the shared brain approaching the flat-index ceiling?
  2. lock reaping        — clear stale bb_lock files older than STALE_AFTER_SEC
  3. staleness sweep     — Draft packets untouched > 7 days, plans stuck "running" > 7 days

Results are prepended to blackboard/22-automation-log.md (newest first, capped at
30 entries) so any session — human or agent — sees drift without being asked to look.
Prepended means exactly that: everything above the first entry is left alone, and
only dated entries this script wrote are rotated out by the cap.

Exit code mirrors brain_scale severity: 0 OK, 1 WATCH, 2 CUTOVER, 3 n/a (brain missing).
Manual run:  python3 scripts/os_nightly.py
"""
import os, re, sys, json, glob, stat, time, datetime, subprocess, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
import bb_lock

LOG = os.path.join(ROOT, "blackboard", "22-automation-log.md")
PACKETS = os.path.join(ROOT, "blackboard", "packets")
PLANS = os.path.join(ROOT, "blackboard", "plans")
STALE_DAYS = 7
MAX_ENTRIES = 30

# Only written when the log file does not exist at all. A real workspace gets
# its preamble from blackboard-template/22-automation-log.md at install time —
# including the launchd plist and the `launchctl bootstrap` line README.md
# sends readers to — and write_entry() below preserves whatever it finds, so
# this is a floor, not the shipped text. It deliberately does NOT claim the
# launchd agent is installed or that it ran on a schedule: this script is just
# as often run by hand, and AGENTS.md forbids implying something ran.
HEADER = """# 22 — Automation Log

Written by `scripts/os_nightly.py`, the unattended daily heartbeat — schedule it
with launchd or cron (see README.md) or run it by hand. Newest first; dated
entries are capped at %d. Any WATCH/CUTOVER or stale item here is a standing
prompt for the next session to act.

""" % MAX_ENTRIES

# A dated entry heading, exactly as write_entry() writes it below. The cap
# applies only to headings that match: anything else in this file was put there
# by a person, and rotating a person's note out of a log as if the script had
# written it is how the launchd snippet and an operator's note both vanished.
ENTRY_HEADING = re.compile(r"^## \d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


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


def split_log(text):
    """Split the log into (head, blocks): the preamble, then each `## ` block.

    2026-07-27: this used to be `text.split("\\n## ")`, and the head — body[0]
    — was thrown away and replaced by HEADER on every write. The shipped
    blackboard-template/22-automation-log.md contains no `## ` heading at all,
    so the FIRST heartbeat of every install deleted its entire 34-line
    preamble: the per-field legend, the launchd plist, and the
    `launchctl bootstrap` line that README.md tells a reader to go and read.
    The installer does not copy blackboard-template/ into the target, so after
    that first run the snippet existed nowhere in the project.
    """
    head, blocks, current = [], [], None
    for line in text.split("\n"):
        if line.startswith("## "):
            if current is not None:
                blocks.append(current)
            current = [line]
        elif current is None:
            head.append(line)
        else:
            current.append(line)
    if current is not None:
        blocks.append(current)
    return "\n".join(head), ["\n".join(b).rstrip() + "\n\n" for b in blocks]


def atomic_write(path, text):
    """Replace path's contents in one rename, never truncating the original.

    2026-07-27: this was `open(path, "w")`, which empties the destination and
    only then writes. Anything interrupting that gap — ENOSPC, kill -9, power
    loss — left a zero-byte automation log, i.e. one failed append destroyed
    the entire 30-entry history. Same defect and same remedy as
    scripts/brain_archive.py's _atomic_write_preserving_mode and
    scripts/plan_artifact.py's packet writer: build the whole text first, put
    it in a sibling tempfile.mkstemp() file (O_CREAT|O_EXCL, so the write can
    never go through a symlink planted at a predictable name), fsync it, then
    os.replace() it on. A reader sees the old log or the new one, never a torn
    or empty one, and a failure anywhere before the rename costs nothing.

    mkstemp always creates 0600 regardless of umask and rename carries the
    temp file's mode onto the destination, so an existing log's mode is
    reproduced before the swap rather than silently narrowed.
    """
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        mode = None
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                               prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_entry(lines):
    """Prepend a dated entry to the log under bb_lock, keep newest MAX_ENTRIES.

    Preserves everything the file already held that this script did not write:
    the preamble above the first heading, and any `## ` block whose heading is
    not one of our timestamps. The cap counts our own entries only.
    """
    entry = "## " + datetime.datetime.now().isoformat(timespec="minutes") + "\n" \
            + "\n".join("- " + l for l in lines) + "\n\n"
    if not bb_lock.acquire(LOG, agent="nightly", wait=15):
        print("FAILED: could not lock automation log", file=sys.stderr)
        return False
    try:
        # 2026-07-27: everything above the first `## <timestamp>` entry is the
        # log's own documentation, written by the installer, not by this run --
        # including the launchd plist and `launchctl bootstrap` line that README
        # tells the user to read in this file's header. Substituting HEADER for
        # the whole preamble deleted the installed project's only copy of its own
        # scheduling instructions on the very first heartbeat. Keep what is
        # there; HEADER only seeds a file that does not exist yet.
        head, blocks = HEADER, []
        if os.path.exists(LOG):
            with open(LOG, encoding="utf-8") as f:
                existing_head, blocks = split_log(f.read())
            # A file that starts straight in on a heading has no preamble to
            # keep; only then do we supply our own.
            if existing_head.strip():
                head = existing_head.rstrip("\n") + "\n\n"
        kept, mine = [], 0
        for block in blocks:
            if ENTRY_HEADING.match(block):
                if mine >= MAX_ENTRIES - 1:  # the new entry takes the last slot
                    continue
                mine += 1
            kept.append(block)
        atomic_write(LOG, head + entry + "".join(kept))
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
