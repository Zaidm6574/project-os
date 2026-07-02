// Path-allowlist guard + field stripper. Every filesystem read goes through canRead().
import fs from 'node:fs'
import path from 'node:path'
import { READ_ROOTS, DENY_SUBSTR, STRIP_ITEM_FIELDS } from './config.mjs'

function denied(resolved) {
  const lower = resolved.toLowerCase() // case-insensitive (macOS FS is case-insensitive)
  return DENY_SUBSTR.some((d) => lower.includes(d.toLowerCase()))
}

// Dereference symlinks so a symlink cannot smuggle a denied/outside target past the checks.
function realResolve(p) {
  const resolved = path.resolve(p)
  try {
    return fs.existsSync(resolved) ? fs.realpathSync(resolved) : resolved
  } catch {
    return resolved
  }
}

function underRoot(resolved, root) {
  const r = realResolve(root)
  return resolved === r || resolved.startsWith(r + path.sep)
}

export function canRead(p) {
  const resolved = realResolve(p)
  if (denied(resolved)) return false
  return READ_ROOTS.some((root) => underRoot(resolved, root))
}

// Presence-only existence check (boolean, no content read) — safe to use outside the read allowlist,
// e.g. for engine-directory detection.
export function pathExists(p) {
  try {
    return fs.existsSync(p)
  } catch {
    return false
  }
}

// Safe read helpers — return null instead of throwing, and refuse non-allowlisted paths.
export function readText(p) {
  try {
    if (!canRead(p) || !fs.existsSync(p)) return null
    const st = fs.statSync(p)
    if (!st.isFile() || st.size > 25 * 1024 * 1024) return null
    return fs.readFileSync(p, 'utf8')
  } catch {
    return null
  }
}

export function readJson(p, maxBytes = 60 * 1024 * 1024) {
  try {
    if (!canRead(p) || !fs.existsSync(p)) return null
    const st = fs.statSync(p)
    if (!st.isFile() || st.size > maxBytes) return null
    return JSON.parse(fs.readFileSync(p, 'utf8'))
  } catch {
    return null
  }
}

export function listDir(p) {
  try {
    if (!canRead(p) || !fs.existsSync(p)) return []
    return fs.readdirSync(p, { withFileTypes: true })
  } catch {
    return []
  }
}

export function exists(p) {
  try {
    return canRead(p) && fs.existsSync(p)
  } catch {
    return false
  }
}

// Strip raw vectors / bulky graph payloads from an item before it leaves the server.
export function stripItem(it) {
  if (!it || typeof it !== 'object') return it
  const out = {}
  for (const k of Object.keys(it)) {
    if (STRIP_ITEM_FIELDS.includes(k)) continue
    out[k] = it[k]
  }
  return out
}
