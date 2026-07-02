import type { ReactNode } from 'react'
import type { Engine, LoopStatus } from './types'

const ENGINE_META: Record<Engine, { label: string; color: string }> = {
  claude: { label: 'Claude', color: 'var(--color-claude)' },
  codex: { label: 'Codex', color: 'var(--color-codex)' },
  antigravity: { label: 'Antigravity', color: 'var(--color-anti)' },
  'project-os': { label: 'Project OS', color: 'var(--color-pos)' },
}

export function EngineBadge({ engine }: { engine: Engine }) {
  const m = ENGINE_META[engine] ?? ENGINE_META['project-os']
  return (
    <span
      className="chip"
      style={{ color: m.color, borderColor: `color-mix(in srgb, ${m.color} 45%, var(--color-edge))` }}
      title={`engine: ${m.label}`}
    >
      <span style={{ background: m.color }} className="inline-block w-1.5 h-1.5 rounded-full mr-1.5 align-middle" />
      {m.label}
    </span>
  )
}

const STATUS_COLOR: Record<LoopStatus, string> = {
  active: 'var(--color-accent)',
  'awaiting-approval': 'var(--color-warn)',
  done: 'var(--color-good)',
  archived: 'var(--color-dim)',
  blocked: 'var(--color-bad)',
}

export function StatusBadge({ status }: { status: LoopStatus }) {
  const c = STATUS_COLOR[status] ?? 'var(--color-mut)'
  return (
    <span className="chip" style={{ color: c, borderColor: `color-mix(in srgb, ${c} 45%, var(--color-edge))` }}>
      {status}
    </span>
  )
}

export function Chip({ children, title }: { children: ReactNode; title?: string }) {
  return <span className="chip" title={title}>{children}</span>
}

export function Field({ label, children, mono }: { label: string; children: ReactNode; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--color-dim)' }}>{label}</div>
      <div className={'text-[12.5px] mt-0.5 ' + (mono ? 'mono' : '')} style={{ color: 'var(--color-ink)' }}>{children}</div>
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="card p-6 text-center" style={{ color: 'var(--color-mut)' }}>{children}</div>
  )
}

const PATHS: Record<string, string> = {
  projects: 'M3 7h7l2 2h9v9a2 2 0 0 1-2 2H3z',
  runs: 'M4 6h16M4 12h16M4 18h10',
  brain: 'M12 3a4 4 0 0 0-4 4 3 3 0 0 0-1 6 3 3 0 0 0 3 5 3 3 0 0 0 4-1 3 3 0 0 0 4 1 3 3 0 0 0 3-5 3 3 0 0 0-1-6 4 4 0 0 0-4-4 3 3 0 0 0-3-1z',
  research: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-4.3-4.3',
  inbox: 'M3 13h4l2 3h6l2-3h4M3 13l3-8h12l3 8v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  graph: 'M6 18a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM18 8a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM18 20a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM8 15l8-8M16 15l-7 1',
  settings: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM19 12l1.5 1-1 2-1.8-.4-1.2 1.4.2 1.9-2 .8-1.2-1.5h-1.6L9.5 19l-2-.8.2-1.9-1.2-1.4-1.8.4-1-2L5 12l-1.5-1 1-2 1.8.4 1.2-1.4-.2-1.9 2-.8 1.2 1.5h1.6l1.2-1.5 2 .8-.2 1.9 1.2 1.4 1.8-.4 1 2z',
  terminal: 'M4 5h16v14H4zM7 9l3 3-3 3M13 15h4',
  dot: 'M12 12h.01',
}

export function Icon({ name, size = 16 }: { name: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d={PATHS[name] ?? PATHS.dot} />
    </svg>
  )
}
