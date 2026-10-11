// The Settings section's text and choices (helper/settings.mjs), run under node.

import assert from "node:assert/strict"
import { test } from "node:test"

import * as Settings from "../helper/settings.mjs"

test("timeout and retention presets are upstream's own units", () => {
  // Upstream's advanced settings are typed in minutes (idle) and hours (retention).
  assert.deepEqual(Settings.IDLE_PRESETS, [900, 1800, 3600, 7200, 14400])
  assert.deepEqual(Settings.RETENTION_PRESETS, [3600, 21600, 86400, 172800, 604800])
  // Upstream's defaults are on the list: 1 hour idle, 2 days retention.
  assert.ok(Settings.IDLE_PRESETS.includes(3600))
  assert.ok(Settings.RETENTION_PRESETS.includes(172800))
})

test("durations read in the largest whole unit", () => {
  assert.equal(Settings.durationLabel(900), "15 min")
  assert.equal(Settings.durationLabel(3600), "1 hour")
  assert.equal(Settings.durationLabel(7200), "2 hours")
  assert.equal(Settings.durationLabel(172800), "2 days")
  assert.equal(Settings.durationLabel(604800), "7 days")
  assert.equal(Settings.durationLabel(0), "None")
  assert.equal(Settings.durationLabel(5400), "90 min")
})

test("a step moves to the neighbouring preset and stops at the ends", () => {
  assert.equal(Settings.nextPreset(Settings.IDLE_PRESETS, 3600, 1), 7200)
  assert.equal(Settings.nextPreset(Settings.IDLE_PRESETS, 3600, -1), 1800)
  assert.equal(Settings.nextPreset(Settings.IDLE_PRESETS, 14400, 1), 14400)
  assert.equal(Settings.nextPreset(Settings.IDLE_PRESETS, 900, -1), 900)
})

test("a value that is not a preset steps to the nearest one in that direction", () => {
  // A hand-edited 2700 s (45 min) sits between 30 and 60 minutes.
  assert.equal(Settings.nextPreset(Settings.IDLE_PRESETS, 2700, 1), 3600)
  assert.equal(Settings.nextPreset(Settings.IDLE_PRESETS, 2700, -1), 1800)
  assert.equal(Settings.nextPreset(Settings.IDLE_PRESETS, 99999, -1), 14400)
  assert.equal(Settings.nextPreset(Settings.IDLE_PRESETS, 10, 1), 900)
})

test("hook rows name the agent as GLOSSARY writes it, and say what the config holds", () => {
  assert.equal(Settings.hookName("claude"), "Claude Code")
  assert.equal(Settings.hookName("codex"), "Codex")
  assert.equal(Settings.hookName("junie"), "Junie")
  // An agent upstream adds later shows under its own name, not dropped.
  assert.equal(Settings.hookName("newagent"), "newagent")
  // "On" means SidePulse's own hooks are installed, not any hook (installed comes from the helper).
  assert.equal(Settings.hookStatus({ exists: true, hooks_enabled: true, installed: true }), "On")
  assert.equal(Settings.hookStatus({ exists: true, hooks_enabled: false, installed: false }), "Off")
  assert.equal(Settings.hookStatus({ exists: false, hooks_enabled: false, installed: false }), "No config yet")
  // Another tool's hook on a known event is not SidePulse's: say so, do not claim it is on.
  assert.equal(
    Settings.hookStatus({ exists: true, hooks_enabled: true, installed: false, hook_events: ["Stop"] }),
    "Other hooks only")
  assert.equal(Settings.hookStatus({ provider: "claude", error: "AttributeError: x" }), "Unreadable")
  assert.equal(Settings.hookStatus(undefined), "Checking")
})

test("problems are titled by the part of SidePulse they break", () => {
  assert.equal(Settings.problemTitle("bar_leds"), "Bar LEDs")
  assert.equal(Settings.problemTitle("hook_log"), "Hook log")
  assert.equal(Settings.problemTitle("notify"), "Notifications")
  // A feature without a title is shown under its own name, never dropped.
  assert.equal(Settings.problemTitle("something_new"), "something_new")
})

test("a hook log row reads as time, agent, event and the State it became", () => {
  const row = Settings.logText({ time: "10:04:05", provider: "claude", event: "Stop", label: "Done" })
  assert.equal(row.time, "10:04:05")
  assert.equal(row.what, "Claude Code · Stop")
  assert.equal(row.state, "Done")
  // A decision that set no State shows a dash, not an empty cell.
  assert.equal(Settings.logText({ time: "10:04:05", provider: "codex", event: "SessionStart", label: "" }).state, "-")
})

test("the keep-awake choices are upstream's three policies, in its order", () => {
  assert.deepEqual(Settings.POLICY_CHOICES.map((choice) => choice.value), ["never", "agents", "always"])
  assert.deepEqual(Settings.POLICY_CHOICES.map((choice) => choice.label), ["Never", "When Agents Work", "Always"])
  assert.equal(Settings.policyLabel("agents"), "When Agents Work")
  assert.equal(Settings.policyLabel("sometimes"), "sometimes")
})

test("a step through the policies stops at the ends", () => {
  assert.equal(Settings.nextPolicy("never", 1), "agents")
  assert.equal(Settings.nextPolicy("agents", 1), "always")
  assert.equal(Settings.nextPolicy("always", 1), "always")
  assert.equal(Settings.nextPolicy("always", -1), "agents")
  assert.equal(Settings.nextPolicy("never", -1), "never")
  assert.equal(Settings.nextPolicy("agents", 0), "agents")
  // A value this popup does not know starts from the nearest end, as a preset does.
  assert.equal(Settings.nextPolicy("sometimes", 1), "never")
})

test("the keep-awake row says whether the lock is held and why", () => {
  assert.equal(Settings.awakeText(null), "Checking")
  assert.equal(Settings.awakeText({ holding: true, reason: "agents are working" }), "Holding sleep: agents are working")
  assert.equal(Settings.awakeText({ holding: false, reason: "no agent is working" }), "Not holding: no agent is working")
})

test("keep-awake and battery problems have their own titles", () => {
  assert.equal(Settings.problemTitle("keep_awake"), "Keep awake")
  assert.equal(Settings.problemTitle("power"), "Battery")
})
