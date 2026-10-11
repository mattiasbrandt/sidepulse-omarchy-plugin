// The popup-open OR across widget copies (helper/popup.mjs), run under node.

import assert from "node:assert/strict"
import { test } from "node:test"

import * as Popup from "../helper/popup.mjs"

test("nothing open means the helper gets no device frames", () => {
  assert.equal(Popup.anyPopupOpen([]), false)
})

test("one copy open is enough, and closing it closes the flag", () => {
  const a = {}
  const open = Popup.withOpenPopup([], a, true)
  assert.equal(Popup.anyPopupOpen(open), true)
  assert.equal(Popup.anyPopupOpen(Popup.withOpenPopup(open, a, false)), false)
})

test("the flag stays on while any other copy is still open", () => {
  const center = {}
  const right = {}
  let open = Popup.withOpenPopup([], center, true)
  open = Popup.withOpenPopup(open, right, true)
  open = Popup.withOpenPopup(open, center, false)
  assert.equal(Popup.anyPopupOpen(open), true)
  open = Popup.withOpenPopup(open, right, false)
  assert.equal(Popup.anyPopupOpen(open), false)
})

test("a copy appears once however often it reports open", () => {
  const a = {}
  const open = Popup.withOpenPopup(Popup.withOpenPopup([], a, true), a, true)
  assert.equal(open.length, 1)
})

test("a copy that closes without reporting (destroyed) is removed by its owner key", () => {
  const a = {}
  const b = {}
  let open = Popup.withOpenPopup([], a, true)
  open = Popup.withOpenPopup(open, b, true)
  open = Popup.withOpenPopup(open, b, false)
  assert.deepEqual(open.map((entry) => entry.owner), [a])
})
