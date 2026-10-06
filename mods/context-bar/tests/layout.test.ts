import { describe, expect, test } from 'claude-code/testing'
import type { SessionContextBreakdown } from 'claude-code'

import type { ContextBarUsage } from '../types'
import {
  allocateCells,
  badgeColor,
  barRuns,
  breakdownLines,
  formatTokens,
  hasShifted,
  meter,
  overheadOf,
  packRows,
  rankRows,
  scaleBars,
  toUsage,
  topItems,
  withCount,
} from '../hooks/layout'

describe('allocateCells', () => {
  const cases: [string, number[], number, number[]][] = [
    ['splits the width in proportion to tokens', [50, 50], 10, [5, 5]],
    ['hands a leftover cell to the largest remainder', [2, 3, 5], 7, [1, 2, 4]],
    ['keeps a tiny segment visible as one cell', [1, 999], 10, [1, 9]],
    ['takes the cells it gives tiny segments from the widest', [1, 1, 98], 10, [1, 1, 8]],
    ['gives an empty segment nothing', [0, 100], 10, [0, 10]],
    ['draws nothing at zero width', [5, 5], 0, [0, 0]],
    ['draws nothing when there are no tokens', [0, 0], 10, [0, 0]],
  ]

  for (const [name, tokens, width, want] of cases) {
    test(name, () => {
      expect(allocateCells(tokens, width)).toEqual(want)
    })
  }

  test('never overflows when segments outnumber cells', () => {
    const cells = allocateCells([1, 1, 1], 2)

    expect(cells[0]! + cells[1]! + cells[2]!).toBe(2)
    expect(Math.max(...cells)).toBe(1)
  })
})

describe('formatTokens', () => {
  const cases: [number, string][] = [
    [0, '0'],
    [999, '999'],
    [1_000, '1k'],
    [1_234, '1.2k'],
    [62_400, '62.4k'],
    [99_960, '100k'],
    [200_000, '200k'],
    [212_200, '212k'],
    [787_800, '788k'],
    [999_999, '1M'],
    [1_500_000, '1.5M'],
  ]

  for (const [tokens, want] of cases) {
    test(`${tokens} reads ${want}`, () => {
      expect(formatTokens(tokens)).toBe(want)
    })
  }
})

describe('barRuns', () => {
  test('draws every cell as a seven-eighths block in its segment color', () => {
    const segments = [
      { tokens: 50, color: 'gray' },
      { tokens: 50, color: 'orange' },
    ]

    expect(barRuns(segments, 4)).toEqual([
      { glyph: '▉', color: 'gray', length: 2 },
      { glyph: '▉', color: 'orange', length: 2 },
    ])
  })

  test('keeps a thin segment visible as one cell', () => {
    const segments = [
      { tokens: 1, color: 'gray' },
      { tokens: 99, color: 'orange' },
    ]

    expect(barRuns(segments, 4)).toEqual([
      { glyph: '▉', color: 'gray', length: 1 },
      { glyph: '▉', color: 'orange', length: 3 },
    ])
  })

  test('leaves out a segment with no tokens', () => {
    const segments = [
      { tokens: 0, color: 'gray' },
      { tokens: 10, color: 'orange' },
    ]

    expect(barRuns(segments, 3)).toEqual([{ glyph: '▉', color: 'orange', length: 3 }])
  })

  test('draws nothing when there is no room', () => {
    expect(barRuns([{ tokens: 5, color: 'gray' }], 0)).toEqual([])
  })
})

describe('scaleBars', () => {
  const cases: [string, number[], number, number[]][] = [
    ['fills the width with the largest and scales the rest to it', [26_100, 10_000, 3_500, 2_500, 1_200], 20, [20, 8, 3, 2, 1]],
    ['keeps a tiny value visible as one cell', [100, 1], 10, [10, 1]],
    ['draws nothing for nothing', [0, 0], 10, [0, 0]],
    ['draws nothing when there is no room', [5, 3], 0, [0, 0]],
  ]

  for (const [name, tokens, width, want] of cases) {
    test(name, () => {
      expect(scaleBars(tokens, width)).toEqual(want)
    })
  }
})

describe('packRows', () => {
  const cases: [string, number[], number, number[][]][] = [
    ['fills a row while the next entry and its gap fit', [10, 10, 10], 36, [[0, 1, 2]]],
    ['starts a new row where the next entry would overflow', [10, 10, 10], 34, [[0, 1], [2]]],
    ['gives an entry wider than the row a row of its own', [50, 5], 20, [[0], [1]]],
    ['makes no rows for no entries', [], 20, []],
  ]

  for (const [name, widths, width, want] of cases) {
    test(name, () => {
      expect(packRows(widths, width, 3)).toEqual(want)
    })
  }
})

describe('toUsage', () => {
  const breakdown: SessionContextBreakdown = {
    categories: [
      { name: 'System prompt', tokens: 3_100, color: 'promptBorder', isDeferred: false, kind: 'used' },
      { name: 'MCP tools (deferred)', tokens: 41_000, color: 'inactive', isDeferred: true, kind: 'deferred' },
      { name: 'Messages', tokens: 30_700, color: 'purple_FOR_SUBAGENTS_ONLY', isDeferred: false, kind: 'used' },
      { name: 'Autocompact buffer', tokens: 45_000, color: 'inactive', isDeferred: false, kind: 'buffer' },
      { name: 'Free space', tokens: 121_200, color: 'promptBorder', isDeferred: false, kind: 'free' },
    ],
    totalTokens: 33_800,
    maxTokens: 200_000,
    rawMaxTokens: 200_000,
    autocompactSource: 'auto',
    percentage: 17,
    gridRows: [],
    model: 'claude-opus-5-5',
    memoryFiles: [],
    mcpTools: [],
    agents: [],
    autoCompactThreshold: 155_000,
    isAutoCompactEnabled: true,
    apiUsage: null,
  }

  test('leaves out the tool schemas deferred until searched for', () => {
    expect(toUsage(breakdown).segments.map(segment => segment.name)).not.toContain('MCP tools (deferred)')
  })

  test('knows where compaction starts, and that it may not', () => {
    expect(toUsage(breakdown).compactsAt).toBe(155_000)

    const { autoCompactThreshold, ...manual } = breakdown
    expect(toUsage({ ...manual, isAutoCompactEnabled: false }).compactsAt).toBeNull()
  })
})

describe('meter', () => {
  const used = (name: string, tokens: number) => ({ name, tokens, kind: 'used' as const })
  const usage: ContextBarUsage = {
    segments: [
      used('System prompt', 3_100),
      used('System tools', 14_600),
      used('Custom agents', 1_000),
      used('Plugin hooks', 800),
      used('Messages', 29_500),
      { name: 'Free space', tokens: 106_000, kind: 'free' },
      { name: 'Autocompact buffer', tokens: 45_000, kind: 'buffer' },
    ],
    totalTokens: 49_000,
    maxTokens: 200_000,
    compactsAt: 155_000,
  }

  test('fills toward compaction: the conversation, the overhead, and the room left', () => {
    expect(meter(usage)).toEqual({
      capacity: 155_000,
      used: 49_000,
      percent: 32,
      messages: 29_500,
      overhead: {
        tokens: 19_500,
        parts: [
          { label: 'tools', tokens: 14_600 },
          { label: 'system', tokens: 3_100 },
          { label: 'agents', tokens: 1_000 },
          { label: 'plugin hooks', tokens: 800 },
        ],
      },
      free: 106_000,
    })
  })

  /**
   * The window as /context lists it with auto-compaction off: no compaction
   * point, the buffers held back for /compact, the rest free.
   */
  function off(buffers: number[], totalTokens = 49_000): ContextBarUsage {
    const held = buffers.reduce((sum, n) => sum + n, 0)

    return {
      ...usage,
      segments: [
        ...usage.segments.filter(segment => segment.kind === 'used' && segment.name !== 'Messages'),
        used('Messages', totalTokens - 19_500),
        ...buffers.map(tokens => ({ name: 'Compact buffer', tokens, kind: 'buffer' as const })),
        { name: 'Free space', tokens: Math.max(0, 200_000 - totalTokens - held), kind: 'free' as const },
      ],
      totalTokens,
      compactsAt: null,
    }
  }

  test('with auto-compaction off, leaves the buffer for /compact out of the room left, and reads the percentage of the window', () => {
    // /context reads 98.8k of 200k as 49%, with 98.2k free past its 3k compact buffer.
    const m = meter(off([3_000], 98_800))

    expect(m.capacity).toBe(197_000)
    expect(m.free).toBe(98_200)
    expect(m.percent).toBe(49)
    expect(badgeColor(m)).toBe('success')
  })

  test('adds up every buffer held back', () => {
    const m = meter(off([2_000, 1_000]))

    expect(m.capacity).toBe(197_000)
    expect(m.free).toBe(148_000)
  })

  test('fills toward the whole window when auto-compaction is off and nothing is held back', () => {
    const m = meter(off([]))

    expect(m.capacity).toBe(200_000)
    expect(m.free).toBe(151_000)
  })

  test('falls back to the whole window should a buffer ever fill it', () => {
    const m = meter(off([250_000]))

    expect(m.capacity).toBe(200_000)
    expect(m.percent).toBe(25)
    expect(m.free).toBe(151_000)
  })

  test('follows the compaction point where an auto-compact window sets it further in than the buffer', () => {
    // Unlike `usage`, whose 155k is the window less its 45k buffer, 120k is not: only reading the point itself passes.
    const m = meter({ ...usage, compactsAt: 120_000 })

    expect(m.capacity).toBe(120_000)
    expect(m.free).toBe(71_000)
  })

  test('has no room left, not less than none, past compaction', () => {
    const m = meter({ ...usage, totalTokens: 160_000 })

    expect(m.free).toBe(0)
    expect(m.percent).toBe(103)
  })

  test("puts both of MCP's categories under one mcp entry, with what each is made of", () => {
    const m = meter({
      ...usage,
      segments: [
        used('MCP server instructions', 1_300),
        { ...used('MCP tools', 900), items: [{ name: 'playwright', tokens: 900 }] },
        used('Messages', 29_500),
      ],
    })

    expect(m.overhead.parts).toEqual([
      {
        label: 'mcp',
        tokens: 2_200,
        items: [
          { name: 'playwright', tokens: 900 },
        ],
      },
    ])
  })
})

describe('the exact count', () => {
  const used = (name: string, tokens: number) => ({ name, tokens, kind: 'used' as const })
  const estimate: ContextBarUsage = {
    segments: [
      used('System prompt', 5_600),
      used('System tools', 34_100),
      used('Messages', 258_300),
      { name: 'Free space', tokens: 682_000, kind: 'free' },
    ],
    totalTokens: 298_000,
    maxTokens: 1_000_000,
    compactsAt: null,
  }

  test('takes the overhead from the count and leaves the conversation the rest of the total', () => {
    const counted = withCount(estimate, {
      basis: [],
      segments: [used('System prompt', 2_600), used('System tools', 14_800)],
    })

    expect(counted.totalTokens).toBe(298_000)
    expect(meter(counted).overhead.tokens).toBe(17_400)
    expect(meter(counted).messages).toBe(280_600)
    expect(counted.segments.find(segment => segment.kind === 'free')?.tokens).toBe(682_000)
  })

  test('keeps the estimates without a count, or when the count failed', () => {
    expect(withCount(estimate, null)).toBe(estimate)
    expect(withCount(estimate, { basis: [], segments: null })).toBe(estimate)
  })

  test('reads the overhead as every category in use but the conversation', () => {
    expect(overheadOf(estimate).map(segment => segment.name)).toEqual(['System prompt', 'System tools'])
  })

  const basis = [
    { name: 'System tools', tokens: 34_100 },
    { name: 'Skills', tokens: 10_000 },
  ]
  const cases: [string, { name: string; tokens: number }[], boolean][] = [
    ['stands while nothing moved', basis, false],
    ['stands through drift under 200 tokens', [{ name: 'System tools', tokens: 34_100 }, { name: 'Skills', tokens: 10_150 }], false],
    ['stands through drift under 2% of a large category', [{ name: 'System tools', tokens: 34_700 }, { name: 'Skills', tokens: 10_000 }], false],
    ['shifts when a category moves past both', [{ name: 'System tools', tokens: 35_100 }, { name: 'Skills', tokens: 10_000 }], true],
    ['shifts when a category appears', [...basis, { name: 'MCP tools', tokens: 300 }], true],
    ['shifts when a category goes', basis.slice(0, 1), true],
  ]

  for (const [name, estimates, want] of cases) {
    test(name, () => {
      expect(hasShifted(estimates, basis)).toBe(want)
    })
  }
})

describe('breakdownLines', () => {
  const parts = [
    { label: 'tools', tokens: 34_100 },
    { label: 'memory files', tokens: 7_100 },
    { label: 'agents', tokens: 3_700 },
  ]

  test('keeps the breakdown on one line where it fits', () => {
    expect(breakdownLines(parts, 80)).toEqual(['overhead: tools 34.1k, memory files 7.1k, agents 3.7k'])
  })

  test('breaks only between entries, never inside one', () => {
    expect(breakdownLines(parts, 34)).toEqual(['overhead: tools 34.1k,', 'memory files 7.1k, agents 3.7k'])
  })

  test('has no lines for no overhead', () => {
    expect(breakdownLines([], 80)).toEqual([])
  })
})

describe('toUsage items', () => {
  const detailed: SessionContextBreakdown = {
    categories: [
      { name: 'System prompt', tokens: 3_100, color: 'promptBorder', isDeferred: false, kind: 'used' },
      { name: 'MCP tools', tokens: 9_800, color: 'cyan_FOR_SUBAGENTS_ONLY', isDeferred: false, kind: 'used' },
      { name: 'Custom agents', tokens: 1_000, color: 'permission', isDeferred: false, kind: 'used' },
      { name: 'Memory files', tokens: 1_200, color: 'claude', isDeferred: false, kind: 'used' },
      { name: 'Skills', tokens: 700, color: 'warning', isDeferred: false, kind: 'used' },
      { name: 'Messages', tokens: 30_000, color: 'purple_FOR_SUBAGENTS_ONLY', isDeferred: false, kind: 'used' },
    ],
    totalTokens: 45_800,
    maxTokens: 200_000,
    rawMaxTokens: 200_000,
    autocompactSource: 'auto',
    percentage: 23,
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
      { name: 'mcp__github__search', serverName: 'github', tokens: 1_000, isLoaded: false },
    ],
    agents: [
      { agentType: 'plugin-dev:plugin-validator', source: 'plugin', tokens: 600 },
      { agentType: 'code-reviewer', source: 'userSettings', tokens: 400 },
    ],
    skills: {
      totalSkills: 2,
      includedSkills: 2,
      tokens: 700,
      skillFrontmatter: [
        { name: 'dataviz', source: 'built-in', tokens: 300 },
        { name: 'superpowers:brainstorming', source: 'plugin', pluginName: 'superpowers', tokens: 400 },
      ],
    },
    autoCompactThreshold: 155_000,
    isAutoCompactEnabled: true,
    apiUsage: null,
  }
  const itemsOf = (name: string) => toUsage(detailed).segments.find(segment => segment.name === name)?.items

  test('gathers the MCP tools in the window by server, leaving out those still deferred', () => {
    expect(itemsOf('MCP tools')).toEqual([
      { name: 'playwright', tokens: 5_600 },
      { name: 'github', tokens: 4_200 },
    ])
  })

  test('names agents by type, memory files by file and where they load from, skills as listed', () => {
    expect(itemsOf('Custom agents')).toEqual([
      { name: 'plugin-dev:plugin-validator', tokens: 600 },
      { name: 'code-reviewer', tokens: 400 },
    ])
    expect(itemsOf('Memory files')).toEqual([
      { name: 'CLAUDE.md (user)', tokens: 700 },
      { name: 'CLAUDE.md (project)', tokens: 500 },
    ])
    expect(itemsOf('Skills')).toEqual([
      { name: 'dataviz', tokens: 300 },
      { name: 'superpowers:brainstorming', tokens: 400 },
    ])
  })

  test('makes the MCP server instructions one item, so they sit beside the tools under mcp', () => {
    const withInstructions = {
      ...detailed,
      categories: [
        ...detailed.categories,
        { name: 'MCP server instructions', tokens: 1_300, color: 'green_FOR_SUBAGENTS_ONLY', isDeferred: false, kind: 'used' as const },
      ],
    }
    const mcp = meter(toUsage(withInstructions)).overhead.parts.find(part => part.label === 'mcp')

    expect(mcp?.tokens).toBe(11_100)
    expect(mcp?.items).toEqual([
      { name: 'playwright', tokens: 5_600 },
      { name: 'github', tokens: 4_200 },
      { name: 'instructions', tokens: 1_300 },
    ])
  })

  test('gives a category the breakdown does not itemize no items', () => {
    expect(itemsOf('System prompt')).toBeUndefined()
    expect(itemsOf('Messages')).toBeUndefined()
  })

  test('carries the items into the meter, largest first', () => {
    const skills = meter(toUsage(detailed)).overhead.parts.find(part => part.label === 'skills')

    expect(skills?.items).toEqual([
      { name: 'superpowers:brainstorming', tokens: 400 },
      { name: 'dataviz', tokens: 300 },
    ])
  })
})

describe('topItems', () => {
  const items = [500, 400, 300, 200, 100].map((tokens, i) => ({ name: `item ${i}`, tokens }))

  test('keeps the first few and sums up the rest', () => {
    expect(topItems(items, 3)).toEqual({
      shown: items.slice(0, 3),
      rest: { count: 2, tokens: 300 },
    })
  })

  test('has no rest when every item fits', () => {
    expect(topItems(items, 5)).toEqual({ shown: items, rest: null })
  })
})

describe('rankRows', () => {
  test('pads each label to the widest, scales each bar to the largest, and sets out the amounts', () => {
    expect(rankRows([{ label: 'tools', tokens: 26_100 }, { label: 'system prompt', tokens: 2_500 }], 40)).toEqual({
      labelWidth: 13,
      rows: [
        { label: 'tools', pad: 8, cells: 19, amount: '26.1k' },
        { label: 'system prompt', pad: 0, cells: 2, amount: '2.5k' },
      ],
    })
  })

  test('cuts a label longer than the most it may take, with an ellipsis', () => {
    const { labelWidth, rows } = rankRows([{ label: 'plugin:chrome-devtools-mcp:chrome-devtools', tokens: 900 }], 60, 20)

    expect(labelWidth).toBe(20)
    expect(rows[0]?.label).toBe('plugin:chrome-devto…')
    expect(rows[0]?.pad).toBe(0)
  })
})
