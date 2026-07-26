#!/usr/bin/env python3
"""plan_artifact — wave plans as inspectable, replayable data (not prose).

Implements the planOnly / createPlanArtifact / runFromPlan primitives (behavior
pinned by tests/test_loop_tooling.py and tests/test_audit_fixes.py): a plan is a JSON artifact in
blackboard/plans/ that a human can inspect and approve BEFORE any agent runs, and
that can be compiled into per-step worker packets deterministically afterwards.

Lifecycle:  planned -> approved -> running -> done      (human gate at approve)

Usage:
  python3 scripts/plan_artifact.py create --goal "..." --steps-file steps.json [--id ID]
  python3 scripts/plan_artifact.py validate <id>
  python3 scripts/plan_artifact.py approve  <id>          # the human gate
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
"""
import os, sys, json, re, datetime, hashlib

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


def plan_path(pid):
    p = pid if pid.endswith(".json") else os.path.join(PLANS, pid + ".json")
    return p


def load(pid):
    path = plan_path(pid)
    try:
        with open(path, encoding="utf-8") as f:
            plan = json.load(f)
    except OSError as e:
        raise PlanInputError(f"cannot read plan file: {e}") from None
    except json.JSONDecodeError as e:
        raise PlanInputError(f"invalid JSON in plan file: {e.msg} "
                             f"(line {e.lineno}, column {e.colno})") from None
    if not isinstance(plan, dict):
        raise PlanInputError("plan file must contain a JSON object")
    return plan


def save(plan, pid=None):
    # Write back to the SAME location the plan was loaded from (pid may be a
    # full path); fall back to the id-derived path under PLANS.
    dest = plan_path(pid) if pid else plan_path(plan["id"])
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)


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
                "This disables the plan/v2 checks the human approved."
                % (approved_schema, plan.get("schema"))
            )
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


def main():
    args = sys.argv[1:]

    def usage_error(message):
        print(f"usage error: {message}", file=sys.stderr)
        sys.exit(2)

    # only these exact tokens may be rejected as a flag's "missing value" —
    # arbitrary values starting with '--' (e.g. a goal of '--- draft ---')
    # are legitimate (audit finding F3, 2026-07-17)
    known_flags = {"--goal", "--steps-file", "--id", "--step", "--force",
                   "--instructions-file"}

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
        plan = {"schema": "plan/v1", "id": pid, "goal": goal, "created": today,
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
        save(plan)
        print(f"created {plan_path(pid)} (status: planned — needs `approve` before compile)")
        sys.exit(0)

    if cmd == "list":
        if os.path.isdir(PLANS):
            for f in sorted(os.listdir(PLANS)):
                if f.endswith(".json"):
                    p = load(os.path.join(PLANS, f))
                    done = sum(1 for s in p["steps"] if s.get("done"))
                    print(f"{p['id']}  [{p['status']}]  {done}/{len(p['steps'])} steps  — {p['goal'][:60]}")
        sys.exit(0)

    if not args and cmd not in ("list",):
        print("missing <id>", file=sys.stderr)
        sys.exit(2)
    pid = args.pop(0) if args else None

    if cmd == "validate":
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
        for i, s in enumerate(ordered_steps, 1):
            fp = os.path.join(packets_dir, f"{plan['id']}-{s['id']}.md")
            # build the full packet body BEFORE touching disk, then write
            # atomically (temp + rename) so no failure path can leave a
            # partial or zero-byte packet behind (audit finding F2, 2026-07-17)
            body = f"""Packet ID: {plan['id']}-{s['id']}
Agent: {s['role']}{f" (model hint: {s['model_hint']})" if s.get('model_hint') else ""}
Task: {s['task']}
Evidence: (fill during run)
Conclusion: (fill during run)
Confidence: (fill during run)
Risks: (fill during run)
Recommended Next Step: {("after: " + ", ".join(s['depends_on'])) if s['depends_on'] else "no dependencies — can start immediately"}
Status: Draft

Plan: {plan['id']} · step {i}/{len(plan['steps'])} · expected outputs: {", ".join(s['outputs']) or "(unspecified)"}
{f"Isolation: WORKTREE — before touching code run: python3 scripts/wt.py create {plan['id']}-{s['id']}  (work + commit there; merge via wt.py merge)" + chr(10) if s.get('isolation') == 'worktree' else ""}On completion run: python3 scripts/plan_artifact.py complete {plan['id']} --step {s['id']}
"""
            tmp = fp + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(body)
                os.replace(tmp, fp)
            except OSError as e:
                try:
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
