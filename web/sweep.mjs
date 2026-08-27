/** Measures every portal screen: page height, and any card with dead vertical space. */
import { chromium } from 'playwright'
const BASE = 'http://127.0.0.1:3001'
const ACCOUNTS = { tenant_admin: 'northwind.admin', client: 'northwind.client',
  ai_team: 'northwind.analyst', devops: 'devops', platform_admin: 'admin' }
const b = await chromium.launch({ headless: true })
const c = await b.newContext({ viewport: { width: 1280, height: 900 } })
const p = await c.newPage()
async function login(u) {
  await p.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' })
  await p.evaluate(() => { try { localStorage.clear(); sessionStorage.clear() } catch {} })
  await p.context().clearCookies()
  await p.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' }); await p.waitForTimeout(1000)
  await p.locator('input[name="username"], input#username, input[type="text"]').first().fill(u)
  await p.locator('input[type="password"]').first().fill('demo')
  await p.locator('button[type="submit"]').first().click()
  await p.waitForURL((x) => !x.pathname.startsWith('/login'), { timeout: 25000 }).catch(()=>{})
  await p.waitForTimeout(2000)
}
const src = await (await fetch(`${BASE}/`)).text().catch(()=>null)
const SECTIONS = JSON.parse(process.env.SECTIONS)
const out = []
for (const [role, secs] of Object.entries(SECTIONS)) {
  await login(ACCOUNTS[role])
  for (const s of secs) {
    await p.goto(`${BASE}/app/${role}/${s}`, { waitUntil: 'domcontentloaded' })
    await p.waitForTimeout(2600)
    const m = await p.evaluate(() => {
      const h = document.body.scrollHeight
      // a grid row where one card is much shorter than its sibling = dead canvas
      let gaps = 0
      for (const g of document.querySelectorAll('div[class*="grid"]')) {
        const kids = [...g.children].filter((k) => k.getBoundingClientRect().height > 0)
        if (kids.length < 2) continue
        const hs = kids.map((k) => k.getBoundingClientRect().height)
        const max = Math.max(...hs), min = Math.min(...hs)
        if (max - min > 220) gaps++
      }
      return { h, gaps }
    })
    out.push({ screen: `${role}/${s}`, ...m })
  }
}
await p.close(); await c.close(); await b.close()
out.sort((a,z)=>z.h-a.h)
console.log('  TALLEST 12:')
for (const r of out.slice(0,12)) console.log(`    ${String(r.h).padStart(6)}px  gaps=${r.gaps}  ${r.screen}`)
const g = out.filter(r=>r.gaps>0)
console.log(`\n  screens with dead canvas: ${g.length}/${out.length}`)
for (const r of g.slice(0,10)) console.log(`    ${r.screen} (${r.gaps})`)
