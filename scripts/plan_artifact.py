#!/usr/bin/env python3
"""plan_artifact — wave plans as inspectable, replayable data (not prose).

Implements the planOnly / createPlanArtifact / runFromPlan primitives (verified
2-1 from open-multi-agent, July 2026 radar run): a plan is a JSON artifact in
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
import os, sys, json, re, datetime

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
    known_flags = {"--goal", "--steps-file", "--id", "--step", "--force"}

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
        plan = {"schema": "plan/v1", "id": pid, "goal": goal, "created": today,
                "status": "planned",
                "steps": steps}
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
