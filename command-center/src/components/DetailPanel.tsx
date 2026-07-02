import type { Loop, GraphNode } from '../types'
import { EngineBadge, StatusBadge, Field, Icon } from '../ui'
import { LoopReceiptCard } from './LoopReceipt'

export function DetailPanel({
  loop, node, onClose, onSave,
}: {
  loop: Loop | null
  node: GraphNode | null
  onClose: () => void
  onSave: () => void
}) {
  return (
    <aside className="h-full flex flex-col" style={{ background: 'var(--color-panel)', borderLeft: '1px solid var(--color-edge)' }}>
      <div className="px-3 py-2.5 flex items-center gap-2" style={{ borderBottom: '1px solid var(--color-edge)' }}>
        <span className="text-[11px] uppercase tracking-wider" style={{ color: 'var(--color-dim)' }}>Detail</span>
        <button className="btn ml-auto" onClick={onClose}>✕</button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {node && (
          <div className="card p-3">
            <div className="text-[11px] uppercase tracking-wider mb-1" style={{ color: 'var(--color-dim)' }}>Graph node</div>
            <div className="text-[13px] font-semibold">{node.name}</div>
            <div className="flex gap-2 mt-2">
              <span className="chip">{node.type || 'node'}</span>
              <span className="chip">val {node.val}</span>
            </div>
            <div className="text-[10px] mono mt-2" style={{ color: 'var(--color-dim)' }}>{node.id}</div>
          </div>
        )}

        {!loop && !node && (
          <div className="card p-4 text-[12px]" style={{ color: 'var(--color-mut)' }}>
            Select a run or a graph node.
          </div>
        )}

        {loop && (
          <>
            <div className="card p-3">
              <div className="flex items-center gap-1.5 mb-2 flex-wrap">
                <EngineBadge engine={loop.engine} />
                <StatusBadge status={loop.status} />
                {loop.tier && <span className="chip">{loop.tier}</span>}
              </div>
              <div className="text-[13.5px] font-semibold mb-1.5">{loop.title}</div>
              <div className="text-[12px] mb-3" style={{ color: 'var(--color-mut)' }}>{loop.goal}</div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-2.5">
                <Field label="Next action">{loop.nextAction}</Field>
                <Field label="Cost mode">{loop.costMode}</Field>
                <Field label="Verifier">{loop.verifier}</Field>
                <Field label="Stop">{loop.stopCondition}</Field>
              </div>
              <div className="mt-3 pt-2.5 text-[10px] mono" style={{ borderTop: '1px solid var(--color-edge)', color: 'var(--color-dim)' }}>
                {loop.source.path}
              </div>
            </div>

            {loop.links.length > 0 && (
              <div className="card p-3">
                <div className="text-[11px] uppercase tracking-wider mb-1.5" style={{ color: 'var(--color-dim)' }}>Links</div>
                <div className="flex flex-wrap gap-1.5">
                  {loop.links.map((l, i) => (
                    <span key={i} className="chip"><Icon name="graph" size={11} /> <span className="ml-1">{l.kind}: {l.label}</span></span>
                  ))}
                </div>
              </div>
            )}

            <LoopReceiptCard receipt={loop.receipt} />

            <button className="btn w-full" onClick={onSave}>＋ Save note to this run (/save · gated)</button>
          </>
        )}
      </div>
    </aside>
  )
}
