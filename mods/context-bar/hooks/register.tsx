import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { ContextBarCount, ContextBarSegment } from '../types'
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

    let segments: ContextBarSegment[] | null = null
    try {
      const exact = (await $.session.usage({ breakdown: 'full' })).context.breakdown
      if (exact !== undefined) segments = overheadOf(toUsage(exact))
    } catch {
      // Keeps the estimates; the basis below holds the next attempt off until they move.
    }

    const count: ContextBarCount = { basis: overheadOf(estimate).map(({ name, tokens }) => ({ name, tokens })), segments }
    await update($, counted, () => count)
    await update($, usage, () => withCount(estimate, count))
  } finally {
    isCounting = false
  }
}

/** Shows or hides the card, and remembers which for the next session. */
async function show($: EngineInterface, shown: boolean) {
  if (shown) await refresh($)
  await update($, isVisible, () => shown)
  await $.store.set('isVisible', shown)
}

/** Shows the card again if the person left it on, in this session or an earlier one. */
async function restore($: EngineInterface) {
  const shown = (await $.store.get('isVisible')) === true
  if (shown) await refresh($)
  await update($, isVisible, () => shown)
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
      description: 'Show or hide your context window as a bar above the prompt; overhead opens its breakdown',
      argumentHint: '[overhead [category]]',
      immediate: true,
    })
    await restore($)

    return next(e)
  })

  on('command.run', { command: 'context-bar' }, async ($, e) => {
    const option = e.args.trim().replace(/\s+/g, ' ')

    if (option === 'overhead') {
      const drilled = !(await read($, isDrilled))
      if (drilled && !(await read($, isVisible))) await show($, true)
      await update($, isDrilled, () => drilled)

      return { text: drilled ? 'overhead shown' : 'overhead hidden' }
    }
    if (option.startsWith('overhead ')) return { text: await openOverhead($, option.slice('overhead '.length)) }
    if (option !== '') return { text: `unknown option "${option}": try /context-bar or /context-bar overhead` }

    const shown = !(await read($, isVisible))
    await show($, shown)

    return { text: shown ? 'on' : 'off' }
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

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey || !(await read($, isVisible))) return next(e)

    const measured = await read($, usage)
    if (measured === null) return next(e)

    const { Box, Button, Text } = $.ui.resolve(e)
    const drilled = await read($, isDrilled)
    const open = await read($, openCategory)
    const expanded = await read($, expandedCategory)
    const m = meter(measured)
    const inner = e.props.bodyColumns - CHROME
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
      <Box flexDirection="column" borderStyle="round" borderColor="subtle" paddingX={1}>
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
  })
}
