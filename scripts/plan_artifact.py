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
              "depends_on": [], "outputs": ["path/or/artifact"]}, ...]
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


def plan_path(pid):
    p = pid if pid.endswith(".json") else os.path.join(PLANS, pid + ".json")
    return p


def load(pid):
    with open(plan_path(pid), encoding="utf-8") as f:
        return json.load(f)


def save(plan):
    os.makedirs(PLANS, exist_ok=True)
    with open(plan_path(plan["id"]), "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)


def locked_update(pid, mutate):
    """load -> mutate(plan) -> save under bb_lock, so two agents completing
    different steps concurrently can't clobber each other's writes
    (audit finding, 2026-07-02). Returns the mutated plan."""
    p = plan_path(pid)
    if not bb_lock.acquire(p, agent="plan", wait=10):
        print("FAILED: could not lock plan file", file=sys.stderr)
        sys.exit(1)
    try:
        plan = load(pid)
        mutate(plan)
        save(plan)
        return plan
    finally:
        bb_lock.release(p, agent="plan", force=True)


def validate(plan):
    """Returns a list of problems (empty = valid)."""
    probs = []
    for k in ("schema", "id", "goal", "status", "steps"):
        if k not in plan:
            probs.append(f"missing field: {k}")
    if probs:
        return probs
    if plan["status"] not in STATUSES:
        probs.append(f"bad status: {plan['status']}")
    ids = [s.get("id") for s in plan["steps"]]
    if len(ids) != len(set(ids)):
        probs.append("duplicate step ids")
    for s in plan["steps"]:
        for k in ("id", "role", "task"):
            if not s.get(k):
                probs.append(f"step {s.get('id','?')}: missing {k}")
        for dep in s.get("depends_on", []):
            if dep not in ids:
                probs.append(f"step {s['id']}: unknown dependency {dep}")
    # maker/checker enforcement: multi-step plans need >=1 checker step that
    # depends on the work it checks (protocol was policy-only before 2026-07-02)
    if len(plan["steps"]) > 1:
        checkers = [s for s in plan["steps"] if CHECKER_ROLES.search(s.get("role") or "")]
        if not checkers:
            probs.append("no checker step: multi-step plans need >=1 step whose role is a "
                         "checker/verifier/reviewer/evaluator (maker-checker split)")
        elif not any(s.get("depends_on") for s in checkers):
            probs.append("checker step(s) have empty depends_on — a checker must depend on "
                         "the step(s) it checks")
    # cycle check (Kahn)
    deps = {s["id"]: set(s.get("depends_on", [])) for s in plan["steps"]}
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
        for k in ready:
            del deps[k]
            order.append(by_id[k])
        for v in deps.values():
            v.difference_update(ready)
    return order


def main():
    args = sys.argv[1:]

    def flag(name, default=None):
        if name in args:
            i = args.index(name)
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
        plan = {"schema": "plan/v1", "id": pid, "goal": goal, "created": today,
                "status": "planned",
                "steps": json.load(open(steps_file, encoding="utf-8"))}
        for s in plan["steps"]:
            s.setdefault("depends_on", [])
            s.setdefault("outputs", [])
            s.setdefault("done", False)
        probs = validate(plan)
        if probs:
            print("INVALID:\n- " + "\n- ".join(probs), file=sys.stderr)
            sys.exit(1)
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
        if plan["status"] == "planned" and "--force" not in args:
            print("plan is not approved (planOnly). Run `approve` first, or --force.",
                  file=sys.stderr)
            sys.exit(1)
        os.makedirs(PACKETS, exist_ok=True)
        made = []
        for i, s in enumerate(topo_order(plan), 1):
            fp = os.path.join(PACKETS, f"{plan['id']}-{s['id']}.md")
            with open(fp, "w", encoding="utf-8") as f:
                f.write(f"""Packet ID: {plan['id']}-{s['id']}
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
""")
            made.append(fp)
        locked_update(plan["id"], lambda p: p.__setitem__("status", "running"))
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
    main()
