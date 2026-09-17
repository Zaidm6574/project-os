# Publishing To GitHub

Use this after reviewing the template for private information.

Before publishing, make sure Git and Python 3.10+ are installed; you can sign in to GitHub; and you know the GitHub username or organization that will own the repo.

## 1. Create The Repo

On GitHub, create a new empty repository.

Recommended names:

- `project-os`
- `ai-project-os`
- `agent-project-os`
- `ai-workflow-starter`

## 2. Push From Your Computer

From this folder, first inspect the intended public files. Stage the complete, explicitly reviewed file list for your release; the three-file list below is only a syntax example. Run each step separately and stop on a failed review or scanner error. An assistant must obtain the user's explicit publishing approval before the final push:

```bash
git init
git status --short --ignored
python3 scripts/prepublish_check.py .
# Review and stage only the intended public files, then check their staged bytes.
git add README.md AGENTS.md CLAUDE.md
python3 scripts/prepublish_check.py --tracked
git commit -m "Initial Project OS template"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/project-os.git
git push -u origin main
```

Replace `YOUR-USERNAME` with your GitHub username or organization name. If `git remote add origin` says the remote already exists, inspect its configured destination locally and confirm it points to the intended empty GitHub repo before pushing; do not print a credential-bearing URL into shared logs.

The scanner prints match locations, not credential values. `--tracked` scans stage-0 index blobs, including unchanged tracked files; add `--working-tree` only for a separate audit of local edits. It reports selected/scanned counts and refuses empty or ambiguous selection. Review synthetic fixture matches locally and block real private information. Never paste a matching line into a chat or log. The scanner does not review Git history, author metadata or remote configuration; inspect those privately as a separate release check, including any credential-bearing remote URL. Recheck staged bytes after any edits or re-staging.

Also read `docs/friend-review.md` before pushing if other people will use the template. It lists the checks a beginner or skeptical reviewer should run.

## 3. Make It A Template

In GitHub:

1. Open the repo.
2. Go to **Settings**.
3. Check **Template repository**.

Now your friend can click **Use this template**.

## 4. Friend Setup

If the GitHub repo is marked as a template, the easiest path is:

1. Click **Use this template** in GitHub.
2. Create a new repo.
3. Clone that new repo.
4. Run setup inside the cloned repo.

```bash
git clone https://github.com/THEIR-USERNAME/their-project.git
cd their-project
./install.sh . --check-tools
git status --short --ignored
```

Then open the project in the AI tool and say “Use Project OS to help me build…” or ask it to follow `prompts/workflows/project.md`. A plain install does not register `/project`. For Claude commands use `--claude-engine`; for Codex skills use `--codex-engine`; either flag activates the full engine. See `docs/install-from-github.md`.

If they are cloning your template repo directly instead of using **Use this template**, send them `docs/install-from-github.md`.

## 5. Optional Chat Memory

If they have exported old chats:

```bash
python3 scripts/import_chat_history.py --input /path/to/export --output private-memory/chat-memory.md
```

The default output is a private local review report, not verified memory. They should review the original exports locally and copy only short approved summaries in their own words into `blackboard/01-user-memory.md` or `blackboard/08-memory-index.md`.

## 6. Public Repo Polish

Before sharing broadly, add or review:

- `LICENSE`, so friends know how they may reuse the template.
- `SECURITY.md`, so people know how to report privacy or security issues.
- `install.sh`, so a friend can run one local installer after cloning.
- `scripts/check_optional_tools.py`, so the install path can recommend GraphOS, OSVec, and full-engine add-ons without claiming they are already active.
- `scripts/install_full_engine.py` and `addons/full-engine/`, so the opt-in engine path is reviewable before publishing.
- `docs/friend-review.md`, so reviewers know what feedback is useful.
- A final `git status --short --ignored` check.
