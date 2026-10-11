// The Bar LEDs' blend and tone map (helper/barleds.mjs), checked against vectors
// that upstream's own functions produced (tests/barleds_vectors.json, verified
// against upstream's virtual_device.py by tests/test_bar_leds.py).

import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"

import * as Band from "../helper/barleds.mjs"

const vectors = JSON.parse(readFileSync(new URL("./barleds_vectors.json", import.meta.url), "utf8"))

function close(actual, expected, label) {
  assert.equal(actual.length, expected.length, label)
  actual.forEach((value, index) => {
    assert.ok(Math.abs(value - expected[index]) < 1e-9, `${label}[${index}]: ${value} != ${expected[index]}`)
  })
}

test("the blend matches upstream's blended_led_color_at_x", () => {
  for (const case_ of vectors.blend) {
    close(
      Band.blendedColourAt(vectors.colours, case_.x, case_.led_width),
      case_.expect,
      `blend at ${case_.x} of ${case_.led_width}`,
    )
  }
})

test("the tone map matches upstream's tone_mapped_led_color", () => {
  for (const case_ of vectors.tone) {
    const [red, green, blue, alpha] = case_.in
    close(
      Band.toneMappedColour(red, green, blue, alpha, case_.boost, case_.alpha_scale),
      case_.expect,
      `tone ${case_.in} boost ${case_.boost}`,
    )
  }
})

test("a zero-width LED slot blends to nothing", () => {
  assert.deepEqual(Band.blendedColourAt(vectors.colours, 10, 0), [0, 0, 0, 0])
})

test("frames convert to upstream's (r, g, b, alpha) with alpha the largest channel", () => {
  assert.deepEqual(Band.ledColours([[255, 0, 102], [0, 0, 0]]), [
    [1, 0, 0.4, 1],
    [0, 0, 0, 0],
  ])
})

test("the band is cut into columns of upstream's width and covers the whole band", () => {
  const columns = Band.bandColumns(Band.ledColours([[0, 229, 255]]), 220)
  assert.equal(columns.length, 55)
  assert.equal(columns[0].x, 0)
  assert.equal(columns[54].x + columns[54].width, 220)
  assert.ok(columns.every((column) => column.width === Band.BLEND_COLUMN_WIDTH || column.x === 216))
})

test("dark LEDs leave no columns to paint", () => {
  const columns = Band.bandColumns(Band.ledColours([[0, 0, 0], [0, 0, 0]]), 220)
  assert.ok(columns.every((column) => column.core === null && column.hotline === null))
})

test("paintBand fills each lit column's core and hotline on the outer edge", () => {
  const calls = []
  const ctx = {
    fillStyle: "",
    clearRect: (...args) => calls.push(["clear", ...args]),
    fillRect: (x, y, w, h) => calls.push([ctx.fillStyle, x, y, w, h]),
  }
  Band.paintBand(ctx, Band.ledColours([[0, 229, 255]]), 8, true)
  assert.deepEqual(calls[0], ["clear", 0, 0, 8, Band.LED_BAND_HEIGHT])
  const hot = calls.filter((call) => call[2] !== 0 && call[0] !== "clear")
  assert.ok(hot.length > 0)
  assert.ok(hot.every((call) => Math.abs(call[2] - (Band.LED_BAND_HEIGHT - 1.15)) < 1e-9))
})
