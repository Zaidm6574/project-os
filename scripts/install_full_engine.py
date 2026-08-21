#!/usr/bin/env python3
"""Install the optional Project OS full engine add-on into a target project."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import FrozenSet, Optional


TEMPLATE_ROOT = Path(__file__).resolve().parents[1]
ADDON_ROOT = TEMPLATE_ROOT / "addons" / "full-engine"
LEGACY_CENTRAL_BRAIN_ENV = "PROJECT_OS_LEGACY_CENTRAL_BRAIN"
GENERATED_DIRS = {"__pycache__", "store", "out", "graphify-out", ".turbovec"}
GENERATED_SUFFIXES = (
    ".pyc", ".tvim", ".sidecar.json", ".manifest.json",
    ".db", ".db.tmp", ".db-wal", ".db-shm", ".db-journal",
    ".db.tmp-wal", ".db.tmp-shm", ".db.tmp-journal",
)
PRIVATE_DIRS = {
    "secrets", ".secrets", "archive", "archives", "backup", "backups",
    "credential", "credentials",
}
PRIVATE_NAMES = {
    ".env",
    ".envrc",
    ".netrc",
    ".secrets",
    "credential",
    "auth.json",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secret.env",
    "secrets",
    "service-account.json",
    "service_account.json",
    "token.json",
    "client_secret",
    "client-secret",
    "service_account",
    "service-account",
}
PRIVATE_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".jks", ".keystore")
ARCHIVE_SUFFIXES = (
    ".bak", ".backup", ".old", ".orig", ".save", ".tar", ".tar.gz",
    ".tar.bz2", ".tar.xz", ".tgz", ".zip", ".7z", ".rar",
)
CREDENTIAL_STEMS = (
    "credential", "credentials", "client_secret", "client-secrets", "client-secret",
    "service_account", "service-account",
)
CREDENTIAL_DATA_SUFFIXES = (".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".txt")
DATABASE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
SQLITE_SIDECARS = ("-wal", "-shm", "-journal", "-tmp", ".wal", ".shm", ".journal", ".tmp")
REQUIRED_FULL_ENGINE_PAYLOADS = (
    "memory/new_run.py",
    "memory/validate_run.py",
    "memory/score_rubric.py",
    "brain/brain.py",
    "brain/central_brain.py",
    "blackboard-addons/21-agent-roster.md",
)
REQUIRED_CLAUDE_PAYLOADS = (
    "staged/agents/project-os-ceo.md",
    "staged/agents/context-scout.md",
    "staged/commands/kickoff.md",
    "staged/commands/new-run.md",
)
REQUIRED_CODEX_PAYLOADS = (
    "staged/codex-skills/project/SKILL.md",
    "staged/codex-skills/kickoff/SKILL.md",
    "staged/codex-skills/new-run/SKILL.md",
)
RESOLVER_COHORT_SOURCES = {
    "memory/mneme_adapter.py": "memory/mneme_adapter.py",
    "scripts/brain_append.py": "scripts/brain_append.py",
    "scripts/brain_archive.py": "scripts/brain_archive.py",
    "scripts/brain_scale.py": "scripts/brain_scale.py",
    "scripts/harvest.py": "scripts/harvest.py",
    "brain/brain.py": "addons/full-engine/brain/brain.py",
    "brain/central_brain.py": "addons/full-engine/brain/central_brain.py",
    "scripts/brain_paths.py": "scripts/brain_paths.py",
}


def _resolver_cohort_signatures(
    *,
    mneme=None,
    append=None,
    archive=None,
    scale=None,
    harvest=None,
    brain=None,
    central=None,
):
    signatures = {}
    values = (
        ("memory/mneme_adapter.py", mneme),
        ("scripts/brain_append.py", append),
        ("scripts/brain_archive.py", archive),
        ("scripts/brain_scale.py", scale),
        ("scripts/harvest.py", harvest),
    )
    signatures.update((relative, digest) for relative, digest in values if digest)
    if brain:
        signatures["brain/brain.py"] = brain
        signatures["addons/full-engine/brain/brain.py"] = brain
    if central:
        signatures["brain/central_brain.py"] = central
        signatures["addons/full-engine/brain/central_brain.py"] = central
    return signatures


# Known unedited resolver cohorts only. A present non-current file must agree
# with one of these complete revision maps; mixed cohorts fail closed.
LEGACY_RESOLVER_SIGNATURES = {
    "published-v0.1.0-v0.1.1": _resolver_cohort_signatures(
        brain="74df69f9c1f4db51e497fc779866d8f70e96da51500b169e016c7d1c571c5c6c",
        central="04f95e4ccb8b5419fca1fcc3f5f6ade5c09fa3eded469310262ed0031e0b8359",
    ),
    "published-f034ea5": _resolver_cohort_signatures(
        mneme="3de3551e4cbf102e6b874fefa9e90b9aa9d65ced690ea293a18483018c693632",
        append="371262dd22704524e47cf93dd931ff29c270a32a87f8cc32c9a432fb416bfb5a",
        scale="2471330128bf5f747d607468d709dc9b30674f65017b686f6c4340f222209432",
        harvest="2ac5862df95bf9a0b69fd26c0c06cf76b7253f3140eed42acb67b22b2fdcb56a",
        brain="74df69f9c1f4db51e497fc779866d8f70e96da51500b169e016c7d1c571c5c6c",
        central="04f95e4ccb8b5419fca1fcc3f5f6ade5c09fa3eded469310262ed0031e0b8359",
    ),
    "published-07b5805": _resolver_cohort_signatures(
        mneme="d852f669fadfa1d84fe9ce5a06eee0655a1914b9be3c6d2c92e38f99c4aeb17c",
        append="371262dd22704524e47cf93dd931ff29c270a32a87f8cc32c9a432fb416bfb5a",
        archive="e645147ba2179ee82a9ab1444f3195ead4be436de1c68e5e6be44f341add89be",
        scale="8f9587e33d26fa64033e059cfa993fe57c91cfbe7a52c2a1347cd6e3a3a1715c",
        harvest="2ac5862df95bf9a0b69fd26c0c06cf76b7253f3140eed42acb67b22b2fdcb56a",
        brain="74df69f9c1f4db51e497fc779866d8f70e96da51500b169e016c7d1c571c5c6c",
        central="04f95e4ccb8b5419fca1fcc3f5f6ade5c09fa3eded469310262ed0031e0b8359",
    ),
    "published-df17b3f": _resolver_cohort_signatures(
        mneme="fc08f910ca9d39f9d62bdd91032a598bb03ae5757ae7cc7dcdb89a7575667ccf",
        append="4420cdd64263287a57c0dca3522e522dcf30608017762812f1c22b936afa5db6",
        archive="e645147ba2179ee82a9ab1444f3195ead4be436de1c68e5e6be44f341add89be",
        scale="242e67a58dc4119db5f6b2dd3da666c3d02d7e6e8440a5223e527d2c0393fb7d",
        harvest="a88a4a91813ce74ea9ed172956e36d817c64c2b8a5416e83caf5e08ff2a8ac33",
        brain="74df69f9c1f4db51e497fc779866d8f70e96da51500b169e016c7d1c571c5c6c",
        central="04f95e4ccb8b5419fca1fcc3f5f6ade5c09fa3eded469310262ed0031e0b8359",
    ),
    "published-e4c6ca0": _resolver_cohort_signatures(
        mneme="fc08f910ca9d39f9d62bdd91032a598bb03ae5757ae7cc7dcdb89a7575667ccf",
        append="327fac967595174fcf89d6bb19d80bbe576b1dbea02c9fe099804b2b6473d543",
        archive="e645147ba2179ee82a9ab1444f3195ead4be436de1c68e5e6be44f341add89be",
        scale="ebe30be1eff425718ac0cc889130d815aafadfa07161f423fd978455ef1d3f3f",
        harvest="a88a4a91813ce74ea9ed172956e36d817c64c2b8a5416e83caf5e08ff2a8ac33",
        brain="c9d27a0d559eec65c61f18430b6792bd776cb283ea27e8611fc49039f654a85e",
        central="335b2571fc0f4b7e6a24561841b62829d2c6a6936ca8e83897da0a9e3e684c7e",
    ),
    "published-ef0cb9": _resolver_cohort_signatures(
        mneme="fc08f910ca9d39f9d62bdd91032a598bb03ae5757ae7cc7dcdb89a7575667ccf",
        append="327fac967595174fcf89d6bb19d80bbe576b1dbea02c9fe099804b2b6473d543",
        archive="e645147ba2179ee82a9ab1444f3195ead4be436de1c68e5e6be44f341add89be",
        scale="d6da473483a6d4c2baefe026119ea9848eaa57dc2706db780b2ed068c74b7e53",
        harvest="a88a4a91813ce74ea9ed172956e36d817c64c2b8a5416e83caf5e08ff2a8ac33",
        brain="c9d27a0d559eec65c61f18430b6792bd776cb283ea27e8611fc49039f654a85e",
        central="335b2571fc0f4b7e6a24561841b62829d2c6a6936ca8e83897da0a9e3e684c7e",
    ),
    "verified-pre-canonical-2026-07-26": _resolver_cohort_signatures(
        mneme="0a7f56aa92bae3d480d0abff1a087c1aad5cd8f22907996f3584104e32e85235",
        append="b4e9f879f5487e564707173d5505eba5bdc14c3a6de648917172f82b6684d50f",
        archive="ea5ac7faf19599b8511b1fba23a2d461c1325f0d4594b5aaabe57431592cc5c4",
        scale="2553c63cc35d092c5a3c055eeb98029da58469ce26c81beff36975c01c6defbb",
        harvest="9888271cb564d91cb2d7e61bdd12966eb79b922b0001ff11c016806e38af007d",
        brain="56e17b53a792f0abc9df0fcd6dd342fa8c17749089168abac0bab251be40bedf",
        central="d163b1a6ca1a5619a7ba903d5220004a407254651b2e710e888a0def3adbde15",
    ),
}



def nonempty_path(value: str) -> Path:
    if not value.strip():
        raise argparse.ArgumentTypeError("target must be a non-empty path")
    return Path(value)


def is_distributable(src: Path, src_dir: Path) -> bool:
    rel = src.relative_to(src_dir)
    name = src.name.lower()
    parts = tuple(part.lower() for part in rel.parts)
    return (
        not src.is_symlink()
        and not name.startswith("shared-brain")
        and not name.startswith("shared_brain")
        and not name.endswith("-archive.jsonl")
        and ".pre-archive-" not in name
        and not name.endswith(".pre-force")
        and name not in PRIVATE_NAMES
        and not name.startswith(".env.")
        and not name.startswith("secrets.")
        and not name.endswith(PRIVATE_SUFFIXES)
        and not name.endswith(ARCHIVE_SUFFIXES)
        and not any(
            (name == stem or name.startswith(stem + "."))
            and name.endswith(CREDENTIAL_DATA_SUFFIXES)
            for stem in CREDENTIAL_STEMS
        )
        and not any(part in GENERATED_DIRS or part in PRIVATE_DIRS for part in parts)
        and not name.endswith(GENERATED_SUFFIXES)
        and not any(
            name.endswith(suffix)
            or any(name.endswith(suffix + sidecar) for sidecar in SQLITE_SIDECARS)
            for suffix in DATABASE_SUFFIXES
        )
    )


def absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(str(path.expanduser())))


def existing_node(path: Path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except NotADirectoryError as exc:
        raise ValueError(f"unsafe destination node type (expected directory): {path.parent}") from exc


def validate_existing_node(path: Path, expected_kind: str) -> None:
    node = existing_node(path)
    if node is None:
        return
    if stat.S_ISLNK(node.st_mode):
        raise ValueError(f"unsafe destination symlink: {path}")
    if expected_kind == "directory":
        if not stat.S_ISDIR(node.st_mode):
            raise ValueError(f"unsafe destination node type (expected directory): {path}")
        return
    if not stat.S_ISREG(node.st_mode):
        raise ValueError(f"unsafe destination node type (expected regular file): {path}")
    if node.st_nlink > 1:
        raise ValueError(f"unsafe destination hardlink (link count {node.st_nlink}): {path}")


def validate_source(path: Path, expected_root: Path, *, directory: bool) -> None:
    expected_root = absolute_path(expected_root)
    path = absolute_path(path)
    if expected_root.is_symlink():
        raise ValueError(f"unsafe source symlink root: {expected_root}")
    if not expected_root.is_dir():
        raise FileNotFoundError(f"missing source root: {expected_root}")
    try:
        rel = path.relative_to(expected_root)
    except ValueError as exc:
        raise ValueError(f"source escapes add-on root: {path}") from exc

    current = expected_root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"unsafe source symlink: {current}")
    if directory:
        if not path.is_dir():
            raise FileNotFoundError(f"missing add-on folder: {path}")
    elif not path.is_file():
        raise FileNotFoundError(f"missing add-on file: {path}")

    root_real = expected_root.resolve()
    path_real = path.resolve()
    try:
        common = Path(os.path.commonpath((str(root_real), str(path_real))))
    except ValueError as exc:
        raise ValueError(f"source escapes add-on root: {path}") from exc
    if common != root_real:
        raise ValueError(f"source escapes add-on root: {path}")


def validate_source_file(path: Path, expected_root: Path) -> None:
    validate_source(path, expected_root, directory=False)


def validate_source_dir(path: Path, expected_root: Path) -> None:
    validate_source(path, expected_root, directory=True)


def resolved_target(target: Path) -> tuple[Path, Path]:
    target = absolute_path(target)
    validate_existing_node(target, "directory")
    for parent in target.parents:
        if not parent.is_symlink():
            continue
        resolved_parent = parent.resolve()
        is_macos_system_alias = parent.parent == Path("/") and resolved_parent.parent == Path("/private")
        if not is_macos_system_alias:
            raise ValueError(f"unsafe destination symlink parent: {parent}")
    target = target.resolve()
    return target, target


def validate_destination(
    dst: Path,
    target: Path,
    target_resolved: Optional[Path] = None,
    destination_kind: Optional[str] = None,
) -> None:
    target = absolute_path(target)
    dst = absolute_path(dst)
    resolved_root = target.resolve() if target_resolved is None else target_resolved
    try:
        rel = dst.relative_to(target)
    except ValueError as exc:
        raise ValueError(f"destination escapes target: {dst}") from exc

    validate_existing_node(target, "directory")
    current = target
    for index, part in enumerate(rel.parts):
        current = current / part
        is_destination = index == len(rel.parts) - 1
        expected_kind = destination_kind if is_destination and destination_kind else "directory"
        validate_existing_node(current, expected_kind)

    if not rel.parts and destination_kind:
        validate_existing_node(dst, destination_kind)

    resolved_dst = dst.resolve()
    try:
        common = Path(os.path.commonpath((str(resolved_root), str(resolved_dst))))
    except ValueError as exc:
        raise ValueError(f"destination escapes target: {dst}") from exc
    if common != resolved_root:
        raise ValueError(f"destination escapes target: {dst}")


def _is_macos_system_alias(path: Path) -> bool:
    if not path.is_symlink():
        return False
    resolved = path.resolve()
    return path.parent == Path("/") and resolved.parent == Path("/private")


def validate_central_brain_path(path: Path) -> Path:
    path = absolute_path(path)
    if not path.is_absolute():
        raise ValueError(f"central brain path must be absolute: {path}")

    current = Path(path.anchor)
    nearest_existing = current
    for part in path.parts[1:]:
        current = current / part
        try:
            node = current.lstat()
        except FileNotFoundError:
            break
        except NotADirectoryError as exc:
            raise ValueError(f"central brain path has a non-directory parent: {current.parent}") from exc
        if stat.S_ISLNK(node.st_mode):
            if _is_macos_system_alias(current):
                nearest_existing = current
                continue
            raise ValueError(f"unsafe central brain symlink: {current}")
        if not stat.S_ISDIR(node.st_mode):
            raise ValueError(f"central brain path must be a directory: {current}")
        nearest_existing = current

    if not os.access(nearest_existing, os.W_OK | os.X_OK):
        raise ValueError(f"central brain path is not writable: {nearest_existing}")

    if path.is_dir():
        if not os.access(path, os.W_OK | os.X_OK):
            raise ValueError(f"central brain path is not writable: {path}")
        brain_file = path / "shared-brain.jsonl"
        node = existing_node(brain_file)
        if node is not None:
            if stat.S_ISLNK(node.st_mode):
                raise ValueError(f"unsafe central brain symlink: {brain_file}")
            if not stat.S_ISREG(node.st_mode):
                raise ValueError(f"central brain record must be a regular file: {brain_file}")
            if node.st_nlink > 1:
                raise ValueError(f"unsafe central brain hardlink: {brain_file}")
        readme = path / "README.md"
        readme_node = existing_node(readme)
        if readme_node is not None and stat.S_ISLNK(readme_node.st_mode):
            raise ValueError(f"unsafe central brain symlink: {readme}")
    return path

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _create_private(path) -> bool:
    """Create `path` as an empty 0600 file, or leave an existing one untouched.

    The shared brain holds lesson text harvested from every project, and every
    record is credential-scanned before it is allowed in, so it must not be
    born at the umask default (0644 on a stock umask 022 account).
    scripts/brain_archive.py already creates this same data with
    os.open(..., O_CREAT|O_EXCL, 0o600) and gives the reasoning in
    _mode_or_private(); this is that pattern, kept byte-identical in every
    module that can bring a brain file into existence -- scripts/brain_append.py,
    scripts/install_full_engine.py, addons/full-engine/brain/brain.py and
    addons/full-engine/brain/central_brain.py. Path.touch(), Path.write_text()
    and open(path, "a") all create at 0666 & ~umask instead, and this repo has
    already shipped a guard applied to one of two installers, so a sync test
    pins these copies together.

    O_EXCL is what makes it safe to call unconditionally: it fails with EEXIST
    when the path already exists -- including when the path is a symlink, even
    a dangling one -- so an operator who deliberately chose 0640 keeps 0640,
    and nothing is ever created through a pre-planted name. Returns True only
    when this call is the one that created the file.
    """
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    os.close(fd)
    return True


def match_resolver_cohort(present_hashes, current_hashes):
    """Return one coherent legacy cohort, None for current/missing, or fail closed."""
    candidates = None
    legacy_paths = []
    for relative, digest in present_hashes.items():
        if digest == current_hashes.get(relative):
            continue
        matches = {
            cohort
            for cohort, signatures in LEGACY_RESOLVER_SIGNATURES.items()
            if signatures.get(relative) == digest
        }
        if not matches:
            raise ValueError(
                "resolver cohort has edited, unknown, or mixed content at "
                f"{relative}; automatic --brain-migration is refused even with "
                "--force — review the local changes and migrate manually"
            )
        legacy_paths.append(relative)
        candidates = matches if candidates is None else candidates & matches
        if not candidates:
            raise ValueError(
                "resolver cohort has edited, unknown, or mixed legacy revisions; "
                "automatic --brain-migration is refused even with --force — "
                "review the local changes and migrate manually"
            )
    if not legacy_paths:
        return None
    return sorted(candidates)[0]


def _snapshot_identity(node):
    return (
        node.st_dev,
        node.st_ino,
        node.st_size,
        node.st_mtime_ns,
        node.st_ctime_ns,
    )


def _safe_regular_snapshot(path: Path, label: str):
    """Read a regular single-link file without following or accepting torn state."""
    try:
        node = path.lstat()
    except FileNotFoundError:
        return {"exists": False}
    except NotADirectoryError as exc:
        raise ValueError(f"{label} has a non-directory parent: {path.parent}") from exc
    if stat.S_ISLNK(node.st_mode):
        raise ValueError(f"unsafe {label} symlink: {path}")
    if not stat.S_ISREG(node.st_mode):
        raise ValueError(f"unsafe {label} node type (expected regular file): {path}")
    if node.st_nlink > 1:
        raise ValueError(f"unsafe {label} hardlink (link count {node.st_nlink}): {path}")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"could not safely open {label}: {path}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink > 1:
            raise ValueError(f"unsafe {label} changed while opening: {path}")
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(fd)
    finally:
        os.close(fd)

    try:
        final = path.lstat()
    except OSError as exc:
        raise ValueError(f"{label} changed while reading: {path}") from exc
    if (
        _snapshot_identity(before) != _snapshot_identity(after)
        or before.st_dev != final.st_dev
        or before.st_ino != final.st_ino
        or final.st_nlink > 1
        or not stat.S_ISREG(final.st_mode)
    ):
        raise ValueError(f"{label} changed while reading: {path}")
    return {
        "exists": True,
        "data": b"".join(chunks),
        "mode": stat.S_IMODE(after.st_mode),
        "identity": _snapshot_identity(after),
    }


def _same_snapshot(left, right):
    return left == right


def _load_sibling_module(name: str):
    path = TEMPLATE_ROOT / "scripts" / f"{name}.py"
    validate_source_file(path, TEMPLATE_ROOT)
    module_name = f"_project_os_installer_{name}_{id(path)}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def _preflight_resolver_cohort(target: Path, target_real: Path):
    sources = {}
    current_hashes = {}
    target_snapshots = {}
    present_hashes = {}
    for installed_relative, source_relative in RESOLVER_COHORT_SOURCES.items():
        source = TEMPLATE_ROOT / source_relative
        validate_source_file(source, TEMPLATE_ROOT)
        source_snapshot = _safe_regular_snapshot(source, "resolver source")
        sources[installed_relative] = {
            "path": source,
            "data": source_snapshot["data"],
            "mode": source_snapshot["mode"],
        }
        current_hashes[installed_relative] = _sha256(source_snapshot["data"])

        destination = target / installed_relative
        validate_destination(
            destination,
            target,
            target_real,
            destination_kind="file",
        )
        destination_snapshot = _safe_regular_snapshot(
            destination, "resolver cohort destination"
        )
        target_snapshots[installed_relative] = destination_snapshot
        if destination_snapshot["exists"]:
            present_hashes[installed_relative] = _sha256(
                destination_snapshot["data"]
            )

    legacy_cohort = match_resolver_cohort(present_hashes, current_hashes)
    return {
        "sources": sources,
        "snapshots": target_snapshots,
        "legacy_cohort": legacy_cohort,
    }


def _compact_json_bytes(payload) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _stage_bytes(destination: Path, data: bytes, mode: int) -> Path:
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.installer-",
        dir=destination.parent,
    )
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, mode)
        view = memoryview(data)
        written = 0
        while written < len(view):
            written += os.write(fd, view[written:])
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        temporary_path.unlink(missing_ok=True)
        raise
    os.close(fd)
    return temporary_path


def _restore_snapshot(destination: Path, snapshot) -> None:
    if not snapshot["exists"]:
        destination.unlink(missing_ok=True)
        return
    staged = _stage_bytes(destination, snapshot["data"], snapshot["mode"])
    try:
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)


def _publish_transaction(
    outputs,
    expected_snapshots,
    target: Path,
    *,
    verify_fence=None,
) -> None:
    staged = {}
    originals = {}
    committed = []
    for destination, _, _ in outputs:
        validate_destination(destination, target, destination_kind="file")
        current = _safe_regular_snapshot(destination, "migration destination")
        expected = expected_snapshots[destination]
        if not _same_snapshot(current, expected):
            raise OSError(f"migration destination changed after preflight: {destination}")
        originals[destination] = current

    try:
        for destination, data, mode in outputs:
            validate_destination(destination.parent, target, destination_kind="directory")
            destination.parent.mkdir(parents=True, exist_ok=True)
            validate_destination(destination, target, destination_kind="file")
            staged[destination] = _stage_bytes(destination, data, mode)

        if verify_fence is not None and not verify_fence():
            raise OSError("legacy shared-brain lock lease was lost before publication")

        try:
            for destination, _, _ in outputs:
                current = _safe_regular_snapshot(destination, "migration destination")
                if not _same_snapshot(current, expected_snapshots[destination]):
                    raise OSError(
                        f"migration destination changed before publication: {destination}"
                    )
                os.replace(staged[destination], destination)
                committed.append(destination)
            for parent in {destination.parent for destination, _, _ in outputs}:
                _fsync_directory(parent)
        except BaseException as publish_error:
            rollback_errors = []
            for destination in reversed(committed):
                try:
                    _restore_snapshot(destination, originals[destination])
                except BaseException as rollback_error:
                    rollback_errors.append(f"{destination}: {rollback_error}")
            if rollback_errors:
                raise OSError(
                    "migration publication failed and rollback was incomplete: "
                    + "; ".join(rollback_errors)
                ) from publish_error
            raise
    finally:
        for temporary_path in staged.values():
            temporary_path.unlink(missing_ok=True)


def _copy_resolver_cohort(plan, target: Path, dry_run: bool):
    results = []
    for relative, source in plan["sources"].items():
        destination = target / relative
        snapshot = plan["snapshots"][relative]
        if snapshot["exists"] and snapshot["data"] == source["data"]:
            results.append(f"kept current resolver {destination}")
            continue
        if dry_run:
            action = "update" if snapshot["exists"] else "write"
            results.append(f"would {action} resolver {destination}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        _publish_transaction(
            [(destination, source["data"], source["mode"])],
            {destination: snapshot},
            target,
        )
        results.append(f"wrote current resolver {destination}")
    return results


def _locked_legacy_snapshots(active: Path, archive: Path):
    bb_lock = _load_sibling_module("bb_lock")
    token = bb_lock.acquire(
        str(active),
        agent="brain-migration-installer",
        wait=15,
    )
    if not token:
        raise OSError(f"could not acquire legacy shared-brain lock: {active}")
    release_ok = False
    try:
        snapshots = {
            active: _safe_regular_snapshot(active, "legacy active brain"),
            archive: _safe_regular_snapshot(archive, "legacy archive brain"),
        }
        if not bb_lock.renew(str(active), token):
            raise OSError("legacy shared-brain lock lease was lost during preflight")
        return snapshots
    finally:
        release_ok = bb_lock.release(
            str(active),
            agent="brain-migration-installer",
            token=token,
        )
        if not release_ok:
            raise OSError(
                "could not release legacy shared-brain lock with its fencing token"
            )


def _resolve_legacy_central_brain(override: Path | None = None) -> Path:
    """Resolve the legacy central-brain directory used by migration.

    An explicit CLI value wins over ``PROJECT_OS_LEGACY_CENTRAL_BRAIN``;
    otherwise the portable default is ``~/.project-os/central-brain``. The
    path is resolved before inspecting its records so a symlinked home alias
    (for example macOS's ``/var`` aliases) has the same behavior as the
    historical default while the final brain files remain safety-checked.
    """
    if override is None:
        raw = os.environ.get(LEGACY_CENTRAL_BRAIN_ENV)
        if raw is not None:
            if not raw.strip():
                raise ValueError(
                    f"{LEGACY_CENTRAL_BRAIN_ENV} must be a non-empty absolute path"
                )
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                raise ValueError(
                    f"{LEGACY_CENTRAL_BRAIN_ENV} must be an absolute path"
                )
        else:
            candidate = Path.home() / ".project-os" / "central-brain"
    else:
        candidate = override.expanduser()

    candidate = absolute_path(candidate).resolve()
    validate_central_brain_path(candidate)
    return candidate


def _preflight_brain_migration(
    target: Path,
    target_real: Path,
    resolver_plan,
    brain_migration,
    legacy_central_brain: Path | None = None,
):
    brain_paths = _load_sibling_module("brain_paths")
    if brain_migration is not None and brain_migration not in brain_paths.MIGRATION_MODES:
        raise ValueError(
            "brain migration mode must be one of: "
            + ", ".join(brain_paths.MIGRATION_MODES)
        )
    try:
        explicit = brain_paths.explicit_shared_brain(os.environ)
    except brain_paths.SharedBrainPathError as exc:
        raise ValueError(str(exc)) from exc
    if explicit is not None or resolver_plan["legacy_cohort"] is None:
        return None

    # Resolve the legacy directory chain (macOS /tmp and friends are
    # symlinks) but keep the final file components unresolved so a
    # symlinked brain file is still refused by the safe snapshot below.
    legacy_dir = _resolve_legacy_central_brain(legacy_central_brain)
    legacy_active = legacy_dir / "shared-brain.jsonl"
    legacy_archive = Path(brain_paths.archive_path(legacy_active))
    if legacy_archive.parent != legacy_active.parent:
        raise ValueError("legacy archive path must be a sibling of the active brain")

    legacy_snapshots = _locked_legacy_snapshots(legacy_active, legacy_archive)
    has_data = any(
        snapshot["exists"] and bool(snapshot["data"].strip())
        for snapshot in legacy_snapshots.values()
    )
    if has_data and brain_migration is None:
        raise ValueError(
            "legacy shared brain data requires "
            "--brain-migration {migrate,bind,fresh-local}"
        )
    if brain_migration is None:
        return None

    local_active = Path(brain_paths.local_shared_brain(target))
    local_archive = Path(brain_paths.archive_path(local_active))
    binding = Path(brain_paths.binding_path(target))
    receipt = Path(brain_paths.migration_receipt_path(target))
    destinations = (local_active, local_archive, binding, receipt)
    target_snapshots = {}
    for destination in destinations:
        validate_destination(
            destination,
            target,
            target_real,
            destination_kind="file",
        )
        target_snapshots[destination] = _safe_regular_snapshot(
            destination, "brain migration target"
        )
    if brain_migration == "migrate":
        for destination in (local_active, local_archive):
            snapshot = target_snapshots[destination]
            if snapshot["exists"] and snapshot["data"]:
                raise ValueError(
                    f"migrate refuses nonempty local target: {destination}"
                )

    return {
        "mode": brain_migration,
        "brain_paths": brain_paths,
        "legacy_active": legacy_active,
        "legacy_archive": legacy_archive,
        "legacy_snapshots": legacy_snapshots,
        "local_active": local_active,
        "local_archive": local_archive,
        "binding": binding,
        "receipt": receipt,
        "target_snapshots": target_snapshots,
    }


def _apply_brain_migration(plan, target: Path, dry_run: bool):
    mode = plan["mode"]
    if dry_run:
        return [f"would {mode} legacy shared brain"]

    brain_paths = plan["brain_paths"]
    receipt_bytes = _compact_json_bytes(
        brain_paths.receipt_payload(
            mode,
            plan["legacy_active"],
            plan["legacy_archive"],
        )
    )
    if mode == "bind":
        outputs = [
            (
                plan["binding"],
                _compact_json_bytes(
                    brain_paths.binding_payload(plan["legacy_active"])
                ),
                0o600,
            )
        ]
        _publish_transaction(
            outputs,
            {plan["binding"]: plan["target_snapshots"][plan["binding"]]},
            target,
        )
        return [f"bound shared brain to {plan['legacy_active']}"]

    if mode == "fresh-local":
        outputs = [(plan["receipt"], receipt_bytes, 0o600)]
        _publish_transaction(
            outputs,
            {plan["receipt"]: plan["target_snapshots"][plan["receipt"]]},
            target,
        )
        return [f"recorded fresh-local brain migration at {plan['receipt']}"]

    bb_lock = _load_sibling_module("bb_lock")
    token = bb_lock.acquire(
        str(plan["legacy_active"]),
        agent="brain-migration-installer",
        wait=15,
    )
    if not token:
        raise OSError(
            f"could not acquire legacy shared-brain lock: {plan['legacy_active']}"
        )
    release_ok = False
    try:
        final_snapshots = {
            plan["legacy_active"]: _safe_regular_snapshot(
                plan["legacy_active"], "legacy active brain"
            ),
            plan["legacy_archive"]: _safe_regular_snapshot(
                plan["legacy_archive"], "legacy archive brain"
            ),
        }
        if final_snapshots != plan["legacy_snapshots"]:
            raise OSError("legacy shared brain changed after migration preflight")

        active_snapshot = final_snapshots[plan["legacy_active"]]
        active_data = active_snapshot["data"] if active_snapshot["exists"] else b""
        active_mode = (
            active_snapshot["mode"] & 0o600
            if active_snapshot["exists"]
            else 0o600
        )
        outputs = [(plan["local_active"], active_data, active_mode)]
        archive_snapshot = final_snapshots[plan["legacy_archive"]]
        if archive_snapshot["exists"]:
            outputs.append(
                (
                    plan["local_archive"],
                    archive_snapshot["data"],
                    archive_snapshot["mode"] & 0o600,
                )
            )
        outputs.append((plan["receipt"], receipt_bytes, 0o600))
        expected = {
            destination: plan["target_snapshots"][destination]
            for destination, _, _ in outputs
        }
        _publish_transaction(
            outputs,
            expected,
            target,
            verify_fence=lambda: bb_lock.renew(
                str(plan["legacy_active"]), token
            ),
        )
        return [f"migrated legacy shared brain to {plan['local_active']}"]
    finally:
        release_ok = bb_lock.release(
            str(plan["legacy_active"]),
            agent="brain-migration-installer",
            token=token,
        )
        if not release_ok:
            raise OSError(
                "could not release legacy shared-brain lock with its fencing token"
            )



def distributable_files(src_dir: Path, skip_relpaths: FrozenSet[str] = frozenset(),
                        expected_root: Optional[Path] = None) -> list[Path]:
    source_root = src_dir.parent if expected_root is None else expected_root
    validate_source_dir(src_dir, source_root)
    files = sorted(
        p
        for p in src_dir.rglob("*")
        if p.is_file()
        and str(p.relative_to(src_dir)) not in skip_relpaths
        and is_distributable(p, src_dir)
    )
    for src in files:
        validate_source_file(src, src_dir)
    return files


def copy_file(src: Path, dst: Path, force: bool, dry_run: bool = False,
              target_root: Optional[Path] = None,
              source_root: Optional[Path] = None) -> str:
    validate_source_file(src, src.parent if source_root is None else source_root)
    root = dst.parent if target_root is None else target_root
    validate_destination(dst, root, destination_kind="file")
    if dst.exists() and not force:
        return f"kept existing {dst}"
    if dry_run:
        action = "overwrite" if dst.exists() else "write"
        return f"would {action} {dst}"
    validate_destination(dst.parent, root, destination_kind="directory")
    dst.parent.mkdir(parents=True, exist_ok=True)
    validate_destination(dst, root, destination_kind="file")
    if dst.exists() and dst.read_bytes() != src.read_bytes():
        backup = dst.with_name(dst.name + ".pre-force")
        validate_destination(backup, root, destination_kind="file")
        validate_destination(dst, root, destination_kind="file")
        validate_destination(backup, root, destination_kind="file")
        shutil.copy2(dst, backup)
        validate_destination(dst, root, destination_kind="file")
        shutil.copy2(src, dst)
        return f"wrote {dst} (previous version saved to {backup.name})"
    shutil.copy2(src, dst)
    return f"wrote {dst}"


def copy_tree(src_dir: Path, dst_dir: Path, force: bool, dry_run: bool = False,
              keep_existing: FrozenSet[str] = frozenset(),
              skip_relpaths: FrozenSet[str] = frozenset(),
              target_root: Optional[Path] = None,
              source_root: Optional[Path] = None) -> list[str]:
    results: list[str] = []
    root = dst_dir if target_root is None else target_root
    expected_root = src_dir.parent if source_root is None else source_root
    for src in distributable_files(src_dir, skip_relpaths=skip_relpaths,
                                   expected_root=expected_root):
        rel = src.relative_to(src_dir)
        # keep_existing: the core-delivered variant wins even under --force
        file_force = force and str(rel) not in keep_existing
        results.append(copy_file(src, dst_dir / rel, file_force, dry_run=dry_run,
                                 target_root=root, source_root=src_dir))
    return results


def load_central_brain_module():
    central_path = ADDON_ROOT / "brain" / "central_brain.py"
    validate_source_file(central_path, ADDON_ROOT)
    spec = importlib.util.spec_from_file_location("project_os_central_brain", central_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {central_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_full_engine(
    target: Path,
    force: bool = False,
    claude: bool = False,
    codex: bool = False,
    central_brain: Path | None = None,
    project_id: str | None = None,
    dry_run: bool = False,
    starter_planned: bool = False,
    brain_migration: str | None = None,
    legacy_central_brain: Path | None = None,
) -> list[str]:
    target, target_real = resolved_target(target)
    validate_source_dir(ADDON_ROOT, TEMPLATE_ROOT)

    required_payloads = (
        REQUIRED_FULL_ENGINE_PAYLOADS
        + (REQUIRED_CLAUDE_PAYLOADS if claude else ())
        + (REQUIRED_CODEX_PAYLOADS if codex else ())
    )
    missing_payloads = []
    for relative in required_payloads:
        payload = ADDON_ROOT / relative
        if not payload.is_file():
            missing_payloads.append(payload)
    if missing_payloads:
        raise FileNotFoundError(
            "missing required full-engine payload(s): " + ", ".join(str(path) for path in missing_payloads)
        )
    for relative in required_payloads:
        validate_source_file(ADDON_ROOT / relative, ADDON_ROOT)

    if central_brain is not None:
        central_brain = validate_central_brain_path(central_brain)

    resolver_plan = _preflight_resolver_cohort(target, target_real)

    memory_skips = {"build_graph.py"}
    starter_osvec = target / "memory" / "osvec_adapter.py"
    validate_destination(starter_osvec, target, target_real, destination_kind="file")
    if starter_planned or starter_osvec.is_file():
        memory_skips.add("osvec_adapter.py")
    tree_copies = [
        (ADDON_ROOT / "memory", target / "memory", frozenset(memory_skips)),
        (
            ADDON_ROOT / "brain",
            target / "brain",
            frozenset({"brain.py", "central_brain.py"}),
        ),
        (ADDON_ROOT / "blackboard-addons", target / "blackboard", frozenset()),
    ]
    runtime_tree_copies = []
    if claude:
        runtime_tree_copies.extend((
            (ADDON_ROOT / "staged" / "agents", target / ".claude" / "agents", frozenset()),
            (ADDON_ROOT / "staged" / "commands", target / ".claude" / "commands", frozenset()),
        ))
    if codex:
        runtime_tree_copies.append(
            (ADDON_ROOT / "staged" / "codex-skills", target / ".agents" / "skills", frozenset())
        )
    tree_copies.extend(runtime_tree_copies)
    central_module = ADDON_ROOT / "brain" / "central_brain.py"
    if central_brain is not None:
        validate_source_file(central_module, ADDON_ROOT)

    planned_copies = []
    for src_dir, dst_dir, skipped in tree_copies:
        planned_copies.extend(
            (src, dst_dir / src.relative_to(src_dir))
            for src in distributable_files(src_dir, skip_relpaths=skipped,
                                           expected_root=ADDON_ROOT)
        )
    planned_file_destinations = [destination for _, destination in planned_copies]
    planned_file_destinations.append(target / "brain" / "shared-brain.jsonl")
    if central_brain is not None and project_id:
        planned_file_destinations.append(target / "brain" / "CENTRAL_BRAIN.md")
    planned_directory_destinations = (target, target / "memory" / "store", target / "brain")
    for destination in planned_file_destinations:
        validate_destination(destination, target, target_real, destination_kind="file")
    for destination in planned_directory_destinations:
        validate_destination(destination, target, target_real, destination_kind="directory")
    if force:
        for src, destination in planned_copies:
            if destination.exists() and destination.read_bytes() != src.read_bytes():
                validate_destination(
                    destination.with_name(destination.name + ".pre-force"),
                    target,
                    target_real,
                    destination_kind="file",
                )

    if not (dry_run and starter_planned):
        starter_markers = (target / "AGENTS.md", target / "blackboard" / "00-project-goal.md")
        if not all(marker.is_file() for marker in starter_markers):
            raise FileNotFoundError(
                "full-engine install needs a starter Project OS workspace; "
                "run install.sh <target> --full-engine or bootstrap the target first"
            )

    migration_plan = _preflight_brain_migration(
        target,
        target_real,
        resolver_plan,
        brain_migration,
        legacy_central_brain=legacy_central_brain,
    )

    results: list[str] = []
    if dry_run and not starter_planned and not target.exists():
        results.append(f"would create {target}")
    if not dry_run:
        validate_destination(target, target, target_real, destination_kind="directory")
        target.mkdir(parents=True, exist_ok=True)

    results.extend(_copy_resolver_cohort(resolver_plan, target, dry_run))

    for src_dir, dst_dir, skipped in tree_copies[:3]:
        results.extend(copy_tree(src_dir, dst_dir, force, dry_run=dry_run,
                                 skip_relpaths=skipped, target_root=target,
                                 source_root=ADDON_ROOT))

    memory_store = target / "memory" / "store"
    brain_dir = target / "brain"
    validate_destination(memory_store, target, target_real, destination_kind="directory")
    validate_destination(brain_dir, target, target_real, destination_kind="directory")
    if dry_run:
        results.append(f"would ensure {memory_store}")
        results.append(f"would ensure {brain_dir}")
    else:
        validate_destination(memory_store, target, target_real, destination_kind="directory")
        validate_destination(brain_dir, target, target_real, destination_kind="directory")
        memory_store.mkdir(parents=True, exist_ok=True)
        brain_dir.mkdir(parents=True, exist_ok=True)
    shared_brain = target / "brain" / "shared-brain.jsonl"
    validate_destination(shared_brain, target, target_real, destination_kind="file")
    skip_local_brain = (
        migration_plan is not None
        and migration_plan["mode"] in {"migrate", "bind"}
    )
    if not skip_local_brain and not shared_brain.exists():
        if dry_run:
            results.append(f"would write {shared_brain}")
        else:
            validate_destination(shared_brain, target, target_real, destination_kind="file")
            _create_private(shared_brain)
            results.append(f"wrote {shared_brain}")
    elif not skip_local_brain:
        results.append(f"kept existing {shared_brain}")

    if migration_plan is not None:
        results.extend(_apply_brain_migration(migration_plan, target, dry_run))

    for src_dir, dst_dir, skipped in runtime_tree_copies:
        results.extend(copy_tree(src_dir, dst_dir, force, dry_run=dry_run,
                                 skip_relpaths=skipped, target_root=target,
                                 source_root=ADDON_ROOT))
    if not claude:
        results.append("skipped .claude agents/commands; pass --claude to install them")
    if not codex:
        results.append("skipped .agents skills; pass --codex to install them")

    if central_brain is not None:
        central_path = validate_central_brain_path(central_brain).resolve()
        if dry_run:
            if project_id:
                results.append(f"would write {target / 'brain' / 'CENTRAL_BRAIN.md'}")
            results.append(f"would initialize central brain at {central_path / 'shared-brain.jsonl'}")
            return results
        central_path = validate_central_brain_path(central_path).resolve()
        central = load_central_brain_module()
        brain_file = central.init_central(central_path)
        if project_id:
            marker = target / "brain" / "CENTRAL_BRAIN.md"
            validate_destination(marker, target, target_real, destination_kind="file")
            quoted_central_path = shlex.quote(str(central_path))
            quoted_project_id = shlex.quote(project_id)
            validate_destination(marker, target, target_real, destination_kind="file")
            marker.write_text(
                "# Central Brain Connection\n\n"
                f"Central brain path: `{central_path}`\n"
                f"Project ID: `{project_id}`\n\n"
                "Push lessons (chat-derived records sync only as approved summaries;\n"
                "raw or private-tagged records never leave the project):\n\n"
                "```bash\n"
                f"python3 brain/central_brain.py push --path {quoted_central_path} --project . --project-id {quoted_project_id}\n"
                "```\n\n"
                "Pull lessons (same privacy gate applies on the way in):\n\n"
                "```bash\n"
                f"python3 brain/central_brain.py pull --path {quoted_central_path} --project . --project-id {quoted_project_id}\n"
                "```\n",
                encoding="utf-8",
            )
            results.append(f"wrote {marker}")
        results.append(f"initialized central brain at {brain_file}")

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the optional Project OS full engine add-on.")
    parser.add_argument(
        "--target",
        type=nonempty_path,
        default=Path("."),
        help="Existing starter Project OS folder to update. Default: current folder.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite add-on files that already exist.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be copied without writing files.")
    parser.add_argument("--starter-planned", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--preflight-central-brain", type=nonempty_path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--claude", action="store_true", help="Also install Claude Code agents and slash commands.")
    parser.add_argument("--codex", action="store_true", help="Also install Codex workflow skills.")
    parser.add_argument(
        "--brain-migration",
        choices=("migrate", "bind", "fresh-local"),
        default=None,
        help="Resolve verified legacy shared-brain data during this upgrade.",
    )
    parser.add_argument(
        "--legacy-central-brain",
        default=None,
        help=(
            "Legacy central-brain directory to inspect during migration. "
            f"Overrides {LEGACY_CENTRAL_BRAIN_ENV}; default: ~/.project-os/central-brain."
        ),
    )
    parser.add_argument(
        "--central-brain",
        default=None,
        help="Optional central brain folder to initialize/connect after installing the full engine.",
    )
    parser.add_argument("--project-id", default=None, help="Stable id to use when pushing this project to central brain.")
    args = parser.parse_args()

    try:
        if args.preflight_central_brain is not None:
            validate_central_brain_path(args.preflight_central_brain)
            return 0
        results = install_full_engine(
            args.target,
            force=args.force,
            claude=args.claude,
            codex=args.codex,
            central_brain=Path(args.central_brain) if args.central_brain else None,
            project_id=args.project_id,
            dry_run=args.dry_run,
            starter_planned=args.starter_planned,
            brain_migration=args.brain_migration,
            legacy_central_brain=(
                Path(args.legacy_central_brain)
                if args.legacy_central_brain
                else None
            ),
        )
    except (FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        # Destination validation aborts before any write when a symlink is in
        # the planned path.  Make that refusal observable and preserve the
        # sibling installer's exit contract: an in-target link is skipped,
        # while a link resolving outside the target fails the install.
        detail = str(exc)
        if detail.startswith("unsafe destination symlink"):
            print(f"REFUSED: {detail}", file=sys.stderr)
            link_text = detail.rsplit(": ", 1)[-1]
            try:
                target_root = absolute_path(args.target).resolve()
                link_real = Path(link_text).resolve()
                escapes = Path(os.path.commonpath((str(target_root), str(link_real)))) != target_root
            except (OSError, ValueError):
                escapes = True
            return 1 if escapes else 0
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        print("Project OS full engine add-on dry run complete.")
    else:
        print("Project OS full engine add-on install complete.")
    for result in results:
        print(f"- {result}")
    print()
    print("Next:")
    print("1. Run: python3 memory/new_run.py demo --tier solo")
    print("2. Run: python3 memory/score_rubric.py --selftest")
    print("3. Optional central brain: python3 brain/central_brain.py sync --path <central-brain> --project . --project-id <project-id>")
    print("4. If you passed --claude or --codex, restart that AI tool or reload the project.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
