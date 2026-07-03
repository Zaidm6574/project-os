#!/usr/bin/env python3
"""Optional central brain sync for Project OS.

Each Project OS project keeps its own local `brain/shared-brain.jsonl`. This
tool creates or connects a central JSONL exchange so approved lessons can move
between projects without importing raw chats or private dumps.

Usage:
  python3 brain/central_brain.py init --path ~/.project-os/central-brain
  python3 brain/central_brain.py push --path ~/.project-os/central-brain --project . --project-id my-project
  python3 brain/central_brain.py pull --path ~/.project-os/central-brain --project . --project-id my-project
  python3 brain/central_brain.py sync --path ~/.project-os/central-brain --project . --project-id my-project
  python3 brain/central_brain.py status --path ~/.project-os/central-brain
  python3 brain/central_brain.py --selftest

Local filesystem only. No network calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_PROJECT = HERE.parent
DEFAULT_CENTRAL = Path(os.environ.get("PROJECT_OS_CENTRAL_BRAIN", "~/.project-os/central-brain")).expanduser()
PROJECT_BRAIN = Path("brain") / "shared-brain.jsonl"
CENTRAL_FILE = "shared-brain.jsonl"
README = "README.md"

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*\S{6,}"),
]


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


def read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def append_new(path: Path, records: list[dict]) -> int:
    existing = {record.get("id") for record in read_jsonl(path)}
    added = 0
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            rid = record.get("id")
            if not rid or rid in existing:
                continue
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            existing.add(rid)
            added += 1
    return added


def project_brain_path(project: Path) -> Path:
    return project.expanduser().resolve() / PROJECT_BRAIN


def lessons_for_central(project: Path, pid: str) -> list[dict]:
    src = project_brain_path(project)
    lessons: list[dict] = []
    for record in read_jsonl(src):
        if record.get("central_import") or record.get("central_id") or record.get("source") == "central-brain":
            continue
        if record.get("type") != "lesson" and record.get("memory_type") != "lesson":
            continue
        text = str(record.get("text", "")).strip()
        if not text or looks_like_secret(text):
            continue
        origin_id = str(record.get("id") or record.get("memory_id") or "")
        tags = list(record.get("tags") or [])
        project_tag = f"project:{pid}"
        if project_tag not in tags:
            tags.append(project_tag)
        lessons.append(
            {
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
        )
    return lessons


def push(path: Path, project: Path, explicit_project_id: str | None = None) -> int:
    brain_file = init_central(path)
    pid = project_id(project, explicit_project_id)
    return append_new(brain_file, lessons_for_central(project, pid))


def pull(path: Path, project: Path, explicit_project_id: str | None = None) -> int:
    brain_file = init_central(path)
    pid = project_id(project, explicit_project_id)
    dest = project_brain_path(project)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.touch(exist_ok=True)
    safe_records = []
    for record in read_jsonl(brain_file):
        if record.get("project_id") == pid:
            continue
        if record.get("type") != "lesson":
            continue
        text = str(record.get("text", "")).strip()
        if not text or looks_like_secret(text):
            continue
        tags = list(record.get("tags") or [])
        if "central-brain" not in tags:
            tags.append("central-brain")
        safe_records.append(
            {
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
        )
    return append_new(dest, safe_records)


def status(path: Path) -> tuple[int, list[str]]:
    brain_file = init_central(path)
    records = read_jsonl(brain_file)
    projects = sorted({str(record.get("project_id")) for record in records if record.get("project_id")})
    return len(records), projects


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
                }
            )
            + "\n",
            encoding="utf-8",
        )

        assert push(central, project_a, "alpha") == 1
        assert push(central, project_a, "alpha") == 0
        count, projects = status(central)
        assert count == 1, count
        assert projects == ["alpha"], projects
        assert pull(central, project_b, "beta") == 1
        assert pull(central, project_b, "beta") == 0
        pulled = read_jsonl(project_b / PROJECT_BRAIN)
        assert pulled and pulled[0]["text"].startswith("Prefer explicit")
        assert pulled[0]["central_import"] is True
        assert push(central, project_b, "beta") == 0
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
    if args.cmd == "push":
        added = push(Path(args.path), Path(args.project), args.project_id)
        print(f"central brain push: {added} lesson(s) added")
        return 0
    if args.cmd == "pull":
        added = pull(Path(args.path), Path(args.project), args.project_id)
        print(f"central brain pull: {added} lesson(s) added to project")
        return 0
    if args.cmd == "sync":
        pushed = push(Path(args.path), Path(args.project), args.project_id)
        pulled = pull(Path(args.path), Path(args.project), args.project_id)
        print(f"central brain sync: {pushed} pushed, {pulled} pulled")
        return 0
    if args.cmd == "status":
        count, projects = status(Path(args.path))
        print(f"central brain status: {count} lesson(s), {len(projects)} project(s)")
        if projects:
            print("projects: " + ", ".join(projects))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
