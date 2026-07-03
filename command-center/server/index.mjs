// Project OS Command Center — read-only local adapter (Express).
// Binds 127.0.0.1 only. Serves normalized Loops, a decimated graph, honest brain
// status, and a command dispatcher with a hard approval gate. Privacy via safe.mjs.
import express from 'express'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  PORT, PROJECT_OS_DIR, SECOND_BRAIN_DATA, SHARED_BRAIN, ANTIGRAVITY_BRAIN,
  PROJECT_ROOTS, ENGINES, LOCKS_DIR,
} from './config.mjs'
import { canRead, readText, readJson, listDir, exists, stripItem, pathExists } from './safe.mjs'

const app = express()
// Host allowlist: the server binds 127.0.0.1, but a malicious page could still
// reach it via DNS rebinding (a hostname that resolves to 127.0.0.1 passes the
// browser's same-origin checks while hitting us). Reject anything that isn't
// an explicit loopback Host before any route runs.
app.use((req, res, next) => {
  const host = String(req.headers.host || '').split(':')[0]
  if (host === '127.0.0.1' || host === 'localhost' || host === '[::1]') return next()
  res.status(403).json({ error: 'forbidden host' })
})
// CORS: a companion local UI (loopback :8799) reads us cross-port.
// Allowlist is exact origins — never a wildcard — so a rebound hostname that
// slipped the Host check still can't be read by a foreign page. Preflights
// (OPTIONS) carry the JSON content-type and the x-*-key engine headers.
const UI_ORIGINS = new Set(['http://127.0.0.1:8799', 'http://localhost:8799'])
app.use((req, res, next) => {
  const origin = String(req.headers.origin || '')
  if (UI_ORIGINS.has(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin)
    res.setHeader('Vary', 'Origin')
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    res.setHeader('Access-Control-Allow-Headers', 'content-type, x-anthropic-key, x-openai-key, x-gemini-key')
    res.setHeader('Access-Control-Max-Age', '600')
  }
  if (req.method === 'OPTIONS') return res.status(204).end()
  next()
})
app.use(express.json({ limit: '256kb' }))

// ---------- markdown helpers ----------
function section(md, headingRe) {
  if (!md) return ''
  const lines = md.split('\n')
  let i = lines.findIndex((l) => headingRe.test(l))
  if (i === -1) return ''
  const out = []
  for (let j = i + 1; j < lines.length; j++) {
    if (/^#{1,6}\s/.test(lines[j])) break
    out.push(lines[j])
  }
  return out.join('\n').trim()
}

function firstParagraph(text) {
  if (!text) return ''
  for (const block of text.split('\n\n')) {
    const cleaned = block
      .split('\n')
      .map((l) => l.replace(/^>\s?/, '').trim())
      .filter(Boolean)
      .join(' ')
      .trim()
    if (cleaned && !cleaned.startsWith('|') && !cleaned.startsWith('```')) return cleaned
  }
  return ''
}

function firstHeading(md) {
  if (!md) return ''
  const m = md.match(/^#{1,3}\s+(.+)$/m)
  return m ? m[1].trim() : ''
}

function matchValue(md, re) {
  if (!md) return ''
  const m = md.match(re)
  return m ? m[1].replace(/\*+/g, '').trim() : ''
}

// Parse a GitHub-style markdown table into row objects keyed by header.
function parseTable(md) {
  if (!md) return []
  const lines = md.split('\n').filter((l) => l.trim().startsWith('|'))
  if (lines.length < 2) return []
  const headers = lines[0].split('|').map((s) => s.trim()).filter(Boolean)
  const rows = []
  for (let i = 2; i < lines.length; i++) {
    const cells = lines[i].split('|').map((s) => s.trim())
    // drop leading/trailing empties from the pipe split
    const trimmed = cells.slice(1, 1 + headers.length)
    if (trimmed.every((c) => !c)) continue
    const row = {}
    headers.forEach((h, idx) => { row[h] = trimmed[idx] || '' })
    rows.push(row)
  }
  return rows
}

function dirMtime(dir) {
  let latest = 0
  for (const e of listDir(dir)) {
    if (!e.isFile()) continue
    try {
      const t = fs.statSync(path.join(dir, e.name)).mtimeMs
      if (t > latest) latest = t
    } catch { /* ignore */ }
  }
  return latest ? new Date(latest).toISOString() : null
}

function prettyName(p) {
  return path.basename(p)
    .replace(/[-_]+/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim()
}

// ---------- engine inference ----------
function inferEngine(root) {
  if (exists(path.join(root, '.codex')) ||
      exists(path.join(root, 'docs', 'superpowers')) ||
      exists(path.join(root, '.superpowers'))) return 'codex'
  return 'project-os'
}

// ---------- Loop normalization ----------
function cardsFromTable(rows, idKeys, titleKeys) {
  return rows.map((r, idx) => {
    const id = idKeys.map((k) => r[k]).find(Boolean) || String(idx + 1)
    const title = titleKeys.map((k) => r[k]).find(Boolean) || Object.values(r).find(Boolean) || ''
    return { id, title, body: Object.entries(r).map(([k, v]) => `${k}: ${v}`).join(' · '), status: r['Status'] || r['status'] || '' }
  })
}

function parseReceipt(md) {
  if (!md) return null
  const grab = (re) => {
    const t = section(md, re)
    if (!t) return []
    return t.split('\n').map((l) => l.replace(/^[-*]\s?/, '').trim()).filter(Boolean).slice(0, 12)
  }
  const ran = firstParagraph(section(md, /what loop ran|loop ran|summary/i)) || firstHeading(md)
  const toolsChecked = grab(/tools? checked|verification|tools/i)
  const failed = grab(/failed|failures|issues/i)
  const changed = grab(/changed|what changed|files? (changed|edited)/i)
  const saved = grab(/saved|what got saved|artifacts|memory/i)
  if (!ran && !toolsChecked.length && !changed.length && !saved.length) return null
  return { ran, toolsChecked, failed, changed, saved }
}

// Build a Loop from a directory that contains the numbered blackboard files.
function loopFromBlackboard(bbDir, { id, engine, root, kind }) {
  const goalMd = readText(path.join(bbDir, '00-project-goal.md')) || ''
  const decMd = readText(path.join(bbDir, '03-decisions.md')) || ''
  const riskMd = readText(path.join(bbDir, '04-risks.md')) || ''
  const oqMd = readText(path.join(bbDir, '06-open-questions.md')) || ''
  const planMd = readText(path.join(bbDir, '07-approved-plan.md')) || ''
  const roster = readText(path.join(bbDir, '13-agent-roster.md')) || ''
  const delivery = readText(path.join(bbDir, '13-delivery-report.md')) || ''
  const researchMd = readText(path.join(bbDir, '02-research.md')) || ''
  const evalMd = readText(path.join(bbDir, '12-evaluation-log.md')) || ''

  const goal =
    firstParagraph(section(goalMd, /canonical goal/i)) ||
    firstParagraph(section(goalMd, /^##?\s*summary/im)) ||
    firstParagraph(goalMd)
  const goalHash =
    matchValue(goalMd, /goal hash[^a-f0-9]*([a-f0-9]{8,64})/i) ||
    matchValue(roster, /SHA-256:\s*([a-f0-9]{8,64})/i)
  const tier =
    matchValue(goalMd, /tier[^:]*:\s*\**([A-Za-z][A-Za-z \-]*?(?:Swarm|Loop|Solo|Mini|Full))/i) ||
    matchValue(goalMd, /recommended level:\s*([^\n]+)/i)
  const costMode = matchValue(goalMd, /cost mode:\s*\**([A-Za-z\-]+)/i)

  const decisions = cardsFromTable(parseTable(decMd), ['ID', 'id'], ['Decision', 'decision'])
  const risks = cardsFromTable(parseTable(riskMd), ['ID', 'id', '#'], ['Risk', 'risk', 'Description'])
  const openQuestions = cardsFromTable(parseTable(oqMd), ['#', 'ID', 'id'], ['Question', 'question'])

  const receipt = parseReceipt(delivery)
  const research = firstParagraph(section(researchMd, /summary|method|inventory|mapping/i)) || firstParagraph(researchMd)
  const evaluation = firstParagraph(evalMd) || (matchValue(evalMd, /score[^0-9]*([0-9.]+)/i) ? `score ${matchValue(evalMd, /score[^0-9]*([0-9.]+)/i)}` : '')
  const hasPlan = !!planMd
  const status = receipt ? 'done' : hasPlan ? 'active' : 'awaiting-approval'
  const gateRequired = /full/i.test(tier) || /max-?effort/i.test(costMode) || status === 'awaiting-approval'

  const openTitles = openQuestions.filter((q) => /open|blocking/i.test(q.status) || !q.status).map((q) => q.title)
  const nextAction =
    (status === 'awaiting-approval' ? 'Approve plan / launch next wave'
      : openTitles[0] || (hasPlan ? 'Execute approved plan' : 'Define plan')) || 'Define next action'

  const links = [{ kind: 'run', id, label: path.relative(PROJECT_OS_DIR, bbDir) || bbDir }]

  return {
    id, engine, kind,
    title: firstHeading(goalMd) || prettyName(id),
    goal: goal || '(no goal recorded)',
    goalHash: goalHash || null,
    nextAction,
    verifier: /evaluator|rubric|0\.80/i.test(goalMd + planMd) ? 'Evaluator ≥ 0.80 (DoD rubric)' : 'Manual review',
    stopCondition: /definition of done/i.test(goalMd) ? 'All DoD items verified + eval pass' : 'Goal met',
    costMode: costMode || 'balanced',
    gate: { required: gateRequired, reason: gateRequired ? 'Full tier / Max-effort / pending approval' : '' },
    status, tier: tier || null,
    decisions, risks, openQuestions,
    nextActions: openTitles.slice(0, 5),
    receipt,
    research,
    evaluation,
    links,
    updatedAt: dirMtime(bbDir),
    source: { path: root || bbDir, kind: 'blackboard' },
  }
}

// Build a Loop from a generic project (superpowers plans/specs or README/package.json).
function loopFromGenericProject(root) {
  const engine = inferEngine(root)
  const pkg = readJson(path.join(root, 'package.json'), 2 * 1024 * 1024)
  const readme = readText(path.join(root, 'README.md')) || ''
  const spDirs = [
    path.join(root, 'docs', 'superpowers', 'specs'),
    path.join(root, 'docs', 'superpowers', 'plans'),
  ]
  let specMd = ''
  let planTitle = ''
  for (const d of spDirs) {
    const files = listDir(d).filter((e) => e.isFile() && e.name.endsWith('.md'))
    if (files.length) {
      const f = files.sort((a, b) => b.name.localeCompare(a.name))[0]
      const txt = readText(path.join(d, f.name)) || ''
      if (!specMd) specMd = txt
      if (!planTitle) planTitle = firstHeading(txt)
    }
  }
  const title = (pkg && pkg.name) ? prettyName(pkg.name) : prettyName(root)
  const goal =
    firstParagraph(section(specMd, /goal|summary|overview|objective/i)) ||
    firstParagraph(specMd) ||
    (pkg && pkg.description) ||
    firstParagraph(readme) ||
    '(no goal recorded)'
  const built = exists(path.join(root, 'dist')) || /built|verified|launch/i.test(specMd + readme)
  return {
    id: path.basename(root),
    engine,
    kind: 'project',
    title,
    goal,
    goalHash: null,
    nextAction: built ? 'Maintain / iterate' : 'Continue build',
    verifier: /verif|test|build_and_run/i.test(specMd + readme) ? 'Build + verify script' : 'Manual review',
    stopCondition: 'Project goal met',
    costMode: 'balanced',
    gate: { required: false, reason: '' },
    status: built ? 'done' : 'active',
    tier: null,
    decisions: [], risks: [], openQuestions: [],
    nextActions: [],
    receipt: null,
    links: [{ kind: 'project', id: path.basename(root), label: planTitle || title }],
    updatedAt: dirMtime(root),
    source: { path: root, kind: engine === 'codex' ? 'superpowers' : 'project' },
  }
}

function loopFromAntigravity(runDir) {
  const task = readText(path.join(runDir, 'task.md')) || ''
  const plan = readText(path.join(runDir, 'implementation_plan.md')) || ''
  const walk = readText(path.join(runDir, 'walkthrough.md')) || ''
  if (!task && !plan && !walk) return null
  const title = firstHeading(task) || firstHeading(plan) || `Antigravity run ${path.basename(runDir).slice(0, 8)}`
  const goal = firstParagraph(task) || firstParagraph(plan) || '(antigravity run notes)'
  return {
    id: `antigravity-${path.basename(runDir).slice(0, 8)}`,
    engine: 'antigravity',
    kind: 'run',
    title,
    goal,
    goalHash: null,
    nextAction: 'Review notes',
    verifier: /walkthrough/i.test(walk) ? 'Walkthrough notes' : 'Manual review',
    stopCondition: 'Run archived',
    costMode: 'balanced',
    gate: { required: false, reason: '' },
    status: 'archived',
    tier: null,
    decisions: [], risks: [], openQuestions: [], nextActions: [],
    receipt: null,
    links: [{ kind: 'run', id: path.basename(runDir), label: 'antigravity/brain' }],
    updatedAt: dirMtime(runDir),
    source: { path: runDir, kind: 'antigravity' },
  }
}

// ---------- discovery ----------
// Split into CORE (fast, local: project-os runs + root blackboard + antigravity notes)
// and PROJECTS (slow: external/iCloud-synced tracked roots). The Runs dashboard only
// needs CORE, so it paints instantly; PROJECTS load lazily when the Projects tab opens.
let _coreCache = null, _coreAt = 0
let _projCache = null, _projAt = 0
const TTL = 120000

function discoverCore() {
  if (_coreCache && Date.now() - _coreAt < TTL) return _coreCache
  const loops = []

  // Project OS runs/*
  const runsDir = path.join(PROJECT_OS_DIR, 'runs')
  for (const e of listDir(runsDir)) {
    if (!e.isDirectory()) continue
    const dir = path.join(runsDir, e.name)
    if (exists(path.join(dir, '00-project-goal.md'))) {
      loops.push(loopFromBlackboard(dir, { id: e.name, engine: 'project-os', root: dir, kind: 'run' }))
    }
  }

  // Project OS root blackboard (the active/example run)
  const rootBb = path.join(PROJECT_OS_DIR, 'blackboard')
  if (exists(path.join(rootBb, '00-project-goal.md'))) {
    loops.push(loopFromBlackboard(rootBb, { id: 'blackboard', engine: 'project-os', root: rootBb, kind: 'run' }))
  }

  // Antigravity run notes (safe md only, capped)
  let agCount = 0
  for (const e of listDir(ANTIGRAVITY_BRAIN)) {
    if (agCount >= 12) break
    if (!e.isDirectory()) continue
    const l = loopFromAntigravity(path.join(ANTIGRAVITY_BRAIN, e.name))
    if (l) { loops.push(l); agCount++ }
  }

  loops.sort((a, b) => (b.updatedAt || '').localeCompare(a.updatedAt || ''))
  _coreCache = loops
  _coreAt = Date.now()
  return loops
}

function discoverProjects() {
  if (_projCache && Date.now() - _projAt < TTL) return _projCache
  const loops = []
  // Wall-clock budget: external roots may be iCloud-evicted (slow synchronous reads).
  // Stop after the budget so the Projects tab returns partial-but-fast instead of hanging.
  const start = Date.now()
  const BUDGET_MS = 9000
  let truncated = false
  for (const root of PROJECT_ROOTS) {
    if (Date.now() - start > BUDGET_MS) { truncated = true; break }
    if (!exists(root)) continue
    try {
      const bb = exists(path.join(root, 'blackboard', '00-project-goal.md'))
        ? path.join(root, 'blackboard')
        : exists(path.join(root, '00-project-goal.md')) ? root : null
      if (bb) {
        loops.push(loopFromBlackboard(bb, { id: path.basename(root), engine: inferEngine(root), root, kind: 'project' }))
      } else {
        loops.push(loopFromGenericProject(root))
      }
    } catch { /* skip a root that fails/blocks */ }
  }
  loops.sort((a, b) => (b.updatedAt || '').localeCompare(a.updatedAt || ''))
  if (truncated) loops.push({
    id: '_more', engine: 'project-os', kind: 'project', title: 'More projects not shown',
    goal: 'Some tracked projects were skipped (slow iCloud/Documents reads). Open their folder in Finder once to materialize, or reopen this tab.',
    goalHash: null, nextAction: '', verifier: '', stopCondition: '', costMode: '',
    gate: { required: false, reason: '' }, status: 'archived', tier: null,
    decisions: [], risks: [], openQuestions: [], nextActions: [], receipt: null,
    links: [], updatedAt: null, source: { path: '', kind: 'note' },
  })
  // Only cache a full (non-truncated) result, so a slow first open can fully populate later.
  if (!truncated) { _projCache = loops; _projAt = Date.now() }
  return loops
}

function allLoops() {
  return [...discoverCore(), ...discoverProjects()]
}

function findLoop(id) {
  return discoverCore().find((l) => l.id === id) || discoverProjects().find((l) => l.id === id) || null
}

// ---------- graph (decimated) ----------
let _graphCache = null

// Fallback graph: the Arachne project graph (memory/build_graph.py output).
// Renders the OS -> runs -> decisions/risks/questions hierarchy so a fresh
// install gets a real visual graph of its OWN project data out of the box,
// not an empty state that only fills if a personal second brain exists.
function arachneGraph() {
  const out = readJson(path.join(PROJECT_OS_DIR, 'arachne-out', 'graph.json'))
    || readJson(path.join(PROJECT_OS_DIR, 'graphify-out', 'graph.json')) // legacy name
  if (!out || !Array.isArray(out.nodes) || !out.nodes.length) return null
  const degree = new Map()
  for (const e of out.edges || []) {
    degree.set(e.source, (degree.get(e.source) || 0) + 1)
    degree.set(e.target, (degree.get(e.target) || 0) + 1)
  }
  return {
    nodes: out.nodes.map((n) => ({
      id: n.id, name: n.label || n.id, type: n.type || '',
      val: Math.max(1, degree.get(n.id) || 1),
    })),
    links: (out.edges || []).map((e) => ({ source: e.source, target: e.target, type: e.type || '', weight: 1 })),
    domains: [],
    totals: { nodes: out.nodes.length, links: (out.edges || []).length },
    shown: { nodes: out.nodes.length, links: (out.edges || []).length },
    note: 'project graph (Arachne) — rebuild with: python3 memory/build_graph.py',
  }
}

function buildGraph() {
  if (_graphCache && Date.now() - _graphCache.at < 600000) return _graphCache.data
  const raw = readJson(path.join(SECOND_BRAIN_DATA, 'links.json'))
  if (!raw || !Array.isArray(raw.nodes)) {
    const data = arachneGraph()
      || { nodes: [], links: [], domains: [], totals: { nodes: 0, links: 0 }, note: 'no graph data yet — run: python3 memory/build_graph.py' }
    _graphCache = { at: Date.now(), data }
    return data
  }
  const totalNodes = raw.nodes.length
  const totalLinks = (raw.links || []).length

  const rawLinks = raw.links || []
  // Use only STRUCTURAL links — the domain -> source -> subtopic -> item hierarchy
  // (+ semantic). The ~68k 'tag' and ~3k 'entity' links are noise that produce an
  // unreadable hairball, so we drop them.
  const STRUCTURAL = new Set(['domain-source', 'source-subtopic', 'subtopic-item', 'semantic'])
  const structLinks = rawLinks.filter((l) => STRUCTURAL.has(l.type))

  const degreeMap = new Map()
  for (const l of structLinks) {
    degreeMap.set(l.source, (degreeMap.get(l.source) || 0) + 1)
    degreeMap.set(l.target, (degreeMap.get(l.target) || 0) + 1)
  }
  const deg = (id) => degreeMap.get(id) || 0

  const involved = new Set()
  for (const l of structLinks) { involved.add(l.source); involved.add(l.target) }
  const present = raw.nodes.filter((n) => involved.has(n.id))
  // Keep every structural hub (domain/source/subtopic) + the top items by degree.
  const hubs = present.filter((n) => n.type !== 'item')
  const items = present.filter((n) => n.type === 'item').sort((a, b) => deg(b.id) - deg(a.id)).slice(0, 360)
  const topNodes = [...hubs, ...items]

  const keep = new Set(topNodes.map((n) => n.id))
  const links = structLinks
    .filter((l) => keep.has(l.source) && keep.has(l.target))
    .map((l) => ({ source: l.source, target: l.target, type: l.type || '', weight: l.weight || 1 }))
  const nodes = topNodes.map((n) => ({ id: n.id, name: n.name || n.id, type: n.type || '', val: n.val || 1 }))
  const domains = raw.domain_summaries
    ? Object.keys(raw.domain_summaries).map((d) => ({ domain: d, summary: String(raw.domain_summaries[d]).slice(0, 240) }))
    : []
  const data = { nodes, links, domains, totals: { nodes: totalNodes, links: totalLinks }, shown: { nodes: nodes.length, links: links.length } }
  _graphCache = { at: Date.now(), data }
  return data
}

// ---------- brain (honest capability + lessons) ----------
function brainStatus() {
  const memDir = path.join(PROJECT_OS_DIR, 'memory')
  const probe = (f) => exists(path.join(memDir, f))
  const graphScript = probe('build_graph.py') || probe('graphos.py')
  // vector adapter may be named mneme (post-rebrand), osvec (template), or turbovec
  const vecScript = probe('mneme_adapter.py') || probe('osvec_adapter.py') || probe('turbovec_adapter.py')
  const tvim = exists(path.join(SECOND_BRAIN_DATA, 'analyzed', 'turbovec_index.tvim'))

  // Read the built artifacts to report real counts (available) vs script-only (configured).
  const graphOut = readJson(path.join(PROJECT_OS_DIR, 'graphify-out', 'graph.json'))
  const vecIdxName = ['mneme_index.json', 'osvec_index.json'].find((f) => exists(path.join(memDir, f)))
  const vecIdx = vecIdxName ? readJson(path.join(memDir, vecIdxName)) : null
  const graphStatus = graphOut ? 'available' : graphScript ? 'configured' : 'not-configured'
  const graphEvidence = graphOut
    ? `${(graphOut.nodes || []).length} nodes · ${(graphOut.edges || []).length} edges (graphify-out/graph.json)`
    : graphScript ? 'build_graph.py present — run it to build graph.json' : 'no build_graph.py in memory/'
  const vecStatus = vecIdx ? 'available' : vecScript ? 'configured' : 'not-configured'
  const vecEvidence = vecIdx
    ? `${vecIdx.count || (vecIdx.entries || []).length} vectors · ${vecIdx.embedder || 'local'} (${vecIdxName})`
    : vecScript ? 'vector adapter present — run build to index' : 'no vector adapter in memory/'

  // shared-brain lessons (read-only; truncate text)
  const lessons = []
  const types = {}
  const sb = readText(SHARED_BRAIN)
  if (sb) {
    for (const line of sb.split('\n')) {
      const t = line.trim()
      if (!t) continue
      try {
        const o = JSON.parse(t)
        types[o.type || 'note'] = (types[o.type || 'note'] || 0) + 1
        if (lessons.length < 8) lessons.push({ type: o.type || 'note', project: o.project_name || o.project_id || '', text: String(o.text || '').slice(0, 200), ts: o.ts || '' })
      } catch { /* skip malformed */ }
    }
  }

  return {
    capabilities: [
      { name: 'Graphify (knowledge graph)', status: graphStatus, evidence: graphEvidence },
      { name: 'Vector memory (Mneme/OSVec)', status: vecStatus, evidence: vecEvidence },
      { name: 'TurboVec index (second-brain)', status: tvim ? 'present-unwired' : 'not-found', evidence: tvim ? 'analyzed/turbovec_index.tvim present (read-only metadata)' : 'no .tvim' },
      { name: 'Cost actuals', status: probe('cost_actuals.py') ? 'available' : 'not-configured', evidence: probe('cost_actuals.py') ? 'cost_actuals.py present' : 'estimates only (no cost_actuals.py)' },
    ],
    sharedBrain: { path: SHARED_BRAIN, present: !!sb, total: Object.values(types).reduce((a, b) => a + b, 0), byType: types, lessons },
  }
}

// ---------- loop-state (gauge · locks · plans · evolution) ----------
// Read-only view over the loop-tooling adopted 2026-07-02: brain_scale gauge (via
// the nightly automation log), bb_lock lockfiles, plan artifacts, evolution records.
function loopsState() {
  // gauge: newest entry of blackboard/22-automation-log.md (written by os_nightly.py)
  const autoMd = readText(path.join(PROJECT_OS_DIR, 'blackboard', '22-automation-log.md')) || ''
  const entryM = autoMd.match(/^## (.+)$/m)
  const gaugeAt = entryM ? entryM[1] : null
  const body = entryM
    ? autoMd.slice(autoMd.indexOf(entryM[0]) + entryM[0].length).split('\n## ')[0]
    : ''
  const gaugeLines = body.split('\n').map((l) => l.replace(/^-\s?/, '').trim()).filter(Boolean)
  const gaugeStatus = /CUTOVER/.test(body) ? 'cutover' : /WATCH/.test(body) ? 'watch' : gaugeAt ? 'ok' : 'unknown'

  // active bb_lock lockfiles (stale after 60s unless reaped)
  const locks = []
  for (const e of listDir(LOCKS_DIR)) {
    if (!e.isFile() || !e.name.endsWith('.lock')) continue
    const o = readJson(path.join(LOCKS_DIR, e.name)) || {}
    const ageSec = o.ts ? Math.round(Date.now() / 1000 - o.ts) : null
    locks.push({
      target: o.path ? path.basename(o.path) : e.name,
      agent: o.agent || '?', pid: o.pid || null,
      ageSec, stale: ageSec !== null && ageSec > 60,
    })
  }

  // plan artifacts (planned -> approved -> running -> done)
  const plans = []
  const plansDir = path.join(PROJECT_OS_DIR, 'blackboard', 'plans')
  for (const e of listDir(plansDir)) {
    if (!e.isFile() || !e.name.endsWith('.json')) continue
    const p = readJson(path.join(plansDir, e.name))
    if (!p || !Array.isArray(p.steps)) continue
    plans.push({
      id: p.id || e.name, status: p.status || '?',
      goal: String(p.goal || '').slice(0, 120),
      done: p.steps.filter((s) => s.done).length, total: p.steps.length,
      created: p.created || null,
    })
  }
  plans.sort((a, b) => String(b.created || '').localeCompare(String(a.created || '')))

  // evolution records per run (evolve-from-best)
  const evolution = []
  const runsDir = path.join(PROJECT_OS_DIR, 'runs')
  for (const e of listDir(runsDir)) {
    if (!e.isDirectory()) continue
    const ev = readJson(path.join(runsDir, e.name, 'evolution.json'))
    if (!ev || !Array.isArray(ev.records) || !ev.records.length) continue
    const scored = ev.records.filter((r) => r.score != null)
    const best = scored.length ? scored.reduce((a, b) => (b.score > a.score ? b : a)) : null
    evolution.push({
      run: e.name, variants: ev.records.length,
      best: best ? { variant: best.variant, score: best.score } : null,
      latest: ev.records[ev.records.length - 1].ts || null,
    })
  }

  return { gauge: { status: gaugeStatus, at: gaugeAt, lines: gaugeLines }, locks, plans, evolution }
}

// ---------- command matrix ----------
const COMMANDS = {
  '/brain': { mode: 'live', desc: 'Show capability + memory health', sideEffects: 'read-only' },
  '/reality': { mode: 'live', desc: 'Capability preflight reality check', sideEffects: 'read-only' },
  '/lint': { mode: 'live', desc: 'Lint active run (missing DoD, drift, staleness)', sideEffects: 'read-only' },
  '/save': { mode: 'gated-local', desc: 'Write a note/card into the active run blackboard', sideEffects: 'writes one file under runs/' },
  '/prune': { mode: 'gated', desc: 'Suggest memory cleanup (suggestions only in slice 1)', sideEffects: 'read-only suggestions' },
  '/research': { mode: 'gated-stub', desc: 'Spawn a researcher wave', sideEffects: 'agent wave · spends tokens', engineDefault: 'claude' },
  '/kickoff': { mode: 'gated-stub', desc: 'Start a new supervised run', sideEffects: 'agent wave · spends tokens', engineDefault: 'claude' },
  '/trust': { mode: 'gated-stub', desc: 'Run a security/trust review wave', sideEffects: 'agent wave · spends tokens', engineDefault: 'claude' },
}

function lintRun(loop) {
  const issues = []
  if (!loop) return ['No active run selected']
  if (!loop.goal || loop.goal.includes('no goal')) issues.push('Goal is empty')
  if (!loop.goalHash) issues.push('No goal hash (drift guard missing)')
  if (loop.kind === 'run' && !loop.decisions.length) issues.push('No decisions logged')
  if (loop.openQuestions.some((q) => /blocking/i.test(q.status))) issues.push('Blocking open questions remain')
  if (loop.status === 'awaiting-approval') issues.push('Plan not yet approved')
  return issues.length ? issues : ['Clean — no lint issues']
}

// ---------- routes ----------
app.get('/api/health', (_req, res) => res.json({ ok: true, ts: new Date().toISOString() }))

app.get('/api/engines', (_req, res) => {
  // presence-only existence (engine dirs are intentionally outside the read allowlist)
  res.json(ENGINES.map((e) => ({ id: e.id, label: e.label, present: pathExists(e.root) })))
})

// list view: omit heavy card bodies, keep counts
const toListView = (loops) => loops.map((l) => ({
  ...l,
  decisions: [],
  risks: [],
  openQuestions: [],
  counts: { decisions: l.decisions.length, risks: l.risks.length, openQuestions: l.openQuestions.length },
}))

// Fast: local project-os runs + antigravity (Runs dashboard — paints instantly).
app.get('/api/runs', (_req, res) => res.json(toListView(discoverCore())))

// Lazy: core + external/iCloud tracked projects (Projects tab only).
app.get('/api/projects', (_req, res) => res.json(toListView(allLoops())))

app.get('/api/runs/:id', (req, res) => {
  const loop = findLoop(req.params.id)
  if (!loop) return res.status(404).json({ error: 'run not found' })
  res.json(loop)
})

app.get('/api/graph', (_req, res) => res.json(buildGraph()))

app.get('/api/brain', (_req, res) => res.json(brainStatus()))

app.get('/api/loops-state', (_req, res) => res.json(loopsState()))

app.get('/api/items', (req, res) => {
  const limit = Math.min(Number(req.query.limit) || 100, 500)
  const offset = Number(req.query.offset) || 0
  let arr = readJson(path.join(SECOND_BRAIN_DATA, 'analyzed', 'items.json'))
  if (!Array.isArray(arr)) arr = (arr && Array.isArray(arr.items)) ? arr.items : []
  const total = arr.length
  const items = arr.slice(offset, offset + limit).map(stripItem)
  res.json({ items, total, offset, limit })
})

app.get('/api/research', (_req, res) => {
  // Derived: research cards across all loops (open questions tagged research) + none external.
  const loops = allLoops()
  const cards = []
  for (const l of loops) {
    for (const q of l.openQuestions || []) {
      if (/research|verify|investigat/i.test(q.title)) cards.push({ run: l.id, engine: l.engine, question: q.title })
    }
  }
  res.json({ cards, note: cards.length ? '' : 'No research items yet (honest empty state).' })
})

app.get('/api/inbox', (_req, res) => {
  res.json({ items: [], note: 'Inbox is empty — wire a safe source in slice 2 (honest empty state).' })
})

app.post('/api/command', (req, res) => {
  const { command, runId, confirm, payload } = req.body || {}
  const spec = COMMANDS[command]
  if (!spec) return res.status(400).json({ error: `unknown command ${command}` })
  const loop = runId ? findLoop(runId) : null

  // LIVE read-only commands execute immediately.
  if (spec.mode === 'live') {
    if (command === '/brain' || command === '/reality') {
      return res.json({ command, mode: 'executed', result: brainStatus() })
    }
    if (command === '/lint') {
      return res.json({ command, mode: 'executed', result: { run: runId, issues: lintRun(loop) } })
    }
  }

  // /prune — read-only suggestions only.
  if (command === '/prune') {
    const bs = brainStatus()
    const suggestions = bs.sharedBrain.total > 20
      ? [`Review ${bs.sharedBrain.total} shared-brain lessons; dedupe by origin_id.`]
      : ['No pruning pressure — lesson store is small.']
    return res.json({ command, mode: 'executed', result: { suggestions, applyGated: true } })
  }

  // Anything mutating / wave-spawning requires the gate.
  if (!confirm) {
    return res.json({
      command, mode: 'gate',
      gate: {
        title: spec.mode === 'gated-stub' ? 'Agent wave — approval required' : 'Local write — approval required',
        desc: spec.desc,
        target: command === '/save'
          ? (loop ? `${loop.source.path}/<note>.md` : 'runs/<active>/...')
          : `${spec.engineDefault || 'claude'} runner`,
        sideEffects: spec.sideEffects,
        engine: spec.engineDefault || loop?.engine || 'claude',
        estimate: spec.mode === 'gated-stub' ? '~tokens (see cost estimate) · no external $' : 'one file write',
        confirmLabel: spec.mode === 'gated-stub' ? 'Write gated intent (does NOT fire runner in slice 1)' : 'Write file',
      },
    })
  }

  // Confirmed:
  if (command === '/save' && loop && loop.source.kind === 'blackboard') {
    // Only ever write under runs/ (WRITE allowlist is enforced here by construction).
    const runsRoot = path.join(PROJECT_OS_DIR, 'runs')
    if (!path.resolve(loop.source.path).startsWith(path.resolve(runsRoot))) {
      return res.json({ command, mode: 'refused', reason: 'Active run is not under runs/; /save is restricted to runs/.' })
    }
    const note = (payload && String(payload.text || '').trim()) || ''
    if (!note) return res.json({ command, mode: 'refused', reason: 'Empty note.' })
    const file = path.join(loop.source.path, 'packets', `note-${Date.now()}.md`)
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true })
      fs.writeFileSync(file, `# Note (via /save)\n\n${note}\n`, 'utf8')
      _coreCache = null // invalidate discovery cache
      return res.json({ command, mode: 'executed', wrote: file })
    } catch (e) {
      return res.json({ command, mode: 'error', reason: String(e.message || e) })
    }
  }

  // gated-stub confirmed → write an intent record (no runner fires in slice 1).
  if (spec.mode === 'gated-stub') {
    const runsRoot = path.join(PROJECT_OS_DIR, 'runs')
    const targetDir = loop && path.resolve(loop.source.path).startsWith(path.resolve(runsRoot))
      ? path.join(loop.source.path, 'packets')
      : path.join(runsRoot, 'project-os-command-center', 'packets')
    const file = path.join(targetDir, `intent-${command.slice(1)}-${Date.now()}.md`)
    try {
      fs.mkdirSync(targetDir, { recursive: true })
      fs.writeFileSync(file,
        `# Gated intent: ${command}\n\n- When: ${new Date().toISOString()}\n- Engine: ${spec.engineDefault || 'claude'}\n- Effect: ${spec.sideEffects}\n- Status: QUEUED (runner not wired in slice 1 — requires human launch)\n- Payload: ${JSON.stringify(payload || {})}\n`,
        'utf8')
      return res.json({ command, mode: 'intent-queued', wrote: file, note: 'Runner is stubbed in slice 1; no tokens spent.' })
    } catch (e) {
      return res.json({ command, mode: 'error', reason: String(e.message || e) })
    }
  }

  return res.json({ command, mode: 'noop' })
})

// Serve the built UI same-origin with /api (used by the Electron desktop app and `npm start`).
// API routes are registered above, so they take precedence over the static/catch-all below.
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const distDir = path.join(__dirname, '..', 'dist')
if (fs.existsSync(distDir)) {
  app.use(express.static(distDir))
  app.get(/^(?!\/api).*/, (_req, res) => res.sendFile(path.join(distDir, 'index.html')))
}

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[command-center] read-only adapter on http://127.0.0.1:${PORT}`)
  console.log(`[command-center] project-os: ${PROJECT_OS_DIR}`)
  console.log(`[command-center] tracked projects: ${PROJECT_ROOTS.length}`)
})
