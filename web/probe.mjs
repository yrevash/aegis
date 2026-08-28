import { chromium } from 'playwright'
const BASE='http://127.0.0.1:3001'
const b=await chromium.launch({headless:true})
const c=await b.newContext({viewport:{width:1280,height:900}})
const p=await c.newPage()
async function login(u){
  await p.goto(`${BASE}/`,{waitUntil:'domcontentloaded'})
  await p.evaluate(()=>{try{localStorage.clear();sessionStorage.clear()}catch{}})
  await p.context().clearCookies()
  await p.goto(`${BASE}/login`,{waitUntil:'domcontentloaded'});await p.waitForTimeout(1000)
  await p.locator('input[name="username"], input#username, input[type="text"]').first().fill(u)
  await p.locator('input[type="password"]').first().fill('demo')
  await p.locator('button[type="submit"]').first().click()
  await p.waitForURL(x=>!x.pathname.startsWith('/login'),{timeout:25000}).catch(()=>{})
  await p.waitForTimeout(2000)
}
await login('northwind.analyst')
for (const s of ['graph','memory','llmops']) {
  await p.goto(`${BASE}/app/ai_team/${s}`,{waitUntil:'domcontentloaded'});await p.waitForTimeout(3500)
  const tall = await p.evaluate(()=>{
    const out=[]
    for (const el of document.querySelectorAll('section,div[class*="rounded"],article')) {
      const h=el.getBoundingClientRect().height
      if (h>700) {
        const head=el.querySelector('h1,h2,h3')
        out.push({h:Math.round(h), t:(head?.textContent||'').trim().slice(0,44), kids:el.children.length})
      }
    }
    return out.sort((a,z)=>z.h-a.h).slice(0,6)
  })
  console.log(`\n  ── ai_team/${s}`)
  for (const t of tall) console.log(`     ${String(t.h).padStart(6)}px  kids=${String(t.kids).padStart(3)}  ${t.t||'(no heading)'}`)
}
await p.close();await c.close();await b.close()
