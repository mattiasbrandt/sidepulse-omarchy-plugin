// formatAge below is ported from SidePulse's status_bar.py
// (https://github.com/inteliwear/sidepulse). See THIRD_PARTY_NOTICES.md.
//
// Copyright (c) 2026 Peter Kuhar
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in all
// copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
// SOFTWARE.

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

// A session's last message on one line: upstream's messages can carry newlines
// and markdown, and a row has one line for them.
export function oneLine(text) {
  return String(text || "").replace(/\s+/g, " ").trim()
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
