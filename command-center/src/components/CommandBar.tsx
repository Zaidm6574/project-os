import { useEffect, useState } from 'react'
import type { ReactNode, FormEvent } from 'react'
import { api, type CommandResult } from '../api'

const COMMANDS = ['/kickoff', '/research', '/save', '/brain', '/prune', '/lint', '/trust', '/reality']

const HINT: Record<string, string> = {
  '/kickoff': 'start a new supervised run', '/research': 'spawn a researcher wave',
  '/save': 'write a note to the active run', '/brain': 'capability + memory health',
  '/prune': 'memory cleanup suggestions', '/lint': 'lint the active run',
  '/trust': 'security / trust review', '/reality': 'capability reality check',
}

function ResultBody({ r }: { r: CommandResult }) {
  if (r.mode === 'executed' && r.command === '/lint') {
    const issues = (r.result as any)?.issues as string[] | undefined
    return <ul className="space-y-1">{(issues || []).map((i, k) => <li key={k} className="text-[12px]" style={{ color: 'var(--color-mut)' }}>• {i}</li>)}</ul>
  }
  if (r.mode === 'executed' && (r.command === '/brain' || r.command === '/reality')) {
    const caps = (r.result as any)?.capabilities as { name: string; status: string }[] | undefined
    return <ul className="space-y-1">{(caps || []).map((c, k) => <li key={k} className="text-[12px]" style={{ color: 'var(--color-mut)' }}>• {c.name}: <b>{c.status}</b></li>)}</ul>
  }
  if (r.mode === 'executed' && r.command === '/prune') {
    const s = (r.result as any)?.suggestions as string[] | undefined
    return <ul className="space-y-1">{(s || []).map((i, k) => <li key={k} className="text-[12px]" style={{ color: 'var(--color-mut)' }}>• {i}</li>)}</ul>
  }
  if (r.mode === 'intent-queued') return <div className="text-[12px]" style={{ color: 'var(--color-good)' }}>Intent queued → {r.wrote}<div className="text-[11px] mt-1" style={{ color: 'var(--color-dim)' }}>{r.note}</div></div>
  if (r.mode === 'executed' && r.wrote) return <div className="text-[12px]" style={{ color: 'var(--color-good)' }}>Wrote {r.wrote}</div>
  if (r.mode === 'refused' || r.mode === 'error') return <div className="text-[12px]" style={{ color: 'var(--color-bad)' }}>{r.reason}</div>
  return <div className="text-[12px]" style={{ color: 'var(--color-mut)' }}>Done.</div>
}

export function CommandBar({ runId, onChanged }: { runId: string | null; onChanged: () => void }) {
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<CommandResult | null>(null)
  const [gate, setGate] = useState<CommandResult | null>(null)
  const [pendingCmd, setPendingCmd] = useState('')
  const [saveText, setSaveText] = useState('')
  const [saveFor, setSaveFor] = useState('')

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); document.getElementById('cmd-input')?.focus() }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])

  // DetailPanel "Save note" button dispatches this event.
  useEffect(() => {
    const h = () => { setSaveFor(runId || ''); setSaveText('') }
    window.addEventListener('cc-save', h)
    return () => window.removeEventListener('cc-save', h)
  }, [runId])

  async function run(command: string) {
    if (command === '/save') { setSaveFor(runId || ''); setSaveText(''); return }
    setBusy(true); setResult(null)
    try {
      const r = await api.command({ command, runId: runId || undefined })
      if (r.mode === 'gate') { setGate(r); setPendingCmd(command) }
      else { setResult(r); onChanged() }
    } catch (e) { setResult({ command, mode: 'error', reason: String(e) }) }
    finally { setBusy(false) }
  }

  async function confirmGate() {
    if (!pendingCmd) return
    setBusy(true)
    try {
      const r = await api.command({ command: pendingCmd, runId: runId || undefined, confirm: true })
      setResult(r); onChanged()
    } catch (e) { setResult({ command: pendingCmd, mode: 'error', reason: String(e) }) }
    finally { setBusy(false); setGate(null); setPendingCmd('') }
  }

  async function confirmSave() {
    setBusy(true)
    try {
      const r = await api.command({ command: '/save', runId: saveFor || undefined, confirm: true, payload: { text: saveText } })
      setResult(r); onChanged()
    } catch (e) { setResult({ command: '/save', mode: 'error', reason: String(e) }) }
    finally { setBusy(false); setSaveFor(''); setSaveText('') }
  }

  function submitInput(e: FormEvent) {
    e.preventDefault()
    const c = input.trim().split(/\s+/)[0]
    if (COMMANDS.includes(c)) run(c)
    else setResult({ command: c || '?', mode: 'error', reason: `unknown command. try: ${COMMANDS.join(' ')}` })
    setInput('')
  }

  return (
    <>
      <div className="px-3 py-2 flex items-center gap-2" style={{ background: 'var(--color-panel)', borderTop: '1px solid var(--color-edge)' }}>
        <div className="flex gap-1 flex-wrap">
          {COMMANDS.map((c) => (
            <button key={c} className="btn mono" title={HINT[c]} disabled={busy} onClick={() => run(c)}>{c}</button>
          ))}
        </div>
        <form onSubmit={submitInput} className="ml-auto flex-1 max-w-md flex items-center gap-2">
          <input id="cmd-input" value={input} onChange={(e) => setInput(e.target.value)}
            placeholder="type a command…  (⌘K)"
            className="mono w-full px-3 py-1.5 rounded-md text-[12px] outline-none"
            style={{ background: 'var(--color-panel2)', border: '1px solid var(--color-edge)', color: 'var(--color-ink)' }} />
        </form>
      </div>

      {/* Gate modal */}
      {gate && gate.gate && (
        <Overlay onClose={() => setGate(null)}>
          <div className="flex items-center gap-2 mb-2">
            <span style={{ color: 'var(--color-warn)' }}>⛚</span>
            <span className="font-semibold text-[13px]">{gate.gate.title}</span>
          </div>
          <div className="text-[12px] mb-3" style={{ color: 'var(--color-mut)' }}>{gate.gate.desc}</div>
          <div className="grid grid-cols-2 gap-y-2 gap-x-4 text-[12px] mb-4">
            <Kv k="Command" v={<span className="mono">{pendingCmd}</span>} />
            <Kv k="Engine" v={gate.gate.engine} />
            <Kv k="Target" v={<span className="mono text-[11px]">{gate.gate.target}</span>} />
            <Kv k="Side effects" v={gate.gate.sideEffects} />
            <Kv k="Estimate" v={gate.gate.estimate} />
          </div>
          <div className="flex gap-2 justify-end">
            <button className="btn" onClick={() => setGate(null)}>Cancel</button>
            <button className="btn btn-warn" disabled={busy} onClick={confirmGate}>{gate.gate.confirmLabel}</button>
          </div>
        </Overlay>
      )}

      {/* Save modal */}
      {saveFor !== '' && (
        <Overlay onClose={() => setSaveFor('')}>
          <div className="font-semibold text-[13px] mb-1">Save note → <span className="mono">{saveFor || '(no run)'}</span></div>
          <div className="text-[11px] mb-2" style={{ color: 'var(--color-dim)' }}>Writes one markdown file under <span className="mono">runs/&lt;run&gt;/packets/</span> (gated, allowlisted).</div>
          <textarea value={saveText} onChange={(e) => setSaveText(e.target.value)} rows={4}
            className="w-full p-2 rounded-md text-[12px] outline-none mb-3"
            style={{ background: 'var(--color-panel2)', border: '1px solid var(--color-edge)', color: 'var(--color-ink)' }} placeholder="note text…" />
          <div className="flex gap-2 justify-end">
            <button className="btn" onClick={() => setSaveFor('')}>Cancel</button>
            <button className="btn btn-go" disabled={busy || !saveText.trim() || !saveFor} onClick={confirmSave}>Write file</button>
          </div>
        </Overlay>
      )}

      {/* Result modal */}
      {result && (
        <Overlay onClose={() => setResult(null)}>
          <div className="flex items-center gap-2 mb-2">
            <span className="mono text-[12px]">{result.command}</span>
            <span className="chip">{result.mode}</span>
          </div>
          <ResultBody r={result} />
          <div className="flex justify-end mt-4"><button className="btn" onClick={() => setResult(null)}>Close</button></div>
        </Overlay>
      )}
    </>
  )
}

function Kv({ k, v }: { k: string; v: ReactNode }) {
  return <div><span style={{ color: 'var(--color-dim)' }}>{k}: </span><span style={{ color: 'var(--color-ink)' }}>{v}</span></div>
}

function Overlay({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.55)' }} onClick={onClose}>
      <div className="card p-4 w-full max-w-md" style={{ background: 'var(--color-panel)' }} onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>
  )
}
