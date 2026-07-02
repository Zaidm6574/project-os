import { useEffect, useState } from 'react'
import type { LoopsState } from '../types'
import { api } from '../api'
import { Empty } from '../ui'

const GAUGE_COLOR: Record<string, string> = {
  ok: 'var(--color-good)',
  watch: 'var(--color-warn, var(--color-accent))',
  cutover: 'var(--color-bad)',
  unknown: 'var(--color-dim)',
}

const PLAN_COLOR: Record<string, string> = {
  planned: 'var(--color-dim)',
  approved: 'var(--color-accent)',
  running: 'var(--color-warn, var(--color-accent))',
  done: 'var(--color-good)',
}

function Section({ title, chip, children }: { title: string; chip?: string; children: React.ReactNode }) {
  return (
    <div className="card overflow-hidden">
      <div className="card-h px-4 py-2.5 flex items-center gap-2" style={{ borderBottom: '1px solid var(--color-edge)' }}>
        <span className="font-semibold text-[13px]">{title}</span>
        {chip && <span className="chip ml-auto">{chip}</span>}
      </div>
      <div className="p-2 space-y-1.5">{children}</div>
    </div>
  )
}

export function LoopsView() {
  const [state, setState] = useState<LoopsState | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => { api.loopsState().then(setState).catch((e) => setErr(String(e))) }, [])

  if (err) return <Empty>Loop state unavailable: {err}</Empty>
  if (!state) return <Empty>Loading loop state…</Empty>

  const { gauge, locks, plans, evolution } = state

  return (
    <div className="space-y-3">
      {/* Brain scale gauge — written nightly by os_nightly.py (launchd ai.projectos.nightly) */}
      <div className="card overflow-hidden">
        <div className="card-h px-4 py-2.5 flex items-center gap-2" style={{ borderBottom: '1px solid var(--color-edge)' }}>
          <span className="w-2 h-2 rounded-full" style={{ background: GAUGE_COLOR[gauge.status] }} />
          <span className="font-semibold text-[13px]">Brain scale gauge</span>
          <span className="chip" style={{ color: GAUGE_COLOR[gauge.status] }}>{gauge.status.toUpperCase()}</span>
          <span className="ml-auto text-[10px] mono" style={{ color: 'var(--color-dim)' }}>
            {gauge.at ? `nightly @ ${gauge.at}` : 'no nightly entry yet — run scripts/os_nightly.py'}
          </span>
        </div>
        <div className="p-3 space-y-1">
          {gauge.lines.length ? gauge.lines.map((l, i) => (
            <div key={i} className="text-[11.5px] mono" style={{ color: 'var(--color-mut)' }}>{l}</div>
          )) : <div className="text-[11px]" style={{ color: 'var(--color-dim)' }}>No gauge data (honest empty state).</div>}
        </div>
      </div>

      {/* Plan artifacts: planned -> approved (human gate) -> running -> done */}
      <Section title="Plans" chip={`${plans.length} artifact${plans.length === 1 ? '' : 's'}`}>
        {plans.length ? plans.map((p) => (
          <div key={p.id} className="rounded-md p-2.5 flex items-center gap-2 flex-wrap" style={{ background: 'var(--color-panel2)', border: '1px solid var(--color-edge)' }}>
            <span className="chip" style={{ color: PLAN_COLOR[p.status] ?? 'var(--color-mut)' }}>{p.status}</span>
            <span className="text-[12px] font-medium mono">{p.id}</span>
            <span className="text-[11.5px]" style={{ color: 'var(--color-mut)' }}>{p.goal}</span>
            <span className="ml-auto text-[11px] mono" style={{ color: p.done === p.total ? 'var(--color-good)' : 'var(--color-dim)' }}>
              {p.done}/{p.total} steps
            </span>
          </div>
        )) : <div className="text-[11px] p-2" style={{ color: 'var(--color-dim)' }}>No plan artifacts yet — create one with <span className="mono">scripts/plan_artifact.py create</span>.</div>}
      </Section>

      {/* Evolution records: always evolve from the best-scoring variant */}
      <Section title="Evolution" chip={`${evolution.length} run${evolution.length === 1 ? '' : 's'}`}>
        {evolution.length ? evolution.map((e) => (
          <div key={e.run} className="rounded-md p-2.5 flex items-center gap-2 flex-wrap" style={{ background: 'var(--color-panel2)', border: '1px solid var(--color-edge)' }}>
            <span className="text-[12px] font-medium mono">{e.run}</span>
            <span className="chip">{e.variants} variant{e.variants === 1 ? '' : 's'}</span>
            {e.best
              ? <span className="chip" style={{ color: 'var(--color-good)' }}>best: {e.best.variant} ({e.best.score})</span>
              : <span className="chip" style={{ color: 'var(--color-dim)' }}>no scored variants</span>}
            <span className="ml-auto text-[10px] mono" style={{ color: 'var(--color-dim)' }}>{e.latest?.slice(0, 16) ?? ''}</span>
          </div>
        )) : <div className="text-[11px] p-2" style={{ color: 'var(--color-dim)' }}>No evolution records yet — evaluators record with <span className="mono">scripts/evolution.py record</span>.</div>}
      </Section>

      {/* Live bb_lock lockfiles (stale after 60s; nightly reaps them) */}
      <Section title="Locks" chip={locks.length ? `${locks.length} held` : 'none held'}>
        {locks.length ? locks.map((l, i) => (
          <div key={i} className="rounded-md p-2.5 flex items-center gap-2" style={{ background: 'var(--color-panel2)', border: '1px solid var(--color-edge)' }}>
            <span className="w-2 h-2 rounded-full" style={{ background: l.stale ? 'var(--color-bad)' : 'var(--color-good)' }} />
            <span className="text-[12px] mono">{l.target}</span>
            <span className="chip">{l.agent}{l.pid ? ` · pid ${l.pid}` : ''}</span>
            <span className="ml-auto text-[11px] mono" style={{ color: l.stale ? 'var(--color-bad)' : 'var(--color-dim)' }}>
              {l.ageSec !== null ? `${l.ageSec}s${l.stale ? ' · STALE' : ''}` : '?'}
            </span>
          </div>
        )) : <div className="text-[11px] p-2" style={{ color: 'var(--color-dim)' }}>No locks held — blackboard is uncontended.</div>}
      </Section>
    </div>
  )
}
