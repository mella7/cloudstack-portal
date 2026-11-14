// End-to-end check of the whole flow in a real browser, against a DRY_RUN backend.
// Needs the backend on :8000 and the frontend on BASE_URL. See README, "Tests".
import assert from 'node:assert/strict'
import puppeteer from 'puppeteer-core'

const BASE = process.env.BASE_URL ?? 'http://localhost:5173'
const SHOTS = process.env.SCREENSHOT_DIR

async function waitForServer() {
  for (let i = 0; i < 60; i++) {
    try {
      const health = await (await fetch(`${BASE}/api/health`)).json()
      assert.equal(health.dry_run, true, 'refusing to run: the backend is not in DRY_RUN mode')
      return
    } catch (e) {
      if (e instanceof assert.AssertionError) throw e
      await new Promise((r) => setTimeout(r, 500))
    }
  }
  throw new Error(`no backend behind ${BASE}/api/health`)
}

await waitForServer()
const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH ?? '/usr/bin/google-chrome',
  args: ['--no-sandbox'],
})
const page = await browser.newPage()
await page.setViewport({ width: 1200, height: 1400 })
const pageErrors = []
page.on('pageerror', (e) => pageErrors.push(String(e)))

const text = (sel) => page.$eval(sel, (e) => e.textContent)
const shot = async (name) => SHOTS && (await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true }))
const fill = async (sel, value) => {
  await page.focus(sel)
  await page.keyboard.down('Control')
  await page.keyboard.press('a')
  await page.keyboard.up('Control')
  await page.keyboard.press('Backspace')
  await page.type(sel, value)
}
const findButton = (label) =>
  [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === label && !b.disabled)
const clickButton = async (label) => {
  await page.waitForFunction(`(${findButton})(${JSON.stringify(label)})`)
  await page.evaluate(`(${findButton})(${JSON.stringify(label)}).click()`)
}
const vmNames = () =>
  page.$$eval('tr', (rows) => rows.filter((r) => r.querySelector('.row-actions')).map((r) => r.cells[0].textContent))

try {
  // 1. The form validates in the browser and does not submit bad input.
  await page.goto(BASE, { waitUntil: 'networkidle0' })
  const name = `e2e${Date.now() % 100000}`
  await fill('#name', 'Bad Name')
  await clickButton('Create plan')
  assert.match(await text('.field-error'), /Lowercase letters/)
  assert.equal(await page.evaluate(() => location.hash), '')

  // 2. A valid form starts a job and shows the plan, but creates nothing.
  await fill('#name', name)
  await fill('#instance_count', '2')
  await page.select('#keypair', 'ops-team')
  await page.click('.advanced summary')
  await page.type('#tags', 'env prod')
  await page.keyboard.press('Tab')
  assert.match(await text('.advanced .field-error'), /not a valid tag key/)
  await fill('#tags', 'env=e2e, team=ops')
  await page.select('#data_disk_offering', await page.$eval('#data_disk_offering option:last-child', (o) => o.value))
  await fill('#data_disk_size_gb', '80')
  await page.type('#user_data', '#cloud-config\n# e2e-secret-marker')
  await clickButton('Create plan')
  await page.waitForFunction(() => location.hash.startsWith('#/jobs/'))
  const createHash = await page.evaluate(() => location.hash)

  // 3. Reloading while the job runs reconnects to it and replays the log.
  await page.waitForSelector('.log-line')
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.card-attention')
  assert.match(await text('.plan-counts'), /4 to add, 0 to change, 0 to destroy/)
  assert.match(await text('.card-attention table'), new RegExp(`data disk.*${name}-1-data.*Custom size.*80 GB`))
  assert.match(await text('.facts'), /env=e2e, team=ops/)
  assert.ok(!(await text('body')).includes('e2e-secret-marker'), 'user data is never shown again')
  assert.match(await text('.card-attention table'), new RegExp(`${name}-1.*ops-team`))
  assert.match(await text('.log'), /terraform init/, 'the log from before the reload was replayed')
  await shot('plan')

  // 4. Confirming applies the plan.
  await clickButton('Confirm and apply')
  await page.waitForSelector('.status-done')
  assert.match(await text('.log'), /Apply complete! Resources: 4 added/)
  assert.equal(await text('.conn'), 'stream closed')

  // 5. The instances exist, and power actions go through the API.
  await page.goto(`${BASE}/#/`, { waitUntil: 'networkidle0' })
  await page.waitForSelector('.row-actions')
  assert.ok((await vmNames()).includes(`${name}-1`) && (await vmNames()).includes(`${name}-2`))
  await shot('overview')

  // 6. Destroy goes through the same review step.
  await page.goto(`${BASE}/${createHash}`, { waitUntil: 'networkidle0' })
  await clickButton('Destroy these instances...')
  await page.waitForFunction((h) => location.hash.startsWith('#/jobs/') && location.hash !== h, {}, createHash)
  await page.waitForSelector('.card-danger')
  assert.match(await text('.plan-counts'), /0 to add, 0 to change, 4 to destroy/)
  await shot('destroy-plan')
  await clickButton('Confirm and destroy')
  await page.waitForSelector('.status-done')
  await page.goto(`${BASE}/#/`, { waitUntil: 'networkidle0' })
  await page.waitForSelector('.row-actions')
  assert.ok(!(await vmNames()).some((n) => n.startsWith(name)), 'the instances are gone')

  assert.deepEqual(pageErrors, [])
  console.log('e2e ok')
} finally {
  await browser.close()
}
