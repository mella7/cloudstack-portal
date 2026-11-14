// Records the frames for docs/demo.gif against a DRY_RUN backend.
// Usage: start backend (DRY_RUN=true) and frontend, then
//   FRAMES_DIR=/tmp/frames node e2e/record-demo.mjs
//   ffmpeg -framerate 5 -i /tmp/frames/f%04d.png -vf "scale=900:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=none" ../docs/demo.gif
import assert from 'node:assert/strict'
import { mkdirSync } from 'node:fs'
import puppeteer from 'puppeteer-core'

const BASE = process.env.BASE_URL ?? 'http://localhost:5173'
const FRAMES = process.env.FRAMES_DIR ?? '/tmp/frames'
const FRAME_MS = 200
mkdirSync(FRAMES, { recursive: true })

const health = await (await fetch(`${BASE}/api/health`)).json()
assert.equal(health.dry_run, true, 'refusing to record: the backend is not in DRY_RUN mode')

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH ?? '/usr/bin/google-chrome',
  args: ['--no-sandbox'],
})
const page = await browser.newPage()
await page.setViewport({ width: 1100, height: 760 })

let frame = 0
let recording = true
const recorder = (async () => {
  while (recording) {
    const started = Date.now()
    await page.screenshot({ path: `${FRAMES}/f${String(frame++).padStart(4, '0')}.png` })
    await sleep(Math.max(0, FRAME_MS - (Date.now() - started)))
  }
})()

const show = (selector, block = 'center') =>
  page.evaluate((s, b) => document.querySelector(s)?.scrollIntoView({ block: b, behavior: 'instant' }), selector, block)
const button = (label) => [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === label && !b.disabled)
const click = async (label) => {
  await page.waitForFunction(`(${button})(${JSON.stringify(label)})`)
  await page.evaluate(`(${button})(${JSON.stringify(label)}).click()`)
}

try {
  // Fill in the form.
  await page.goto(BASE, { waitUntil: 'networkidle0' })
  await sleep(800)
  await page.type('#name', 'web', { delay: 120 })
  await page.focus('#instance_count')
  await page.keyboard.press('Backspace')
  await page.type('#instance_count', '2', { delay: 120 })
  await page.select('#keypair', 'ops-team')
  await sleep(1000)

  // Stage 1: the plan streams in, then waits for confirmation.
  await click('Create plan')
  await page.waitForSelector('.log-line')
  const jobHash = await page.evaluate(() => location.hash)
  await show('.log', 'end')
  await page.waitForSelector('.card-attention')
  await sleep(600)
  await show('.card-attention')
  await sleep(2500)

  // Stage 2: confirm and apply.
  await click('Confirm and apply')
  await sleep(300)
  await show('.log', 'end')
  await page.waitForSelector('.status-done')
  await sleep(1500)
  await show('.topbar', 'start')
  await sleep(1500)

  // The new VMs, live from the API, with a power action.
  await page.goto(`${BASE}/#/`, { waitUntil: 'networkidle0' })
  await page.waitForSelector('.row-actions')
  await show('section.card:last-of-type', 'end')
  await sleep(1500)
  await page.evaluate(() => {
    const row = [...document.querySelectorAll('tr')].find((r) => r.cells[0]?.textContent === 'web-1')
    ;[...row.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Stop').click()
  })
  await sleep(2000)

  // Destroy goes through the same review.
  await page.goto(`${BASE}/${jobHash}`, { waitUntil: 'networkidle0' })
  await sleep(800)
  await click('Destroy these instances...')
  await page.waitForSelector('.card-danger')
  await show('.card-danger')
  await sleep(2500)
  await click('Confirm and destroy')
  await sleep(300)
  await show('.log', 'end')
  await page.waitForSelector('.status-done')
  await sleep(2000)
} finally {
  recording = false
  await recorder
  await browser.close()
}
console.log(`${frame} frames in ${FRAMES}`)
