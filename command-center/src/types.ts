export type Engine = 'claude' | 'codex' | 'antigravity' | 'project-os'
export type LoopStatus = 'active' | 'blocked' | 'awaiting-approval' | 'done' | 'archived'

export interface Card { id: string; title: string; body: string; status?: string }
export interface LoopLink { kind: string; id: string; label: string }
export interface LoopReceipt {
  ran: string
  toolsChecked: string[]
  failed: string[]
  changed: string[]
  saved: string[]
  date?: string
}

export interface Loop {
  id: string
  engine: Engine
  kind: string
  title: string
  goal: string
  goalHash: string | null
  nextAction: string
  verifier: string
  stopCondition: string
  costMode: string
  gate: { required: boolean; reason: string }
  status: LoopStatus
  tier: string | null
  decisions: Card[]
  risks: Card[]
  openQuestions: Card[]
  nextActions: string[]
  receipt: LoopReceipt | null
  research?: string
  evaluation?: string
  links: LoopLink[]
  updatedAt: string | null
  source: { path: string; kind: string }
  counts?: { decisions: number; risks: number; openQuestions: number }
}

export interface EngineInfo { id: Engine; label: string; present: boolean }

export interface GraphNode { id: string; name: string; type: string; val: number }
export interface GraphLink { source: string; target: string; type: string; weight: number }
export interface GraphData {
  nodes: GraphNode[]
  links: GraphLink[]
  domains: { domain: string; summary: string }[]
  totals: { nodes: number; links: number }
  shown?: { nodes: number; links: number }
  note?: string
}

export interface Capability { name: string; status: string; evidence: string }
export interface Lesson { type: string; project: string; text: string; ts: string }
export interface BrainStatus {
  capabilities: Capability[]
  sharedBrain: { path: string; present: boolean; total: number; byType: Record<string, number>; lessons: Lesson[] }
}

export interface Item {
  id?: string
  title?: string
  summary?: string
  domain?: string
  tags?: string[]
  source?: string
  source_url?: string
  saved_date?: string
  content_type?: string
  key_takeaways?: unknown[]
  entities?: unknown[]
}

export type ViewKey = 'projects' | 'runs' | 'loops' | 'brain' | 'research' | 'inbox' | 'graph' | 'settings'

// /api/loops-state — gauge · locks · plans · evolution (loop tooling, 2026-07-02)
export interface LoopsState {
  gauge: { status: 'ok' | 'watch' | 'cutover' | 'unknown'; at: string | null; lines: string[] }
  locks: { target: string; agent: string; pid: number | null; ageSec: number | null; stale: boolean }[]
  plans: { id: string; status: string; goal: string; done: number; total: number; created: string | null }[]
  evolution: { run: string; variants: number; best: { variant: string; score: number } | null; latest: string | null }[]
}
