import type { Loop, Card } from '../types'
import { Empty } from '../ui'
import { LoopReceiptCard } from './LoopReceipt'

function CardCol({ title, cards, accent }: { title: string; cards: Card[]; accent: string }) {
  return (
    <div className="card overflow-hidden flex flex-col">
      <div className="card-h px-3 py-2 flex items-center gap-2" style={{ borderBottom: '1px solid var(--color-edge)' }}>
        <span className="w-1.5 h-1.5 rounded-full" style={{ background: accent }} />
        <span className="font-semibold text-[12px]">{title}</span>
        <span className="ml-auto chip">{cards.length}</span>
      </div>
      <div className="p-2 space-y-1.5 overflow-y-auto" style={{ maxHeight: 360 }}>
        {cards.length ? cards.map((c) => (
          <div key={c.id} className="rounded-md p-2" style={{ background: 'var(--color-panel2)', border: '1px solid var(--color-edge)' }}>
            <div className="flex gap-2">
              <span className="chip mono">{c.id}</span>
              {c.status && <span className="chip">{c.status}</span>}
            </div>
            <div className="text-[11.5px] mt-1" style={{ color: 'var(--color-ink)' }}>{c.title}</div>
          </div>
        )) : <div className="text-[11px] p-2" style={{ color: 'var(--color-dim)' }}>—</div>}
      </div>
    </div>
  )
}

export function BlackboardView({ loop }: { loop: Loop | null }) {
  if (!loop) return <Empty>Select a run to view its blackboard.</Empty>
  const goalCard: Card[] = [{ id: 'goal', title: loop.goal, body: '' }]
  const planCard: Card[] = loop.nextActions.map((a, i) => ({ id: `P${i + 1}`, title: a, body: '' }))
  const researchCard: Card[] = loop.research ? [{ id: 'research', title: loop.research, body: '' }] : []
  const evalCard: Card[] = loop.evaluation ? [{ id: 'eval', title: loop.evaluation, body: '' }] : []
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        <CardCol title="Goal" cards={goalCard} accent="var(--color-accent)" />
        <CardCol title="Decisions" cards={loop.decisions} accent="var(--color-good)" />
        <CardCol title="Risks" cards={loop.risks} accent="var(--color-bad)" />
        <CardCol title="Open questions" cards={loop.openQuestions} accent="var(--color-warn)" />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        <CardCol title="Research" cards={researchCard} accent="var(--color-anti)" />
        <CardCol title="Plan / next actions" cards={planCard} accent="var(--color-codex)" />
        <CardCol title="Evaluation" cards={evalCard} accent="var(--color-accent)" />
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wider mb-1.5" style={{ color: 'var(--color-dim)' }}>Delivery</div>
        <LoopReceiptCard receipt={loop.receipt} />
      </div>
    </div>
  )
}
