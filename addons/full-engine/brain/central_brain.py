#!/usr/bin/env python3
"""Optional central brain sync for Project OS.

Each Project OS project keeps its own local `brain/shared-brain.jsonl`. This
tool creates or connects a central JSONL exchange so lessons can move between
projects without importing raw chats or private dumps. Plain lesson records
sync as-is; chat-derived records sync only as explicitly approved summaries.

Usage:
  python3 brain/central_brain.py init --path ~/.project-os/central-brain
  python3 brain/central_brain.py push --path ~/.project-os/central-brain --project . --project-id my-project
  python3 brain/central_brain.py pull --path ~/.project-os/central-brain --project . --project-id my-project
  python3 brain/central_brain.py sync --path ~/.project-os/central-brain --project . --project-id my-project
  python3 brain/central_brain.py status --path ~/.project-os/central-brain
  python3 brain/central_brain.py --selftest

Local filesystem only. No network calls.

push/pull/sync/status exit 1 when the JSONL they read contains an unparsable
line: that line is a record which did NOT sync, and it is reported by file and
line number instead of being dropped in silence.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_PROJECT = HERE.parent
DEFAULT_CENTRAL = Path(os.environ.get("PROJECT_OS_CENTRAL_BRAIN", "~/.project-os/central-brain")).expanduser()
PROJECT_BRAIN = Path("brain") / "shared-brain.jsonl"
CENTRAL_FILE = "shared-brain.jsonl"
README = "README.md"

# Single source of truth for secret shapes: brain.py in this same directory.
# Keeping a second copy here is how the two drifted (cross-check 2026-07-25):
# brain.py was widened to 23 patterns while this file stayed on 7.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# SCANNED_FIELDS is deliberately NOT imported: record_secret_hit() already
# walks every scannable field, and importing an unused name here made this
# module look like it did its own field-level scan when it did not.
from brain import SECRET_PATTERNS, record_secret_hit  # noqa: E402
sys.path.pop(0)


# --------------------------------------------------------------------------- #
# Concurrent-write lock (audit 2026-07-27)
# --------------------------------------------------------------------------- #
# append_new() below is an UNLOCKED read-modify-append on shared-brain.jsonl:
# it reads the existing id set, decides which records are new, then appends
# them. scripts/brain_archive.py rewrites that SAME file and guards every
# mutation with scripts/bb_lock.py (an O_CREAT|O_EXCL lockfile keyed by the
# file's realpath). This writer took no lock at all, so two concurrent agents
# (a push racing another push, or a push racing brain_archive) each read the
# pre-append snapshot, both judge the same record "new", and both append it --
# a duplicated record and an over-counted return in the CROSS-PROJECT brain.
#
# We deliberately reuse bb_lock rather than a bare fcntl.flock on the data
# file: bb_lock serialises through a *separate* lockfile, so a flock on the
# JSONL itself would not be mutually exclusive with brain_archive's lock and
# the two would still race. Reusing bb_lock is the only way the two "don't
# fight". bb_lock lives in the base Project OS `scripts/` dir (a prerequisite
# of the full-engine add-on), which sits at a different depth in the repo than
# in an installed project, so we search upward for it rather than hard-coding a
# relative path. If it cannot be found or loaded (e.g. a non-POSIX target with
# no fcntl), locking degrades to best-effort and never blocks the caller.
def _load_bb_lock():
    try:
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidate = parent / "scripts" / "bb_lock.py"
            if candidate.is_file():
                spec = importlib.util.spec_from_file_location(
                    "project_os_central_brain_bb_lock", candidate)
                module = importlib.util.module_from_spec(spec)
                # Not registered in sys.modules on purpose: keeps the module's
                # env-derived LOCK_DIR re-readable if the process re-imports us
                # under a changed BB_LOCK_DIR (the tests rely on this).
                spec.loader.exec_module(module)
                return module
    except Exception:
        pass
    return None


_BB_LOCK = _load_bb_lock()


def _acquire_brain_lock(path: Path):
    """Take the shared-brain lock for `path`; return a fencing token or None.

    Best-effort: a missing/unloadable bb_lock, or a wait that times out under
    contention, returns None and the caller proceeds unlocked rather than
    raising -- append_new() must still return an int count."""
    if _BB_LOCK is None:
        return None
    try:
        token = _BB_LOCK.acquire(str(path), agent="central-brain", wait=30)
    except Exception:
        return None
    return token or None


def _release_brain_lock(path: Path, token) -> None:
    if _BB_LOCK is None or not token:
        return
    try:
        _BB_LOCK.release(str(path), agent="central-brain", force=True, token=token)
    except Exception:
        pass


def looks_like_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def project_id(project: Path, explicit: str | None = None) -> str:
    if explicit:
        raw = explicit
    else:
        raw = project.resolve().name or "project"
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw).strip("-").lower()
    return safe or "project"


def central_id(pid: str, origin_id: str, text: str) -> str:
    origin = origin_id or hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    digest = hashlib.sha256(f"{pid}\0{origin}\0{text}".encode("utf-8")).hexdigest()[:12]
    return f"{pid}/{origin}-{digest}"


def init_central(path: Path) -> Path:
    path = path.expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    brain_file = path / CENTRAL_FILE
    brain_file.touch(exist_ok=True)
    readme = path / README
    if not readme.exists():
        readme.write_text(
            "# Project OS Central Brain\n\n"
            "This folder stores approved Project OS lesson summaries in JSONL.\n\n"
            "It is local-only. Do not put raw chats, API keys, passwords, private "
            "credentials, or unnecessary sensitive personal data here.\n",
            encoding="utf-8",
        )
    return brain_file


def read_jsonl(path: Path, unparsable: list | None = None) -> list[dict]:
    """Read a JSONL brain file, skipping any line that is not a JSON object.

    Skipping stays right for THIS file: a crash-truncated tail is expected
    here (brain.py::_heal_truncated_tail, append_new below), and one damaged
    line must not take a whole sync down. But a skipped line is a LOST record,
    and push/pull/sync/status printed only "N lesson(s) added" and exited 0, so
    the loss was invisible on every surface -- the repo's
    data-loss-looks-like-success signature (audit 2026-07-27).

    Pass `unparsable` to collect the 1-based line numbers that were dropped so
    the caller can report them; memory/brain_fts_mirror.py::read_records, the
    strict reader of this same format, names line numbers the same way. The
    single-argument call still returns a plain list, unchanged.
    """
    records: list[dict] = []
    if not path.exists():
        return records
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            if unparsable is not None:
                unparsable.append(line_no)
            continue
        if isinstance(record, dict):
            records.append(record)
        elif unparsable is not None:
            # A valid-JSON non-object (a list, a bare string) is dropped just
            # as silently; brain_fts_mirror refuses it outright.
            unparsable.append(line_no)
    return records


def append_new(path: Path, records: list[dict]) -> int:
    # The read (existing id set) MUST be inside the lock together with the
    # append: that read-then-append is the exact window two agents race on.
    # Same lock scripts/brain_archive.py holds for this file, so a push here
    # and an archive rewrite there also serialise against each other.
    token = _acquire_brain_lock(path)
    try:
        existing = {record.get("id") for record in read_jsonl(path)}
        added = 0
        with path.open("a", encoding="utf-8") as handle:
            # Never weld onto a crash-truncated last line: appending to a partial
            # JSON fragment makes ONE unparseable line, destroying the damaged
            # record AND the one being written, while read_jsonl skips it silently
            # and this function reports success. scripts/brain_append.py was healed
            # in d71aeca; this writer -- which serves BOTH push and pull, i.e. the
            # CROSS-PROJECT brain -- was not (adversarial verify 2026-07-26).
            if handle.tell():
                with path.open("rb") as probe:
                    probe.seek(-1, os.SEEK_END)
                    if probe.read(1) != b"\n":
                        handle.write("\n")
            for record in records:
                rid = record.get("id")
                if not rid or rid in existing:
                    continue
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                existing.add(rid)
                added += 1
        return added
    finally:
        _release_brain_lock(path, token)


def project_brain_path(project: Path) -> Path:
    return project.expanduser().resolve() / PROJECT_BRAIN


def syncable_summary(record: dict) -> bool:
    """Fail closed for chat-derived records; keep plain lessons flowing.

    Records that use the chat approval vocabulary (any of approved /
    summary_only / raw_chat present) or carry a chat source/tag must be an
    explicitly approved summary. Plain lesson records from the export /
    brain_append pipeline never carry those fields and stay syncable —
    requiring the fields on every record silently zeroed the whole sync
    flow (hostile-judge finding F1, 2026-07-17). Private markers always
    exclude a record."""
    source_value = record.get("source", "")
    tags_value = record.get("tags", [])
    if not isinstance(source_value, str):
        return False
    if not isinstance(tags_value, list) or not all(isinstance(tag, str) for tag in tags_value):
        return False
    source = source_value.casefold()
    tags = {tag.casefold() for tag in tags_value}
    private_tags = {"raw-chat", "private", "sensitive", "local-only", "do-not-sync"}
    if source in {"chat-raw", "raw-chat"} or source.startswith("private") or (tags & private_tags):
        return False
    chat_derived = (
        any(field in record for field in ("approved", "summary_only", "raw_chat"))
        or source == "chat-summary"
        or "chat-summary" in tags
    )
    if chat_derived:
        return (
            record.get("approved") is True
            and record.get("summary_only") is True
            and record.get("raw_chat") is False
        )
    return True


def lessons_for_central(project: Path, pid: str, unparsable: list | None = None) -> tuple[list[dict], int]:
    """Returns (syncable lessons, count skipped by the privacy/type gate).

    `unparsable` collects the project brain's dropped line numbers; those are
    NOT part of `skipped`, which counts only deliberate privacy/type refusals.
    """
    src = project_brain_path(project)
    lessons: list[dict] = []
    skipped = 0
    for record in read_jsonl(src, unparsable):
        if record.get("central_import") or record.get("central_id") or record.get("source") == "central-brain":
            continue
        if not syncable_summary(record):
            skipped += 1
            continue
        if record.get("type") != "lesson" and record.get("memory_type") != "lesson":
            skipped += 1
            continue
        text = str(record.get("text", "")).strip()
        if not text or looks_like_secret(text) or record_secret_hit(record):
            skipped += 1
            continue
        origin_id = str(record.get("id") or record.get("memory_id") or "")
        tags = list(record.get("tags") or [])
        project_tag = f"project:{pid}"
        if project_tag not in tags:
            tags.append(project_tag)
        central_record = {
                "id": central_id(pid, origin_id, text),
                "origin_id": origin_id,
                "project_id": pid,
                "project_name": project.expanduser().resolve().name,
                "ts": record.get("ts") or record.get("created_at") or "",
                "source": record.get("source") or "project-os",
                "type": "lesson",
                "text": text,
                "tags": tags,
            }
        for field in ("approved", "summary_only", "raw_chat"):
            if field in record:
                central_record[field] = record[field]
        lessons.append(central_record)
    return lessons, skipped


def push(path: Path, project: Path, explicit_project_id: str | None = None,
         unparsable: list | None = None) -> tuple[int, int]:
    brain_file = init_central(path)
    pid = project_id(project, explicit_project_id)
    lessons, skipped = lessons_for_central(project, pid, unparsable)
    return append_new(brain_file, lessons), skipped


def pull(path: Path, project: Path, explicit_project_id: str | None = None,
         unparsable: list | None = None) -> tuple[int, int]:
    brain_file = init_central(path)
    pid = project_id(project, explicit_project_id)
    dest = project_brain_path(project)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.touch(exist_ok=True)
    safe_records = []
    skipped = 0
    for record in read_jsonl(brain_file, unparsable):
        if record.get("project_id") == pid:
            continue
        if not syncable_summary(record):
            skipped += 1
            continue
        if record.get("type") != "lesson":
            skipped += 1
            continue
        text = str(record.get("text", "")).strip()
        if not text or looks_like_secret(text) or record_secret_hit(record):
            skipped += 1
            continue
        tags = list(record.get("tags") or [])
        if "central-brain" not in tags:
            tags.append("central-brain")
        local_record = {
                "id": record.get("id"),
                "central_id": record.get("id"),
                "origin_id": record.get("origin_id") or record.get("id"),
                "origin_project_id": record.get("project_id") or "",
                "ts": record.get("ts") or "",
                "source": "central-brain",
                "type": "lesson",
                "text": text,
                "tags": tags,
                "central_import": True,
            }
        for field in ("approved", "summary_only", "raw_chat"):
            if field in record:
                local_record[field] = record[field]
        safe_records.append(local_record)
    return append_new(dest, safe_records), skipped


def status(path: Path, unparsable: list | None = None) -> tuple[int, list[str]]:
    brain_file = init_central(path)
    records = read_jsonl(brain_file, unparsable)
    projects = sorted({str(record.get("project_id")) for record in records if record.get("project_id")})
    return len(records), projects


def report_unparsable(path: Path, lines: list, action: str) -> bool:
    """Print the dropped lines for `path`; return True if any were dropped.

    Always printed on its own line when there is damage, and never printed
    when there is none -- a warning that fires on healthy files is one nobody
    reads. Line numbers make the record recoverable by hand, the way
    memory/brain_fts_mirror.py reports a malformed line.
    """
    if not lines:
        return False
    shown = ", ".join(str(n) for n in lines[:10])
    more = f" +{len(lines) - 10} more" if len(lines) > 10 else ""
    print(
        f"central brain WARNING: {len(lines)} unparsable line(s) in {path} "
        f"were NOT {action} (line {shown}{more}); repair the file and re-run"
    )
    return True


def selftest() -> int:
    with tempfile.TemporaryDirectory(prefix="central-brain-selftest-") as tmp:
        base = Path(tmp)
        central = base / "central"
        project_a = base / "project-a"
        project_b = base / "project-b"
        (project_a / "brain").mkdir(parents=True)
        (project_b / "brain").mkdir(parents=True)
        (project_a / PROJECT_BRAIN).write_text(
            json.dumps(
                {
                    "id": "lesson-001",
                    "ts": "2026-06-25T00:00:00Z",
                    "source": "project-os",
                    "type": "lesson",
                    "text": "Prefer explicit full-engine opt-in before central memory sync.",
                    "tags": ["install"],
                    "approved": True,
                    "summary_only": True,
                    "raw_chat": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        assert push(central, project_a, "alpha") == (1, 0)
        assert push(central, project_a, "alpha") == (0, 0)
        count, projects = status(central)
        assert count == 1, count
        assert projects == ["alpha"], projects
        assert pull(central, project_b, "beta") == (1, 0)
        assert pull(central, project_b, "beta") == (0, 0)
        pulled = read_jsonl(project_b / PROJECT_BRAIN)
        assert pulled and pulled[0]["text"].startswith("Prefer explicit")
        assert pulled[0]["central_import"] is True
        assert push(central, project_b, "beta") == (0, 0)

        # A crash-truncated line must be REPORTED, never dropped in silence:
        # the good record still syncs, and the damaged one is named by line.
        project_c = base / "project-c"
        (project_c / "brain").mkdir(parents=True)
        (project_c / PROJECT_BRAIN).write_text(
            json.dumps(
                {
                    "id": "lesson-002",
                    "type": "lesson",
                    "text": "A damaged neighbour must not hide this lesson.",
                }
            )
            + "\n"
            + '{"id": "lesson-003", "type": "les',
            encoding="utf-8",
        )
        damaged: list = []
        assert push(central, project_c, "gamma", damaged) == (1, 0)
        assert damaged == [2], damaged
    print("central_brain selftest: OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Project OS optional central brain sync.")
    parser.add_argument("--selftest", action="store_true", help="run built-in selftest")
    sub = parser.add_subparsers(dest="cmd")

    for name in ("init", "push", "pull", "sync", "status"):
        command = sub.add_parser(name)
        command.add_argument("--path", default=str(DEFAULT_CENTRAL), help="central brain folder")
        if name in {"push", "pull", "sync"}:
            command.add_argument("--project", default=str(DEFAULT_PROJECT), help="Project OS project folder")
            command.add_argument("--project-id", default=None, help="stable central project id")

    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.cmd == "init":
        brain_file = init_central(Path(args.path))
        print(f"central brain initialized: {brain_file}")
        return 0
    # An unparsable line is a record that did NOT move. Each command reports
    # the file it READ records from (push: the project brain, pull/status: the
    # central brain, sync: both), and exits non-zero so a script cannot read
    # a partial sync as a complete one.
    if args.cmd == "push":
        damaged: list = []
        added, skipped = push(Path(args.path), Path(args.project), args.project_id, damaged)
        note = f" ({skipped} record(s) skipped by privacy/type gate)" if skipped else ""
        print(f"central brain push: {added} lesson(s) added{note}")
        return 1 if report_unparsable(project_brain_path(Path(args.project)), damaged, "pushed") else 0
    if args.cmd == "pull":
        central_file = Path(args.path).expanduser().resolve() / CENTRAL_FILE
        damaged: list = []
        added, skipped = pull(Path(args.path), Path(args.project), args.project_id, damaged)
        note = f" ({skipped} record(s) skipped by privacy/type gate)" if skipped else ""
        print(f"central brain pull: {added} lesson(s) added to project{note}")
        return 1 if report_unparsable(central_file, damaged, "pulled") else 0
    if args.cmd == "sync":
        central_file = Path(args.path).expanduser().resolve() / CENTRAL_FILE
        push_damaged: list = []
        pull_damaged: list = []
        pushed, push_skipped = push(Path(args.path), Path(args.project), args.project_id, push_damaged)
        pulled, pull_skipped = pull(Path(args.path), Path(args.project), args.project_id, pull_damaged)
        skipped = push_skipped + pull_skipped
        note = f" ({skipped} record(s) skipped by privacy/type gate)" if skipped else ""
        print(f"central brain sync: {pushed} pushed, {pulled} pulled{note}")
        # Both sides are reported before the exit code is decided: `or` would
        # short-circuit the second report away.
        damaged_push = report_unparsable(project_brain_path(Path(args.project)), push_damaged, "pushed")
        damaged_pull = report_unparsable(central_file, pull_damaged, "pulled")
        return 1 if (damaged_push or damaged_pull) else 0
    if args.cmd == "status":
        central_file = Path(args.path).expanduser().resolve() / CENTRAL_FILE
        damaged: list = []
        count, projects = status(Path(args.path), damaged)
        print(f"central brain status: {count} lesson(s), {len(projects)} project(s)")
        if projects:
            print("projects: " + ", ".join(projects))
        return 1 if report_unparsable(central_file, damaged, "counted") else 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
