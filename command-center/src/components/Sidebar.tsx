import type { EngineInfo, ViewKey } from '../types'
import { Icon } from '../ui'

const NAV: { key: ViewKey; label: string; icon: string }[] = [
  { key: 'projects', label: 'Projects', icon: 'projects' },
  { key: 'runs', label: 'Runs', icon: 'runs' },
  { key: 'loops', label: 'Loops', icon: 'runs' },
  { key: 'brain', label: 'Brain', icon: 'brain' },
  { key: 'research', label: 'Research', icon: 'research' },
  { key: 'inbox', label: 'Inbox', icon: 'inbox' },
  { key: 'graph', label: 'Graph', icon: 'graph' },
  { key: 'settings', label: 'Settings', icon: 'settings' },
]

export function Sidebar({
  view, setView, engines,
}: { view: ViewKey; setView: (v: ViewKey) => void; engines: EngineInfo[] }) {
  return (
    <aside
      className="h-full flex flex-col"
      style={{ background: 'var(--color-panel)', borderRight: '1px solid var(--color-edge)' }}
    >
      {/* Logo / wordmark */}
      <div className="px-4 py-3" style={{ borderBottom: '1px solid var(--color-edge)' }}>
        <div className="flex items-center gap-2.5">
          <div
            className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0"
            style={{
              background: 'linear-gradient(135deg, var(--color-accent), color-mix(in srgb, var(--color-accent) 60%, var(--color-codex)))',
              boxShadow: '0 2px 8px color-mix(in srgb, var(--color-accent) 40%, transparent)',
            }}
          >
            <Icon name="terminal" size={14} />
          </div>
          <div>
            <div className="font-semibold text-[13px] leading-tight tracking-tight" style={{ color: 'var(--color-ink)' }}>
              Project OS
            </div>
            <div className="text-[10px] tracking-wide" style={{ color: 'var(--color-dim)' }}>Command Center</div>
          </div>
        </div>
      </div>

      {/* Nav items */}
      <nav className="flex-1 p-2 space-y-px overflow-y-auto">
        {NAV.map((n) => {
          const active = view === n.key
          return (
            <button
              key={n.key}
              onClick={() => setView(n.key)}
              className="w-full flex items-center gap-2.5 px-2.5 py-[7px] rounded-lg text-left text-[12.5px] transition-colors"
              style={{
                background: active ? 'var(--color-panel2)' : 'transparent',
                color: active ? 'var(--color-ink)' : 'var(--color-mut)',
                borderLeft: `2px solid ${active ? 'var(--color-accent)' : 'transparent'}`,
                fontWeight: active ? 500 : 400,
              }}
            >
              <span style={{ color: active ? 'var(--color-accent)' : 'inherit', opacity: active ? 1 : 0.7 }}>
                <Icon name={n.icon} size={15} />
              </span>
              {n.label}
            </button>
          )
        })}
      </nav>

      {/* Engine status footer */}
      <div className="p-3 space-y-1.5" style={{ borderTop: '1px solid var(--color-edge)' }}>
        <div
          className="text-[10px] uppercase tracking-widest mb-2"
          style={{ color: 'var(--color-dim)', letterSpacing: '0.1em' }}
        >
          Engines
        </div>
        {engines.length ? engines.map((e) => (
          <div key={e.id} className="flex items-center justify-between text-[11px]">
            <span style={{ color: 'var(--color-mut)' }}>{e.label}</span>
            <span
              className="flex items-center gap-1"
              style={{ color: e.present ? 'var(--color-good)' : 'var(--color-dim)' }}
            >
              <span
                style={{
                  display: 'inline-block',
                  width: 5,
                  height: 5,
                  borderRadius: '50%',
                  background: e.present ? 'var(--color-good)' : 'var(--color-dim)',
                  boxShadow: e.present ? '0 0 5px var(--color-good)' : 'none',
                }}
              />
              {e.present ? 'live' : '—'}
            </span>
          </div>
        )) : (
          <div className="text-[11px]" style={{ color: 'var(--color-dim)' }}>No engines configured</div>
        )}
        <div className="text-[10px] pt-1" style={{ color: 'var(--color-dim)', borderTop: '1px solid var(--color-edge)', marginTop: 6 }}>
          local-first · read-only adapter
        </div>
      </div>
    </aside>
  )
}
