// Replays a recorded Claude Code session through xterm.js and writes each marked
// screen as a terminal window in HTML, ready for a browser to capture.
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import xterm from '@xterm/headless'

const { Terminal } = xterm

// The window frame belongs to the api-call skill, so that an installed copy of the skill carries it;
// this tool, which only runs from the repository, reads it in place.
const FRAME_FILE = join(dirname(fileURLToPath(import.meta.url)), '../../skills/api-call/skills/api-call/scripts/frame.html')

// The frame's text and background, for cells drawn inverse.
const FG = '#e4e4e7'
const BG = '#1c1c1f'
// The 16 ANSI colors, toned for a dark window; Claude Code draws most of its UI in true color.
const ANSI = ['#1c1c1f', '#e06c75', '#98c379', '#e5c07b', '#61afef', '#c678dd', '#56b6c2', '#d4d4d8',
  '#5c6370', '#ef7b85', '#a9d48a', '#f0cf8c', '#74bdf5', '#d38ae6', '#67c7d3', '#ffffff']

/** The frame's title bar, padding and row heights, in pixels: what a capture's height is made of. */
export const FRAME = { titleBar: 37, padding: 22, row: 20 }

const hex = n => `#${n.toString(16).padStart(6, '0')}`
const escape = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

function palette(n) {
  if (n < 16) return ANSI[n]
  if (n < 232) {
    const c = n - 16
    const v = x => (x === 0 ? 0 : 55 + x * 40)
    return `rgb(${v(Math.floor(c / 36))},${v(Math.floor(c / 6) % 6)},${v(c % 6)})`
  }
  const g = 8 + (n - 232) * 10
  return `rgb(${g},${g},${g})`
}

function color(cell, which) {
  const value = which === 'fg' ? cell.getFgColor() : cell.getBgColor()
  if (which === 'fg' ? cell.isFgRGB() : cell.isBgRGB()) return hex(value)
  if (which === 'fg' ? cell.isFgPalette() : cell.isBgPalette()) return palette(value)
  return null
}

/** The screen as it stands: one line of styled HTML and one of plain text per row. */
function screen(term, cols, rows, redact) {
  const buffer = term.buffer.active
  const cell = buffer.getNullCell()
  const html = []
  const text = []
  for (let y = 0; y < rows; y++) {
    const line = buffer.getLine(buffer.viewportY + y)
    let markup = ''
    let plain = ''
    let run = null
    const flush = () => {
      if (run) markup += run.style ? `<span style="${run.style}">${escape(run.text)}</span>` : escape(run.text)
      run = null
    }
    for (let x = 0; x < cols; x++) {
      line?.getCell(x, cell)
      if (!line || cell.getWidth() === 0) continue
      const chars = cell.getChars() || ' '
      let fg = color(cell, 'fg')
      let bg = color(cell, 'bg')
      if (cell.isInverse()) [fg, bg] = [bg ?? BG, fg ?? FG]
      const style = [
        fg && `color:${fg}`,
        bg && `background:${bg}`,
        cell.isBold() && 'font-weight:700',
        cell.isDim() && 'opacity:.55',
        cell.isItalic() && 'font-style:italic',
        cell.isUnderline() && 'text-decoration:underline',
      ].filter(Boolean).join(';')
      if (run && run.style === style) run.text += chars
      else {
        flush()
        run = { style, text: chars }
      }
      plain += chars
    }
    flush()
    for (const [from, to] of Object.entries(redact)) {
      markup = markup.split(escape(from)).join(escape(to))
      plain = plain.split(from).join(to)
    }
    html.push(markup)
    text.push(plain.replace(/\s+$/, ''))
  }
  return { html, text }
}

/**
 * The rows a shot keeps: from the first prompt (past the startup banner) to the last
 * row drawn, with any empty stretch longer than two rows squeezed down to two.
 */
function keptRows(text) {
  let first = text.findIndex(line => line.startsWith('❯'))
  if (first < 0) first = 0
  let last = text.length - 1
  while (last > first && text[last].trim() === '') last--
  const kept = []
  let blanks = 0
  for (let y = first; y <= last; y++) {
    blanks = text[y].trim() === '' ? blanks + 1 : 0
    if (blanks <= 2) kept.push(y)
  }
  return kept
}

/** The frame with the title and the screen's HTML in place; a function replacement keeps `$` in them literal. */
function page(title, body) {
  return readFileSync(FRAME_FILE, 'utf8').replaceAll('{{title}}', () => escape(title)).replaceAll('{{body}}', () => body)
}

/**
 * Replays `<runDir>/raw.bin` and writes `<runDir>/<mark>.html` for each shot the
 * scenario names, returning each one's kept rows as plain text.
 */
export async function renderShots(runDir, { cols = 120, rows = 34, shots, redact = {} }) {
  const raw = readFileSync(`${runDir}/raw.bin`)
  const marks = JSON.parse(readFileSync(`${runDir}/marks.json`, 'utf8'))
  const term = new Terminal({ cols, rows, allowProposedApi: true, scrollback: 0 })
  const write = data => new Promise(resolve => term.write(data, resolve))
  const rendered = []
  let offset = 0
  for (const [mark, at] of Object.entries(marks).sort((a, b) => a[1] - b[1])) {
    await write(raw.subarray(offset, at))
    offset = at
    if (!(mark in shots)) continue
    const { html, text } = screen(term, cols, rows, redact)
    const kept = keptRows(text)
    writeFileSync(`${runDir}/${mark}.html`, page(shots[mark], kept.map(y => html[y]).join('\n')))
    rendered.push({ mark, html: `${runDir}/${mark}.html`, text: kept.map(y => text[y]) })
  }
  return rendered
}
