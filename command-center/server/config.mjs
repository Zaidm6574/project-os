// Read-only adapter configuration + allowlist.
// Loads a minimal .env (no dependency) and defines the ONLY roots the server may read.
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'

function loadEnv() {
  const envPath = path.resolve(process.cwd(), '.env')
  if (!fs.existsSync(envPath)) return
  for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const t = line.trim()
    if (!t || t.startsWith('#')) continue
    const i = t.indexOf('=')
    if (i === -1) continue
    const k = t.slice(0, i).trim()
    const v = t.slice(i + 1).trim()
    if (!(k in process.env)) process.env[k] = v
  }
}
loadEnv()

const HOME = os.homedir()

export const PORT = Number(process.env.PORT) || 4317

const PROJECT_OS_DIR_DEFAULT = path.join(HOME, 'project-os')
export const PROJECT_OS_DIR =
  process.env.PROJECT_OS_DIR || PROJECT_OS_DIR_DEFAULT

// Optional second-brain data dir (items.json / links.json). Set in .env; when
// unset, the graph/items views show honest empty states.
export const SECOND_BRAIN_DATA =
  process.env.SECOND_BRAIN_DATA || path.join(PROJECT_OS_DIR_DEFAULT, 'second-brain-data')

export const SHARED_BRAIN =
  path.join(HOME, '.project-os', 'central-brain', 'shared-brain.jsonl')

// bb_lock lockfiles (read-only lock visibility for the Loops view).
export const LOCKS_DIR =
  process.env.BB_LOCK_DIR || path.join(HOME, '.project-os', 'locks')

// Antigravity run notes root (each subdir = a run; we read ONLY safe *.md notes,
// never .system_generated/ or messages/ — enforced by DENY_SUBSTR).
export const ANTIGRAVITY_BRAIN =
  path.join(HOME, '.gemini', 'antigravity', 'brain')

// Engine roots — presence detection only; we never read session/chat files.
export const ENGINES = [
  { id: 'claude', label: 'Claude Code', root: path.join(HOME, '.claude') },
  { id: 'codex', label: 'Codex', root: path.join(HOME, '.codex') },
  { id: 'antigravity', label: 'Antigravity', root: path.join(HOME, '.gemini', 'antigravity') },
  { id: 'project-os', label: 'Project OS Brain', root: path.join(HOME, '.project-os', 'central-brain') },
]

// Tracked external project roots (multi-project, multi-engine).
// Each is scanned for blackboard/, docs/superpowers/, .superpowers/, package.json, or README.
// Configure with PROJECT_ROOTS=path1:path2 in .env (gitignored) — there are NO
// hardcoded defaults, so the public copy never carries anyone's project list.
// Reads are SAFE-markdown/JSON only (chats/DBs are denied).
export const PROJECT_ROOTS = (
  process.env.PROJECT_ROOTS ? process.env.PROJECT_ROOTS.split(':') : []
).filter(Boolean)

// Allowlisted READ roots. Any read must resolve under one of these.
export const READ_ROOTS = [
  path.join(PROJECT_OS_DIR, 'runs'),
  path.join(PROJECT_OS_DIR, 'blackboard'),
  path.join(PROJECT_OS_DIR, 'memory'),
  path.join(PROJECT_OS_DIR, 'graphify-out'),
  SECOND_BRAIN_DATA,
  SHARED_BRAIN,
  ANTIGRAVITY_BRAIN,
  LOCKS_DIR,
  ...PROJECT_ROOTS,
]

// Hard-DENY substrings — refused even if nested under an allowlisted root.
// Protects private chats, raw DBs, embeddings, staged raw dumps, logs.
export const DENY_SUBSTR = [
  '.system_generated',
  'embeddings_cache.json',
  path.sep + 'staged' + path.sep,
  path.sep + 'logs' + path.sep,
  '.db',
  '.sqlite',
  '.vscdb',
  path.sep + 'messages' + path.sep,
]

// Fields stripped from any item before it leaves the server (never serve raw vectors / bulky graph payloads).
export const STRIP_ITEM_FIELDS = ['embed_data', 'embedding', 'connections', 'vector']
