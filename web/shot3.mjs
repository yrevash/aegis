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
const targets = JSON.parse(process.env.T)
for (const [role,sec] of targets) {
  await login(ACC[role])
  await p.goto(`${BASE}/app/${role}/${sec}`,{waitUntil:'domcontentloaded'});await p.waitForTimeout(3800)
  // list every top-level card with its heading and height, so I can decide from data
  const cards = await p.evaluate(()=>{
    const main=document.querySelector('main')||document.body
    const out=[]
    const walk=(el,d)=>{ for (const k of el.children){
      const cs=getComputedStyle(k), r=k.getBoundingClientRect()
      if (cs.borderRadius!=='0px' && r.height>60 && k.querySelector('h1,h2,h3')) {
        out.push({h:Math.round(r.height), t:(k.querySelector('h1,h2,h3')?.textContent||'').trim().slice(0,38)})
      } else if (d<4) walk(k,d+1)
    }}
    walk(main,0)
    return out
  })
  console.log(`\n══ ${role}/${sec}`)
  for (const x of cards) console.log(`   ${String(x.h).padStart(5)}px  ${x.t}`)
}
await p.close();await c.close();await b.close()
