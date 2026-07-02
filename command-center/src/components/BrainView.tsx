import { useEffect, useState } from 'react'
import type { BrainStatus } from '../types'
import { api } from '../api'
import { Empty } from '../ui'

const STATUS_COLOR: Record<string, string> = {
  available: 'var(--color-good)',
  configured: 'var(--color-accent)',
  'present-unwired': 'var(--color-warn)',
  'not-configured': 'var(--color-dim)',
  'not-found': 'var(--color-dim)',
}

export function BrainView() {
  const [brain, setBrain] = useState<BrainStatus | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => { api.brain().then(setBrain).catch((e) => setErr(String(e))) }, [])

  if (err) return <Empty>Brain status unavailable: {err}</Empty>
  if (!brain) return <Empty>Loading brain status…</Empty>

  return (
    <div className="space-y-3">
      {/* Honest capability health card (Q12: single card in slice 1) */}
      <div className="card overflow-hidden">
        <div className="card-h px-4 py-2.5 flex items-center gap-2" style={{ borderBottom: '1px solid var(--color-edge)' }}>
          <span className="font-semibold text-[13px]">Capability &amp; memory health</span>
          <span className="ml-auto chip">honest status — no faked health</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-px" style={{ background: 'var(--color-edge)' }}>
          {brain.capabilities.map((c) => (
            <div key={c.name} className="p-3" style={{ background: 'var(--color-panel)' }}>
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full" style={{ background: STATUS_COLOR[c.status] ?? 'var(--color-mut)' }} />
                <span className="text-[12.5px] font-medium">{c.name}</span>
                <span className="chip ml-auto" style={{ color: STATUS_COLOR[c.status] ?? 'var(--color-mut)' }}>{c.status}</span>
              </div>
              <div className="text-[11px] mt-1 ml-4" style={{ color: 'var(--color-dim)' }}>{c.evidence}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Shared brain lessons (Codex⇄Claude bridge) */}
      <div className="card overflow-hidden">
        <div className="card-h px-4 py-2.5 flex items-center gap-2 flex-wrap" style={{ borderBottom: '1px solid var(--color-edge)' }}>
          <span className="font-semibold text-[13px]">Lessons</span>
          <span className="chip">{brain.sharedBrain.total} total</span>
          {Object.entries(brain.sharedBrain.byType).map(([t, n]) => (
            <span key={t} className="chip">{t}: {n}</span>
          ))}
          <span className="ml-auto text-[10px] mono" style={{ color: 'var(--color-dim)' }}>
            {brain.sharedBrain.present ? 'shared-brain.jsonl' : 'no shared brain'}
          </span>
        </div>
        <div className="p-2 space-y-1.5">
          {brain.sharedBrain.lessons.length ? brain.sharedBrain.lessons.map((l, i) => (
            <div key={i} className="rounded-md p-2.5" style={{ background: 'var(--color-panel2)', border: '1px solid var(--color-edge)' }}>
              <div className="flex items-center gap-2 mb-1">
                <span className="chip" style={{ color: 'var(--color-codex)' }}>{l.type}</span>
                {l.project && <span className="chip">{l.project}</span>}
                <span className="ml-auto text-[10px]" style={{ color: 'var(--color-dim)' }}>{l.ts?.slice(0, 10)}</span>
              </div>
              <div className="text-[11.5px]" style={{ color: 'var(--color-mut)' }}>{l.text}</div>
            </div>
          )) : <div className="text-[11px] p-2" style={{ color: 'var(--color-dim)' }}>No lessons stored yet.</div>}
        </div>
      </div>
    </div>
  )
}
