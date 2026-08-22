/**
 * The Flow tab's graph has to stay inside its own box.
 *
 * It did not. `FlowCanvas` sized its canvas to the graph's own extent — `Math.max(height,
 * maxLayer * LAYER_Y + …)`, roughly 1,200px for the seventeen-stage graph — inside a
 * console column that is about 520px tall, and the tab panel holding it did not clip. A
 * React Flow node is absolutely positioned, so the surplus did not merely spill: the
 * lower stages painted across the composer, which is exactly what the owner reported.
 *
 * The fix is containment — the box is whatever height the panel has, `fitView` scales the
 * graph into it — and containment is a property of two class lists and one absent prop.
 * That is what this reads off the source. The browser sweep in `flowOverlap.shot.mjs`
 * measures the rendered result; this is the cheap guard that stops the *cause* coming
 * back, because "the canvas grows to the graph" is a change somebody makes on purpose,
 * believing it helps legibility, and nothing else in the suite would notice.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const read = (name) =>
  readFileSync(fileURLToPath(new URL(`../../src/components/console/${name}`, import.meta.url)), 'utf8')

const canvas = read('FlowCanvas.tsx')
const console_ = read('ChatConsole.tsx')

test('the canvas is bounded by its parent, not by the graph', () => {
  // The failure mode, named: a height derived from how many layers the graph has.
  assert.ok(
    !/canvasHeight|maxLayer\s*\*\s*LAYER_Y/.test(canvas),
    'FlowCanvas is sizing its box from the graph extent again — that is the overlap bug',
  )
  assert.ok(
    !/style=\{\{\s*height:/.test(canvas),
    'FlowCanvas is setting an inline canvas height again',
  )
  assert.ok(
    !/<FlowCanvas[^>]*height=/.test(console_),
    'ChatConsole is handing FlowCanvas a fixed height again',
  )

  const box = /className=\{cn\(\s*'relative min-h-0 w-full min-w-0 flex-1 overflow-hidden'/.test(canvas)
  assert.ok(box, 'the canvas box must stay a clipping, shrinkable flex child (min-h-0 flex-1 overflow-hidden)')
})

test('the flow tab panel clips, so nothing can paint onto the composer', () => {
  const at = console_.indexOf('id="console-panel-flow"')
  assert.ok(at > 0, 'could not find the flow tab panel')
  // The panel's own attributes, up to the first child it renders.
  const panel = console_.slice(at, console_.indexOf('>', console_.indexOf('className', at)))
  for (const required of ['overflow-hidden', 'min-h-0', 'flex-col']) {
    assert.ok(panel.includes(required), `the flow tab panel lost '${required}':\n${panel}`)
  }
})

test('the fit is allowed to shrink the graph, and has a readability floor', () => {
  const fit = /const FIT: FitViewOptions = \{([^}]+)\}/.exec(canvas)
  assert.ok(fit, 'FlowCanvas no longer declares its fitView options in one place')
  const minZoom = Number(/minZoom:\s*([\d.]+)/.exec(fit[1])?.[1])
  // `minZoom: 1` is what the old canvas used: a "fit" that may only centre, never scale,
  // which is why the graph overflowed instead of fitting.
  assert.ok(minZoom > 0 && minZoom < 1, `fitView must be able to scale down (minZoom was ${minZoom})`)
  assert.ok(minZoom >= 0.4, `a fit below ${minZoom} is smaller than the stage labels survive`)
})
