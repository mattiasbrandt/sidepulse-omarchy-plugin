// The Sessions rows' text and names (helper/sessions.mjs), run under node.

import assert from "node:assert/strict"
import { test } from "node:test"

import * as Sessions from "../helper/sessions.mjs"

test("ages read as upstream's format_age writes them", () => {
  assert.equal(Sessions.formatAge(0), "0s")
  assert.equal(Sessions.formatAge(59.9), "59s")
  assert.equal(Sessions.formatAge(60), "1m00s")
  assert.equal(Sessions.formatAge(187), "3m07s")
  assert.equal(Sessions.formatAge(3599), "59m59s")
  assert.equal(Sessions.formatAge(3600), "1h00m")
  assert.equal(Sessions.formatAge(7384), "2h03m")
})

test("a negative age reads as zero, and an unknown one as blank", () => {
  assert.equal(Sessions.formatAge(-5), "0s")
  assert.equal(Sessions.formatAge(NaN), "")
})

test("age is measured from the helper's epoch milliseconds, never negative", () => {
  assert.equal(Sessions.ageSeconds(1000, 61000), 60)
  assert.equal(Sessions.ageSeconds(5000, 1000), 0)
})

test("a message reads on one line, whatever newlines it carries", () => {
  assert.equal(Sessions.oneLine("<analysis>\n**Session flow**\n\n1. Architecture  "), "<analysis> **Session flow** 1. Architecture")
  assert.equal(Sessions.oneLine(null), "")
})

test("the provider reads with a capital, and nothing when unknown", () => {
  assert.equal(Sessions.providerLabel("claude"), "Claude")
  assert.equal(Sessions.providerLabel("codex"), "Codex")
  assert.equal(Sessions.providerLabel(undefined), "")
})

test("each session row has its own cursor name", () => {
  assert.equal(Sessions.sessionRow(0), "session:0")
  assert.equal(Sessions.sessionRow(9), "session:9")
})
