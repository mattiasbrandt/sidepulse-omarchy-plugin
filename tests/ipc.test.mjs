// Which widget copy owns the IPC target (helper/ipc.mjs), run under node. Quickshell keeps one
// IpcHandler per target and silently ignores the others (log: "another handler is registered").

import assert from "node:assert/strict"
import { test } from "node:test"

import * as Ipc from "../helper/ipc.mjs"

const center = {}
const right = {}
const hidden = {}

test("no copy claims means no owner", () => {
  assert.equal(Ipc.ipcOwner([]), null)
})

test("the first drawn copy owns the target, not the first copy", () => {
  const claims = [{ owner: hidden, drawn: false }, { owner: center, drawn: true }, { owner: right, drawn: true }]
  assert.equal(Ipc.ipcOwner(claims), center)
})

test("with no drawn copy, the first claim owns it", () => {
  const claims = [{ owner: hidden, drawn: false }, { owner: right, drawn: false }]
  assert.equal(Ipc.ipcOwner(claims), hidden)
})

test("claiming replaces the copy's own claim and keeps the others in order", () => {
  let claims = Ipc.withIpcClaim([], center, true)
  claims = Ipc.withIpcClaim(claims, right, true)
  claims = Ipc.withIpcClaim(claims, center, false)
  assert.deepEqual(claims.map((claim) => claim.owner), [right, center])
  assert.equal(Ipc.ipcOwner(claims), right)
})

test("dropping a copy hands the target to the next one", () => {
  let claims = Ipc.withIpcClaim([], center, true)
  claims = Ipc.withIpcClaim(claims, right, true)
  claims = Ipc.withoutIpcClaim(claims, center)
  assert.equal(Ipc.ipcOwner(claims), right)
  assert.equal(Ipc.ipcOwner(Ipc.withoutIpcClaim(claims, right)), null)
})
