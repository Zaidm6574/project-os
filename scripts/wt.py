#!/usr/bin/env python3
"""wt — git worktree isolation for parallel Project OS workers.

Parallel builder steps that mutate the same repo get one worktree each instead of
fighting over one checkout (bb_lock protects blackboard FILES; worktrees protect
CODE). Also the antidote to the stale-checkout trap: sessions have edited a
worktree while someone read the main checkout and called it truth — `wt.py list`
shows every checkout and who's dirty. Check it before trusting any tree.

Worktrees live in ~/.project-os/worktrees/<repo>/<name> on branch wt/<name>.

Usage (run anywhere inside the target repo, or pass --repo):
  python3 scripts/wt.py create <name>     # new worktree at HEAD (warns if tree dirty)
  python3 scripts/wt.py list              # all worktrees + dirty state
  python3 scripts/wt.py merge <name>      # merge wt/<name> into the CURRENT branch
  python3 scripts/wt.py remove <name> [--force]   # refuses dirty/unmerged without --force
"""
import os, sys, subprocess

HOME = os.path.expanduser("~")


def git(args, cwd=None, check=True):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def repo_root(override=None):
    if override:
        return git(["rev-parse", "--show-toplevel"], cwd=override)
    return git(["rev-parse", "--show-toplevel"])


def main_root(root):
    """The MAIN checkout (first worktree entry). All commands anchor here, so
    running wt.py from inside a worktree can't merge a branch into itself or
    compute paths off the worktree's own name (both real bugs, caught 2026-07-02)."""
    out = git(["worktree", "list", "--porcelain"], cwd=root)
    for line in out.splitlines():
        if line.startswith("worktree "):
            return line.split(" ", 1)[1]
    return root


def wt_dir(root, name):
    # defect (2026-07-25): `name` came straight from argv into os.path.join with no
    # containment check, so `../../../../tmp/evil-marker-dir/sub` escaped
    # ~/.project-os/worktrees/<repo>/ and os.makedirs created it before git ever
    # validated the branch ref. Reject traversal/separators before building the path.
    base = os.path.join(HOME, ".project-os", "worktrees", os.path.basename(root))
    path = os.path.normpath(os.path.join(base, name))
    if os.path.commonpath([os.path.normpath(base), path]) != os.path.normpath(base):
        sys.exit(f"REFUSED: worktree name escapes worktrees root: {name!r}")
    return path


def dirty(tree):
    return bool(git(["status", "--porcelain"], cwd=tree))


def cmd_create(root, name):
    path = wt_dir(root, name)
    if os.path.exists(path):
        sys.exit(f"worktree already exists: {path}")
    if dirty(root):
        print("WARNING: main checkout has uncommitted changes — they will NOT be in "
              "the worktree (worktrees branch from HEAD, not the dirty tree).", file=sys.stderr)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    git(["worktree", "add", "-b", f"wt/{name}", path], cwd=root)
    print(path)
    print(f"branch wt/{name} @ HEAD — work there, commit there, then: wt.py merge {name}")
    return 0


def cmd_list(root):
    out = git(["worktree", "list", "--porcelain"], cwd=root)
    trees = []
    cur = {}
    for line in out.splitlines() + [""]:
        if not line:
            if cur:
                trees.append(cur)
            cur = {}
        elif " " in line:
            k, v = line.split(" ", 1)
            cur[k] = v
        else:
            cur[line] = True
    for t in trees:
        p = t.get("worktree", "?")
        branch = t.get("branch", "(detached)").replace("refs/heads/", "")
        d = "DIRTY" if os.path.isdir(p) and dirty(p) else "clean"
        main = " <- main checkout" if os.path.realpath(p) == os.path.realpath(root) else ""
        print(f"[{d:>5}] {branch:<28} {p}{main}")
    return 0


def cmd_merge(root, name):
    if dirty(root):
        sys.exit("REFUSED: current checkout is dirty — commit or stash before merging a worktree branch.")
    wt_path = wt_dir(root, name)
    if os.path.isdir(wt_path) and dirty(wt_path):
        sys.exit(f"REFUSED: worktree {name} has uncommitted changes — commit them there first.")
    print(git(["merge", "--no-edit", f"wt/{name}"], cwd=root) or f"merged wt/{name}")
    print(f"merged. clean up with: wt.py remove {name}")
    return 0


def cmd_remove(root, name, force):
    path = wt_dir(root, name)
    branch = f"wt/{name}"
    if not os.path.isdir(path):
        sys.exit(f"no such worktree: {path}")
    if dirty(path) and not force:
        sys.exit(f"REFUSED: {path} has uncommitted changes. --force discards them.")
    # unmerged commits check: anything on the branch not reachable from main HEAD?
    r = subprocess.run(["git", "log", "--oneline", f"HEAD..{branch}"],
                       cwd=root, capture_output=True, text=True)
    unmerged = r.stdout.strip()
    if unmerged and not force:
        sys.exit(f"REFUSED: {branch} has unmerged commits:\n{unmerged}\n"
                 f"merge first (wt.py merge {name}) or --force to discard.")
    git(["worktree", "remove", path] + (["--force"] if force else []), cwd=root)
    rb = subprocess.run(["git", "branch", "-D" if force else "-d", branch],
                        cwd=root, capture_output=True, text=True)
    # report honestly — never print "removed" when something survived
    if os.path.exists(path):
        sys.exit(f"FAILED: {path} still exists after worktree remove")
    if rb.returncode != 0:
        print(f"worktree removed, but branch {branch} was NOT deleted: "
              f"{(rb.stderr or rb.stdout).strip()}", file=sys.stderr)
        return 1
    print(f"removed {path} and branch {branch}")
    return 0


def main():
    a = sys.argv[1:]
    repo = None
    if "--repo" in a:
        i = a.index("--repo")
        repo = a[i + 1]
        del a[i:i + 2]
    force = "--force" in a
    if force:
        a.remove("--force")
    if not a:
        print(__doc__)
        return 2
    root = main_root(repo_root(repo))  # always anchor to the MAIN checkout
    cmd = a[0]
    if cmd == "list":
        return cmd_list(root)
    if len(a) < 2:
        sys.exit(f"{cmd} needs <name>")
    if cmd == "create":
        return cmd_create(root, a[1])
    if cmd == "merge":
        return cmd_merge(root, a[1])
    if cmd == "remove":
        return cmd_remove(root, a[1], force)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
