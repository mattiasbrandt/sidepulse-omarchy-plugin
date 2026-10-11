// The bar icon's tooltip (D12) and the IPC status() line (D5, protocol in docs/spec.md) as pure
// functions over what the widget holds: the helper's last `state` event, whether it is
// not installed, and whether setup is needed. Panel.qml calls them; tests/status.test.mjs runs them under node.

// What a click does: left click opens the popup, and every tooltip says so.
const CLICK_HINT = " · click for details"

export function tooltipText({ notInstalled, agentState, needsSetup }) {
  if (notInstalled) return "SidePulse: not installed" + CLICK_HINT
  if (!agentState) return "SidePulse: starting" + CLICK_HINT
  let text = "SidePulse: " + agentState.label
  if (agentState.active_count > 0) text += ", " + agentState.active_count + " active"
  if (agentState.top) text += ", " + agentState.top.display_name
  if (needsSetup) text += " (setup needed)"
  return text + CLICK_HINT
}

// status(): one JSON object. Before the helper reports, state is null and label says why.
export function statusJson({ notInstalled, agentState, needsSetup }) {
  if (!agentState) {
    return JSON.stringify({
      state: null,
      label: notInstalled ? "not installed" : "starting",
      urgent: false,
      active_count: 0,
      top: null,
      needs_setup: needsSetup,
    })
  }
  return JSON.stringify({
    state: agentState.state,
    label: agentState.label,
    urgent: agentState.urgent === true,
    active_count: agentState.active_count,
    top: agentState.top ? agentState.top.display_name : null,
    needs_setup: needsSetup,
  })
}
