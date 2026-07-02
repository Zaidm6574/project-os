import { useEffect, useState } from 'react'
import type { Item, EngineInfo } from '../types'
import { api } from '../api'
import { Empty } from '../ui'
import { THEMES } from '../theme'
import type { Theme } from '../theme'

export function ResearchView() {
  const [cards, setCards] = useState<{ run: string; engine: string; question: string }[]>([])
  const [items, setItems] = useState<Item[]>([])
  const [total, setTotal] = useState(0)
  const [note, setNote] = useState('')
  useEffect(() => {
    api.research().then((r) => { setCards(r.cards); setNote(r.note) }).catch(() => {})
    api.items(40, 0).then((r) => { setItems(r.items); setTotal(r.total) }).catch(() => {})
  }, [])
  return (
    <div className="space-y-3">
      <div className="card overflow-hidden">
        <div className="card-h px-4 py-2.5 flex items-center gap-2" style={{ borderBottom: '1px solid var(--color-edge)' }}>
          <span className="font-semibold text-[13px]">Research questions</span>
          <span className="chip">{cards.length}</span>
        </div>
        <div className="p-2 space-y-1.5">
          {cards.length ? cards.map((c, i) => (
            <div
              key={i}
              className="rounded-lg p-2 flex items-center gap-2"
              style={{ background: 'var(--color-panel2)', border: '1px solid var(--color-edge)' }}
            >
              <span className="chip">{c.engine}</span>
              <span className="text-[12px]" style={{ color: 'var(--color-ink)' }}>{c.question}</span>
              <span className="ml-auto text-[10px] mono" style={{ color: 'var(--color-dim)' }}>{c.run}</span>
            </div>
          )) : (
            <div className="text-[11px] p-2" style={{ color: 'var(--color-dim)' }}>{note || 'No research items.'}</div>
          )}
        </div>
      </div>

      <div className="card overflow-hidden">
        <div className="card-h px-4 py-2.5 flex items-center gap-2" style={{ borderBottom: '1px solid var(--color-edge)' }}>
          <span className="font-semibold text-[13px]">Knowledge base (second brain)</span>
          <span className="chip">{total} items · showing {items.length}</span>
          <span className="ml-auto text-[10px]" style={{ color: 'var(--color-dim)' }}>embeddings &amp; raw vectors stripped</span>
        </div>
        <div className="p-2 grid grid-cols-1 md:grid-cols-2 gap-1.5">
          {items.map((it, i) => (
            <div
              key={it.id || i}
              className="rounded-lg p-2.5"
              style={{ background: 'var(--color-panel2)', border: '1px solid var(--color-edge)' }}
            >
              <div className="flex items-center gap-2 mb-1">
                {it.domain && <span className="chip">{it.domain}</span>}
                {it.source && <span className="chip">{it.source}</span>}
              </div>
              <div className="text-[12px] font-medium line-clamp-1" style={{ color: 'var(--color-ink)' }}>
                {it.title || '(untitled)'}
              </div>
              {it.summary && (
                <div
                  className="text-[11px] mt-0.5"
                  style={{ color: 'var(--color-mut)', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}
                >
                  {it.summary}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export function InboxView() {
  const [note, setNote] = useState('')
  useEffect(() => { api.inbox().then((r) => setNote(r.note)).catch(() => setNote('Inbox unavailable')) }, [])
  return <Empty>&#128229; {note || 'Inbox is empty.'}</Empty>
}

export function SettingsView({
  engines,
  onSelectTheme,
  currentTheme,
}: {
  engines: EngineInfo[]
  onSelectTheme?: (t: Theme) => void
  currentTheme?: Theme
}) {
  const rows = [
    ['App', 'Project OS Command Center — slice 1'],
    ['Mode', 'local-first · read-only adapter · 127.0.0.1:4317'],
    ['Spines', 'Project OS runs/blackboard + second-brain links.json + tracked projects'],
  ]
  return (
    <div className="space-y-3">
      <div className="card p-4">
        <div className="font-semibold text-[13px] mb-2.5">Configuration</div>
        <div className="space-y-0">
          {rows.map(([k, v]) => (
            <div
              key={k}
              className="flex gap-3 text-[12px] py-2"
              style={{ borderBottom: '1px solid var(--color-edge)' }}
            >
              <span className="w-24 shrink-0" style={{ color: 'var(--color-dim)' }}>{k}</span>
              <span style={{ color: 'var(--color-ink)' }}>{v}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Theme picker panel */}
      {onSelectTheme && (
        <div className="card p-4">
          <div className="font-semibold text-[13px] mb-2.5">Theme</div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            {THEMES.map((t) => {
              const active = currentTheme?.id === t.id
              return (
                <button
                  key={t.id}
                  onClick={() => onSelectTheme(t)}
                  className="rounded-lg p-2.5 text-left transition-all"
                  style={{
                    background: active ? 'color-mix(in srgb, var(--color-accent) 10%, var(--color-panel2))' : 'var(--color-panel2)',
                    border: `1px solid ${active ? 'var(--color-accent)' : 'var(--color-edge)'}`,
                    outline: 'none',
                  }}
                >
                  {/* Color preview swatches */}
                  <div className="flex gap-1 mb-1.5">
                    {(['--color-bg', '--color-accent', '--color-good', '--color-bad', '--color-codex'] as const).map((key) => (
                      <span
                        key={key}
                        style={{
                          display: 'block',
                          width: 10,
                          height: 10,
                          borderRadius: '50%',
                          background: t.vars[key],
                          border: '1px solid rgba(255,255,255,0.08)',
                        }}
                      />
                    ))}
                  </div>
                  <div
                    className="text-[11.5px] font-medium truncate"
                    style={{ color: active ? 'var(--color-accent)' : 'var(--color-ink)' }}
                  >
                    {t.name}
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      )}

      <div className="card p-4">
        <div className="font-semibold text-[13px] mb-2.5">Privacy allowlist <span className="chip ml-1">enforced server-side</span></div>
        <div className="space-y-1.5">
          <div className="text-[12px] rounded-md p-2.5" style={{ background: 'color-mix(in srgb, var(--color-good) 8%, var(--color-panel2))', border: '1px solid color-mix(in srgb, var(--color-good) 25%, var(--color-edge))', color: 'var(--color-good)' }}>
            READ: project-os runs/blackboard/memory · second-brain links.json + analyzed/items.json (stripped) · gephi CSV · shared-brain.jsonl · antigravity *.md notes · tracked project plans/specs/README
          </div>
          <div className="text-[12px] rounded-md p-2.5" style={{ background: 'color-mix(in srgb, var(--color-bad) 8%, var(--color-panel2))', border: '1px solid color-mix(in srgb, var(--color-bad) 25%, var(--color-edge))', color: 'var(--color-bad)' }}>
            NEVER: .system_generated/messages · embeddings_cache.json · staged/ · *.db/*.sqlite · logs · raw chats · the Codex ult-guide path is never written
          </div>
        </div>
      </div>

      <div className="card p-4">
        <div className="font-semibold text-[13px] mb-2.5">Engines</div>
        <div className="grid grid-cols-2 gap-2">
          {engines.map((e) => (
            <div
              key={e.id}
              className="flex items-center gap-2.5 text-[12px] rounded-lg px-3 py-2"
              style={{ background: 'var(--color-panel2)', border: '1px solid var(--color-edge)' }}
            >
              <span
                style={{
                  display: 'inline-block',
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: e.present ? 'var(--color-good)' : 'var(--color-dim)',
                  boxShadow: e.present ? '0 0 6px var(--color-good)' : 'none',
                }}
              />
              <span style={{ color: 'var(--color-ink)' }}>{e.label}</span>
              <span className="ml-auto text-[10px]" style={{ color: e.present ? 'var(--color-good)' : 'var(--color-dim)' }}>
                {e.present ? 'live' : '—'}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
