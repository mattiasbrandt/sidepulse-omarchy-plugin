// The helper's lifecycle (helper/lifecycle.mjs) driven the way
// HelperProcess.qml drives it: a simulated clock, process and two timers that
// apply the actions each step returns. Run with `node --test tests/`.

import assert from "node:assert/strict"
import { test } from "node:test"

import * as Lifecycle from "../helper/lifecycle.mjs"

class Sim {
  constructor() {
    this.now = 0
    this.life = Lifecycle.initial()
    this.running = false
    this.starts = 0
    this.stops = 0
    this.timers = { stop: null, restart: null } // due time, or null
  }

  apply({ state, actions }) {
    this.life = state
    for (const action of actions) {
      if (action.type === "start") {
        this.running = true
        this.starts += 1
      } else if (action.type === "stop") {
        this.stop()
      } else if (action.type === "scheduleStop") {
        this.timers.stop = this.now + action.ms
      } else if (action.type === "cancelStop") {
        this.timers.stop = null
      } else if (action.type === "scheduleRestart") {
        this.timers.restart = this.now + action.ms
      } else if (action.type === "cancelRestart") {
        this.timers.restart = null
      } else {
        throw new Error(`unknown action ${action.type}`)
      }
    }
  }

  // The process ends: stopped by an action, or crashed.
  stop() {
    this.stops += 1
    this.end()
  }

  end() {
    if (!this.running) return
    this.running = false
    this.apply(Lifecycle.processEnded(this.life))
  }

  register() { this.apply(Lifecycle.register(this.life, this.running)) }
  unregister() { this.apply(Lifecycle.unregister(this.life)) }
  hello() { this.apply(Lifecycle.hello(this.life)) }

  // Move the clock, firing timers in due order.
  advance(ms) {
    const until = this.now + ms
    for (;;) {
      const due = Object.entries(this.timers)
        .filter(([, at]) => at !== null && at <= until)
        .sort((a, b) => a[1] - b[1])[0]
      if (!due) break
      const [name, at] = due
      this.now = at
      this.timers[name] = null
      if (name === "stop") this.apply(Lifecycle.stopDue(this.life, this.running))
      else this.apply(Lifecycle.restartDue(this.life, this.running))
    }
    this.now = until
  }
}

test("a crash while copies are registered restarts the helper after the backoff", () => {
  const sim = new Sim()
  sim.register()
  sim.end() // crash
  assert.equal(sim.running, false)
  sim.advance(Lifecycle.RESTART_MIN_MS - 1)
  assert.equal(sim.running, false)
  sim.advance(1)
  assert.equal(sim.running, true)
  assert.equal(sim.starts, 2)
})

test("the last copy leaving during a restart backoff leaves no helper running (#26)", () => {
  const sim = new Sim()
  sim.register()
  sim.end() // crash: a restart is scheduled
  sim.advance(100)
  sim.unregister() // plugin disabled before the restart fires
  sim.advance(Lifecycle.RESTART_MAX_MS * 2)
  assert.equal(sim.running, false)
  assert.equal(sim.starts, 1)
})

test("a restart that fires with no copy registered does not start the helper", () => {
  // The guard that does not depend on the timer having been cancelled.
  const { actions } = Lifecycle.restartDue({ ...Lifecycle.initial(), refs: 0 }, false)
  assert.deepEqual(actions, [])
})

test("the last copy leaving cancels a pending restart", () => {
  const { actions } = Lifecycle.unregister({ ...Lifecycle.initial(), refs: 1 })
  assert.ok(actions.some((a) => a.type === "cancelRestart"))
})

test("a reload (1 -> 0 -> 1 inside the debounce) keeps the same helper", () => {
  const sim = new Sim()
  sim.register()
  sim.unregister()
  sim.advance(Lifecycle.STOP_DEBOUNCE_MS - 1)
  sim.register()
  sim.advance(Lifecycle.STOP_DEBOUNCE_MS * 2)
  assert.equal(sim.running, true)
  assert.equal(sim.starts, 1)
  assert.equal(sim.stops, 0)
})

test("the helper stops once the count has stayed at 0 for the debounce", () => {
  const sim = new Sim()
  sim.register()
  sim.unregister()
  sim.advance(Lifecycle.STOP_DEBOUNCE_MS - 1)
  assert.equal(sim.running, true)
  sim.advance(1)
  assert.equal(sim.running, false)
  assert.equal(sim.stops, 1)
  sim.advance(Lifecycle.RESTART_MAX_MS * 2)
  assert.equal(sim.running, false) // a deliberate stop is not a crash
})

test("a second copy registering does not start a second helper", () => {
  const sim = new Sim()
  sim.register()
  sim.register()
  assert.equal(sim.starts, 1)
  sim.unregister()
  sim.advance(Lifecycle.STOP_DEBOUNCE_MS * 2)
  assert.equal(sim.running, true)
})

test("the backoff stops growing at the maximum and resets on hello", () => {
  const sim = new Sim()
  sim.register()
  for (let i = 0; i < 8; i++) {
    sim.end()
    sim.advance(Lifecycle.RESTART_MAX_MS)
    assert.equal(sim.running, true)
  }
  assert.equal(sim.life.backoffMs, Lifecycle.RESTART_MAX_MS)
  sim.hello()
  assert.equal(sim.life.backoffMs, Lifecycle.RESTART_MIN_MS)
})

test("each restart waits the current backoff", () => {
  const sim = new Sim()
  sim.register()
  const waits = []
  for (let i = 0; i < 7; i++) {
    sim.end()
    let waited = 0
    while (!sim.running) {
      sim.advance(1)
      waited += 1
    }
    waits.push(waited)
  }
  assert.deepEqual(waits, [1000, 2000, 4000, 8000, 16000, 30000, 30000])
})
