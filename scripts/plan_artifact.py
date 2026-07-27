#!/usr/bin/env python3
"""plan_artifact — wave plans as inspectable, replayable data (not prose).

Implements the planOnly / createPlanArtifact / runFromPlan primitives (behavior
pinned by tests/test_loop_tooling.py and tests/test_audit_fixes.py): a plan is a JSON artifact in
blackboard/plans/ that a human can inspect and approve BEFORE any agent runs, and
that can be compiled into per-step worker packets deterministically afterwards.

Lifecycle:  planned -> approved -> running -> done      (human gate at approve)

Usage:
  python3 scripts/plan_artifact.py create --goal "..." --steps-file steps.json [--id ID]
  python3 scripts/plan_artifact.py validate <id> [--migrate]
  python3 scripts/plan_artifact.py approve  <id>          # the human gate
  python3 scripts/plan_artifact.py migrate  <id>          # re-pin a bumped SCHEMA only
  python3 scripts/plan_artifact.py compile  <id>          # runFromPlan: emit worker packets
  python3 scripts/plan_artifact.py complete <id> --step <step-id>
  python3 scripts/plan_artifact.py show <id> | list

steps.json: [{"id": "s1", "role": "builder", "task": "...", "model_hint": "haiku",
              "depends_on": [], "outputs": ["path/or/artifact"]},
             {"id": "s2", "role": "checker", "task": "verify s1's output",
              "depends_on": ["s1"],
              "verification": {"method": "how the check is performed",
                               "expected": "what a pass looks like"}}]

Multi-step plans REQUIRE: every work step covered by some checker's depends_on,
and >=1 work-dependent checker carrying verification with nonempty,
non-placeholder method/expected ("-", "n/a", "todo", "tbd", ... are rejected).
NOTE: validation re-runs at approve and compile (even with --force), so plans
approved before 2026-07-17 must be re-validated — they may no longer compile.

`migrate` is the ONLY supported way to move an approved plan's schema pin
forward, and it is deliberately not a way to re-approve anything: it refuses
unless the approved CONTENT (goal, steps, instruction provenance) still hashes
to the digest recorded at approve time, and it appends an audit record to
plan["migrations"]. Anything else — edited steps, a backwards schema, a plan
approved before content digests were recorded — needs the human gate again.
"""
import os, sys, json, re, datetime, hashlib, copy, tempfile, stat

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import bb_lock

PLANS = os.path.join(ROOT, "blackboard", "plans")
PACKETS = os.path.join(ROOT, "blackboard", "packets")
STATUSES = ("planned", "approved", "running", "done")
# roles that count as the "checker" half of the maker/checker split
CHECKER_ROLES = re.compile(r"check|verif|review|critic|evaluat|test|audit|red.?team", re.I)


class PlanInputError(ValueError):
    pass


# The STORED plan id must be a slug, because compile() interpolates it into
# every worker-packet filename: with an absolute id like /tmp/x/p9.json,
# os.path.join(PACKETS, id + "-s1.md") discards PACKETS entirely and the
# packets landed beside the plan file instead of under packets/
# (audit 2026-07-25, reproduced).
#
# Writing the plan FILE to an explicit path stays supported — the CLI documents
# `<id>` as accepting a path and callers rely on it — but the id recorded
# INSIDE the artifact is always the slugified basename.
#
# \A...\Z, not ^...$: '$' also matches just BEFORE a trailing newline, so
# "p1\n" satisfied ^[A-Za-z0-9][A-Za-z0-9._-]*$ and carried a newline straight
# into a filename. Same anchors as the run-slug validator in memory/new_run.py
# (audit 2026-07-26).
SAFE_PLAN_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")
# The id shares ONE filename with each step id ("<plan-id>-<step-id>.md"), and
# a filename is capped at 255 bytes on every filesystem this runs on. Refuse an
# over-long id up front, with a message, instead of discovering it as a
# half-written packet run and an ENAMETOOLONG on step 7.
MAX_PLAN_ID_LEN = 120


def _short(value, limit=80):
    """repr() that cannot flood stderr with a megabyte-long hostile id."""
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "...(truncated)"


def require_path_safe_plan_id(pid):
    """Refuse a STORED plan id that is anything but a single file name.

    compile() interpolates plan["id"] into every worker-packet filename, so an
    id of "../../../../tmp/x" — or an absolute one, or one carrying a newline
    or a NUL — makes os.path.join walk out of blackboard/packets/ and write
    wherever it points. `create` slugifies the id it records, but a plan file
    is plain JSON that gets hand-edited and pasted in from another agent or a
    teammate, so a create-time check is bypassable: the id has to be validated
    where it is CONSUMED as a path (audit 2026-07-26). Same rule, shape and
    voice as _reject_unsafe_slug() in memory/new_run.py.
    """
    if (not isinstance(pid, str) or not SAFE_PLAN_ID.match(pid)
            or pid != os.path.basename(pid) or ".." in pid):
        raise PlanInputError(
            "refusing plan id %s: a plan id is a single file name under "
            "blackboard/plans/, matching [A-Za-z0-9][A-Za-z0-9._-]* — not a "
            "path. It becomes part of every worker-packet filename."
            % _short(pid))
    if len(pid) > MAX_PLAN_ID_LEN:
        raise PlanInputError(
            "refusing plan id %s: %d characters is over the %d-character "
            "limit, and the id shares one filename with each step id."
            % (_short(pid), len(pid), MAX_PLAN_ID_LEN))
    return pid


def safe_plan_id(pid):
    """Slug to record as the plan's id, derived from an id or a .json path."""
    slug = os.path.basename((pid or "").strip())
    if slug.endswith(".json"):
        slug = slug[: -len(".json")]
    return require_path_safe_plan_id(slug)


def packet_path(packets_dir, name):
    """Path for one worker packet, refusing anything that escapes packets_dir.

    Second layer behind require_path_safe_plan_id(): step ids land in the same
    filename and validate() only requires them to be nonempty strings, and
    packets_dir can itself be reached through a symlink. Comparing the RESOLVED
    path against the RESOLVED directory is what makes "packets stay under
    packets/" verified rather than assumed (same realpath/commonpath idiom as
    brain.py and wt.py).
    """
    fp = os.path.join(packets_dir, name)
    try:
        base = os.path.realpath(packets_dir)
        real = os.path.realpath(fp)
        contained = (os.path.commonpath([real, base]) == base
                     and os.path.dirname(real) == base)
    except (OSError, ValueError) as e:
        # realpath() raises ValueError on an embedded NUL and commonpath()
        # raises on paths it cannot compare — both are refusals, not crashes.
        raise PlanInputError(
            "refusing packet name %s: %s" % (_short(name), e)) from None
    if not contained:
        raise PlanInputError(
            "refusing packet name %s: it resolves to %s, outside the packets "
            "directory %s" % (_short(name), _short(real), _short(base)))
    return fp


def plan_path(pid):
    """Resolve an id OR an explicit .json path to a plan file.

    Reading an existing plan by full path stays supported (the CLI documents
    `<id>` as accepting a path, and callers pass one). Building a NEW plan id
    goes through safe_plan_id() instead — see `create`.
    """
    p = pid if pid.endswith(".json") else os.path.join(PLANS, pid + ".json")
    return p


def load(pid):
    path = plan_path(pid)
    try:
        with open(path, encoding="utf-8") as f:
            plan = json.load(f)
    except OSError as e:
        raise PlanInputError(f"cannot read plan file: {e}") from None
    except UnicodeDecodeError as e:
        # a stray binary file in plans/ is a malformed plan, not a crash
        raise PlanInputError(f"plan file is not valid UTF-8 text: {e}") from None
    except json.JSONDecodeError as e:
        raise PlanInputError(f"invalid JSON in plan file: {e.msg} "
                             f"(line {e.lineno}, column {e.colno})") from None
    if not isinstance(plan, dict):
        raise PlanInputError("plan file must contain a JSON object")
    return plan


def save(plan, pid=None):
    # Write back to the SAME location the plan was loaded from (pid may be a
    # full path); fall back to the id-derived path under PLANS.
    dest = os.path.abspath(plan_path(pid) if pid else plan_path(plan["id"]))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    # Temp file + os.replace(), never a truncating write (audit 2026-07-27):
    # open(dest, "w") EMPTIES the live plan before one new byte is flushed and
    # json.dump writes incrementally, so a SIGKILL, a full disk or a
    # serialization error on a hand-edited plan left the only copy of the run's
    # coordination state half-written -- `complete` and `approve` on that plan
    # then died with "invalid JSON in plan file". The DISPOSABLE worker packets
    # this module compiles were already written this way (see `compile`); the
    # irreplaceable artifact was not.
    #
    # dest is NOT realpath()ed. os.replace() does not follow a symlink -- it
    # replaces the link itself -- so writing to the literal path leaves
    # whatever a planted link points at alone, instead of destroying it. Same
    # rule and same reason as scripts/brain_archive.py (adversary 2026-07-26).
    #
    # mkstemp() creates its file 0600 regardless of umask and os.replace()
    # carries that mode onto the destination, so the mode has to be reproduced
    # explicitly or every save silently narrows a world-readable plan to
    # owner-only -- the exact silent narrowing that already burned
    # addons/full-engine/memory/cost_actuals.py (audit 2026-07-26).
    try:
        dest_stat = os.stat(dest)
    except OSError:
        dest_stat = None
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest),
                               prefix=os.path.basename(dest) + ".",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if dest_stat is not None:
            os.chmod(tmp, stat.S_IMODE(dest_stat.st_mode))
        else:
            # No destination to copy from: fall back to what the old
            # open(dest, "w") produced -- an ordinary create honoring the
            # process umask -- rather than leaving mkstemp's 0600.
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o666 & ~umask)
        os.replace(tmp, dest)
    except BaseException:
        # BaseException, not OSError: a TypeError out of json.dump (a plan
        # holding a value json cannot encode) must not leave the temp file
        # behind either.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def locked_update(pid, mutate):
    """load -> mutate(plan) -> save under bb_lock, so two agents completing
    different steps concurrently can't clobber each other's writes
    (audit finding, 2026-07-02). Returns the mutated plan."""
    p = plan_path(pid)
    token = bb_lock.acquire(p, agent="plan", wait=10)
    if not token:
        print("FAILED: could not lock plan file", file=sys.stderr)
        sys.exit(1)
    try:
        plan = load(pid)
        mutate(plan)
        # Re-verify ownership before writing: if the lease expired mid-mutate
        # and another agent took over, saving now would overwrite their newer
        # plan. renew() refuses when our token no longer owns the lock
        # (independent review finding, 2026-07-17).
        if not bb_lock.renew(p, token):
            print("FAILED: plan lock lease lost during update; aborting "
                  "without saving", file=sys.stderr)
            sys.exit(1)
        save(plan, pid)
        return plan
    finally:
        # token-fenced release: if our lease was reaped and re-acquired by
        # another agent, this refuses instead of deleting their lock
        # (audit finding, 2026-07-17)
        bb_lock.release(p, agent="plan", token=token)


# Placeholder tokens that satisfy "nonempty" but name no actual check. The
# gate is a forcing function for declaring verification, so a bare dash or
# "tbd" must not pass it (external review finding, 2026-07-17).
VERIFICATION_PLACEHOLDERS = {"-", "--", "x", "n/a", "na", "none", "todo", "tbd", "?",
                             "...", "pending"}
# a fixed set invites near-miss bypasses ("---", "??", "xx", "tba", "n.a.");
# also reject any punctuation-only run, x-run, or n/a-tbd variant
PLACEHOLDER_PATTERN = re.compile(r"^(?:[\W_]+|x+|n\W?a\W?|t\.?b\.?[ad]\.?)$", re.I)


def _real_verification_value(value):
    if not (isinstance(value, str) and value.strip()):
        return False
    v = value.strip().casefold()
    return v not in VERIFICATION_PLACEHOLDERS and not PLACEHOLDER_PATTERN.match(v)


# ---------------------------------------------------------------------------
# Hardening (private, 2026-07-22): instruction provenance, goal anchoring,
# checker branch contracts, and injection screening of untrusted content.
# plan/v1 keeps legacy behavior; plan/v2 makes all four mandatory. Declared
# `branches` are checked on EVERY schema so a v1 plan can't smuggle an
# execution-continuing failure branch.
# ---------------------------------------------------------------------------
HARDENED_SCHEMA = "plan/v2"
# Oldest -> newest. `migrate` may only move a plan's approved_schema pin
# FORWARD along this list; anything unlisted, or any backwards move, is a
# downgrade attempt and is refused.
SCHEMA_ORDER = ("plan/v1", "plan/v2")
TRUST_LEVELS = ("authoritative", "trusted", "untrusted")
# on_fail must stop the loop: halt outright, escalate to the human gate, or
# roll back. A step id or "continue" here means a FAILED check keeps
# executing, which defeats the checker entirely.
FAIL_CLOSED_BRANCHES = ("halt", "escalate-human", "rollback")
# Prompt-injection tells in retrieved/project text. Screened on
# NON-authoritative instruction content only — the user's own words are
# authoritative by definition and are never screened.
INJECTION_PATTERNS = (
    ("override-prior-instructions",
     re.compile(r"(ignore|disregard|forget)\s+(all\s+|any\s+)?(the\s+)?"
                r"(previous|prior|earlier|above)?\s*"
                r"(instructions|rules|prompts|directives)", re.I)),
    ("persona-switch", re.compile(r"you\s+are\s+now\b", re.I)),
    ("system-prompt-reference", re.compile(r"system\s+prompt", re.I)),
    ("instruction-replacement", re.compile(r"new\s+instructions?\s*:", re.I)),
    ("fabricated-approval",
     re.compile(r"pretend\s+(that\s+)?the\s+(human|user)\s+(has\s+)?"
                r"(already\s+)?approved", re.I)),
    ("approval-bypass",
     re.compile(r"(without|skip(ping)?|bypass(ing)?)\s+(human\s+)?"
                r"(approval|review|the\s+gate)", re.I)),
)


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_goal_anchor(plan):
    """Digest anchor binding the goal + every AUTHORITATIVE instruction to
    this plan id. Recomputed at validate/approve/compile; any later mutation
    of the goal or the user's authoritative instructions breaks the match.
    (Hash-chain integrity, not keyed signing — tamper-evident within the
    artifact, matching the repo's provenance conventions.)"""
    authoritative = sorted(
        (rec.get("ref", ""), rec.get("digest", ""))
        for rec in plan.get("instructions", [])
        if isinstance(rec, dict) and rec.get("trust") == "authoritative")
    payload = json.dumps({
        "v": "goal-anchor/2",
        # `schema` MUST be inside the digest. goal-anchor/1 omitted it, so
        # rewriting "plan/v2" -> "plan/v1" left the anchor matching while
        # turning OFF every plan/v2 hardening check (instruction provenance,
        # the authoritative-instruction requirement, and anchor verification
        # itself). The downgrade was free (audit 2026-07-25).
        "schema": plan.get("schema", ""),
        "id": plan.get("id", ""),
        "created": plan.get("created", ""),
        "goal": plan.get("goal", ""),
        "authoritative": authoritative,
    }, sort_keys=True, separators=(",", ":"))
    return _sha256(payload)


# --- approval content digest ------------------------------------------------
# What the human actually approved is the plan MINUS its bookkeeping: the
# schema string, the lifecycle status, the approval record itself, the audit
# trail, and the goal anchor (which is derived, and covers the schema). Every
# other key — including keys added by future versions — is content, because
# the digest is built by EXCLUSION: a field nobody thought of still lands
# inside the hash instead of silently escaping it.
APPROVAL_DIGEST_VERSION = "approval-content/1"
NON_CONTENT_FIELDS = frozenset({
    "schema", "status", "approved_at", "approved_schema", "approved_digest",
    "migrations", "goal_anchor",
})
# per-step execution state, not approved content
NON_CONTENT_STEP_FIELDS = frozenset({"done"})


def compute_content_digest(plan):
    """Digest of the substantive plan content (goal, steps, instructions, ...).

    Recorded at `approve` so a later `migrate` can PROVE that only the schema
    version moved. `create` normalizes depends_on/outputs, so both are
    normalized here too — an explicit `"outputs": []` and an absent one mean
    the same plan and must hash the same.
    """
    if not isinstance(plan, dict):
        return _sha256("")
    body = {k: v for k, v in plan.items() if k not in NON_CONTENT_FIELDS}
    steps = []
    for s in plan.get("steps", []) if isinstance(plan.get("steps"), list) else []:
        if isinstance(s, dict):
            step = {k: v for k, v in s.items() if k not in NON_CONTENT_STEP_FIELDS}
            step.setdefault("depends_on", [])
            step.setdefault("outputs", [])
            steps.append(step)
        else:
            steps.append(s)
    if "steps" in body:
        body["steps"] = steps
    payload = json.dumps({"v": APPROVAL_DIGEST_VERSION, "plan": body},
                         sort_keys=True, separators=(",", ":"), default=str)
    return _sha256(payload)


def plan_migration(plan):
    """Decide whether a schema-version migration may be applied.

    Returns (problems, record). A nonempty `problems` means REFUSE — the plan
    needs the human gate (`approve`) again, not a migration. `record` is the
    audit entry to append to plan["migrations"].

    The guarantee this must never break: `migrate` re-pins approved_schema, so
    it must be impossible to use it as an approval-laundering tool. It may only
    move the SCHEMA VERSION FORWARD on a plan whose approved content still
    matches the digest taken at approve time; content edits, sideways/backwards
    schema moves, and plans with no digest to compare against are all refused.
    """
    probs = []
    if not isinstance(plan, dict):
        return ["plan must be a JSON object"], None
    pinned = plan.get("approved_schema")
    current = plan.get("schema")
    if not plan.get("approved_at"):
        probs.append("plan is not approved, so there is no approval to carry "
                     "forward. Run `approve` (the human gate) instead.")
    if not isinstance(pinned, str) or not pinned.strip():
        probs.append("no approved_schema pin to migrate — the approval record "
                     "was never written or was edited. Re-run `approve`.")
    migrations = plan.get("migrations")
    if migrations is not None and not isinstance(migrations, list):
        probs.append("migrations must be a JSON array (the audit trail was "
                     "overwritten with something else)")
    if probs:
        return probs, None
    if pinned not in SCHEMA_ORDER:
        probs.append("unknown approved schema %r — cannot order it against %s"
                     % (pinned, "|".join(SCHEMA_ORDER)))
    if not isinstance(current, str) or current not in SCHEMA_ORDER:
        probs.append("unknown target schema %r — cannot order it against %s"
                     % (current, "|".join(SCHEMA_ORDER)))
    if probs:
        return probs, None
    if SCHEMA_ORDER.index(current) <= SCHEMA_ORDER.index(pinned):
        probs.append(
            "migration is forward-only: approved as %r, plan now declares %r "
            "which is not newer. Restore schema=%r, or re-approve." %
            (pinned, current, pinned))
        return probs, None
    approved_digest = plan.get("approved_digest")
    if not isinstance(approved_digest, str) or not approved_digest.strip():
        probs.append(
            "plan carries no approved_digest, so there is no record of what "
            "was approved and migration cannot prove the content is unchanged. "
            "Re-run `approve` (human gate) to establish one.")
        return probs, None
    if approved_digest != compute_content_digest(plan):
        probs.append(
            "plan content changed after approval (goal, steps or instruction "
            "provenance no longer match approved_digest). `migrate` only bumps "
            "the schema version — a changed plan needs re-approval by a human.")
        return probs, None
    anchor = plan.get("goal_anchor")
    if anchor is not None:
        as_approved = dict(plan)
        as_approved["schema"] = pinned
        if anchor != compute_goal_anchor(as_approved):
            probs.append(
                "goal_anchor does not match the plan as it was approved; "
                "migrating would silently repair it. Re-run `approve`.")
            return probs, None
    record = {
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "from_schema": pinned,
        "to_schema": current,
        "approved_at": plan.get("approved_at"),
        "approved_digest": approved_digest,
        "note": "schema version re-pinned; approved content unchanged "
                "(no re-approval)",
    }
    if anchor is not None:
        record["goal_anchor_before"] = anchor
    # Dry-run the migrated artifact: a plan that cannot pass validate() under
    # the newer schema must not get the pin (e.g. plan/v1 -> plan/v2 without
    # the provenance records v2 requires — adding those is a content change,
    # so it goes back through the human gate).
    candidate = copy.deepcopy(plan)
    apply_migration(candidate, record)
    candidate_probs = validate(candidate)
    if candidate_probs:
        probs.append("plan does not validate as %s: %s" %
                     (current, "; ".join(candidate_probs)))
        return probs, None
    return [], record


def apply_migration(plan, record):
    """Apply an approved-content-preserving schema re-pin, auditably."""
    plan["approved_schema"] = plan.get("schema")
    if plan.get("goal_anchor") is not None:
        # the anchor covers `schema`, so it MUST be recomputed; every other
        # anchor input is pinned by approved_digest, which was verified above
        plan["goal_anchor"] = compute_goal_anchor(plan)
    trail = plan.get("migrations")
    if not isinstance(trail, list):
        trail = []
    plan["migrations"] = trail + [record]


def _validate_instructions(plan, probs):
    """Validate provenance records; returns the set of known refs."""
    records = plan.get("instructions", [])
    if not isinstance(records, list):
        probs.append("instructions must be a JSON array of provenance records")
        return set()
    refs = set()
    for index, rec in enumerate(records, 1):
        if not isinstance(rec, dict):
            probs.append(f"instruction {index} must be a JSON object")
            continue
        ref = rec.get("ref")
        label = ref if isinstance(ref, str) and ref else f"#{index}"
        for field in ("ref", "source", "trust", "digest", "content"):
            if not isinstance(rec.get(field), str) or not rec[field].strip():
                probs.append(f"instruction {label}: missing {field}")
        if not isinstance(ref, str) or not ref.strip():
            continue
        if ref in refs:
            probs.append(f"instruction {label}: duplicate ref")
        refs.add(ref)
        trust = rec.get("trust")
        if isinstance(trust, str) and trust not in TRUST_LEVELS:
            probs.append(f"instruction {label}: unknown trust level {trust!r} "
                         f"(must be one of {', '.join(TRUST_LEVELS)})")
        content = rec.get("content")
        digest = rec.get("digest")
        if isinstance(content, str) and isinstance(digest, str) and digest.strip():
            if _sha256(content) != digest:
                probs.append(f"instruction {label}: digest mismatch — content "
                             "does not match its provenance digest (tampered?)")
        if trust == "authoritative" and rec.get("source") != "user":
            probs.append(f"instruction {label}: only user-sourced instructions "
                         f"may be authoritative (source is {rec.get('source')!r})")
        if trust in ("trusted", "untrusted") and isinstance(content, str):
            for name, pattern in INJECTION_PATTERNS:
                if pattern.search(content):
                    probs.append(f"instruction {label}: injection pattern "
                                 f"detected in {trust} content ({name})")
                    break
    return refs


def _validate_branches(step, sid, known_ids, probs, required):
    branches = step.get("branches")
    if branches is None:
        if required:
            probs.append(f"step {sid}: checker requires explicit branches "
                         "with on_pass and fail-closed on_fail (plan/v2)")
        return
    if not isinstance(branches, dict):
        probs.append(f"step {sid}: branches must be a JSON object")
        return
    on_fail = branches.get("on_fail")
    if on_fail not in FAIL_CLOSED_BRANCHES:
        probs.append(f"step {sid}: on_fail must be fail-closed "
                     f"({'|'.join(FAIL_CLOSED_BRANCHES)}), got {on_fail!r} — "
                     "a failure branch must not continue execution")
    on_pass = branches.get("on_pass")
    if not (on_pass == "continue" or on_pass in known_ids):
        probs.append(f"step {sid}: on_pass must be 'continue' or a known "
                     f"step id, got {on_pass!r}")


def validate(plan):
    """Returns a list of problems (empty = valid)."""
    if not isinstance(plan, dict):
        return ["plan must be a JSON object"]
    probs = []
    for k in ("schema", "id", "goal", "status", "steps"):
        if k not in plan:
            probs.append(f"missing field: {k}")
    if probs:
        return probs
    if plan["status"] not in STATUSES:
        probs.append(f"bad status: {plan['status']}")
    hardened = plan.get("schema") == HARDENED_SCHEMA
    # Downgrade guard, independent of the digest: a plan that carries hardened
    # fields but declares a weaker schema is a plan/v2 with its checks switched
    # off. Refuse it outright rather than validating it as a v1 (audit
    # 2026-07-25).
    if not hardened and ("goal_anchor" in plan or "instructions" in plan):
        probs.append(
            "schema downgrade: plan declares %r but carries plan/v2 fields "
            "(%s). Restore schema=%s or remove those fields."
            % (plan.get("schema"),
               ", ".join(sorted(k for k in ("goal_anchor", "instructions") if k in plan)),
               HARDENED_SCHEMA)
        )
        return probs

    # Post-approval downgrade guard. The field-presence check above cannot see a
    # plan that had goal_anchor AND instructions stripped together -- that is
    # byte-indistinguishable from a plan that was never hardened. But `approve`
    # pins the schema the human signed off on, so any later change to `schema`
    # IS visible. Once a plan has left `planned`, that pin must exist and match:
    # to downgrade you would have to reset status and go back through the human
    # gate (adversarial verify 2026-07-25).
    if plan.get("approved_at"):
        approved_schema = plan.get("approved_schema")
        if approved_schema is None:
            probs.append(
                "plan records approved_at but no approved_schema pin; the "
                "approval record was edited. Re-run `approve` to re-establish "
                "the human gate."
            )
        elif approved_schema != plan.get("schema"):
            probs.append(
                "schema changed after approval: approved as %r, now declares %r. "
                "This disables the plan/v2 checks the human approved. If the "
                "schema was bumped FORWARD and nothing else changed, run "
                "`plan_artifact.py migrate <id>` to re-pin it auditably; "
                "otherwise the plan needs re-approval."
                % (approved_schema, plan.get("schema"))
            )
        # Content pin, when the approval recorded one. Plans approved before
        # approved_digest existed carry none: those stay as evidence-poor as
        # they already were (no new failure mode), but they can never be
        # migrated — plan_migration() refuses without a digest to compare.
        approved_digest = plan.get("approved_digest")
        if isinstance(approved_digest, str) and approved_digest.strip():
            if approved_digest != compute_content_digest(plan):
                probs.append(
                    "plan content changed after approval: goal, steps or "
                    "instruction provenance no longer match approved_digest. "
                    "Re-run `approve` (the human gate) if the change is intended."
                )
    if "migrations" in plan and not isinstance(plan.get("migrations"), list):
        probs.append("migrations must be a JSON array of migration records")
    instruction_refs = set()
    if hardened or "instructions" in plan:
        instruction_refs = _validate_instructions(plan, probs)
    if hardened:
        records = plan.get("instructions")
        if not isinstance(records, list) or not records:
            probs.append("plan/v2 requires an instructions provenance list")
        elif not any(isinstance(r, dict) and r.get("trust") == "authoritative"
                     for r in records):
            probs.append("plan/v2 requires at least one authoritative "
                         "user-sourced instruction")
        anchor = plan.get("goal_anchor")
        if not isinstance(anchor, str) or not anchor.strip():
            probs.append("plan/v2 requires goal_anchor")
        elif anchor != compute_goal_anchor(plan):
            probs.append("goal_anchor mismatch — the goal or an authoritative "
                         "instruction was mutated after the plan was created/approved")
    steps = plan["steps"]
    if not isinstance(steps, list):
        probs.append("steps must be a JSON array")
        return probs

    ids = []
    valid_steps = []
    graph_ok = True
    for index, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            probs.append(f"step {index} must be a JSON object")
            graph_ok = False
            continue
        valid_steps.append(s)
        sid = s.get("id")
        if not isinstance(sid, str) or not sid.strip():
            probs.append(f"step {index}: id must be a nonempty string")
            graph_ok = False
        else:
            ids.append(sid)
        for k in ("role", "task"):
            if not isinstance(s.get(k), str) or not s[k].strip():
                probs.append(f"step {sid if isinstance(sid, str) and sid else '?'}: "
                             f"missing {k}")
        depends_on = s.get("depends_on", [])
        if not isinstance(depends_on, list):
            probs.append(f"step {sid if isinstance(sid, str) and sid else index}: "
                         "depends_on must be a JSON array")
            graph_ok = False
        elif any(not isinstance(dep, str) or not dep for dep in depends_on):
            probs.append(f"step {sid if isinstance(sid, str) and sid else index}: "
                         "dependencies must be nonempty strings")
            graph_ok = False
        outputs = s.get("outputs", [])
        if not isinstance(outputs, list) or any(not isinstance(v, str) for v in outputs):
            probs.append(f"step {sid if isinstance(sid, str) and sid else index}: "
                         "outputs must be a JSON array of strings")
        step_instructions = s.get("instructions")
        if step_instructions is not None:
            label = sid if isinstance(sid, str) and sid else index
            if not isinstance(step_instructions, list) or any(
                    not isinstance(r, str) for r in step_instructions):
                probs.append(f"step {label}: instructions must be a JSON array "
                             "of provenance refs")
            else:
                for ref in step_instructions:
                    if ref not in instruction_refs:
                        probs.append(f"step {label}: unknown instruction "
                                     f"reference {ref} (no provenance record)")

    if len(ids) != len(set(ids)):
        probs.append("duplicate step ids")
        graph_ok = False
    for s in valid_steps:
        if not isinstance(s.get("depends_on", []), list):
            continue
        for dep in s.get("depends_on", []):
            if not isinstance(dep, str):
                continue
            if dep not in ids:
                probs.append(f"step {s.get('id', '?')}: unknown dependency {dep}")
                graph_ok = False
    # Branch contract: any checker DECLARING branches must be fail-closed on
    # every schema; plan/v2 checkers must declare them (hardening 2026-07-22).
    all_ids = set(ids)
    for s in valid_steps:
        if isinstance(s.get("role"), str) and CHECKER_ROLES.search(s["role"]):
            _validate_branches(s, s.get("id", "?"), all_ids, probs,
                               required=hardened)
        elif s.get("branches") is not None:
            _validate_branches(s, s.get("id", "?"), all_ids, probs,
                               required=False)
    # maker/checker enforcement: multi-step plans need >=1 checker step that
    # depends on the work it checks (protocol was policy-only before 2026-07-02)
    if len(steps) > 1:
        checkers = [s for s in valid_steps
                    if isinstance(s.get("role"), str) and CHECKER_ROLES.search(s["role"])]
        work_ids = {s["id"] for s in valid_steps
                    if isinstance(s.get("id"), str)
                    and isinstance(s.get("role"), str)
                    and not CHECKER_ROLES.search(s["role"])}
        if not checkers:
            probs.append("no checker step: multi-step plans need >=1 step whose role is a "
                         "checker/verifier/reviewer/evaluator (maker-checker split)")
        else:
            dependent_checkers = [s for s in checkers
                                  if isinstance(s.get("depends_on", []), list)
                                  and any(dep in work_ids
                                          for dep in s.get("depends_on", []))]
            if not dependent_checkers:
                probs.append("checker step(s) must depend on at least one non-checker work step")
            elif not any(
                    isinstance(s.get("verification"), dict)
                    and _real_verification_value(s["verification"].get("method"))
                    and _real_verification_value(s["verification"].get("expected"))
                    for s in dependent_checkers):
                probs.append("checker verification must be a JSON object with nonempty, "
                             "non-placeholder method and expected strings")
            # F4 (2026-07-17): no work step may go unchecked — depending on
            # *some* work step is not maker-checker if other work ships
            # unreviewed
            checked = {dep for s in checkers
                       if isinstance(s.get("depends_on", []), list)
                       for dep in s.get("depends_on", []) if dep in work_ids}
            for wid in sorted(work_ids - checked):
                probs.append(f"work step {wid} is not covered by any checker's depends_on")
            # optional explicit binding: checks: [step ids] must name known
            # work steps the checker also depends on
            for s in checkers:
                checks = s.get("checks")
                if checks is None:
                    continue
                if not isinstance(checks, list):
                    probs.append(f"step {s.get('id', '?')}: checks must be a JSON array of step ids")
                    continue
                deps = set(s.get("depends_on", []) if isinstance(s.get("depends_on", []), list) else [])
                for c in checks:
                    if not isinstance(c, str) or c not in work_ids:
                        probs.append(f"step {s.get('id', '?')}: checks unknown or non-work step {c}")
                    elif c not in deps:
                        probs.append(f"step {s.get('id', '?')}: checks step {c} but does not depend_on it")
    # cycle check (Kahn)
    if not graph_ok:
        return probs
    deps = {s["id"]: set(s.get("depends_on", [])) for s in valid_steps}
    order = []
    while deps:
        ready = [k for k, v in deps.items() if not v]
        if not ready:
            probs.append(f"dependency cycle among: {sorted(deps)}")
            break
        for k in ready:
            del deps[k]
            order.append(k)
        for v in deps.values():
            v.difference_update(ready)
    return probs


def topo_order(plan):
    deps = {s["id"]: set(s.get("depends_on", [])) for s in plan["steps"]}
    by_id = {s["id"]: s for s in plan["steps"]}
    order = []
    while deps:
        ready = sorted(k for k, v in deps.items() if not v)
        if not ready:
            raise ValueError(f"dependency cycle among: {sorted(deps)}")
        for k in ready:
            del deps[k]
            order.append(by_id[k])
        for v in deps.values():
            v.difference_update(ready)
    return order


def list_row(plan):
    """One `list` line for a plan, or PlanInputError naming what is malformed.

    blackboard/plans/ is a directory of hand-editable JSON, so a half-written
    draft, an empty {} or a JSON list is a normal thing to find there — and
    indexing straight into it made `list` die on the first one.
    """
    missing = [k for k in ("id", "status", "goal", "steps") if k not in plan]
    if missing:
        raise PlanInputError("not a plan artifact (missing %s)"
                             % ", ".join(missing))
    steps = plan["steps"]
    if not isinstance(steps, list):
        raise PlanInputError("steps must be a JSON array")
    done = sum(1 for s in steps if isinstance(s, dict) and s.get("done"))
    return "%s  [%s]  %d/%d steps  — %s" % (
        plan["id"], plan["status"], done, len(steps), str(plan["goal"])[:60])


def run_migration(pid):
    """CLI half of `migrate`: refuse loudly, or re-pin under the plan lock."""
    plan = load(pid)
    if plan.get("approved_at") and plan.get("approved_schema") == plan.get("schema"):
        print("nothing to migrate: %s is already pinned to %r"
              % (plan.get("id", pid), plan.get("schema")))
        sys.exit(0)
    probs, _record = plan_migration(plan)
    if probs:
        print("REFUSED (migrate re-pins a schema version; it never re-approves "
              "a plan):\n- " + "\n- ".join(probs), file=sys.stderr)
        sys.exit(1)

    def _migrate(current_plan):
        # re-decide against the plan as locked, not the copy read a moment ago
        fresh_probs, fresh_record = plan_migration(current_plan)
        if fresh_probs:
            print("REFUSED (plan changed while locking):\n- "
                  + "\n- ".join(fresh_probs), file=sys.stderr)
            sys.exit(1)
        apply_migration(current_plan, fresh_record)

    migrated = locked_update(pid, _migrate)
    trail = migrated.get("migrations") or [{}]
    print("migrated %s: approved_schema %r -> %r (approved content unchanged; "
          "recorded in migrations[])"
          % (migrated.get("id", pid), trail[-1].get("from_schema"),
             trail[-1].get("to_schema")))
    sys.exit(0)


def main():
    args = sys.argv[1:]

    def usage_error(message):
        print(f"usage error: {message}", file=sys.stderr)
        sys.exit(2)

    # only these exact tokens may be rejected as a flag's "missing value" —
    # arbitrary values starting with '--' (e.g. a goal of '--- draft ---')
    # are legitimate (audit finding F3, 2026-07-17)
    known_flags = {"--goal", "--steps-file", "--id", "--step", "--force",
                   "--instructions-file", "--migrate"}

    def flag(name, default=None):
        if name in args:
            i = args.index(name)
            if i + 1 >= len(args) or args[i + 1] in known_flags:
                usage_error(f"missing value for {name}")
            v = args[i + 1]
            del args[i:i + 2]
            return v
        return default

    if not args:
        print(__doc__)
        sys.exit(2)
    cmd = args.pop(0)

    if cmd == "create":
        goal = flag("--goal")
        steps_file = flag("--steps-file")
        if not goal or not steps_file:
            print("create needs --goal and --steps-file", file=sys.stderr)
            sys.exit(2)
        today = datetime.date.today().isoformat()
        pid = flag("--id", f"plan-{today}-" + re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")[:32].strip("-"))
        # `pid` may be a bare name or an explicit .json path. The FILE goes
        # where the caller asked; the id stored inside the artifact is always
        # the slugified basename, so packet filenames stay under packets/.
        dest_path = plan_path(pid)
        try:
            plan_id = safe_plan_id(pid)
        except PlanInputError as e:
            usage_error(str(e))
        try:
            with open(steps_file, encoding="utf-8") as f:
                steps = json.load(f)
        except OSError as e:
            usage_error(f"cannot read --steps-file: {e}")
        except json.JSONDecodeError as e:
            usage_error(f"invalid JSON in --steps-file: {e.msg} "
                        f"(line {e.lineno}, column {e.colno})")
        if not isinstance(steps, list):
            usage_error("--steps-file must contain a JSON array of steps")
        instructions_file = flag("--instructions-file")
        plan = {"schema": "plan/v1", "id": plan_id, "goal": goal, "created": today,
                "status": "planned",
                "steps": steps}
        if instructions_file:
            # hardened path: provenance records supplied by the caller are
            # verified (digests, trust, injection screen) by validate() below,
            # and the goal is anchored so later mutation is rejected.
            try:
                with open(instructions_file, encoding="utf-8") as f:
                    plan["instructions"] = json.load(f)
            except OSError as e:
                usage_error(f"cannot read --instructions-file: {e}")
            except json.JSONDecodeError as e:
                usage_error(f"invalid JSON in --instructions-file: {e.msg} "
                            f"(line {e.lineno}, column {e.colno})")
            plan["schema"] = HARDENED_SCHEMA
            plan["goal_anchor"] = compute_goal_anchor(plan)
        probs = validate(plan)
        if probs:
            print("INVALID:\n- " + "\n- ".join(probs), file=sys.stderr)
            sys.exit(1)
        for s in plan["steps"]:
            s.setdefault("depends_on", [])
            s.setdefault("outputs", [])
            s.setdefault("done", False)

        # Never clobber an existing plan. `create` with an id that already
        # existed silently overwrote it and reset status to "planned",
        # DISCARDING an approval record — which defeats the human gate that
        # approve/approved_schema exist to enforce (audit 2026-07-25).
        dest = dest_path
        if os.path.exists(dest):
            try:
                prior = load(dest)
                prior_status = prior.get("status", "?")
            except PlanInputError:
                prior_status = "unreadable"
            print(
                f"REFUSED: {dest} already exists (status: {prior_status}).\n"
                f"Creating over it would discard that plan and any approval it "
                f"carries. Pick a different --id, or delete the file first.",
                file=sys.stderr,
            )
            sys.exit(1)
        save(plan, dest)
        print(f"created {dest} (status: planned — needs `approve` before compile)")
        sys.exit(0)

    if cmd == "list":
        if os.path.isdir(PLANS):
            for f in sorted(os.listdir(PLANS)):
                if not f.endswith(".json"):
                    continue
                # One unreadable/partial file in plans/ used to abort the whole
                # listing with a raw KeyError/TypeError, hiding every healthy
                # plan behind it. Report it and keep going (audit 2026-07-26).
                # The print must sit INSIDE the guard: a plan id holding a lone
                # surrogate builds a row fine and then raises UnicodeEncodeError
                # at print() time, which put the traceback and the hidden-plans
                # failure straight back (adversary 2026-07-26).
                try:
                    print(list_row(load(os.path.join(PLANS, f))))
                except PlanInputError as e:
                    print(f"{f}: skipped — {e}", file=sys.stderr)
                    continue
                except (UnicodeError, ValueError) as e:
                    print(f"{f}: skipped — unprintable plan ({e})",
                          file=sys.stderr)
                    continue
        sys.exit(0)

    if not args and cmd not in ("list",):
        print("missing <id>", file=sys.stderr)
        sys.exit(2)
    pid = args.pop(0) if args else None

    if cmd in ("validate", "migrate"):
        if cmd == "migrate" or "--migrate" in args:
            run_migration(pid)  # never returns
        probs = validate(load(pid))
        print("VALID" if not probs else "INVALID:\n- " + "\n- ".join(probs))
        sys.exit(0 if not probs else 1)

    if cmd == "show":
        print(json.dumps(load(pid), indent=2))
        sys.exit(0)

    if cmd == "approve":
        def _approve(plan):
            probs = validate(plan)
            if probs:
                print("cannot approve, INVALID:\n- " + "\n- ".join(probs), file=sys.stderr)
                sys.exit(1)
            plan["status"] = "approved"
            plan["approved_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            # Pin the schema the HUMAN actually approved. Stripping goal_anchor
            # and instructions together while setting schema back to plan/v1
            # makes a hardened plan indistinguishable from a native v1 -- the
            # artifact alone cannot tell. Recording what was approved makes the
            # downgrade detectable at compile time (adversarial verify
            # 2026-07-25).
            plan["approved_schema"] = plan.get("schema")
            # Pin WHAT was approved, not just under which schema. Without this
            # there is nothing a later `migrate` could check the plan against,
            # and re-pinning the schema would be indistinguishable from
            # laundering an edited plan through the human gate.
            plan["approved_digest"] = compute_content_digest(plan)
        plan = locked_update(pid, _approve)
        print(f"{plan['id']} approved — compile when ready")
        sys.exit(0)

    if cmd == "compile":
        plan = load(pid)
        # Always validate at the execution boundary — approval can predate
        # edits, and --force must not skip the checker contract
        # (independent review finding, 2026-07-17).
        probs = validate(plan)
        if probs:
            print("cannot compile, INVALID:\n- " + "\n- ".join(probs),
                  file=sys.stderr)
            sys.exit(1)
        # The plan's own id is about to become a path component. Check it HERE,
        # at consumption, before any side effect (the --force backup, the
        # packets mkdir, the first packet write): the file on disk may have been
        # hand-edited since `create` slugified it.
        # Step ids get the SAME check, not a weaker one: they share the packet
        # filename with the plan id, and they are interpolated into the packet
        # BODY -- the document a worker agent is told to execute. A step id
        # carrying a newline needs no path separator to do damage; it forged
        # extra "On completion run:" command lines into that document while the
        # compile exited 0 (adversary 2026-07-26).
        try:
            require_path_safe_plan_id(plan.get("id"))
            for _s in plan.get("steps") or []:
                require_path_safe_plan_id(
                    _s.get("id") if isinstance(_s, dict) else _s)
        except PlanInputError as e:
            print("cannot compile: %s" % e, file=sys.stderr)
            sys.exit(1)
        if plan["status"] != "approved" and "--force" not in args:
            if plan["status"] == "planned":
                print("plan is not approved (planOnly). Run `approve` first, or --force.",
                      file=sys.stderr)
            else:
                print(f"plan status is '{plan['status']}' — recompiling would reset it to "
                      "'running' and regenerate packets. Use --force if you mean it.",
                      file=sys.stderr)
            sys.exit(1)
        # --force recompile of a non-approved plan: back up prior state first.
        if plan["status"] != "approved" and "--force" in args:
            import shutil as _sh
            _bak = plan_path(pid) + ".pre-force"
            try:
                _sh.copy2(plan_path(pid), _bak)
                print(f"backed up prior plan state to {os.path.basename(_bak)}")
            except OSError:
                pass
        # Write packets NEXT TO the plan (…/blackboard/packets), so compiling a
        # plan given by full path lands beside it instead of the repo default.
        packets_dir = (os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(pid))), "packets")
                       if pid.endswith(".json") else PACKETS)
        os.makedirs(packets_dir, exist_ok=True)
        made = []
        # validate() treats depends_on/outputs as optional; normalize here so
        # the packet writer below never KeyErrors on a hand-written plan that
        # validate accepted (audit finding F2, 2026-07-17)
        for s in plan["steps"]:
            s.setdefault("depends_on", [])
            s.setdefault("outputs", [])
            s.setdefault("done", False)
        try:
            ordered_steps = topo_order(plan)
        except ValueError as e:
            print(f"cannot compile: {e}", file=sys.stderr)
            sys.exit(1)
        # Resolve EVERY packet path before writing the first one, so a step id
        # that escapes packets/ (step ids are only checked for being nonempty
        # strings) refuses the whole compile instead of stopping half-written.
        try:
            packet_paths = [packet_path(packets_dir, f"{plan['id']}-{s['id']}.md")
                            for s in ordered_steps]
        except PlanInputError as e:
            print("cannot compile: %s" % e, file=sys.stderr)
            sys.exit(1)
        for i, s in enumerate(ordered_steps, 1):
            fp = packet_paths[i - 1]
            # build the full packet body BEFORE touching disk, then write
            # atomically (temp + rename) so no failure path can leave a
            # partial or zero-byte packet behind (audit finding F2, 2026-07-17)
            # 2026-07-27 (cold-clone reviewer): validate() REFUSES a multi-step
            # plan unless a work-dependent checker declares verification with a
            # nonempty, non-placeholder method AND expected (see :732-736) --
            # and then compile interpolated role/task/depends_on/outputs and
            # dropped the very strings the gate had just forced the author to
            # write. A checker received "Task: check the form submits" with no
            # criteria at all. Collecting a requirement and discarding it at the
            # one point it could do work is a fail-open gate: it reads as
            # enforced because the plan is refused without it, while the
            # document a worker actually executes never carries it.
            #
            # Reuse _real_verification_value rather than testing truthiness, so
            # "-", "n/a", "todo", "tbd" stay ONE definition of "declared".
            # A looser rule here would print placeholder text into the packet
            # while the gate upstream considers the same value absent.
            _v = s.get("verification")
            _v = _v if isinstance(_v, dict) else {}
            _vm = _v.get("method") if _real_verification_value(_v.get("method")) else None
            _ve = _v.get("expected") if _real_verification_value(_v.get("expected")) else None
            # Emitted only when something real was declared: an unconditional
            # block would put "Verification method: (none declared)" on every
            # builder packet in every plan, training readers to skip the line
            # that matters on the packets where it is load-bearing.
            verification_block = ""
            if _vm or _ve:
                verification_block = ("Verification method: %s\nExpected: %s\n"
                                      % (_vm or "(none declared)",
                                         _ve or "(none declared)"))
            body = f"""Packet ID: {plan['id']}-{s['id']}
Agent: {s['role']}{f" (model hint: {s['model_hint']})" if s.get('model_hint') else ""}
Task: {s['task']}
{verification_block}Evidence: (fill during run)
Conclusion: (fill during run)
Confidence: (fill during run)
Risks: (fill during run)
Recommended Next Step: {("after: " + ", ".join(s['depends_on'])) if s['depends_on'] else "no dependencies — can start immediately"}
Status: Draft

Plan: {plan['id']} · step {i}/{len(plan['steps'])} · expected outputs: {", ".join(s['outputs']) or "(unspecified)"}
{f"Isolation: WORKTREE — before touching code run: python3 scripts/wt.py create {plan['id']}-{s['id']}  (work + commit there; merge via wt.py merge)" + chr(10) if s.get('isolation') == 'worktree' else ""}On completion run: python3 scripts/plan_artifact.py complete {plan['id']} --step {s['id']}
"""
            # The packet path itself is validated above, but the write went
            # through the SIBLING `fp + ".tmp"` with a symlink-following
            # open(), so a link planted at that predictable name redirected
            # the write outside the tree while stdout still printed the
            # contained packets/ path (adversary 2026-07-26). A hard link
            # defeats realpath/commonpath entirely -- containment checks
            # cannot see one -- so the tmp file is created with an
            # unpredictable name and O_CREAT|O_EXCL|O_NOFOLLOW semantics,
            # which refuse BOTH a symlink and any pre-existing path.
            # Same defect and same remedy as scripts/brain_archive.py.
            tmp = None
            try:
                fd, tmp = tempfile.mkstemp(
                    dir=os.path.dirname(fp),
                    prefix=os.path.basename(fp) + ".",
                    suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(body)
                os.replace(tmp, fp)
            except OSError as e:
                try:
                    if tmp:
                        os.unlink(tmp)
                except OSError:
                    pass
                print(f"cannot compile: failed writing packet "
                      f"{os.path.basename(fp)}: {e}", file=sys.stderr)
                sys.exit(1)
            made.append(fp)
        locked_update(pid, lambda p: p.__setitem__("status", "running"))
        print(f"compiled {len(made)} worker packets (topological order):")
        for m in made:
            print(" ", m)
        sys.exit(0)

    if cmd == "complete":
        step = flag("--step")
        def _complete(plan):
            hit = [s for s in plan["steps"] if s["id"] == step]
            if not hit:
                print(f"no step {step} in {plan['id']}", file=sys.stderr)
                sys.exit(1)
            hit[0]["done"] = True
            if all(s.get("done") for s in plan["steps"]):
                plan["status"] = "done"
        plan = locked_update(pid, _complete)
        print(f"{plan['id']}: {step} done — plan status {plan['status']}")
        sys.exit(0)

    print(f"unknown command: {cmd}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except PlanInputError as e:
        print(f"usage error: {e}", file=sys.stderr)
        sys.exit(2)
