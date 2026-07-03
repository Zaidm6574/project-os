import { useCallback, useEffect, useState } from 'react'
import type { EngineInfo, GraphNode, Loop, ViewKey } from './types'
import { api } from './api'
import { Sidebar } from './components/Sidebar'
import { LoopHeader } from './components/LoopHeader'
import { RunsDashboard } from './components/RunsDashboard'
import { BlackboardView } from './components/BlackboardView'
import { BrainView } from './components/BrainView'
import { LoopsView } from './components/LoopsView'
import { GraphView } from './components/GraphView'
import { DetailPanel } from './components/DetailPanel'
import { LoopReceiptCard } from './components/LoopReceipt'
import { ResearchView, InboxView, SettingsView } from './components/SimpleViews'
import { CommandBar } from './components/CommandBar'
import { THEMES, applyTheme, loadTheme, saveTheme } from './theme'
import type { Theme } from './theme'

type RunTab = 'dashboard' | 'blackboard' | 'receipt'

const TITLES: Record<ViewKey, string> = {
  projects: 'Projects', runs: 'Runs', loops: 'Loops', brain: 'Brain', research: 'Research',
  inbox: 'Inbox', graph: 'Graph', settings: 'Settings',
}

// Deep-link support: /?view=loops opens that tab directly (also lets headless
// browsers screenshot any view for verification).
function initialView(): ViewKey {
  const v = new URLSearchParams(window.location.search).get('view')
  return v && v in TITLES ? (v as ViewKey) : 'runs'
}

export function App() {
  const [view, setView] = useState<ViewKey>(initialView)
  const [loops, setLoops] = useState<Loop[]>([])
  const [engines, setEngines] = useState<EngineInfo[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [fullLoop, setFullLoop] = useState<Loop | null>(null)
  const [node, setNode] = useState<GraphNode | null>(null)
  const [detailOpen, setDetailOpen] = useState(true)
  const [runTab, setRunTab] = useState<RunTab>('dashboard')
  const [projects, setProjects] = useState<Loop[] | null>(null) // lazy: external/iCloud projects
  const [err, setErr] = useState('')
  const [theme, setTheme] = useState<Theme>(() => loadTheme())
  const [themePickerOpen, setThemePickerOpen] = useState(false)

  // Apply theme on mount and whenever it changes
  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  function selectTheme(t: Theme) {
    setTheme(t)
    saveTheme(t)
    applyTheme(t)
    setThemePickerOpen(false)
  }

  const loadRuns = useCallback(async () => {
    try {
      const ls = await api.runs()
      setLoops(ls)
      setSelectedId((cur) => cur ?? ls.find((l) => l.id === 'project-os-command-center')?.id ?? ls.find((l) => l.kind === 'run')?.id ?? ls[0]?.id ?? null)
    } catch (e) { setErr(String(e)) }
  }, [])

  useEffect(() => { loadRuns(); api.engines().then(setEngines).catch(() => {}) }, [loadRuns])

  useEffect(() => {
    if (!selectedId) { setFullLoop(null); return }
    api.run(selectedId).then(setFullLoop).catch(() => setFullLoop(null))
  }, [selectedId])

  // Lazy-load external/iCloud projects only when the Projects tab is first opened.
  useEffect(() => {
    if (view === 'projects' && projects === null) {
      api.projects().then(setProjects).catch(() => setProjects([]))
    }
  }, [view, projects])

  function selectRun(id: string) { setSelectedId(id); setNode(null); setDetailOpen(true) }
  function selectNode(n: GraphNode) { setNode(n); setDetailOpen(true) }

  const runLoops = loops.filter((l) => l.kind === 'run')

  return (
    <div className="h-screen w-screen grid" style={{ gridTemplateColumns: detailOpen ? '218px 1fr 348px' : '218px 1fr' }}>
      <Sidebar view={view} setView={setView} engines={engines} />

      <div className="flex flex-col min-w-0 h-screen">
        <header
          className="px-4 py-2 flex items-center gap-3 shrink-0"
          style={{ borderBottom: '1px solid var(--color-edge)', background: 'var(--color-panel)' }}
        >
          <span className="font-semibold text-[14px] tracking-tight">{TITLES[view]}</span>

          {(view === 'runs') && runLoops.length > 0 && (
            <select
              value={selectedId ?? ''}
              onChange={(e) => selectRun(e.target.value)}
              className="mono text-[11.5px] px-2 py-1 rounded-md outline-none focus-ring"
              style={{
                background: 'var(--color-panel2)',
                border: '1px solid var(--color-edge)',
                color: 'var(--color-ink)',
              }}
            >
              {runLoops.map((l) => <option key={l.id} value={l.id}>{l.title}</option>)}
            </select>
          )}

          <div className="ml-auto flex items-center gap-2">
            {/* Theme picker */}
            <div className="relative">
              <button
                className="btn flex items-center gap-1.5"
                title={`Theme: ${theme.name}`}
                onClick={() => setThemePickerOpen((o) => !o)}
                style={{ paddingLeft: 8, paddingRight: 8 }}
              >
                <span
                  style={{
                    display: 'inline-block',
                    width: 10,
                    height: 10,
                    borderRadius: '50%',
                    background: theme.vars['--color-accent'],
                    boxShadow: `0 0 0 2px color-mix(in srgb, ${theme.vars['--color-accent']} 35%, transparent)`,
                  }}
                />
                <span style={{ color: 'var(--color-mut)', fontSize: 11 }}>{theme.name}</span>
              </button>

              {themePickerOpen && (
                <>
                  {/* Dismiss backdrop */}
                  <div
                    className="fixed inset-0 z-40"
                    onClick={() => setThemePickerOpen(false)}
                  />
                  <div
                    className="absolute right-0 top-full mt-1.5 z-50 rounded-xl p-2 min-w-[168px]"
                    style={{
                      background: 'var(--color-panel)',
                      border: '1px solid var(--color-edge)',
                      boxShadow: '0 8px 24px rgba(0,0,0,0.35), 0 2px 8px rgba(0,0,0,0.2)',
                    }}
                  >
                    {THEMES.map((t) => (
                      <button
                        key={t.id}
                        onClick={() => selectTheme(t)}
                        className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left text-[12px] transition-colors"
                        style={{
                          background: theme.id === t.id ? 'var(--color-panel2)' : 'transparent',
                          color: theme.id === t.id ? 'var(--color-ink)' : 'var(--color-mut)',
                        }}
                      >
                        {/* Accent + good + bad swatch trio */}
                        <span className="flex gap-1 shrink-0">
                          <span style={{ display: 'block', width: 8, height: 8, borderRadius: '50%', background: t.vars['--color-accent'] }} />
                          <span style={{ display: 'block', width: 8, height: 8, borderRadius: '50%', background: t.vars['--color-good'] }} />
                          <span style={{ display: 'block', width: 8, height: 8, borderRadius: '50%', background: t.vars['--color-bad'] }} />
                        </span>
                        {t.name}
                        {theme.id === t.id && (
                          <span className="ml-auto text-[10px]" style={{ color: 'var(--color-accent)' }}>✓</span>
                        )}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>

            <button className="btn" onClick={loadRuns} title="Refresh data" style={{ fontSize: 14, padding: '3px 8px' }}>⟳</button>
            <button
              className="btn"
              onClick={() => setDetailOpen((o) => !o)}
              style={{ color: detailOpen ? 'var(--color-accent)' : 'var(--color-mut)' }}
            >
              {detailOpen ? 'Hide detail' : 'Show detail'}
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto p-4 min-w-0">
          {err && (
            <div
              className="card p-3 mb-3 text-[12px]"
              style={{ color: 'var(--color-bad)', borderColor: 'color-mix(in srgb, var(--color-bad) 35%, var(--color-edge))' }}
            >
              Adapter error: {err}. Is the server running on :4317? (<span className="mono">npm run dev</span>)
            </div>
          )}

          {view === 'runs' && (
            <>
              <LoopHeader loop={fullLoop} />
              <div className="flex gap-1 mb-3">
                {(['dashboard', 'blackboard', 'receipt'] as RunTab[]).map((t) => (
                  <button
                    key={t}
                    className="btn"
                    onClick={() => setRunTab(t)}
                    style={{
                      borderColor: runTab === t ? 'var(--color-accent)' : 'var(--color-edge)',
                      color: runTab === t ? 'var(--color-accent)' : 'var(--color-mut)',
                      background: runTab === t ? 'color-mix(in srgb, var(--color-accent) 10%, var(--color-panel2))' : 'var(--color-panel2)',
                    }}
                  >
                    {t}
                  </button>
                ))}
              </div>
              {runTab === 'dashboard' && <RunsDashboard loops={runLoops} selectedId={selectedId} onSelect={selectRun} title="Supervised runs" />}
              {runTab === 'blackboard' && <BlackboardView loop={fullLoop} />}
              {runTab === 'receipt' && <LoopReceiptCard receipt={fullLoop?.receipt ?? null} />}
            </>
          )}

          {view === 'projects' && (
            projects === null
              ? <div className="card p-6 text-center" style={{ color: 'var(--color-mut)' }}>Loading projects across engines… (first read of iCloud/Documents may take a few seconds)</div>
              : <RunsDashboard loops={projects} selectedId={selectedId} onSelect={selectRun} grouped title="All projects" />
          )}
          {view === 'loops' && <LoopsView />}
          {view === 'brain' && <BrainView />}
          {view === 'research' && <ResearchView />}
          {view === 'inbox' && <InboxView />}
          {view === 'graph' && <GraphView onSelectNode={selectNode} />}
          {view === 'settings' && <SettingsView engines={engines} onSelectTheme={selectTheme} currentTheme={theme} />}
        </main>

        <CommandBar runId={selectedId} onChanged={loadRuns} />
      </div>

      {detailOpen && (
        <DetailPanel
          loop={fullLoop}
          node={node}
          onClose={() => setDetailOpen(false)}
          onSave={() => {
            const ev = new CustomEvent('cc-save')
            window.dispatchEvent(ev)
          }}
        />
      )}
    </div>
  )
}
