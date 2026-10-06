// Takes a mod's screenshots by running a real Claude Code session.
// Usage: node tools/screenshots/shoot.mjs <scenario.json> [--out <dir>] [--keep]
//
// Creates a throwaway project from the scenario's "project", drives a session in it
// through the scenario's "steps", and writes `<name>-<mark>.png` for each of its
// "shots" next to the scenario, or into --out.
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { FRAME, renderShots } from './render.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const args = process.argv.slice(2)
const flag = name => {
  const i = args.indexOf(name)
  return i < 0 ? undefined : args.splice(i, 2)[1]
}
const keep = args.includes('--keep') && args.splice(args.indexOf('--keep'), 1)
const out = resolve(flag('--out') ?? dirname(resolve(args[0] ?? '.')))
const scenarioFile = args[0] && resolve(args[0])
if (!scenarioFile) {
  console.error('usage: node tools/screenshots/shoot.mjs <scenario.json> [--out <dir>] [--keep]')
  process.exit(2)
}
const scenario = JSON.parse(readFileSync(scenarioFile, 'utf8'))

function run(command, argv, options = {}) {
  const result = spawnSync(command, argv, { stdio: 'inherit', ...options })
  if (result.status !== 0) throw new Error(`${command} ${argv.join(' ')} failed (exit ${result.status})`)
}

function chrome() {
  const candidates = [
    process.env.CHROME,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    'google-chrome',
    'chromium',
    'chromium-browser',
  ].filter(Boolean)
  const found = candidates.find(c => (c.startsWith('/') ? existsSync(c) : spawnSync('which', [c]).status === 0))
  if (!found) throw new Error('no Chrome or Chromium found; set CHROME to its path')
  return found
}

const work = mkdtempSync(join(tmpdir(), 'mod-shots-'))
const workspace = join(work, scenario.project)
try {
  console.log(`project ${scenario.project} in ${work}`)
  mkdirSync(workspace)
  run('git', ['init', '-q'], { cwd: workspace })
  run('python3', ['-I', join(here, 'projects', `${scenario.project}.py`), workspace])

  console.log(`session on ${scenario.model ?? 'the default model'}, ${scenario.cols ?? 120}x${scenario.rows ?? 34}`)
  run('python3', ['-I', join(here, 'drive.py'), workspace, work, scenarioFile])

  // A capture never shows where the project lives or whose machine it ran on.
  const redact = { [workspace]: `~/${scenario.project}`, [homedir()]: '~' }
  const shots = await renderShots(work, { ...scenario, redact })
  const missing = Object.keys(scenario.shots).filter(mark => !shots.some(shot => shot.mark === mark))
  if (missing.length > 0) throw new Error(`the session never reached: ${missing.join(', ')}`)

  const browser = chrome()
  mkdirSync(out, { recursive: true })
  const width = Math.round((scenario.cols ?? 120) * 9.05) + 74
  for (const shot of shots) {
    const leaks = shot.text.filter(line => line.includes(work) || line.includes(homedir()))
    if (leaks.length > 0) throw new Error(`${shot.mark} still shows a local path: ${leaks[0]}`)
    const file = join(out, `${scenario.name}-${shot.mark}.png`)
    const height = FRAME.titleBar + FRAME.padding + shot.text.length * FRAME.row
    run(browser, ['--headless=new', '--disable-gpu', '--hide-scrollbars', `--window-size=${width},${height}`,
      `--screenshot=${file}`, `file://${shot.html}`], { stdio: 'ignore' })
    console.log(`wrote ${file}`)
  }
} finally {
  if (keep) console.log(`kept ${work}`)
  else rmSync(work, { recursive: true, force: true })
}
