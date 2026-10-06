import { atom, read, update } from 'claude-code'
import type { ElementTable, EngineInterface, Register, RenderSurface } from 'claude-code'

import type { ContextBarCount, ContextBarSegment, ContextBarUsage } from '../types'
import {
  CELL,
  FILL,
  badgeColor,
  barRuns,
  breakdownLines,
  formatTokens,
  hasShifted,
  meter,
  orList,
  overheadOf,
  packRows,
  rankRows,
  toUsage,
  topItems,
  withCount,
} from './layout'

const isVisible = atom({ plugin: 'context-bar', key: 'isVisible' } as const, false)
const isDrilled = atom({ plugin: 'context-bar', key: 'isDrilled' } as const, false)
const openCategory = atom({ plugin: 'context-bar', key: 'openCategory' } as const, null)
const expandedCategory = atom({ plugin: 'context-bar', key: 'expandedCategory' } as const, null)
const usage = atom({ plugin: 'context-bar', key: 'usage' } as const, null)
const counted = atom({ plugin: 'context-bar', key: 'counted' } as const, null)

/**
 * Whether an exact count is scheduled or running, so a second one waits for
 * the next turn. Not drawn from, so a module variable will do: a reload clears
 * it, and the most that costs is one count more.
 */
let isCounting = false

/** Long enough for the engine to install a compaction or a /clear before we measure. */
const SETTLE_MS = 250

/**
 * The surfaces that draw the band above the prompt. Everywhere else (Claude
 * Code for VS Code, the mobile app) the card opens in a pane under this id.
 */
const BAND_SURFACES: readonly RenderSurface[] = ['terminal', 'desktop']
const PANE = 'context-bar'

/**
 * How this session draws the card: in the band, where a terminal or the
 * desktop app shows the session; in a pane, where only other surfaces do; or
 * nowhere, in a `-p` run or under the SDK, where nothing is drawn at all.
 */
async function form($: EngineInterface): Promise<'band' | 'pane' | null> {
  const surfaces = await $.session.surfaces()
  if (surfaces.length === 0) return null

  return surfaces.some(surface => BAND_SURFACES.includes(surface)) ? 'band' : 'pane'
}

/** Where the choice to show the card is kept: one for the band, one for the pane, so each form keeps its own. */
const CHOICE = { band: 'isVisible', pane: 'isPaneVisible' } as const

/** The card's border and padding take two cells on each side. */
const CHROME = 4

const TITLE = '◆ context'

/** Cells between legend entries. */
const GAP = 3

/**
 * A legend entry's swatch: one of the bar's own cells, so it stands exactly as
 * wide as a cell of the bar, with the same gap after it. A full block, or a
 * square from the font, runs into that gap and sits out of line with the bar.
 */
const SWATCH = CELL

/** How far the ranked bars sit in from the card's edge; a category's items sit two further. */
const INDENT = 2

/** The items a category opens to before the rest are summed up in one line. */
const MAX_ITEMS = 8

/** The most an item's name may take before it is cut. */
const ITEM_LABEL = 28

/**
 * Measures the window as /context breaks it down. The `summary` breakdown is
 * free and takes its total from the last response's usage, exactly, but its
 * categories are local estimates that can run to twice what /context counts.
 * So the overhead comes from the last exact count, taken again in the
 * background whenever the estimates show the overhead has changed.
 */
async function refresh($: EngineInterface) {
  const { context } = await $.session.usage({ breakdown: 'summary' })
  if (context.breakdown === undefined) {
    await update($, usage, () => null)
    return
  }

  const estimate = toUsage(context.breakdown)
  const count = await read($, counted)
  await update($, usage, () => withCount(estimate, count))
  if (!isCounting && (count === null || hasShifted(overheadOf(estimate), count.basis))) {
    isCounting = true
    $.clock.after(0, () => void countExactly($))
  }
}

/**
 * Counts the overhead with the token-count API, as /context does: one request
 * per tool and memory file, which is why it runs only when the overhead
 * changed. Should the count fail, the estimates stand until they shift again.
 */
async function countExactly($: EngineInterface) {
  try {
    const { context } = await $.session.usage({ breakdown: 'summary' })
    if (context.breakdown === undefined) return
    const estimate = toUsage(context.breakdown)

    const count = await countOverhead($, estimate)
    await update($, counted, () => count)
    await update($, usage, () => withCount(estimate, count))
  } finally {
    isCounting = false
  }
}

/** One exact count of the overhead, with the estimates it was taken against as its basis. */
async function countOverhead($: EngineInterface, estimate: ContextBarUsage): Promise<ContextBarCount> {
  let segments: ContextBarSegment[] | null = null
  try {
    const exact = (await $.session.usage({ breakdown: 'full' })).context.breakdown
    if (exact !== undefined) segments = overheadOf(toUsage(exact))
  } catch {
    // Keeps the estimates; the basis holds the next attempt off until they move.
  }

  return { basis: overheadOf(estimate).map(({ name, tokens }) => ({ name, tokens })), segments }
}

/**
 * The card's figures as text, for a session that draws nowhere: today Claude
 * Code for VS Code, whose panel draws no mod's band or pane yet, and `-p`
 * runs. Measured now, exactly; the last count serves while the overhead holds.
 * With a category, its biggest items too.
 */
async function snapshot($: EngineInterface, category: string | null): Promise<string> {
  const { context } = await $.session.usage({ breakdown: 'summary' })
  if (context.breakdown === undefined) return 'the context window could not be measured'

  const estimate = toUsage(context.breakdown)
  const last = await read($, counted)
  const count = last !== null && !hasShifted(overheadOf(estimate), last.basis) ? last : await countOverhead($, estimate)
  await update($, counted, () => count)

  const measured = withCount(estimate, count)
  const m = meter(measured)
  const limit = measured.compactsAt === null ? ` of ${formatTokens(measured.maxTokens)}` : ' used'
  const compacts = measured.compactsAt === null ? '' : ` · compacts at ${formatTokens(measured.compactsAt)}`
  const lines = [
    `${TITLE}  ${formatTokens(m.used)}${limit}${compacts} · ${m.percent}%`,
    `overhead ${formatTokens(m.overhead.tokens)} · messages ${formatTokens(m.messages)} · free ${formatTokens(m.free)}`,
    ...breakdownLines(m.overhead.parts, Infinity),
  ]

  if (category !== null) {
    const openable = m.overhead.parts.filter(part => (part.items?.length ?? 0) > 0)
    const part = openable.find(one => one.label === category)
    if (part === undefined) {
      return openable.length === 0
        ? 'the overhead has nothing to open'
        : `nothing to open in "${category}": try ${orList(openable.map(one => one.label))}`
    }
    const { shown, rest } = topItems(part.items!, MAX_ITEMS)
    const items = shown.map(item => `${item.name} ${formatTokens(item.tokens)}`)
    if (rest !== null) items.push(`+ ${rest.count} more ${formatTokens(rest.tokens)}`)
    lines.push(`${part.label} ${formatTokens(part.tokens)}: ${items.join(', ')}`)
  }

  return [...lines, "a snapshot: this session can't keep the card on screen; run /context-bar again to measure again"].join('\n')
}

/**
 * Shows or hides the card, as a pane where the session has no band, and
 * remembers which for the next session; says which form it took.
 */
async function show($: EngineInterface, shown: boolean): Promise<'band' | 'pane' | null> {
  const where = await form($)
  if (shown) await refresh($)
  await update($, isVisible, () => shown)
  if (where === 'pane') {
    if (shown) await $.ui.open({ id: PANE, title: 'context' })
    else await $.ui.close({ id: PANE })
  }
  await $.store.set(CHOICE[where ?? 'band'], shown)

  return where
}

/**
 * Shows the card again if the person left it on in an earlier session, in the
 * form this session draws it. A session that draws nowhere measures nothing.
 */
async function restore($: EngineInterface) {
  const where = await form($)
  if (where === null) return

  const shown = (await $.store.get(CHOICE[where])) === true
  if (shown) await refresh($)
  await update($, isVisible, () => shown)
  if (shown && where === 'pane') void $.ui.open({ id: PANE, title: 'context' })
}

/** Opens the overhead to one category's items, `/context-bar overhead <category>`. */
async function openOverhead($: EngineInterface, category: string) {
  if (!(await read($, isVisible))) await show($, true)

  const measured = await read($, usage)
  const openable = measured === null ? [] : meter(measured).overhead.parts.filter(part => (part.items?.length ?? 0) > 0)
  if (!openable.some(part => part.label === category)) {
    return openable.length === 0
      ? 'the overhead has nothing to open'
      : `nothing to open in "${category}": try ${orList(openable.map(part => part.label))}`
  }

  await update($, isDrilled, () => true)
  await update($, expandedCategory, () => null)
  await update($, openCategory, () => category)

  return `showing ${category}`
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'context-bar',
      description: 'Show or hide your context window as a bar above the prompt (its figures where none can draw); overhead opens its breakdown',
      argumentHint: '[overhead [category]]',
      immediate: true,
    })
    await restore($)

    return next(e)
  })

  on('command.run', { command: 'context-bar' }, async ($, e) => {
    const option = e.args.trim().replace(/\s+/g, ' ')

    // Where nothing draws, a card left on would only measure for nobody: answer with the figures instead.
    if ((await form($)) === null && (option === '' || option === 'overhead' || option.startsWith('overhead '))) {
      await update($, isVisible, () => false)

      return { text: await snapshot($, option.startsWith('overhead ') ? option.slice('overhead '.length) : null) }
    }

    if (option === 'overhead') {
      const drilled = !(await read($, isDrilled))
      if (drilled && !(await read($, isVisible))) await show($, true)
      await update($, isDrilled, () => drilled)

      return { text: drilled ? 'overhead shown' : 'overhead hidden' }
    }
    if (option.startsWith('overhead ')) return { text: await openOverhead($, option.slice('overhead '.length)) }
    if (option !== '') return { text: `unknown option "${option}": try /context-bar or /context-bar overhead` }

    const shown = !(await read($, isVisible))
    const where = await show($, shown)
    if (!shown) return { text: 'off' }

    return { text: where === 'pane' ? 'on, in a pane' : 'on' }
  })

  on('session.measure', async ($, e, next) => {
    if (e.changed.includes('context') && (await read($, isVisible))) await refresh($)

    return next(e)
  })

  on('session.compact', async ($, e, next) => {
    const compacted = await next(e)
    if (await read($, isVisible)) $.clock.after(SETTLE_MS, () => void refresh($))

    return compacted
  }).catch(($, e, next) => next(e)) // replays the compaction already run; never runs it twice

  // A /clear or a /resume puts another conversation in place, and no session.start follows a /clear.
  on('session.end', async ($, e, next) => {
    const ended = await next(e)
    if (e.reason === 'clear' || e.reason === 'resume') $.clock.after(SETTLE_MS, () => void restore($))

    return ended
  })

  // Claude Code for VS Code and the mobile app join as they connect, after session.start.
  on('session.attach', async ($, e, next) => {
    const attached = await next(e)
    await restore($)

    return attached
  })

  // Closing the pane is the person's /context-bar off, kept for the next session that draws a pane.
  on('ui.close', { id: PANE }, async ($, e, next) => {
    const closed = await next(e)
    if (e.origin.kind === 'person') {
      await update($, isVisible, () => false)
      await $.store.set(CHOICE.pane, false)
    }

    return closed
  }).catch(($, e, next) => next(e)) // replays the close already made; never closes twice

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey || !(await read($, isVisible))) return next(e)

    const measured = await read($, usage)
    if (measured === null) return next(e)

    return card($, $.ui.resolve(e), measured, e.props.bodyColumns, true)
  })

  // Where the session has no band (Claude Code for VS Code, the mobile app), the card is a pane's body.
  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const measured = await read($, usage)
    if (measured === null) {
      const { Text } = $.ui.resolve(e)
      return <Text dimColor>measuring the context window…</Text>
    }

    return card($, $.ui.resolve(e), measured, e.props.bodyColumns, false)
  })
}

/**
 * The card: the header, the meter, its legend, and the overhead's breakdown or
 * its ranked bars, laid out `columns` cells wide. Framed for the band; a
 * pane's own frame holds it there.
 */
async function card($: EngineInterface, ui: ElementTable, measured: ContextBarUsage, columns: number, isFramed: boolean) {
  const { Box, Button, Text } = ui
  const drilled = await read($, isDrilled)
  const open = await read($, openCategory)
  const expanded = await read($, expandedCategory)
  const m = meter(measured)
  const inner = isFramed ? columns - CHROME : columns
  const total = formatTokens(m.used)
  const limit = measured.compactsAt === null ? ` of ${formatTokens(measured.maxTokens)}` : ' used'
  const compacts = measured.compactsAt === null ? '' : ` · compacts at ${formatTokens(measured.compactsAt)}`
  const badge = ` ${m.percent}% `
  const hasRoom = TITLE.length + 1 + total.length + limit.length + compacts.length + 1 + badge.length <= inner

  const fills = [
    { label: 'overhead', tokens: m.overhead.tokens, color: FILL.overhead },
    { label: 'messages', tokens: m.messages, color: FILL.messages },
    { label: 'free', tokens: m.free, color: FILL.free },
  ].map(fill => ({ ...fill, amount: formatTokens(fill.tokens) }))
  const opener = `overhead ${fills[0]!.amount} ${drilled ? '▾' : '▸'}`
  const rows = packRows(
    fills.map((fill, i) => 2 + (i === 0 ? opener.length : fill.label.length + 1 + fill.amount.length)),
    inner,
    GAP,
  )

  const parts = m.overhead.parts
  const isOpenable = (part: (typeof parts)[number]) => (part.items?.length ?? 0) > 0
  const arrow = (part: (typeof parts)[number]) => (isOpenable(part) ? (open === part.label ? ' ▾' : ' ▸') : '')
  const ranked = rankRows(
    parts.map(part => ({ label: `${part.label}${arrow(part)}`, tokens: part.tokens })),
    inner - INDENT,
  )
  const opened = parts.find(part => part.label === open && isOpenable(part))
  const showsAll = opened !== undefined && opened.label === expanded
  const top = opened === undefined ? null : topItems(opened.items!, showsAll ? Infinity : MAX_ITEMS)
  const canFold = showsAll && opened.items!.length > MAX_ITEMS
  const items =
    top === null
      ? null
      : rankRows(
          top.shown.map(item => ({ label: item.name, tokens: item.tokens })),
          inner - INDENT - 2,
          ITEM_LABEL,
        )
  const breakdown = breakdownLines(parts, inner)

  return (
    <Box flexDirection="column" {...(isFramed ? { borderStyle: 'round', borderColor: 'subtle', paddingX: 1 } : {})}>
      <Box key="header" justifyContent="space-between">
        <Text>
          <Text color="claude">◆</Text> <Text bold>context</Text>
        </Text>
        <Text>
          <Text bold>{total}</Text>
          <Text dimColor>
            {limit}
            {hasRoom ? compacts : ''}
          </Text>{' '}
          <Text color="inverseText" backgroundColor={badgeColor(m)}>
            {badge}
          </Text>
        </Text>
      </Box>
      <Box key="bar">
        {barRuns(fills, inner).map(({ glyph, length, color }) => (
          <Text color={color}>{glyph.repeat(length)}</Text>
        ))}
      </Box>
      <Box key="legend" flexDirection="column">
        {rows.map(row => (
          <Box columnGap={GAP}>
            {row.map(i => {
              const fill = fills[i]!
              if (i === 0) {
                return (
                  <Box>
                    <Text>
                      <Text color={fill.color}>{SWATCH}</Text>{' '}
                    </Text>
                    <Button key="overhead" plain label={opener} onPress={() => update($, isDrilled, shown => !shown)} />
                  </Box>
                )
              }

              return (
                <Text>
                  <Text color={fill.color}>{SWATCH}</Text> {fill.label} <Text bold>{fill.amount}</Text>
                </Text>
              )
            })}
          </Box>
        ))}
      </Box>
      {drilled && parts.length > 0 && (
        <Box key="drill" flexDirection="column">
          {parts.flatMap((part, i) => {
            const row = ranked.rows[i]!
            const line = (
              <Box key={`row:${part.label}`}>
                <Text>{' '.repeat(INDENT)}</Text>
                {isOpenable(part) ? (
                  <Button
                    key={`category:${part.label}`}
                    plain
                    label={row.label}
                    onPress={async () => {
                      await update($, expandedCategory, () => null)
                      await update($, openCategory, current => (current === part.label ? null : part.label))
                    }}
                  />
                ) : (
                  <Text>{row.label}</Text>
                )}
                <Text>
                  {' '.repeat(row.pad + 2)}
                  <Text color={FILL.overhead}>{CELL.repeat(row.cells)}</Text> <Text bold>{row.amount}</Text>
                </Text>
              </Box>
            )
            if (part !== opened || top === null || items === null) return [line]

            return [
              line,
              <Box key="items" flexDirection="column">
                {items.rows.map((item, j) => (
                  <Box key={`row:item:${j}`}>
                    <Text>
                      {' '.repeat(INDENT + 2)}
                      {item.label}
                      {' '.repeat(item.pad + 2)}
                      <Text color={FILL.overhead}>{CELL.repeat(item.cells)}</Text> <Text bold>{item.amount}</Text>
                    </Text>
                  </Box>
                ))}
                {(top.rest !== null || canFold) && (
                  <Box key="more-row">
                    <Text>{' '.repeat(INDENT + 2)}</Text>
                    <Button
                      key="more"
                      plain
                      dimColor
                      label={
                        top.rest === null
                          ? 'show fewer ▴'
                          : `+ ${top.rest.count} more ${formatTokens(top.rest.tokens)} ▸`
                      }
                      onPress={() => update($, expandedCategory, () => (top.rest === null ? null : part.label))}
                    />
                  </Box>
                )}
              </Box>,
            ]
          })}
        </Box>
      )}
      {!drilled && breakdown.length > 0 && (
        <Box key="breakdown" flexDirection="column">
          {breakdown.map(line => (
            <Text dimColor>{line}</Text>
          ))}
        </Box>
      )}
    </Box>
  )
}
