/** One thing a category is made of: an MCP server, an agent, a memory file, a skill. */
export type ContextBarItem = {
  name: string
  tokens: number
}

/**
 * One row of /context's breakdown that sits inside the window: a category
 * in use, the free space, or the autocompact buffer.
 */
export type ContextBarSegment = {
  name: string
  tokens: number
  kind: 'used' | 'free' | 'buffer'
  /** What the category is made of, where the breakdown itemizes it. */
  items?: ContextBarItem[]
}

/** The window as the card draws it, measured after each turn. */
export type ContextBarUsage = {
  /** /context's rows inside the window, in its order. */
  segments: ContextBarSegment[]
  totalTokens: number
  maxTokens: number
  /** The token count auto-compaction runs at, or null when it is off. */
  compactsAt: number | null
}

/** The overhead as last counted with the token-count API, as /context counts it. */
export type ContextBarCount = {
  /** The overhead's local estimates when it was counted; the count is taken again once they shift. */
  basis: ContextBarItem[]
  /** The overhead's categories as counted, or null when the count failed and the estimates stand. */
  segments: ContextBarSegment[] | null
}

declare module 'claude-code' {
  interface PluginState {
    'context-bar': {
      isVisible: boolean
      /** Whether the overhead is open as ranked bars; for the session only. */
      isDrilled: boolean
      /** The overhead category open to its items, by its label; for the session only. */
      openCategory: string | null
      /** The open category showing every item, not only the largest; for the session only. */
      expandedCategory: string | null
      usage: ContextBarUsage | null
      /** The last exact count of the overhead; for the session only. */
      counted: ContextBarCount | null
    }
  }
}
