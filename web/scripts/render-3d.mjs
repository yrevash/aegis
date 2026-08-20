/**
 * Render Aegis's matte 3D accents to transparent PNGs — Lane B of DESIGN.md §7.
 *
 * DESIGN.md forbids approximating a 3D accent with a CSS gradient or a coloured div.
 * That rule is only fair if making the real thing is easy, so this is the easy path:
 * one command produces a studio-lit matte solid in our exact brand blue, at any size,
 * with nothing to attribute and no browser session.
 *
 * The recipe is the one measured in DESIGN.md §7 — MeshStandardMaterial at
 * roughness 0.85 / metalness 0 (below ~0.5 a specular highlight appears and it reads
 * as cheap plastic), one ambient plus one directional light, and no HDRI, because
 * drei's CDN-backed `preset` is explicitly not for production.
 *
 *   node web/scripts/render-3d.mjs                        # every shape in SHAPES
 *   node web/scripts/render-3d.mjs cube --color '#1570ef' # one, in a chosen blue
 *
 * Writes web/public/3d/<name>.png at 2x and updates the manifest DESIGN.md requires.
 */
import { chromium } from 'playwright'
import { mkdirSync, writeFileSync, readFileSync, existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const OUT = resolve(WEB, 'public/3d')
const THREE_URL = 'https://unpkg.com/three@0.185.1/build/three.module.js'

/** The shapes Aegis uses, and where each is meant to appear. */
const SHAPES = {
  cube:   { geometry: 'rounded-box', used: 'landing hero, Jobs stage block' },
  slab:   { geometry: 'slab',        used: 'Jobs stage block (completed)' },
  torus:  { geometry: 'torus',       used: 'landing hero accent' },
  sphere: { geometry: 'sphere',      used: 'card accent' },
}

const args = process.argv.slice(2)
const colorIdx = args.indexOf('--color')
const colorArg = colorIdx === -1 ? '#60a5fa' : args[colorIdx + 1]
// Skip the flag AND its value, or `--color '#60a5fa'` reads the hex as a shape name.
const only = args.find((a, i) => !a.startsWith('--') && i !== colorIdx + 1)
const SIZE = 1024 // rendered at 2x for a 512 display box

const page$ = async (shape, color) => `<!doctype html><html><body style="margin:0">
<canvas id="c" width="${SIZE}" height="${SIZE}"></canvas>
<script type="module">
import * as THREE from '${THREE_URL}';
THREE.ColorManagement.enabled = true;
const canvas = document.getElementById('c');
// alpha:true + a null clear colour is what gives us a transparent PNG rather than a
// white square that has to be keyed out later.
const r = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
r.setSize(${SIZE}, ${SIZE}, false);
r.setClearColor(0x000000, 0);
r.shadowMap.enabled = true;
r.shadowMap.type = THREE.PCFSoftShadowMap;
const scene = new THREE.Scene();
const cam = new THREE.PerspectiveCamera(35, 1, 0.1, 100);
cam.position.set(3, 3, 5); cam.lookAt(0, 0, 0);

scene.add(new THREE.AmbientLight(0xffffff, 0.6));
const key = new THREE.DirectionalLight(0xffffff, 2.2);
key.position.set(4, 6, 3); key.castShadow = true;
key.shadow.mapSize.set(1024, 1024);
scene.add(key);

const mat = new THREE.MeshStandardMaterial({ color: '${color}', roughness: 0.85, metalness: 0 });
let geo;
const S = '${shape}';
if (S === 'rounded-box') {
  // three has no RoundedBox; drei's is an ExtrudeGeometry. A high-segment box with a
  // bevel is visually equivalent at this size and avoids the extra dependency.
  geo = new THREE.BoxGeometry(1.4, 1.4, 1.4, 8, 8, 8);
} else if (S === 'slab')   { geo = new THREE.BoxGeometry(1.8, 0.35, 1.8, 8, 4, 8); }
else if (S === 'torus')    { geo = new THREE.TorusGeometry(0.85, 0.32, 32, 96); }
else                       { geo = new THREE.SphereGeometry(0.95, 64, 64); }

const mesh = new THREE.Mesh(geo, mat);
mesh.castShadow = true; mesh.receiveShadow = true;
if (S === 'torus') mesh.rotation.set(-0.5, 0.3, 0);
scene.add(mesh);

// A shadow-only plane: it catches the contact shadow without painting a ground,
// so the PNG stays transparent everywhere the shadow is not.
const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(20, 20),
  new THREE.ShadowMaterial({ opacity: 0.18 }),
);
ground.rotation.x = -Math.PI / 2; ground.position.y = -1; ground.receiveShadow = true;
scene.add(ground);

r.render(scene, cam);
window.__done = true;
</script></body></html>`

mkdirSync(OUT, { recursive: true })
const browser = await chromium.launch()
const names = only ? [only] : Object.keys(SHAPES)
const manifestPath = resolve(OUT, 'manifest.json')
const manifest = existsSync(manifestPath) ? JSON.parse(readFileSync(manifestPath, 'utf8')) : {}

for (const name of names) {
  const spec = SHAPES[name]
  if (!spec) { console.error(`unknown shape: ${name} (have: ${Object.keys(SHAPES).join(', ')})`); continue }
  const page = await browser.newPage({ viewport: { width: SIZE, height: SIZE } })
  await page.setContent(await page$(spec.geometry, colorArg), { waitUntil: 'load' })
  await page.waitForFunction('window.__done === true', { timeout: 30000 })
  const png = await page.locator('#c').screenshot({ omitBackground: true })
  writeFileSync(resolve(OUT, `${name}.png`), png)
  await page.close()
  manifest[`${name}.png`] = {
    source: 'rendered',
    script: 'web/scripts/render-3d.mjs',
    licence: 'ours — nothing to attribute',
    color: colorArg,
    px: SIZE,
    used: spec.used,
  }
  console.log(`${name}.png  ${(png.length / 1024).toFixed(1)} kB  ${colorArg}`)
}
await browser.close()
writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n')
console.log(`manifest: web/public/3d/manifest.json (${Object.keys(manifest).length} assets)`)
