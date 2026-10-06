import { describe, expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On, RenderElement, RenderSurface, SessionContextBreakdown, SessionUsage } from 'claude-code'

/**
 * /context's breakdown as the engine lists it (its names, colors and order,
 * the buffer before the free space), as the token-count API counts it: 62.4k
 * of a 200k window, compaction at 155k. Overhead is 32.9k of it, the
 * conversation 29.5k.
 */
const BREAKDOWN: SessionContextBreakdown = {
  categories: [
    { name: 'System prompt', tokens: 3_100, color: 'promptBorder', isDeferred: false, kind: 'used' },
    { name: 'System tools', tokens: 14_600, color: 'inactive', isDeferred: false, kind: 'used' },
    { name: 'MCP tools', tokens: 9_800, color: 'cyan_FOR_SUBAGENTS_ONLY', isDeferred: false, kind: 'used' },
    { name: 'MCP server instructions', tokens: 1_200, color: 'green_FOR_SUBAGENTS_ONLY', isDeferred: false, kind: 'used' },
    { name: 'MCP tools (deferred)', tokens: 41_000, color: 'inactive', isDeferred: true, kind: 'deferred' },
    { name: 'Custom agents', tokens: 1_000, color: 'permission', isDeferred: false, kind: 'used' },
    { name: 'Memory files', tokens: 1_200, color: 'claude', isDeferred: false, kind: 'used' },
    { name: 'Skills', tokens: 2_000, color: 'warning', isDeferred: false, kind: 'used' },
    { name: 'Messages', tokens: 29_500, color: 'purple_FOR_SUBAGENTS_ONLY', isDeferred: false, kind: 'used' },
    { name: 'Autocompact buffer', tokens: 45_000, color: 'inactive', isDeferred: false, kind: 'buffer' },
    { name: 'Free space', tokens: 92_600, color: 'promptBorder', isDeferred: false, kind: 'free' },
  ],
  totalTokens: 62_400,
  maxTokens: 200_000,
  rawMaxTokens: 200_000,
  autocompactSource: 'auto',
  percentage: 31,
  gridRows: [],
  model: 'claude-opus-5-5',
  memoryFiles: [
    { path: '/Users/me/.claude/CLAUDE.md', type: 'User', tokens: 700 },
    { path: '/work/CLAUDE.md', type: 'Project', tokens: 500 },
  ],
  mcpTools: [
    { name: 'mcp__playwright__browser_click', serverName: 'playwright', tokens: 3_000, isLoaded: true },
    { name: 'mcp__github__create_issue', serverName: 'github', tokens: 4_200, isLoaded: true },
    { name: 'mcp__playwright__browser_snapshot', serverName: 'playwright', tokens: 2_600, isLoaded: true },
    { name: 'mcp__github__search', serverName: 'github', tokens: 41_000, isLoaded: false },
  ],
  agents: [
    { agentType: 'plugin-dev:plugin-validator', source: 'plugin', tokens: 600 },
    { agentType: 'code-reviewer', source: 'userSettings', tokens: 400 },
  ],
  skills: {
    totalSkills: 10,
    includedSkills: 10,
    tokens: 2_000,
    skillFrontmatter: [
      { name: 'plugin-authoring', source: 'built-in', tokens: 400 },
      { name: 'dataviz', source: 'built-in', tokens: 300 },
      { name: 'superpowers:brainstorming', source: 'plugin', pluginName: 'superpowers', tokens: 250 },
      { name: 'superpowers:test-driven-development', source: 'plugin', pluginName: 'superpowers', tokens: 200 },
      { name: 'superpowers:writing-plans', source: 'plugin', pluginName: 'superpowers', tokens: 200 },
      { name: 'frontend-design:frontend-design', source: 'plugin', pluginName: 'frontend-design', tokens: 150 },
      { name: 'skill-creator', source: 'userSettings', tokens: 150 },
      { name: 'code-review', source: 'built-in', tokens: 120 },
      { name: 'simplify', source: 'built-in', tokens: 120 },
      { name: 'run', source: 'built-in', tokens: 110 },
    ],
  },
  autoCompactThreshold: 155_000,
  isAutoCompactEnabled: true,
  apiUsage: null,
}

/**
 * BREAKDOWN with some rows' tokens changed, and the totals they come to; a row
 * changed to no tokens is left out, as the engine lists only rows that hold some.
 */
function changed(rows: Record<string, number>, totalTokens: number, percentage: number): SessionContextBreakdown {
  return {
    ...BREAKDOWN,
    categories: BREAKDOWN.categories
      .map(row => ({ ...row, tokens: rows[row.name] ?? row.tokens }))
      .filter(row => row.tokens > 0),
    totalTokens,
    percentage,
  }
}

const LATER = changed({ Messages: 77_000, 'Free space': 45_100 }, 109_900, 55)
const NEAR = changed({ Messages: 107_100, 'Free space': 15_000 }, 140_000, 70)
const COMPACTED = changed({ Messages: 3_100, 'Free space': 119_000 }, 36_000, 18)
const CLEARED = changed({ Messages: 0, 'Free space': 122_100 }, 32_900, 16)
/** LATER after a tool search loaded 3k more of MCP tools into the window. */
const LOADED = changed({ 'MCP tools': 12_800, Messages: 74_000 }, 109_900, 55)

/**
 * How far the `summary` breakdown's local estimates run from the exact count,
 * as measured in a long session: the system prompt and tools near double, the
 * MCP server instructions four times over.
 */
const OVERESTIMATE: Record<string, number> = { 'System prompt': 2, 'System tools': 2, 'MCP server instructions': 4 }

/**
 * A breakdown as `summary` estimates it: the same total, from the last
 * response's usage, with the overhead's categories overestimated and the
 * conversation taking what they leave.
 */
function estimated(exact: SessionContextBreakdown): SessionContextBreakdown {
  const rows = exact.categories.map(row => ({ ...row, tokens: Math.round(row.tokens * (OVERESTIMATE[row.name] ?? 1)) }))
  const overhead = rows.filter(row => row.kind === 'used' && row.name !== 'Messages').reduce((sum, row) => sum + row.tokens, 0)

  return {
    ...exact,
    categories: rows.map(row => (row.name === 'Messages' ? { ...row, tokens: Math.max(0, exact.totalTokens - overhead) } : row)),
  }
}

const SURFACES = ['terminal', 'desktop'] as const

function band(bodyColumns = 80, hasSurvey = false) {
  return {
    plugin: 'context-bar',
    component: 'AbovePrompt',
    props: { hasSurvey, isWorking: false, maxRows: 12, bodyColumns, scroll: { offset: 0, bodyRows: 11 }, view: {} },
    viewport: { columns: bodyColumns + 5, rows: 40 },
  } as const
}

/** Runs the work the plugin left on the clock, as the exact count; each test's engine sets it. */
let settle = async () => {}

/** The card's pane, as Claude Code for VS Code seats it beside the conversation. */
function pane(bodyColumns = 80) {
  return {
    plugin: 'context-bar',
    component: 'Pane',
    requestId: 'context-bar',
    props: {
      title: 'context',
      isFocused: false,
      bodyColumns,
      placement: 'dock',
      scroll: { offset: 0, bodyRows: 30 },
      view: {},
    },
    viewport: { columns: bodyColumns + 4, rows: 40 },
  } as const
}

/**
 * Stands in for the engine beneath the plugin: the session it starts, the
 * commands it lists, an empty band, a clock the test advances, the surfaces
 * the session draws on (`surfaces()`, the terminal unless said), the panes it
 * opens and closes, and the usage op, which itemizes the window only when a
 * breakdown is asked for: `full` exactly as `current()` has it, `summary` as
 * estimated. Counts what it was asked for; with `countFails`, refuses counts.
 */
function engine(
  on: On,
  current: () => SessionContextBreakdown,
  countFails = false,
  surfaces: () => readonly RenderSurface[] = () => ['terminal'],
) {
  const clock = mock.clock(on)
  const asked = { full: 0, summary: 0, opened: [] as string[], closed: [] as string[], clock }
  settle = () => clock.advance(1)
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('command.register', ($, e) => ({ value: { command: e.name } }))
  on('session.surfaces', () => ({ value: surfaces() }))
  on('session.attach', ($, e) => ({ clientId: e.clientId }))
  on('ui.open', ($, e) => {
    asked.opened.push(e.id)

    return { value: { isPlaced: true } }
  })
  on('ui.close', ($, e) => {
    asked.closed.push(e.id)

    return { value: undefined }
  })
  on('session.usage', ($, e) => {
    if (e.breakdown !== 'full') {
      if (e.breakdown !== undefined) asked.summary += 1
      return { value: usage(estimated(current()), e.breakdown !== undefined) }
    }
    asked.full += 1

    return countFails ? { deny: 'the token-count API is unavailable' } : { value: usage(current(), true) }
  })
  on('session.measure', ($, e) => ({ changed: e.changed }))
  on('ui.render', { component: 'AbovePrompt' }, () => ({ type: 'Box', children: [] }))

  return asked
}

function usage(breakdown: SessionContextBreakdown, isItemized: boolean): SessionUsage {
  const context = { window: 200_000, tokens: breakdown.totalTokens, percent: breakdown.percentage }

  return { startedAt: 0, context: isItemized ? { ...context, breakdown } : context, rateLimits: [] }
}

async function start($: Engine) {
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await settle()
}

async function toggle($: Engine) {
  await $.command.run({
    command: 'context-bar',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: true, columns: 85 },
  })
  await settle()
}

/** A turn ended and the window it measured is `breakdown`. */
async function measure($: Engine, breakdown: SessionContextBreakdown) {
  await $.session.measure({ context: usage(breakdown, false).context, rateLimits: [], changed: ['context'] })
  await settle()
}

type Drawn = { type: string; props?: Record<string, unknown>; children?: (Drawn | string)[] }
type Drawing = { drawn: () => Promise<RenderElement> }

/** The text an element shows: its strings in order, a Button's label included. */
function shown(node: Drawn | string): string {
  if (typeof node === 'string') return node
  if (node.type === 'Button') return String(node.props?.label ?? '')

  return (node.children ?? []).map(shown).join('')
}

function descendants(node: Drawn | string): Drawn[] {
  return typeof node === 'string' ? [] : [node, ...(node.children ?? []).flatMap(descendants)]
}

/** The element the band last drew under `key`. */
async function keyed(ui: Drawing, key: string): Promise<Drawn | undefined> {
  return descendants((await ui.drawn()) as unknown as Drawn).find(node => node.props?.key === key)
}

/** The text the band shows under `key`, or undefined when it draws no such element. */
async function textOf(ui: Drawing, key: string): Promise<string | undefined> {
  const node = await keyed(ui, key)

  return node && shown(node)
}

/** The bar's runs, left to right, each as its color and its length in cells. */
async function barRunsOf(ui: Drawing): Promise<[unknown, number][]> {
  const runs = (await keyed(ui, 'bar'))?.children ?? []

  return runs.flatMap(run => (typeof run === 'string' ? [] : [[run.props?.color, shown(run).length]]))
}

/** The rows of the box under `key`, each as its text and the length of its bar in cells. */
async function rowsOf(ui: Drawing, key: string): Promise<[string, number][]> {
  const rows = (await keyed(ui, key))?.children ?? []

  return rows.flatMap(row =>
    typeof row === 'string' || !String(row.props?.key ?? '').startsWith('row:')
      ? []
      : [[shown(row), shown(row).split('▉').length - 1]],
  )
}

const drillRows = (ui: Drawing) => rowsOf(ui, 'drill')

/** Row texts with the bar folded to one cell and the spacing to one space. */
const plain = (rows: [string, number][]) => rows.map(([text]) => text.replace(/▉+/, '▉').replace(/ +/g, ' ').trim())

async function overhead($: Engine, category = '') {
  const result = await $.command.run({
    command: 'context-bar',
    args: `overhead ${category}`.trim(),
    origin: { kind: 'composer' },
    presentation: { isFullscreen: true, columns: 85 },
  })
  await settle()

  return result
}

/** The colors of the legend's swatches, in order: each is one of the bar's own cells, as wide as one. */
async function swatchColors(ui: Drawing): Promise<unknown[]> {
  const legend = await keyed(ui, 'legend')

  return (legend ? descendants(legend) : []).filter(node => shown(node) === '▉').map(node => node.props?.color)
}

/** The lines of the box under `key`, each as the text it shows. */
async function linesOf(ui: Drawing, key: string): Promise<string[]> {
  return ((await keyed(ui, key))?.children ?? []).map(shown)
}

describe('context-bar', () => {
  test('/context-bar shows the meter above the prompt, and run again hides it', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ ...band(), surface })
      expect(await textOf(ui, 'legend')).toBeUndefined()

      await toggle($)
      const header = await textOf(ui, 'header')
      expect(header).toContain('context')
      expect(header).toContain('62.4k used · compacts at 155k')
      expect(header).toContain(' 40% ')
      const legend = await textOf(ui, 'legend')
      expect(legend).toContain('overhead 32.9k ▸')
      expect(legend).toContain('messages 29.5k')
      expect(legend).toContain('free 92.6k')
      expect(await linesOf(ui, 'breakdown')).toEqual([
        'overhead: tools 14.6k, mcp 11k, system 3.1k, skills 2k, memory files 1.2k,',
        'agents 1k',
      ])

      await toggle($)
      expect(await textOf(ui, 'legend')).toBeUndefined()
      await ui.unmount()
    }
  })

  test('opens the overhead as ranked bars from its legend entry, and closes them again', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    await toggle($)

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ ...band(), surface })
      expect(await keyed(ui, 'drill')).toBeUndefined()

      await ui.press({ key: 'overhead' })
      expect(await textOf(ui, 'legend')).toContain('overhead 32.9k ▾')
      expect(await keyed(ui, 'breakdown')).toBeUndefined()
      const rows = await drillRows(ui)
      expect(plain(rows)).toEqual([
        'tools ▉ 14.6k',
        'mcp ▸ ▉ 11k',
        'system ▉ 3.1k',
        'skills ▸ ▉ 2k',
        'memory files ▸ ▉ 1.2k',
        'agents ▸ ▉ 1k',
      ])
      expect(rows.map(([, cells]) => cells)).toEqual([52, 39, 11, 7, 4, 4])
      expect(Math.max(...rows.map(([text]) => text.length))).toBeLessThanOrEqual(76)

      await ui.press({ key: 'overhead' })
      expect(await keyed(ui, 'drill')).toBeUndefined()
      expect(await textOf(ui, 'breakdown')).toContain('overhead: tools 14.6k')
      await ui.unmount()
    }
  })

  test('/context-bar overhead opens the ranked bars, showing the card if it was hidden', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })

    await overhead($)
    expect(await textOf(ui, 'header')).toContain('62.4k used')
    expect((await drillRows(ui)).length).toBe(6)

    await overhead($)
    expect(await keyed(ui, 'drill')).toBeUndefined()
    expect(await textOf(ui, 'legend')).toContain('overhead 32.9k ▸')
  })

  test('answers an argument it does not know with the ones it does, changing nothing', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })

    const { text } = await $.command.run({
      command: 'context-bar',
      args: 'everything',
      origin: { kind: 'composer' },
      presentation: { isFullscreen: true, columns: 85 },
    })

    expect(text).toContain('/context-bar overhead')
    expect(await textOf(ui, 'legend')).toBeUndefined()
  })

  test('opens a category to its items from its row, one category at a time', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    await toggle($)

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ ...band(), surface })
      await ui.press({ key: 'overhead' })
      expect(await keyed(ui, 'items')).toBeUndefined()

      await ui.press({ key: 'category:skills' })
      expect(plain(await drillRows(ui))).toContain('skills ▾ ▉ 2k')
      const skills = await rowsOf(ui, 'items')
      expect(plain(skills)).toEqual([
        'plugin-authoring ▉ 400',
        'dataviz ▉ 300',
        'superpowers:brainstorming ▉ 250',
        'superpowers:test-driven-dev… ▉ 200',
        'superpowers:writing-plans ▉ 200',
        'frontend-design:frontend-de… ▉ 150',
        'skill-creator ▉ 150',
        'code-review ▉ 120',
      ])
      expect(skills.map(([, cells]) => cells)).toEqual([38, 29, 24, 19, 19, 14, 14, 11])
      expect(Math.max(...skills.map(([text]) => text.length))).toBeLessThanOrEqual(76)
      expect(await textOf(ui, 'more')).toContain('+ 2 more 230')

      await ui.press({ key: 'category:mcp' })
      expect(plain(await rowsOf(ui, 'items'))).toEqual(['playwright ▉ 5.6k', 'github ▉ 4.2k', 'instructions ▉ 1.2k'])
      expect(await keyed(ui, 'more')).toBeUndefined()

      await ui.press({ key: 'category:mcp' })
      expect(await keyed(ui, 'items')).toBeUndefined()
      await ui.press({ key: 'overhead' })
      await ui.unmount()
    }
  })

  test('opens the rest of a long category, and folds it back', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    await toggle($)

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ ...band(), surface })
      await ui.press({ key: 'overhead' })
      await ui.press({ key: 'category:skills' })
      expect(await textOf(ui, 'more')).toBe('+ 2 more 230 ▸')

      await ui.press({ key: 'more' })
      const all = await rowsOf(ui, 'items')
      expect(all.length).toBe(10)
      expect(plain(all).slice(-2)).toEqual(['simplify ▉ 120', 'run ▉ 110'])
      expect(await textOf(ui, 'more')).toBe('show fewer ▴')

      await ui.press({ key: 'more' })
      expect((await rowsOf(ui, 'items')).length).toBe(8)
      expect(await textOf(ui, 'more')).toBe('+ 2 more 230 ▸')
      await ui.press({ key: 'category:skills' })
      await ui.press({ key: 'overhead' })
      await ui.unmount()
    }
  })

  test('folds a long category back when another one opens', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    await toggle($)
    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })
    await ui.press({ key: 'overhead' })
    await ui.press({ key: 'category:skills' })
    await ui.press({ key: 'more' })

    await ui.press({ key: 'category:agents' })
    await ui.press({ key: 'category:skills' })

    expect((await rowsOf(ui, 'items')).length).toBe(8)
    expect(await textOf(ui, 'more')).toBe('+ 2 more 230 ▸')
  })

  test('opens nothing for a category the breakdown does not itemize', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    await toggle($)

    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })
    await ui.press({ key: 'overhead' })
    const buttons = await ui.findAll({ type: 'Button' })

    expect(buttons.map(button => button.key)).toEqual([
      'overhead',
      'category:mcp',
      'category:skills',
      'category:memory files',
      'category:agents',
    ])
  })

  test('/context-bar overhead memory files opens that category from the keyboard', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })

    await overhead($, 'memory files')

    expect((await drillRows(ui)).length).toBe(6)
    expect(plain(await rowsOf(ui, 'items'))).toEqual(['CLAUDE.md (user) ▉ 700', 'CLAUDE.md (project) ▉ 500'])
  })

  test('answers a category with nothing to open with the ones that have something', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    await toggle($)
    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })

    const { text } = await overhead($, 'system')

    expect(text).toContain('mcp, skills, memory files or agents')
    expect(await keyed(ui, 'items')).toBeUndefined()
  })

  test('writes every label in lower case', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    await toggle($)

    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })
    const card = shown((await ui.drawn()) as unknown as Drawn)
    expect(card).toBe(card.toLowerCase())
  })

  test('fills toward compaction: overhead in gray, the conversation in the accent, the room left as the track', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    await toggle($)

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ ...band(), surface })
      expect(await barRunsOf(ui)).toEqual([
        ['inactive', 16],
        ['claude', 15],
        ['subtle', 45],
      ])
      // Every cell is the same glyph, so all of them stand at one height with a thin gap after each.
      expect((await textOf(ui, 'bar'))?.replaceAll('▉', '')).toBe('')
      const bar = await keyed(ui, 'bar')
      expect(descendants(bar!).some(node => node.props?.backgroundColor !== undefined)).toBe(false)
      expect(await swatchColors(ui)).toEqual(['inactive', 'claude', 'subtle'])
      await ui.unmount()
    }
  })

  test('paints only with the theme keys the mod API documents', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    await toggle($)

    // The ThemeKey union in the mod API's types; any other key resolves today but is an internal.
    const documented = [
      'text', 'inverseText', 'inactive', 'subtle', 'suggestion', 'remember', 'success', 'error',
      'warning', 'merged', 'claude', 'permission', 'planMode', 'autoAccept', 'promptBorder',
      'bashBorder', 'ide', 'diffAdded', 'diffRemoved', 'diffAddedDimmed', 'diffRemovedDimmed',
      'diffAddedWord', 'diffRemovedWord',
    ]
    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })
    const painted = descendants((await ui.drawn()) as unknown as Drawn).flatMap(node =>
      [node.props?.color, node.props?.backgroundColor, node.props?.borderColor].filter(color => color !== undefined),
    )

    expect(painted.length).toBeGreaterThan(0)
    expect(painted.filter(color => !documented.includes(String(color)))).toEqual([])
  })

  test('fills the card to its width, and drops the compaction note where it has no room', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    await toggle($)

    for (const columns of [80, 40, 12]) {
      const ui = await $.ui.mount({ ...band(columns), surface: 'terminal' })
      expect((await textOf(ui, 'bar'))?.length).toBe(columns - 4)
      await ui.unmount()
    }

    const wide = await $.ui.mount({ ...band(80), surface: 'terminal' })
    expect(await textOf(wide, 'header')).toContain('compacts at 155k')
    const narrow = await $.ui.mount({ ...band(40), surface: 'terminal' })
    expect(await textOf(narrow, 'header')).toContain('62.4k used')
    expect(await textOf(narrow, 'header')).not.toContain('compacts at')
  })

  test('packs the legend into as few rows as fit', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    await toggle($)

    const rows = async (columns: number) => {
      const ui = await $.ui.mount({ ...band(columns), surface: 'terminal' })
      const legend = await keyed(ui, 'legend')
      await ui.unmount()

      return legend?.children?.length
    }

    expect(await rows(80)).toBe(1)
    expect(await rows(40)).toBe(2)
  })

  test('follows the context as it grows, turn by turn', async ($, on) => {
    let current = BREAKDOWN
    engine(on, () => current)
    mock.store(on)
    await start($)
    await toggle($)
    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })

    current = LATER
    await measure($, LATER)

    expect(await textOf(ui, 'header')).toContain('110k used')
    expect(await textOf(ui, 'legend')).toContain('messages 77k')
  })

  test('counts the overhead exactly, as /context does, not from the local estimates', async ($, on) => {
    const asked = engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    await toggle($)
    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })

    // The estimates alone would read overhead 54.2k and messages 8.2k.
    const legend = await textOf(ui, 'legend')
    expect(legend).toContain('overhead 32.9k ▸')
    expect(legend).toContain('messages 29.5k')
    expect(await textOf(ui, 'header')).toContain('62.4k used')
    expect(asked.full).toBe(1)
  })

  test('counts again only once the overhead changes, as when a tool loads', async ($, on) => {
    let current = BREAKDOWN
    const asked = engine(on, () => current)
    mock.store(on)
    await start($)
    await toggle($)
    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })

    current = LATER
    await measure($, LATER)
    expect(asked.full).toBe(1)
    expect(await textOf(ui, 'legend')).toContain('messages 77k')

    current = LOADED
    await measure($, LOADED)
    expect(asked.full).toBe(2)
    expect(await textOf(ui, 'legend')).toContain('overhead 35.9k ▸')
    expect(await textOf(ui, 'legend')).toContain('messages 74k')
  })

  test('leaves small drift in the estimates to the standing count', async ($, on) => {
    let current = BREAKDOWN
    const asked = engine(on, () => current)
    mock.store(on)
    await start($)
    await toggle($)
    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })

    current = changed({ Skills: 2_100, Messages: 76_900 }, 109_900, 55)
    await measure($, current)

    expect(asked.full).toBe(1)
    expect(await textOf(ui, 'legend')).toContain('overhead 32.9k ▸')
    expect(await textOf(ui, 'header')).toContain('110k used')
  })

  test('keeps the estimates when the exact count fails, and does not ask again every turn', async ($, on) => {
    let current = BREAKDOWN
    const asked = engine(on, () => current, true)
    mock.store(on)
    await start($)
    await toggle($)
    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })

    expect(await textOf(ui, 'legend')).toContain('overhead 54.2k ▸')

    current = LATER
    await measure($, LATER)
    expect(asked.full).toBe(1)
    expect(await textOf(ui, 'header')).toContain('110k used')
  })

  test('breaks the breakdown only between its entries', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    await toggle($)

    const ui = await $.ui.mount({ ...band(40), surface: 'terminal' })
    expect(await linesOf(ui, 'breakdown')).toEqual([
      'overhead: tools 14.6k, mcp 11k,',
      'system 3.1k, skills 2k,',
      'memory files 1.2k, agents 1k',
    ])
  })

  test('colors the percentage by how close compaction is', async ($, on) => {
    let current = BREAKDOWN
    engine(on, () => current)
    mock.store(on)
    await start($)
    await toggle($)
    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })
    const badge = async () => (await ui.findAll({ type: 'Text', text: /^ \d+% $/ }))[0]?.props.backgroundColor

    expect(await badge()).toBe('success')

    current = LATER
    await measure($, LATER)
    expect(await badge()).toBe('warning')

    current = NEAR
    await measure($, NEAR)
    expect(await badge()).toBe('error')
  })

  test('remembers the choice for the next session', async ($, on) => {
    engine(on, () => BREAKDOWN)
    const saved = new Map<string, unknown>()
    on('store.get', ($, e) => ({ value: saved.get(e.key) }))
    on('store.set', ($, e) => {
      saved.set(e.key, e.value)

      return { value: undefined }
    })
    await start($)

    await toggle($)
    expect(saved.get('isVisible')).toBe(true)

    await toggle($)
    expect(saved.get('isVisible')).toBe(false)
  })

  test('comes back on in a session after one that turned it on', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on, { isVisible: true })
    await start($)

    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })
    expect(await textOf(ui, 'legend')).toContain('messages 29.5k')
  })

  for (const reason of ['clear', 'resume'] as const) {
    test(`measures the new conversation after a /${reason}`, async ($, on) => {
      let current = BREAKDOWN
      const { clock } = engine(on, () => current)
      mock.store(on)
      on('session.end', ($, e) => ({ sessionId: e.sessionId }))
      await start($)
      await toggle($)
      const ui = await $.ui.mount({ ...band(), surface: 'terminal' })

      current = CLEARED
      await $.session.end({ reason, sessionId: 'before', resume: { id: 'before' } })
      await clock.advance(1_000)

      expect(await textOf(ui, 'header')).toContain('32.9k used')
      expect(await textOf(ui, 'legend')).toContain('messages 0')
    })
  }

  test('drops after a compaction', async ($, on) => {
    let current = BREAKDOWN
    const { clock } = engine(on, () => current)
    mock.store(on)
    on('session.compact', () => ({ messages: [{ role: 'user', text: 'Summary: we built a context bar.', toolUses: [] }] }))
    await start($)
    await toggle($)
    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })

    current = COMPACTED
    await $.session.compact({
      trigger: 'manual',
      messages: [
        { role: 'user', text: 'Build a context bar mod.', toolUses: [] },
        { role: 'assistant', text: 'Built it; the tests pass.', toolUses: [] },
      ],
    })
    await clock.advance(1_000)

    expect(await textOf(ui, 'header')).toContain('36k used')
  })

  test('opens the card in a pane where the session has no band, as in VS Code, and closes it again', async ($, on) => {
    const asked = engine(on, () => BREAKDOWN, false, () => ['vscode'])
    mock.store(on)
    await start($)

    const { text } = await $.command.run({
      command: 'context-bar',
      args: '',
      origin: { kind: 'composer' },
      presentation: { isFullscreen: true, columns: 85 },
    })
    await settle()
    expect(text).toBe('on, in a pane')
    expect(asked.opened).toEqual(['context-bar'])
    for (const surface of ['vscode', 'mobile'] as const) {
      const ui = await $.ui.mount({ ...pane(), surface })
      expect(await textOf(ui, 'header')).toContain('62.4k used · compacts at 155k')
      expect(await textOf(ui, 'legend')).toContain('overhead 32.9k ▸')
      // The pane's own frame holds the card, so the bar takes the whole body.
      expect((await textOf(ui, 'bar'))?.length).toBe(80)
      await ui.unmount()
    }

    await toggle($)
    expect(asked.closed).toEqual(['context-bar'])
  })

  test('opens the overhead and its categories in the pane, as in the band', async ($, on) => {
    engine(on, () => BREAKDOWN, false, () => ['vscode'])
    mock.store(on)
    await start($)
    await toggle($)
    const ui = await $.ui.mount({ ...pane(), surface: 'vscode' })

    await ui.press({ key: 'overhead' })
    await ui.press({ key: 'category:mcp' })

    expect((await drillRows(ui)).length).toBe(6)
    expect(plain(await rowsOf(ui, 'items'))).toEqual(['playwright ▉ 5.6k', 'github ▉ 4.2k', 'instructions ▉ 1.2k'])
  })

  test("keeps the pane's choice apart from the band's", async ($, on) => {
    engine(on, () => BREAKDOWN, false, () => ['vscode'])
    const saved = new Map<string, unknown>([['isVisible', true]])
    on('store.get', ($, e) => ({ value: saved.get(e.key) }))
    on('store.set', ($, e) => {
      saved.set(e.key, e.value)

      return { value: undefined }
    })
    await start($)

    await toggle($)
    expect(saved.get('isPaneVisible')).toBe(true)
    await toggle($)
    expect(saved.get('isPaneVisible')).toBe(false)
    expect(saved.get('isVisible')).toBe(true)
  })

  test('opens the pane again when VS Code joins a session it was left on in', async ($, on) => {
    let surfaces: RenderSurface[] = []
    const asked = engine(on, () => BREAKDOWN, false, () => surfaces)
    mock.store(on, { isPaneVisible: true })
    await start($)
    expect(asked.opened).toEqual([])

    surfaces = ['vscode']
    await $.session.attach({ surface: 'vscode', clientId: 'vscode:default' })
    await settle()

    expect(asked.opened).toEqual(['context-bar'])
    const ui = await $.ui.mount({ ...pane(), surface: 'vscode' })
    expect(await textOf(ui, 'legend')).toContain('overhead 32.9k ▸')
  })

  test('measures nothing in a session that draws nowhere, as a -p run', async ($, on) => {
    const asked = engine(on, () => BREAKDOWN, false, () => [])
    mock.store(on, { isVisible: true, isPaneVisible: true })
    await start($)
    await measure($, LATER)

    expect(asked.summary).toBe(0)
    expect(asked.full).toBe(0)
    expect(asked.opened).toEqual([])
  })

  test('answers with the figures where nothing draws, as in VS Code today, and keeps nothing on', async ($, on) => {
    const asked = engine(on, () => BREAKDOWN, false, () => [])
    const saved = new Map<string, unknown>()
    on('store.get', ($, e) => ({ value: saved.get(e.key) }))
    on('store.set', ($, e) => {
      saved.set(e.key, e.value)

      return { value: undefined }
    })
    await start($)
    const run = () =>
      $.command.run({
        command: 'context-bar',
        args: '',
        origin: { kind: 'composer' },
        presentation: { isFullscreen: false, columns: 85 },
      })

    const { text } = await run()
    expect(text?.split('\n').slice(0, 3)).toEqual([
      '◆ context  62.4k used · compacts at 155k · 40%',
      'overhead 32.9k · messages 29.5k · free 92.6k',
      'overhead: tools 14.6k, mcp 11k, system 3.1k, skills 2k, memory files 1.2k, agents 1k',
    ])
    expect(asked.full).toBe(1)
    expect(asked.opened).toEqual([])
    expect(saved.size).toBe(0)

    // The count stands while the overhead holds, and no card measures after a turn.
    await run()
    const summaries = asked.summary
    await measure($, LATER)
    expect(asked.full).toBe(1)
    expect(asked.summary).toBe(summaries)
  })

  test('stops measuring a card left on once a snapshot is asked for where nothing draws', async ($, on) => {
    let surfaces: RenderSurface[] = ['terminal']
    const asked = engine(on, () => BREAKDOWN, false, () => surfaces)
    mock.store(on)
    await start($)
    await toggle($)

    surfaces = []
    await overhead($, '')
    const summaries = asked.summary
    await measure($, LATER)

    expect(asked.summary).toBe(summaries)
  })

  test("lists a category's biggest items in the snapshot", async ($, on) => {
    engine(on, () => BREAKDOWN, false, () => [])
    mock.store(on)
    await start($)

    const { text } = await overhead($, 'skills')
    expect(text).toContain(
      'skills 2k: plugin-authoring 400, dataviz 300, superpowers:brainstorming 250, superpowers:test-driven-development 200, ' +
        'superpowers:writing-plans 200, frontend-design:frontend-design 150, skill-creator 150, code-review 120, + 2 more 230',
    )
    expect((await overhead($, 'system')).text).toContain('mcp, skills, memory files or agents')
  })

  test('draws in the band, not a pane, where a terminal shows the session too', async ($, on) => {
    const asked = engine(on, () => BREAKDOWN, false, () => ['terminal', 'mobile'])
    mock.store(on)
    await start($)
    await toggle($)

    expect(asked.opened).toEqual([])
    const ui = await $.ui.mount({ ...band(), surface: 'terminal' })
    expect(await textOf(ui, 'legend')).toContain('overhead 32.9k ▸')
  })

  test('makes way for a survey', async ($, on) => {
    engine(on, () => BREAKDOWN)
    mock.store(on)
    await start($)
    await toggle($)

    const ui = await $.ui.mount({ ...band(80, true), surface: 'terminal' })
    expect(await textOf(ui, 'legend')).toBeUndefined()
  })
})
