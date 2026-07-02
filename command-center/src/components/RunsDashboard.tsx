import type { Loop, Engine } from '../types'
import { EngineBadge, StatusBadge, Empty } from '../ui'

function timeAgo(iso: string | null): string {
  if (!iso) return ''
  const d = Date.now() - new Date(iso).getTime()
  const m = Math.floor(d / 60000)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function LoopCard({ loop, selected, onSelect }: { loop: Loop; selected: boolean; onSelect: () => void }) {
  const c = loop.counts ?? { decisions: loop.decisions.length, risks: loop.risks.length, openQuestions: loop.openQuestions.length }
  return (
    <button
      onClick={onSelect}
      className="card text-left p-3 hover:translate-y-[-1px] transition-transform"
      style={{ borderColor: selected ? 'var(--color-accent)' : 'var(--color-edge)' }}
    >
      <div className="flex items-center gap-1.5 mb-1.5">
        <EngineBadge engine={loop.engine} />
        <StatusBadge status={loop.status} />
        <span className="ml-auto text-[10px]" style={{ color: 'var(--color-dim)' }}>{timeAgo(loop.updatedAt)}</span>
      </div>
      <div className="font-semibold text-[13px] mb-1 truncate">{loop.title}</div>
      <div className="text-[11.5px] leading-snug mb-2 line-clamp-2" style={{ color: 'var(--color-mut)', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
        {loop.goal}
      </div>
      <div className="flex items-center gap-2 text-[10px]" style={{ color: 'var(--color-dim)' }}>
        <span title="decisions">D {c.decisions}</span>
        <span title="risks">R {c.risks}</span>
        <span title="open questions">Q {c.openQuestions}</span>
        {loop.gate.required && <span style={{ color: 'var(--color-warn)' }}>⛚ gate</span>}
        {loop.receipt && <span style={{ color: 'var(--color-good)' }}>✓ receipt</span>}
      </div>
      <div className="text-[11px] mt-2 pt-2 truncate" style={{ borderTop: '1px solid var(--color-edge)', color: 'var(--color-mut)' }}>
        <span style={{ color: 'var(--color-dim)' }}>next:</span> {loop.nextAction}
      </div>
    </button>
  )
}

const ENGINE_ORDER: Engine[] = ['project-os', 'codex', 'claude', 'antigravity']

export function RunsDashboard({
  loops, selectedId, onSelect, grouped, title,
}: {
  loops: Loop[]
  selectedId: string | null
  onSelect: (id: string) => void
  grouped?: boolean
  title: string
}) {
  if (!loops.length) return <Empty>No runs discovered yet.</Empty>

  if (grouped) {
    const byEngine = ENGINE_ORDER
      .map((e) => ({ engine: e, items: loops.filter((l) => l.engine === e) }))
      .filter((g) => g.items.length)
    return (
      <div className="space-y-5">
        {byEngine.map((g) => (
          <div key={g.engine}>
            <div className="flex items-center gap-2 mb-2">
              <EngineBadge engine={g.engine} />
              <span className="text-[11px]" style={{ color: 'var(--color-dim)' }}>{g.items.length} project{g.items.length > 1 ? 's' : ''}</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {g.items.map((l) => (
                <LoopCard key={l.id} loop={l} selected={selectedId === l.id} onSelect={() => onSelect(l.id)} />
              ))}
            </div>
          </div>
        ))}
      </div>
    )
  }

  return (
    <div>
      <div className="text-[11px] uppercase tracking-wider mb-2" style={{ color: 'var(--color-dim)' }}>{title} · {loops.length}</div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {loops.map((l) => (
          <LoopCard key={l.id} loop={l} selected={selectedId === l.id} onSelect={() => onSelect(l.id)} />
        ))}
      </div>
    </div>
  )
}
