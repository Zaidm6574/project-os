import type { Loop } from '../types'
import { EngineBadge, StatusBadge, Field } from '../ui'

// The anti-mush loop header: every loop renders the same structured fields.
export function LoopHeader({ loop }: { loop: Loop | null }) {
  if (!loop) {
    return (
      <div className="card p-4 mb-3" style={{ color: 'var(--color-mut)' }}>
        Select a run to see its loop state.
      </div>
    )
  }
  return (
    <div className="card mb-3 overflow-hidden">
      <div className="card-h px-4 py-2.5 flex items-center gap-2 flex-wrap" style={{ borderBottom: '1px solid var(--color-edge)' }}>
        <EngineBadge engine={loop.engine} />
        <StatusBadge status={loop.status} />
        {loop.tier && <span className="chip">{loop.tier}</span>}
        <span className="font-semibold text-[13px] ml-1 truncate">{loop.title}</span>
        {loop.gate.required && (
          <span className="chip ml-auto" style={{ color: 'var(--color-warn)', borderColor: 'color-mix(in srgb, var(--color-warn) 50%, var(--color-edge))' }}>
            ⛚ approval gate
          </span>
        )}
      </div>

      <div className="px-4 py-3">
        <div className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--color-dim)' }}>Current goal</div>
        <div className="text-[13px] mt-0.5 mb-3" style={{ color: 'var(--color-ink)' }}>{loop.goal}</div>

        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-x-5 gap-y-3">
          <Field label="Next action">{loop.nextAction}</Field>
          <Field label="Verifier">{loop.verifier}</Field>
          <Field label="Stop condition">{loop.stopCondition}</Field>
          <Field label="Cost mode">{loop.costMode}</Field>
          <Field label="Approval gate">
            <span style={{ color: loop.gate.required ? 'var(--color-warn)' : 'var(--color-good)' }}>
              {loop.gate.required ? `required — ${loop.gate.reason}` : 'not required'}
            </span>
          </Field>
        </div>

        {loop.goalHash && (
          <div className="mt-3 text-[10px] mono" style={{ color: 'var(--color-dim)' }}>
            goal hash {loop.goalHash.slice(0, 12)} · drift guard active
          </div>
        )}
      </div>
    </div>
  )
}
