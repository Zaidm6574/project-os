# Project OS — Command Center

A local-first, privacy-first, **multi-engine** supervised agent-loop cockpit. It turns the second-brain into a control surface for running loops (Claude / Codex / Antigravity / Project OS) as **structured blackboard + graph**, not mushy chat.

## Run it

```bash
cd ~/project-os/command-center
npm install
npm run dev
# open http://localhost:5174   (adapter API on http://127.0.0.1:4317)
```

`npm run dev` starts two processes (via `concurrently`):
- **server** — `node server/index.mjs`, a **read-only** Express adapter on `127.0.0.1:4317`
- **web** — Vite dev server on `5174`, proxying `/api` → 4317

Build a static bundle: `npm run build` (→ `dist/`). Run the API alone: `npm start` (it serves the built UI + API same-origin on :4317).

## Desktop app (macOS)

```bash
npm install
npm run app:build   # builds the UI + packages → release/mac*/Project OS Command Center.app
```

Double-click the `.app` — it has a dock icon and its own window, launches the read-only adapter internally (via Electron's bundled Node), and needs no terminal or `localhost` URL. For a quick dev window without packaging: `npm run electron`. A signed/notarized `.dmg` is `npm run app:dist` (needs an Apple cert).

## Themes

8 built-in themes — **Midnight · Nord · Dracula · Catppuccin Mocha · Solarized Dark · Gruvbox · Synthwave · Paper** — switch from the header swatch or Settings; the choice persists (localStorage). All UI reads CSS variables, so themes apply instantly everywhere including the graph.

## What you get (slice 1)

- **Sidebar:** Projects · Runs · Brain · Research · Inbox · Graph · Settings
- **Runs** (default landing): a **loop header** for the active run — *goal · next action · verifier · stop condition · cost mode · approval gate* — plus a dashboard of all supervised runs, a **Blackboard** tab (goal/decisions/risks/open-questions/plan cards) and a **Receipt** tab.
- **Projects:** every tracked project grouped by engine (Project OS, Codex, Claude, Antigravity).
- **Graph:** a server-decimated D3 force graph of the second-brain (`links.json`).
- **Brain:** honest capability + memory health (no faked "green") + shared-brain lessons.
- **Command bar:** `/kickoff /research /save /brain /prune /lint /trust /reality` — live read-only commands execute; mutating / agent-wave commands stop at a **human-approval gate** (Confirm writes a gated intent; no runner fires, no tokens spent in slice 1).

## Privacy (enforced in `server/safe.mjs`)

- **Reads only** an allowlist: `~/project-os` runs/blackboard/memory; second-brain `links.json` + `analyzed/items.json` (with `embed_data`/`connections` **stripped**) + gephi CSVs; `shared-brain.jsonl`; Antigravity `*.md` notes; tracked-project plans/specs/README.
- **Never** reads `.system_generated/messages`, `embeddings_cache.json`, `staged/`, `*.db`/`*.sqlite`, or logs. Path-traversal is blocked (`path.resolve` before every allowlist check).
- Binds `127.0.0.1` only. The only writes are `/save` notes and gated intent records, **restricted to `runs/`**. Read-only sources are never written.

## Config

`.env` (see `.env.example`): `PORT`, `PROJECT_OS_DIR`, `SECOND_BRAIN_DATA`, and `PROJECT_ROOTS` (`:`-separated extra project dirs to track).
