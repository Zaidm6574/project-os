import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { forceSimulation, forceManyBody, forceLink, forceCollide, forceX, forceY } from 'd3-force'
import type { GraphData, GraphNode } from '../types'
import { api } from '../api'
import { Empty } from '../ui'

type SimNode = GraphNode & { x: number; y: number }
type SimLink = { source: SimNode; target: SimNode; weight: number }

// Fixed colors per hierarchy level (theme-aware).
const TYPE_COLOR: Record<string, string> = {
  // second-brain hierarchy
  domain: 'var(--color-accent)',   // top-level hubs
  source: 'var(--color-good)',     // platforms (tiktok/yt/…)
  subtopic: 'var(--color-codex)',  // clusters
  item: 'var(--color-anti)',       // saved items
  // Arachne project-graph hierarchy (fallback when no second brain is wired)
  os: 'var(--color-accent)',
  run: 'var(--color-good)',
  decision: 'var(--color-codex)',
  risk: 'var(--color-bad)',
  question: 'var(--color-anti)',
}
function colorForType(t: string): string {
  return TYPE_COLOR[t] || 'var(--color-mut)'
}
// Domains big, items small — clear visual hierarchy.
const radius = (v: number) => 2.5 + Math.sqrt(v || 1) * 2.1

// Encode a single M x1 y1 L x2 y2 segment per link into one <path> d string.
function buildLinkPath(links: SimLink[]): string {
  const parts: string[] = []
  for (const l of links) {
    parts.push(`M${l.source.x.toFixed(1)} ${l.source.y.toFixed(1)}L${l.target.x.toFixed(1)} ${l.target.y.toFixed(1)}`)
  }
  return parts.join('')
}

export function GraphView({ onSelectNode }: { onSelectNode: (n: GraphNode) => void }) {
  const [data, setData] = useState<GraphData | null>(null)
  const [err, setErr] = useState('')
  const [layout, setLayout] = useState<{ nodes: SimNode[]; links: SimLink[]; box: number[] } | null>(null)
  const [hover, setHover] = useState<GraphNode | null>(null)

  // Pan/zoom kept entirely in refs — never triggers a React re-render on mousemove/wheel.
  const viewRef = useRef({ k: 1, tx: 0, ty: 0 })
  // The <g> element we apply the transform to directly.
  const gRef = useRef<SVGGElement | null>(null)
  // Cursor overlay div (for grabbing cursor without re-render).
  const overlayRef = useRef<HTMLDivElement | null>(null)
  // Drag state.
  const drag = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null)
  // Pending RAF handle.
  const rafRef = useRef<number | null>(null)

  useEffect(() => { api.graph().then(setData).catch((e) => setErr(String(e))) }, [])

  const types = useMemo(
    () => (data ? Array.from(new Set(data.nodes.map((n) => n.type).filter(Boolean))) : []),
    [data],
  )

  useEffect(() => {
    if (!data || !data.nodes.length) return
    const nodes = data.nodes.map((n) => ({ ...n, x: 0, y: 0 })) as SimNode[]
    const byId = new Map(nodes.map((n) => [n.id, n]))
    const links = data.links
      .map((l) => ({
        source: byId.get(l.source as unknown as string)!,
        target: byId.get(l.target as unknown as string)!,
        weight: l.weight,
      }))
      .filter((l) => l.source && l.target)
    const sim = forceSimulation(nodes as any)
      .force('charge', forceManyBody().strength(-110).distanceMax(420))
      .force('link', forceLink(links as any).id((d: any) => d.id)
        .distance((l: any) => (l.type === 'domain-source' ? 95 : l.type === 'source-subtopic' ? 60 : 30))
        .strength(0.6))
      .force('collide', forceCollide().radius((d: any) => radius(d.val) + 3).strength(0.9))
      // Mild gravity toward origin keeps the whole graph centered + compact (no left-drift).
      .force('x', forceX(0).strength(0.045))
      .force('y', forceY(0).strength(0.045))
      .stop()
    for (let i = 0; i < 300; i++) sim.tick()
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    for (const n of nodes) {
      if (n.x < minX) minX = n.x
      if (n.x > maxX) maxX = n.x
      if (n.y < minY) minY = n.y
      if (n.y > maxY) maxY = n.y
    }
    const pad = 40
    setLayout({
      nodes,
      links,
      box: [minX - pad, minY - pad, (maxX - minX) + pad * 2, (maxY - minY) + pad * 2],
    })
  }, [data])

  // Apply the current viewRef to the <g> DOM element directly — no setState.
  const applyTransform = useCallback(() => {
    if (gRef.current) {
      const { k, tx, ty } = viewRef.current
      gRef.current.setAttribute('transform', `translate(${tx} ${ty}) scale(${k})`)
    }
  }, [])

  // Schedule a RAF-batched transform update.
  const scheduleApply = useCallback(() => {
    if (rafRef.current !== null) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null
      applyTransform()
    })
  }, [applyTransform])

  // Zoom buttons update the ref and flush immediately (they're rare, RAF is fine too).
  const zoomBy = useCallback((factor: number) => {
    viewRef.current = {
      ...viewRef.current,
      k: Math.max(0.3, Math.min(4, viewRef.current.k * factor)),
    }
    applyTransform()
  }, [applyTransform])

  const zoomReset = useCallback(() => {
    viewRef.current = { k: 1, tx: 0, ty: 0 }
    applyTransform()
  }, [applyTransform])

  // Pointer handlers — mutate ref, schedule RAF, never call setState.
  const onMouseDown = useCallback((e: React.MouseEvent) => {
    drag.current = { x: e.clientX, y: e.clientY, tx: viewRef.current.tx, ty: viewRef.current.ty }
    if (overlayRef.current) overlayRef.current.style.cursor = 'grabbing'
  }, [])

  const onMouseMove = useCallback((e: React.MouseEvent) => {
    if (!drag.current) return
    viewRef.current = {
      ...viewRef.current,
      tx: drag.current.tx + (e.clientX - drag.current.x),
      ty: drag.current.ty + (e.clientY - drag.current.y),
    }
    scheduleApply()
  }, [scheduleApply])

  const onMouseUp = useCallback(() => {
    drag.current = null
    if (overlayRef.current) overlayRef.current.style.cursor = 'grab'
  }, [])

  const onWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12
    viewRef.current = {
      ...viewRef.current,
      k: Math.max(0.3, Math.min(4, viewRef.current.k * factor)),
    }
    scheduleApply()
  }, [scheduleApply])

  // Sync the <g> after layout changes (the initial transform is identity but let's be explicit).
  useEffect(() => {
    if (layout) applyTransform()
  }, [layout, applyTransform])

  // Memoize the link <path> so it only rebuilds when layout changes, not on hover/pan.
  const linkPath = useMemo(() => (layout ? buildLinkPath(layout.links) : ''), [layout])

  // Memoize circles — they only change when layout or types change (not on pan/zoom/hover).
  const nodeCircles = useMemo(() => {
    if (!layout) return null
    return layout.nodes.map((n) => (
      <circle
        key={n.id}
        cx={n.x}
        cy={n.y}
        r={radius(n.val)}
        fill={colorForType(n.type)}
        fillOpacity={0.85}
        style={{ cursor: 'pointer' }}
        data-nid={n.id}
        onMouseEnter={() => setHover(n)}
        onMouseLeave={() => setHover((h) => (h?.id === n.id ? null : h))}
        onClick={() => onSelectNode(n)}
      >
        <title>{n.name} ({n.type})</title>
      </circle>
    ))
  }, [layout, types, onSelectNode])

  // Hover highlight: a single overlay circle drawn on top, not a re-render of all circles.
  const hoverOverlay = useMemo(() => {
    if (!hover || !layout) return null
    const n = layout.nodes.find((nd) => nd.id === hover.id)
    if (!n) return null
    return (
      <circle
        cx={n.x}
        cy={n.y}
        r={radius(n.val)}
        fill={colorForType(n.type)}
        fillOpacity={1}
        stroke="#fff"
        strokeWidth={0.8}
        style={{ pointerEvents: 'none' }}
      />
    )
  }, [hover, layout, types])

  if (err) return <Empty>Graph unavailable: {err}</Empty>
  if (!data) return <Empty>Loading graph…</Empty>
  if (data.note && !data.nodes.length) return <Empty>{data.note}</Empty>
  if (!layout) return <Empty>Laying out {data.nodes.length} nodes…</Empty>

  const [bx, by, bw, bh] = layout.box

  return (
    <div className="card overflow-hidden flex flex-col" style={{ height: 'calc(100vh - 210px)' }}>
      {/* Header */}
      <div
        className="card-h px-4 py-2 flex items-center gap-3 flex-wrap text-[11px]"
        style={{ borderBottom: '1px solid var(--color-edge)', color: 'var(--color-mut)' }}
      >
        <span className="font-semibold text-[12.5px]" style={{ color: 'var(--color-ink)' }}>
          Knowledge graph
        </span>
        <span className="chip">
          showing {data.shown?.nodes ?? layout.nodes.length} / {data.totals.nodes} nodes
        </span>
        <span className="chip">
          {data.shown?.links ?? layout.links.length} / {data.totals.links} links
        </span>
        <span className="flex items-center gap-2.5">
          {([['domain', 'var(--color-accent)'], ['source', 'var(--color-good)'], ['subtopic', 'var(--color-codex)'], ['item', 'var(--color-anti)']] as const).map(([t, c]) => (
            <span key={t} className="flex items-center gap-1" style={{ color: 'var(--color-dim)' }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: c, display: 'inline-block' }} />{t}
            </span>
          ))}
        </span>
        <div className="ml-auto flex gap-1">
          <button className="btn" onClick={() => zoomBy(1.25)}>+</button>
          <button className="btn" onClick={() => zoomBy(1 / 1.25)}>−</button>
          <button className="btn" onClick={zoomReset}>reset</button>
        </div>
      </div>

      {/* Canvas */}
      <div
        ref={overlayRef}
        className="flex-1 relative"
        style={{ overflow: 'hidden', cursor: 'grab' }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
        onWheel={onWheel}
      >
        <svg
          width="100%"
          height="100%"
          viewBox={`${bx} ${by} ${bw} ${bh}`}
          preserveAspectRatio="xMidYMid meet"
        >
          {/* Initial transform is identity; applyTransform() keeps it in sync via ref. */}
          <g ref={gRef} transform="translate(0 0) scale(1)">
            {/* All links as a single <path> — one DOM node instead of N <line> elements. */}
            <path
              d={linkPath}
              stroke="var(--color-mut)"
              strokeWidth={0.5}
              strokeOpacity={0.22}
              fill="none"
            />
            {/* Memoized node circles */}
            {nodeCircles}
            {/* Hover highlight overlay — single extra circle, no full re-render */}
            {hoverOverlay}
          </g>
        </svg>

        {/* Hover tooltip */}
        {hover && (
          <div
            className="absolute bottom-2 left-2 card px-3 py-1.5 text-[11.5px]"
            style={{ color: 'var(--color-ink)', pointerEvents: 'none' }}
          >
            <span className="font-medium">{hover.name}</span>
            <span className="ml-2" style={{ color: 'var(--color-dim)' }}>
              {hover.type} · val {hover.val}
            </span>
          </div>
        )}
      </div>

      {/* Domain chips */}
      {data.domains.length > 0 && (
        <div className="px-4 py-2 flex gap-1.5 flex-wrap" style={{ borderTop: '1px solid var(--color-edge)' }}>
          {data.domains.slice(0, 12).map((d) => (
            <span key={d.domain} className="chip" title={d.summary}>
              {d.domain}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
