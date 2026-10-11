// The Settings section's text and choices (D13) as pure functions. Panel.qml
// draws them; tests/settings.test.mjs drives them under node.

// Upstream's advanced settings are typed in minutes (idle) and hours (retention),
// so the presets are whole units of those. Values are seconds, as upstream stores them.
export const IDLE_PRESETS = [900, 1800, 3600, 7200, 14400]
export const RETENTION_PRESETS = [3600, 21600, 86400, 172800, 604800]

// A duration in the largest whole unit: "1 hour", "2 days", "90 min". Zero means
// nothing is kept.
export function durationLabel(seconds) {
  if (!(seconds > 0)) return "None"
  if (seconds % 86400 === 0) {
    const days = seconds / 86400
    return days + (days === 1 ? " day" : " days")
  }
  if (seconds % 3600 === 0) {
    const hours = seconds / 3600
    return hours + (hours === 1 ? " hour" : " hours")
  }
  return Math.round(seconds / 60) + " min"
}

// The preset after current in direction step (+1 or -1). A value that is not a
// preset moves to the nearest one that way; the ends stop the walk.
export function nextPreset(presets, current, step) {
  if (step > 0) {
    const up = presets.find((preset) => preset > current)
    return up === undefined ? presets[presets.length - 1] : up
  }
  if (step < 0) {
    const down = [...presets].reverse().find((preset) => preset < current)
    return down === undefined ? presets[0] : down
  }
  return current
}

// The agent's name as the popup writes it (GLOSSARY: Agent).
const HOOK_NAMES = { claude: "Claude Code", codex: "Codex", grok: "Grok", cursor: "Cursor", junie: "Junie" }

export function hookName(provider) {
  return HOOK_NAMES[provider] || provider
}

// What an agent's SidePulse hooks are, from the helper's `hooks` entry. "On" means
// SidePulse's own hooks are installed (the helper's `installed`), not any hook.
export function hookStatus(entry) {
  if (!entry) return "Checking"
  if (entry.error) return "Unreadable"
  if (!entry.exists) return "No config yet"
  if (entry.installed) return "On"
  return entry.hook_events && entry.hook_events.length > 0 ? "Other hooks only" : "Off"
}

// Keep awake (D11): the policies in upstream's order, with upstream's labels.
export const POLICY_CHOICES = [
  { value: "never", label: "Never" },
  { value: "agents", label: "When Agents Work" },
  { value: "always", label: "Always" },
]

export function policyLabel(value) {
  const choice = POLICY_CHOICES.find((entry) => entry.value === value)
  return choice ? choice.label : value
}

// The policy after current in direction step (+1 or -1); the ends stop the walk. A value that is not a
// choice starts from the nearest end that way.
export function nextPolicy(current, step) {
  if (step === 0) return current
  const at = POLICY_CHOICES.findIndex((entry) => entry.value === current)
  if (at < 0) return POLICY_CHOICES[step > 0 ? 0 : POLICY_CHOICES.length - 1].value
  const next = Math.max(0, Math.min(POLICY_CHOICES.length - 1, at + Math.sign(step)))
  return POLICY_CHOICES[next].value
}

// The keep-awake row's detail: whether the sleep lock is held now, and why (the helper's `awake` event).
export function awakeText(awake) {
  if (!awake) return "Checking"
  return (awake.holding ? "Holding sleep: " : "Not holding: ") + awake.reason
}

// The title of a problem line, by the feature the helper reported it for.
const PROBLEM_TITLES = {
  state: "Agent state",
  sessions: "Sessions",
  service: "Service",
  devices: "Devices",
  bar_leds: "Bar LEDs",
  hooks: "Hooks",
  hook_log: "Hook log",
  settings: "Settings",
  notify: "Notifications",
  keep_awake: "Keep awake",
  power: "Battery",
  command: "Command",
  protocol: "Protocol",
  watch: "Watching",
  helper: "Helper",
}

export function problemTitle(feature) {
  return PROBLEM_TITLES[feature] || feature
}

// One hook log row as the popup lists it. A decision that set no State shows a dash.
export function logText(row) {
  return {
    time: row.time,
    what: hookName(row.provider) + " · " + row.event,
    state: row.label || "-",
  }
}
