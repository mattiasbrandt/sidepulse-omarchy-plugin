// The Bar LEDs' band on each bar edge (D7, amended for vertical bars): where it sits in its window,
// and the turn its canvas takes so that its LEDs run along a left or right bar (helper/barleds.mjs).

import assert from "node:assert/strict"
import { test } from "node:test"

import * as Band from "../helper/barleds.mjs"

// A canvas point mapped into the window, as the context transform (origin, then rotation) maps it.
// Rounded to 9 places: cos of a quarter turn is 6e-17, not 0.
function toWindow(layout, x, y) {
  const cos = Math.cos(layout.rotation)
  const sin = Math.sin(layout.rotation)
  const round = (value) => Math.round(value * 1e9) / 1e9
  return {
    x: round(layout.originX + cos * x - sin * y),
    y: round(layout.originY + sin * x + cos * y),
  }
}

test("a top bar's band sits centred on the inner (bottom) edge, its hotline at the bottom", () => {
  const layout = Band.bandLayout("top", 3440, 40)
  assert.deepEqual(
    { x: layout.x, y: layout.y, width: layout.width, height: layout.height, vertical: layout.vertical },
    { x: 1610, y: 35, width: 220, height: 5, vertical: false },
  )
  assert.equal(layout.outerEdgeIsBottom, true)
  assert.equal(layout.length, 220)
})

test("a bottom bar's band sits at the top edge, its hotline at the top", () => {
  const layout = Band.bandLayout("bottom", 1920, 40)
  assert.equal(layout.y, 0)
  assert.equal(layout.outerEdgeIsBottom, false)
})

test("a left bar's band runs down the inner (right) edge, centred vertically", () => {
  const layout = Band.bandLayout("left", 40, 1000)
  assert.deepEqual(
    { x: layout.x, y: layout.y, width: layout.width, height: layout.height, vertical: layout.vertical },
    { x: 35, y: 390, width: 5, height: 220, vertical: true },
  )
  assert.equal(layout.length, 220)
})

test("a right bar's band runs down the inner (left) edge", () => {
  const layout = Band.bandLayout("right", 40, 1000)
  assert.equal(layout.x, 0)
  assert.equal(layout.y, 390)
})

test("on a left bar the LEDs run top to bottom and the hotline faces the desktop (the right)", () => {
  const layout = Band.bandLayout("left", 40, 1000)
  assert.deepEqual(toWindow(layout, 0, 0), { x: 5, y: 0 })
  assert.deepEqual(toWindow(layout, 220, 0), { x: 5, y: 220 })
  // Local y 0 is the hotline side when outerEdgeIsBottom is false: it lands on the band's right column.
  assert.ok(toWindow(layout, 10, 0.5).x > 4)
})

test("on a right bar the hotline faces the desktop (the left)", () => {
  const layout = Band.bandLayout("right", 40, 1000)
  assert.ok(toWindow(layout, 10, 4.5).x < 1.2)
  assert.deepEqual(toWindow(layout, 0, 0), { x: 5, y: 0 })
})

test("a horizontal band needs no turn", () => {
  const layout = Band.bandLayout("top", 3440, 40)
  assert.equal(layout.rotation, 0)
  assert.equal(layout.originX, 0)
  assert.equal(layout.originY, 0)
})
