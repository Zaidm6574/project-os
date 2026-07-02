import type { Loop, EngineInfo, GraphData, BrainStatus, Item, LoopsState } from './types'

async function jget<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return (await r.json()) as T
}
async function jpost<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return (await r.json()) as T
}

export interface CommandResult {
  command: string
  mode: 'executed' | 'gate' | 'intent-queued' | 'refused' | 'error' | 'noop'
  gate?: {
    title: string; desc: string; target: string; sideEffects: string
    engine: string; estimate: string; confirmLabel: string
  }
  result?: unknown
  wrote?: string
  reason?: string
  note?: string
}

export const api = {
  engines: () => jget<EngineInfo[]>('/api/engines'),
  runs: () => jget<Loop[]>('/api/runs'),
  projects: () => jget<Loop[]>('/api/projects'),
  run: (id: string) => jget<Loop>(`/api/runs/${encodeURIComponent(id)}`),
  graph: () => jget<GraphData>('/api/graph'),
  brain: () => jget<BrainStatus>('/api/brain'),
  loopsState: () => jget<LoopsState>('/api/loops-state'),
  items: (limit = 120, offset = 0) =>
    jget<{ items: Item[]; total: number }>(`/api/items?limit=${limit}&offset=${offset}`),
  research: () =>
    jget<{ cards: { run: string; engine: string; question: string }[]; note: string }>('/api/research'),
  inbox: () => jget<{ items: unknown[]; note: string }>('/api/inbox'),
  command: (body: { command: string; runId?: string; confirm?: boolean; payload?: unknown }) =>
    jpost<CommandResult>('/api/command', body),
}
