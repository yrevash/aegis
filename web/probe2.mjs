import { chromium } from 'playwright'
const BASE='http://127.0.0.1:3001'
const ACC={tenant_admin:'northwind.admin',client:'northwind.client',ai_team:'northwind.analyst',platform_admin:'admin',devops:'devops'}
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
for (const [role,sec] of [['tenant_admin','memory'],['ai_team','guardrails'],['tenant_admin','llmops'],['platform_admin','approvals'],['tenant_admin','forecast']]) {
  await login(ACC[role])
  await p.goto(`${BASE}/app/${role}/${sec}`,{waitUntil:'domcontentloaded'});await p.waitForTimeout(3500)
  const t=await p.evaluate(()=>{
    const out=[]
    for (const el of document.querySelectorAll('div[class*="rounded-xl"],section,article')) {
      const h=el.getBoundingClientRect().height
      if (h>650) { const hd=el.querySelector('h1,h2,h3'); out.push({h:Math.round(h),t:(hd?.textContent||'').trim().slice(0,40)}) }
    }
    return out.sort((a,z)=>z.h-a.h).slice(0,4)
  })
  console.log(`  ── ${role}/${sec}`)
  for (const x of t) console.log(`     ${String(x.h).padStart(6)}px  ${x.t||'(no heading)'}`)
}
await p.close();await c.close();await b.close()
