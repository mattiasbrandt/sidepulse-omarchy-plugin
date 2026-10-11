// The bar icon's tooltip and the IPC status() line (helper/status.mjs), run under node.

import assert from "node:assert/strict"
import { test } from "node:test"

import * as Status from "../helper/status.mjs"

const working = {
  state: "working",
  label: "Working",
  urgent: false,
  active_count: 2,
  top: { display_name: "myproject" },
}

test("the tooltip says what a click does, on every branch", () => {
  const suffix = " · click for details"
  assert.equal(Status.tooltipText({ notInstalled: true, agentState: null, needsSetup: true }),
    "SidePulse: not installed" + suffix)
  assert.equal(Status.tooltipText({ notInstalled: false, agentState: null, needsSetup: false }),
    "SidePulse: starting" + suffix)
  assert.equal(Status.tooltipText({ notInstalled: false, agentState: working, needsSetup: false }),
    "SidePulse: Working, 2 active, myproject" + suffix)
  assert.equal(Status.tooltipText({ notInstalled: false, agentState: working, needsSetup: true }),
    "SidePulse: Working, 2 active, myproject (setup needed)" + suffix)
})

test("the tooltip example from the issue", () => {
  assert.equal(Status.tooltipText({ notInstalled: false, agentState: working, needsSetup: false }),
    "SidePulse: Working, 2 active, myproject · click for details")
})

test("status() before the helper reports says not installed or starting, with null state", () => {
  assert.deepEqual(JSON.parse(Status.statusJson({ notInstalled: true, agentState: null, needsSetup: true })), {
    state: null, label: "not installed", urgent: false, active_count: 0, top: null, needs_setup: true,
  })
  assert.deepEqual(JSON.parse(Status.statusJson({ notInstalled: false, agentState: null, needsSetup: false })), {
    state: null, label: "starting", urgent: false, active_count: 0, top: null, needs_setup: false,
  })
})

test("status() reports the state, label, urgent, count, top session and setup as one line", () => {
  const line = Status.statusJson({ notInstalled: false, agentState: working, needsSetup: true })
  assert.ok(!line.includes("\n"))
  assert.deepEqual(JSON.parse(line), {
    state: "working", label: "Working", urgent: false, active_count: 2, top: "myproject", needs_setup: true,
  })
})

test("status() gives top as null when no session leads", () => {
  const idle = { state: "idle", label: "Idle", urgent: false, active_count: 0, top: null }
  assert.equal(JSON.parse(Status.statusJson({ notInstalled: false, agentState: idle, needsSetup: false })).top, null)
})
