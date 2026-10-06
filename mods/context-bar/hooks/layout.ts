import type { SessionContextBreakdown, ThemeKey } from 'claude-code'

import type { ContextBarCount, ContextBarItem, ContextBarSegment, ContextBarUsage } from '../types'

/**
 * The meter's fills, from the theme keys the mod API documents: the
 * conversation in the accent, the overhead every request carries in quiet
 * gray, and the room left as the track.
 */
export const FILL = { overhead: 'inactive', messages: 'claude', free: 'subtle' } as const satisfies Record<
  string,
  ThemeKey
>

/** The /context category that is the conversation; every other one is overhead. */
const MESSAGES = 'Messages'

/**
 * Shorter names for /context's categories; one it does not know keeps its own,
 * lowercased. Both of MCP's categories go under one name, so the card shows one
 * entry for what MCP servers cost, not two that read alike.
 */
const LABELS = new Map([
  ['System prompt', 'system'],
  ['System tools', 'tools'],
  ['MCP tools', 'mcp'],
  ['MCP server instructions', 'mcp'],
  ['Custom agents', 'agents'],
  ['Memory files', 'memory files'],
  ['Skills', 'skills'],
])

/**
 * The breakdown as the card keeps it, leaving out the tool schemas deferred
 * until searched for, each category with what it is made of where listed.
 */
export function toUsage(breakdown: SessionContextBreakdown): ContextBarUsage {
  return {
    segments: breakdown.categories.flatMap(({ name, tokens, kind }) => {
      if (kind === 'deferred') return []
      const items = itemsOf({ name, tokens }, breakdown)

      return [{ name, tokens, kind, ...(items === undefined ? {} : { items }) }]
    }),
    totalTokens: breakdown.totalTokens,
    maxTokens: breakdown.rawMaxTokens,
    compactsAt: breakdown.autoCompactThreshold ?? null,
  }
}

/**
 * What a /context category is made of, for those the breakdown lists: the MCP
 * server instructions as one item and the MCP tools in the window by server
 * (the two make up `mcp`), the agents, the memory files, the skills.
 */
function itemsOf(category: ContextBarItem, breakdown: SessionContextBreakdown): ContextBarItem[] | undefined {
  switch (category.name) {
    case 'MCP server instructions':
      return [{ name: 'instructions', tokens: category.tokens }]
    case 'MCP tools': {
      const servers = new Map<string, number>()
      for (const tool of breakdown.mcpTools) {
        if (tool.isLoaded) servers.set(tool.serverName, (servers.get(tool.serverName) ?? 0) + tool.tokens)
      }

      return [...servers].map(([name, tokens]) => ({ name, tokens }))
    }
    case 'Custom agents':
      return breakdown.agents.map(agent => ({ name: agent.agentType, tokens: agent.tokens }))
    case 'Memory files':
      return breakdown.memoryFiles.map(file => ({
        name: `${file.path.split(/[\\/]/).pop()} (${file.type.toLowerCase()})`,
        tokens: file.tokens,
      }))
    case 'Skills':
      return breakdown.skills?.skillFrontmatter.map(skill => ({ name: skill.name, tokens: skill.tokens }))
    default:
      return undefined
  }
}

export type OverheadPart = { label: string; tokens: number; items?: ContextBarItem[] }

export type Meter = {
  /** What the meter fills toward: where auto-compaction runs, or the window's end when it is off. */
  capacity: number
  used: number
  /** `used` over `capacity`, as a whole percentage; past 100 once over. */
  percent: number
  messages: number
  /** Every other category in use, largest first, each with its items largest first. */
  overhead: { tokens: number; parts: OverheadPart[] }
  free: number
}

/** The window as a meter: the conversation, the overhead, and the room left before compaction. */
export function meter(usage: ContextBarUsage): Meter {
  const capacity = usage.compactsAt ?? usage.maxTokens
  const inUse = usage.segments.filter(segment => segment.kind === 'used')
  const byLabel = new Map<string, OverheadPart>()
  for (const { name, tokens, items } of inUse.filter(segment => segment.name !== MESSAGES)) {
    const label = LABELS.get(name) ?? name.toLowerCase()
    const part = byLabel.get(label)
    const merged = [...(part?.items ?? []), ...(items ?? [])]
    byLabel.set(label, {
      label,
      tokens: (part?.tokens ?? 0) + tokens,
      ...(part?.items === undefined && items === undefined ? {} : { items: merged.sort(largestFirst) }),
    })
  }
  const parts = [...byLabel.values()].sort(largestFirst)

  return {
    capacity,
    used: usage.totalTokens,
    percent: Math.round((usage.totalTokens / capacity) * 100),
    messages: inUse.find(segment => segment.name === MESSAGES)?.tokens ?? 0,
    overhead: { tokens: parts.reduce((sum, part) => sum + part.tokens, 0), parts },
    free: Math.max(0, capacity - usage.totalTokens),
  }
}

function largestFirst(a: { tokens: number }, b: { tokens: number }): number {
  return b.tokens - a.tokens
}

/** A breakdown's overhead: every category in use but the conversation. */
export function overheadOf(usage: ContextBarUsage): ContextBarSegment[] {
  return usage.segments.filter(segment => segment.kind === 'used' && segment.name !== MESSAGES)
}

/**
 * Whether the overhead's estimates have moved since it was counted: a category
 * came or went, or one moved by more than 2% of itself (and at least 200
 * tokens), as when a tool loads; smaller drift leaves the count standing.
 */
export function hasShifted(estimates: readonly ContextBarItem[], basis: readonly ContextBarItem[]): boolean {
  if (estimates.length !== basis.length) return true
  const before = new Map(basis.map(item => [item.name, item.tokens]))

  return estimates.some(({ name, tokens }) => {
    const was = before.get(name)

    return was === undefined || Math.abs(tokens - was) > Math.max(200, was * 0.02)
  })
}

/**
 * The window with the overhead as counted: the total and the room left stay
 * the estimate's (it takes the total from the last response's usage, exactly),
 * and the conversation is whatever of the total the counted overhead leaves.
 */
export function withCount(estimate: ContextBarUsage, count: ContextBarCount | null): ContextBarUsage {
  if (count === null || count.segments === null) return estimate
  const overhead = count.segments.reduce((sum, segment) => sum + segment.tokens, 0)

  return {
    ...estimate,
    segments: [
      ...count.segments,
      { name: MESSAGES, tokens: Math.max(0, estimate.totalTokens - overhead), kind: 'used' },
      ...estimate.segments.filter(segment => segment.kind !== 'used'),
    ],
  }
}

/**
 * The overhead's one-line breakdown (`overhead: tools 14.6k, mcp 11k, …`)
 * broken into lines `width` cells wide, only ever between entries.
 */
export function breakdownLines(parts: readonly { label: string; tokens: number }[], width: number): string[] {
  const entries = parts.map(
    (part, i) => `${i === 0 ? 'overhead: ' : ''}${part.label} ${formatTokens(part.tokens)}${i < parts.length - 1 ? ',' : ''}`,
  )

  return packRows(
    entries.map(entry => entry.length),
    width,
    1,
  ).map(row => row.map(i => entries[i]).join(' '))
}

/** The first `max` items, and how many more there are and what they come to, if any. */
export function topItems(
  items: readonly ContextBarItem[],
  max: number,
): { shown: ContextBarItem[]; rest: { count: number; tokens: number } | null } {
  const rest = items.slice(max)

  return {
    shown: items.slice(0, max),
    rest: rest.length === 0 ? null : { count: rest.length, tokens: rest.reduce((sum, item) => sum + item.tokens, 0) },
  }
}

export type RankRow = { label: string; pad: number; cells: number; amount: string }

/**
 * A ranked bar chart laid out `width` cells wide: each label padded to the
 * widest (cut with an ellipsis past `maxLabel`), two cells, its bar scaled to
 * the largest value, a cell, and its amount.
 */
export function rankRows(
  entries: readonly { label: string; tokens: number }[],
  width: number,
  maxLabel = Infinity,
): { labelWidth: number; rows: RankRow[] } {
  const labelWidth = Math.min(maxLabel, Math.max(0, ...entries.map(entry => entry.label.length)))
  const labels = entries.map(({ label }) => (label.length > labelWidth ? `${label.slice(0, labelWidth - 1)}…` : label))
  const amounts = entries.map(entry => formatTokens(entry.tokens))
  const amountWidth = Math.max(0, ...amounts.map(amount => amount.length))
  const cells = scaleBars(
    entries.map(entry => entry.tokens),
    width - labelWidth - 2 - 1 - amountWidth,
  )

  return {
    labelWidth,
    rows: labels.map((label, i) => ({ label, pad: labelWidth - label.length, cells: cells[i]!, amount: amounts[i]! })),
  }
}

/** Words joined as a sentence lists them: `a, b or c`. */
export function orList(words: readonly string[]): string {
  return words.length < 2 ? (words[0] ?? '') : `${words.slice(0, -1).join(', ')} or ${words[words.length - 1]}`
}

/** The badge's status color: green while compaction is far off, yellow nearing it, red close. */
export function badgeColor(m: Meter): 'success' | 'warning' | 'error' {
  const fill = m.used / m.capacity

  return fill < 0.5 ? 'success' : fill < 0.8 ? 'warning' : 'error'
}

/**
 * One cell of a bar: seven-eighths of a block, so the eighth left over draws a
 * thin gap after every cell, and every cell stands at the same height.
 */
export const CELL = '▉'

export type BarRun = { glyph: string; length: number; color: string }

/** The bar as runs of cells, `width` long, each segment's cells in its color. */
export function barRuns(segments: readonly { tokens: number; color: string }[], width: number): BarRun[] {
  const cells = allocateCells(
    segments.map(segment => segment.tokens),
    width,
  )

  return segments.flatMap((segment, i) => (cells[i]! > 0 ? [{ glyph: CELL, color: segment.color, length: cells[i]! }] : []))
}

/**
 * Bars for a ranked chart: the largest value's bar fills `width` cells and the
 * rest scale to it, each that holds anything at least one cell long.
 */
export function scaleBars(tokens: readonly number[], width: number): number[] {
  const largest = Math.max(0, ...tokens)
  if (width <= 0 || largest === 0) return tokens.map(() => 0)

  return tokens.map(n => (n > 0 ? Math.max(1, Math.round((n / largest) * width)) : 0))
}

/** Packs entries of the given widths into rows `width` cells wide, `gap` cells apart, as few rows as fit. */
export function packRows(widths: readonly number[], width: number, gap: number): number[][] {
  const rows: number[][] = []
  let filled = 0
  widths.forEach((entry, i) => {
    const row = rows[rows.length - 1]
    if (row !== undefined && filled + gap + entry <= width) {
      row.push(i)
      filled += gap + entry
    } else {
      rows.push([i])
      filled = entry
    }
  })

  return rows
}

/**
 * Splits `width` cells among segments in proportion to their tokens, as whole
 * cells adding up to `width`. While there are cells enough, every segment that
 * holds tokens gets at least one, as /context's grid gives every row a square;
 * those cells come out of the segments drawn widest past their share.
 */
export function allocateCells(tokens: readonly number[], width: number): number[] {
  const total = tokens.reduce((sum, n) => sum + n, 0)
  if (width <= 0 || total <= 0) return tokens.map(() => 0)

  const exact = tokens.map(n => (n / total) * width)
  const cells = exact.map(Math.floor)

  if (tokens.filter(n => n > 0).length <= width) {
    tokens.forEach((n, i) => {
      if (n > 0 && cells[i] === 0) cells[i] = 1
    })
  }

  let spare = width - cells.reduce((sum, n) => sum + n, 0)
  while (spare > 0) {
    const i = widest(exact.map((x, j) => x - cells[j]!))
    cells[i]! += 1
    spare -= 1
  }
  while (spare < 0) {
    const i = widest(cells.map((n, j) => (n > 1 ? n - exact[j]! : -Infinity)))
    cells[i]! -= 1
    spare += 1
  }

  return cells
}

/** The index of the largest value, the first on a tie. */
function widest(values: readonly number[]): number {
  return values.reduce((best, value, i) => (value > values[best]! ? i : best), 0)
}

/** A token count as the card prints one: `999`, `1.2k`, `62.4k`, from 100k whole (`212k`), `1.5M`. */
export function formatTokens(tokens: number): string {
  if (tokens < 1_000) return String(Math.round(tokens))
  if (tokens < 100_000) return `${Math.round(tokens / 100) / 10}k`

  const thousands = Math.round(tokens / 1_000)
  if (thousands < 1_000) return `${thousands}k`

  return `${Math.round(tokens / 100_000) / 10}M`
}
