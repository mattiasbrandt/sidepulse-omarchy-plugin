// The popup's Sessions rows (D13) as pure functions: the text each row shows
// and its cursor name. Panel.qml draws them; tests/sessions.test.mjs drives
// them under node.

// A session's age as upstream's format_age writes it (status_bar.py), so the
// popup and the menu bar read the same: 45s, 3m07s, 2h03m.
export function formatAge(seconds) {
  if (!Number.isFinite(seconds)) return ""
  const total = Math.max(0, Math.floor(seconds))
  if (total < 60) return total + "s"
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  if (minutes < 60) return minutes + "m" + String(rest).padStart(2, "0") + "s"
  const hours = Math.floor(minutes / 60)
  return hours + "h" + String(minutes % 60).padStart(2, "0") + "m"
}

// Seconds since the helper's updated_ms (epoch milliseconds) at nowMs.
export function ageSeconds(updatedMs, nowMs) {
  return Math.max(0, (nowMs - updatedMs) / 1000)
}

// The provider as the row names it: "claude" reads "Claude".
export function providerLabel(provider) {
  if (!provider) return ""
  return provider.charAt(0).toUpperCase() + provider.slice(1)
}

// Cursor name of the session at index in the Sessions list.
export function sessionRow(index) {
  return "session:" + index
}
