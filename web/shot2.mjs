import { chromium } from 'playwright'
const BASE = 'http://127.0.0.1:3001'
const b = await chromium.launch({ headless: true })
const c = await b.newContext({ viewport: { width: 1280, height: 900 } })
const p = await c.newPage()
async function login(u) {
  await p.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' })
  await p.evaluate(() => { try { localStorage.clear(); sessionStorage.clear() } catch {} })
  await p.context().clearCookies()
  await p.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' }); await p.waitForTimeout(1200)
  await p.locator('input[name="username"], input#username, input[type="text"]').first().fill(u)
  await p.locator('input[type="password"]').first().fill('demo')
  await p.locator('button[type="submit"]').first().click()
  await p.waitForURL((x) => !x.pathname.startsWith('/login'), { timeout: 25000 }).catch(() => {})
  await p.waitForTimeout(2500)
}
await login('northwind.admin')
await p.goto(BASE + '/app/tenant_admin/governance', { waitUntil: 'domcontentloaded' })
await p.waitForTimeout(6000)
const h = await p.evaluate(() => document.body.scrollHeight)
console.log('  governance full page height:', h, 'px')
await p.screenshot({ path: '/Users/yrevash/aegis/.demo/frames/gov-full.png', fullPage: true })
await p.close(); await c.close(); await b.close()
