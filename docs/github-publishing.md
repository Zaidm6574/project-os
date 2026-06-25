# Publishing To GitHub

Use this after reviewing the template for private information.

Before publishing, make sure Git, Python 3, and ripgrep (`rg`) are installed; you can sign in to GitHub; and you know the GitHub username or organization that will own the repo.

## 1. Create The Repo

On GitHub, create a new empty repository.

Recommended names:

- `project-os-template`
- `ai-project-os`
- `agent-project-os`
- `ai-workflow-starter`

## 2. Push From Your Computer

From this folder:

```bash
git init
git status --short --ignored
rg -n --hidden --no-ignore -S "/Users|sk-|private key|\\.env" .
git add .
git commit -m "Initial Project OS template"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/project-os-template.git
git push -u origin main
```

Replace `YOUR-USERNAME` with your GitHub username or organization name. If `git remote add origin` says the remote already exists, run `git remote -v` and confirm it points to the intended empty GitHub repo before pushing.

Expected benign `rg` hits include `.gitignore` entries, documentation that mentions privacy checks, tests with fake keys, and redaction regexes in `scripts/import_chat_history.py`. Stop before `git add .` if the scan shows real local paths, real keys, raw exports, or personal notes.

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

Then they can say:

```text
/project I want to build...
```

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
- `scripts/check_optional_tools.py`, so the install path can recommend graph/vector/tool add-ons without claiming they are already active.
- `docs/friend-review.md`, so reviewers know what feedback is useful.
- A final `git status --short --ignored` check.
