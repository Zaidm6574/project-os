#!/usr/bin/env python3
"""Canonical shared-brain path resolution for Project OS.

Resolution is deliberately fail-closed and project-local by default:

1. a valid absolute PROJECT_OS_SHARED_BRAIN path,
2. a strict ignored brain/shared-brain-binding.jsonl record,
3. <project>/brain/shared-brain.jsonl.

There is no implicit cross-project or home-directory fallback.
"""

from __future__ import annotations

import json
import hashlib
import os
import stat
from pathlib import Path
from typing import Mapping


ENV_NAME = "PROJECT_OS_SHARED_BRAIN"
BINDING_RELATIVE = Path("brain") / "shared-brain-binding.jsonl"
BINDING_SCHEMA = "project-os/shared-brain-binding/v1"
BINDING_KEYS = frozenset({"schema", "target"})
BINDING_IGNORE_RULE = "brain/shared-brain-binding.jsonl"
RECEIPT_IGNORE_RULE = "brain/shared-brain-migration-receipt.jsonl"


class BrainPathError(ValueError):
    """A shared-brain path or binding failed the safety contract."""


def _absolute(path: os.PathLike[str] | str) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_macos_system_alias(path: Path) -> bool:
    if path.parent != Path("/"):
        return False
    try:
        if not path.is_symlink():
            return False
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    return resolved.parent == Path("/private") and resolved.name == path.name


def _validate_components(
    path: Path,
    *,
    label: str,
    leaf_kind: str,
    leaf_may_be_missing: bool,
) -> Path:
    """Validate an absolute path without following user-controlled symlinks."""
    if not path.is_absolute():
        raise BrainPathError(f"{label} must be absolute: {path}")

    current = Path(path.anchor)
    parts = path.parts[1:]
    for index, part in enumerate(parts):
        current = current / part
        final = index == len(parts) - 1
        try:
            info = current.lstat()
        except FileNotFoundError:
            if final and not leaf_may_be_missing:
                raise BrainPathError(f"{label} is missing: {current}") from None
            break
        except NotADirectoryError:
            raise BrainPathError(
                f"{label} has a non-directory parent: {current.parent}"
            ) from None
        except OSError as exc:
            raise BrainPathError(f"cannot inspect {label} {current}: {exc}") from None

        if stat.S_ISLNK(info.st_mode):
            if not _is_macos_system_alias(current):
                raise BrainPathError(f"{label} contains a symlink: {current}")
            continue

        if not final:
            if not stat.S_ISDIR(info.st_mode):
                raise BrainPathError(
                    f"{label} has a non-directory parent: {current}"
                )
            continue

        if leaf_kind == "file":
            if not stat.S_ISREG(info.st_mode):
                raise BrainPathError(
                    f"{label} must be a regular non-symlink file: {current}"
                )
            if info.st_nlink != 1:
                raise BrainPathError(
                    f"{label} must not be hardlinked: {current}"
                )
        elif leaf_kind == "directory" and not stat.S_ISDIR(info.st_mode):
            raise BrainPathError(f"{label} must be a directory: {current}")

    return path


def _project_root(project_root: os.PathLike[str] | str) -> Path:
    root = _absolute(project_root)
    _validate_components(
        root,
        label="project root",
        leaf_kind="directory",
        leaf_may_be_missing=False,
    )
    return root


def find_project_root(anchor: os.PathLike[str] | str) -> Path:
    """Find the nearest ancestor carrying the canonical scripts module."""
    start = _absolute(anchor)
    if start.is_file():
        start = start.parent
    for candidate in (start, *start.parents):
        module = candidate / "scripts" / "brain_paths.py"
        try:
            info = module.lstat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            return _project_root(candidate)
    raise BrainPathError(
        f"could not find Project OS root from {anchor}: scripts/brain_paths.py is missing"
    )


def _binding_is_explicitly_ignored(project: Path) -> bool:
    ignore = project / ".gitignore"
    _validate_components(
        ignore,
        label="project .gitignore",
        leaf_kind="file",
        leaf_may_be_missing=False,
    )
    try:
        lines = ignore.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise BrainPathError(f"cannot read project .gitignore: {exc}") from None
    normalized = {line.strip() for line in lines if line.strip()}
    return BINDING_IGNORE_RULE in normalized or f"/{BINDING_IGNORE_RULE}" in normalized


def _external_target(raw: object, *, source: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise BrainPathError(f"{source} target must be a nonempty absolute path")
    target = Path(raw)
    if not target.is_absolute():
        raise BrainPathError(f"{source} target must be absolute: {raw}")
    target = _absolute(target)
    return _validate_components(
        target,
        label=f"{source} target",
        leaf_kind="file",
        leaf_may_be_missing=True,
    )


def _read_binding(project: Path, binding: Path) -> Path:
    _validate_components(
        binding,
        label="shared-brain binding",
        leaf_kind="file",
        leaf_may_be_missing=False,
    )
    if not _binding_is_explicitly_ignored(project):
        raise BrainPathError(
            f"shared-brain binding is not explicitly ignored by "
            f"{project / '.gitignore'} ({BINDING_IGNORE_RULE})"
        )
    try:
        lines = [
            line
            for line in binding.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError) as exc:
        raise BrainPathError(f"cannot read shared-brain binding: {exc}") from None
    if len(lines) != 1:
        raise BrainPathError(
            "shared-brain binding must contain exactly one nonblank JSONL object"
        )
    try:
        row = json.loads(
            lines[0],
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite number {value}")
            ),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise BrainPathError(f"invalid shared-brain binding JSON: {exc}") from None
    if not isinstance(row, dict):
        raise BrainPathError("shared-brain binding must be a JSON object")
    if set(row) != BINDING_KEYS:
        raise BrainPathError(
            f"shared-brain binding keys must be exactly {sorted(BINDING_KEYS)}"
        )
    if row.get("schema") != BINDING_SCHEMA:
        raise BrainPathError(
            f"shared-brain binding schema must be {BINDING_SCHEMA!r}"
        )
    return _external_target(row.get("target"), source="shared-brain binding")


def resolve_shared_brain(
    project_root: os.PathLike[str] | str,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the one authorized shared-brain file for a project."""
    project = _project_root(project_root)
    env = os.environ if environ is None else environ

    if ENV_NAME in env:
        return _external_target(env.get(ENV_NAME), source=ENV_NAME)

    binding = project / BINDING_RELATIVE
    try:
        binding.lstat()
    except FileNotFoundError:
        pass
    except NotADirectoryError:
        raise BrainPathError(
            f"shared-brain binding has a non-directory parent: {binding.parent}"
        ) from None
    else:
        return _read_binding(project, binding)

    local = project / "brain" / "shared-brain.jsonl"
    try:
        local.relative_to(project)
    except ValueError:
        raise BrainPathError(f"local shared brain escapes project: {local}") from None
    return _validate_components(
        local,
        label="local shared brain",
        leaf_kind="file",
        leaf_may_be_missing=True,
    )


TYPE_FIELDS = ("type", "kind", "memory_type")


class BrainRecordError(ValueError):
    """A durable-memory record violates the canonical schema contract."""


def record_type(record: object) -> str | None:
    """Read canonical or legacy type aliases without silently accepting conflicts."""
    if not isinstance(record, dict):
        raise BrainRecordError(
            f"memory record must be a JSON object, got {type(record).__name__}"
        )
    found: list[tuple[str, str]] = []
    for field in TYPE_FIELDS:
        if field not in record:
            continue
        value = record[field]
        if not isinstance(value, str) or not value.strip():
            raise BrainRecordError(f"memory record {field} must be a nonempty string")
        found.append((field, value.strip()))
    if not found:
        return None
    values = {value for _, value in found}
    if len(values) != 1:
        detail = ", ".join(f"{field}={value!r}" for field, value in found)
        raise BrainRecordError(f"memory type alias conflict: {detail}")
    return found[0][1]


def _deterministic_record_id(record: dict) -> str:
    try:
        payload = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise BrainRecordError(f"memory record is not finite JSON: {exc}") from None
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"memory-{digest}"


def canonicalize_record(record: object) -> dict:
    """Return one canonical write shape while keeping legacy rows readable."""
    if not isinstance(record, dict):
        raise BrainRecordError(
            f"memory record must be a JSON object, got {type(record).__name__}"
        )
    canonical = dict(record)
    typ = record_type(canonical)
    canonical.pop("kind", None)
    canonical.pop("memory_type", None)
    if typ is not None:
        canonical["type"] = typ

    rid = canonical.get("id")
    if rid is None or (isinstance(rid, str) and not rid.strip()):
        canonical.pop("id", None)
        canonical["id"] = _deterministic_record_id(canonical)
    elif not isinstance(rid, str):
        raise BrainRecordError("memory record id must be a nonempty string")
    else:
        canonical["id"] = rid.strip()

    # Validate the final object, including finite exponent-overflow values.
    try:
        json.dumps(canonical, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BrainRecordError(f"memory record is not finite JSON: {exc}") from None
    return canonical


# ---------------------------------------------------------------------------
# Installer-facing helpers (scripts/install_full_engine.py --brain-migration).
#
# The installer runs before any addon exists and must describe paths for a
# target project it is only about to create, so these are deliberately pure:
# they compute names and build records, and never touch the filesystem or
# resolve a binding. Path *validation* stays in resolve_shared_brain above.
# ---------------------------------------------------------------------------

PROJECT_OS_SHARED_BRAIN = ENV_NAME
MIGRATION_MODES = ("migrate", "bind", "fresh-local")

BINDING_PAYLOAD_SCHEMA = BINDING_SCHEMA
RECEIPT_PAYLOAD_SCHEMA = "project-os/shared-brain-migration-receipt/v1"


class SharedBrainPathError(BrainPathError):
    """Raised for an unusable installer-supplied shared-brain path.

    Subclasses BrainPathError so a caller that guards the resolver also
    catches installer path errors, rather than having two parallel
    hierarchies that each catch half the failures.
    """


def explicit_shared_brain(environ=None):
    """The absolute PROJECT_OS_SHARED_BRAIN override, or None when unset.

    Returns a Path so the installer can compare it against the target project
    without re-parsing. An empty or relative value is a hard error: silently
    ignoring it would send lessons to a different brain than the operator
    named.
    """
    environ = os.environ if environ is None else environ
    raw = environ.get(ENV_NAME)
    if raw is None:
        return None
    if not raw.strip():
        raise SharedBrainPathError(
            f"{ENV_NAME} must be a non-empty absolute path")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise SharedBrainPathError(f"{ENV_NAME} must be an absolute path")
    return path


def local_shared_brain(project_root):
    """The project-local brain, which is always the default destination."""
    return Path(project_root) / "brain" / "shared-brain.jsonl"


def binding_path(project_root):
    return Path(project_root) / BINDING_RELATIVE


def migration_receipt_path(project_root):
    return Path(project_root) / "brain" / "shared-brain-migration-receipt.jsonl"


def archive_path(brain):
    """The archive tier beside `brain`.

    Mirrors brain_archive.py / brain_scale.py / mneme_adapter._archive_path
    exactly (stem + "-archive.jsonl"), so the installer snapshots the same
    file the runtime tools later read. A hardcoded "shared-brain-archive.jsonl"
    would silently skip the archive of any custom-named brain.
    """
    p = Path(brain)
    if p.name.endswith(".jsonl"):
        return p.with_name(p.stem + "-archive.jsonl")
    return p.with_name(p.name + "-archive.jsonl")


def binding_payload(target):
    """The one strict JSONL record that binds a project to an external brain."""
    return {"schema": BINDING_SCHEMA, "target": str(Path(target).absolute())}


def receipt_payload(mode, legacy_active, legacy_archive):
    """A record of what an upgrade did, so the choice stays auditable later."""
    if mode not in MIGRATION_MODES:
        raise SharedBrainPathError(
            f"unknown brain-migration mode {mode!r}; expected one of "
            + ", ".join(MIGRATION_MODES))
    return {
        "schema": RECEIPT_PAYLOAD_SCHEMA,
        "mode": mode,
        "legacy_active": str(Path(legacy_active).absolute()),
        "legacy_archive": str(Path(legacy_archive).absolute()),
    }


def read_migration_receipt(project_root):
    """Parse the receipt written by a prior upgrade.

    Reads only the first JSONL record: the receipt is single-object by
    contract, and a trailing newline must not read as a decode failure.
    """
    text = migration_receipt_path(project_root).read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip():
            return json.loads(line)
    raise SharedBrainPathError("migration receipt is empty")
