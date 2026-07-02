import type { LoopReceipt as Receipt } from '../types'

function List({ label, items, color }: { label: string; items: string[]; color: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider mb-1" style={{ color: 'var(--color-dim)' }}>{label}</div>
      {items.length ? (
        <ul className="space-y-0.5">
          {items.map((it, i) => (
            <li key={i} className="text-[11.5px] flex gap-1.5" style={{ color: 'var(--color-mut)' }}>
              <span style={{ color }}>•</span>
              <span className="min-w-0">{it}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="text-[11px]" style={{ color: 'var(--color-dim)' }}>—</div>
      )}
    </div>
  )
}

// Loop receipt: what ran · what tools checked · what failed · what changed · what got saved.
export function LoopReceiptCard({ receipt }: { receipt: Receipt | null }) {
  if (!receipt) {
    return (
      <div className="card p-4 text-[12px]" style={{ color: 'var(--color-mut)' }}>
        No loop receipt yet — generated at run close.
      </div>
    )
  }
  return (
    <div className="card overflow-hidden">
      <div className="card-h px-4 py-2 flex items-center gap-2" style={{ borderBottom: '1px solid var(--color-edge)' }}>
        <span style={{ color: 'var(--color-good)' }}>✓</span>
        <span className="font-semibold text-[12.5px]">Loop receipt</span>
        {receipt.date && <span className="ml-auto text-[10px]" style={{ color: 'var(--color-dim)' }}>{receipt.date}</span>}
      </div>
      <div className="p-4 space-y-3">
        {receipt.ran && (
          <div>
            <div className="text-[10px] uppercase tracking-wider mb-1" style={{ color: 'var(--color-dim)' }}>What ran</div>
            <div className="text-[12px]" style={{ color: 'var(--color-ink)' }}>{receipt.ran}</div>
          </div>
        )}
        <div className="grid grid-cols-2 gap-x-5 gap-y-3">
          <List label="Tools checked" items={receipt.toolsChecked} color="var(--color-accent)" />
          <List label="What failed" items={receipt.failed} color="var(--color-bad)" />
          <List label="What changed" items={receipt.changed} color="var(--color-warn)" />
          <List label="What got saved" items={receipt.saved} color="var(--color-good)" />
        </div>
      </div>
    </div>
  )
}
