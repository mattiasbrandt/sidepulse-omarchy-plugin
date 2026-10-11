// Which widget copy owns the IPC target (D5: one owner per target). Quickshell keeps one IpcHandler
// per target and ignores the others, so each copy claims with whether it is drawn (the shell's
// isDrawnSlot rule, BarModel.js) and the first drawn claim owns the target; with none drawn, the first
// claim does. Pure functions; HelperProcess.qml keeps the claims, tests/ipc.test.mjs runs them under node.

// The claims with owner's entry set to drawn, replacing its earlier entry and keeping the order.
export function withIpcClaim(claims, owner, drawn) {
  const rest = claims.filter((claim) => claim.owner !== owner)
  return rest.concat([{ owner, drawn }])
}

// The claims without owner's entry.
export function withoutIpcClaim(claims, owner) {
  return claims.filter((claim) => claim.owner !== owner)
}

// The copy that owns the target, or null when no copy claims it.
export function ipcOwner(claims) {
  const drawn = claims.find((claim) => claim.drawn)
  const pick = drawn || claims[0]
  return pick ? pick.owner : null
}
