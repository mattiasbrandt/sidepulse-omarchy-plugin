pragma Singleton

import QtQuick
import Quickshell
import Quickshell.Io
import "lifecycle.mjs" as Lifecycle
import "popup.mjs" as Popup

// The one helper process for the whole shell (D5). Each widget copy registers
// on creation and unregisters on destruction. When the process starts, stops
// and restarts is decided in lifecycle.mjs (tested under node); this file
// applies its actions to the Process and the Timers.
//
// This file owns the process, parses the NDJSON lines and exposes the latest
// event of each kind. Logic lives in sidepulse_omarchy.py beside it. Edits here
// do not hot-reload: `omarchy restart shell` picks them up.
Item {
  id: root

  // Holds the Timers and the Process below; an Item takes children, a QtObject does not.
  visible: false

  readonly property string notInstalledMarker: "SIDEPULSE_NOT_INSTALLED"

  // Empty means SidePulse's own venv, the one the official installer makes.
  property string pythonPath: ""
  readonly property string python: pythonPath !== ""
    ? pythonPath
    : Quickshell.env("HOME") + "/.local/share/sidepulse/venv/bin/python"
  readonly property string helperPath: decodeURIComponent(
    Qt.resolvedUrl("sidepulse_omarchy.py").toString().replace("file://", ""))

  // Registered copies and the next restart delay (lifecycle.mjs owns both).
  property var life: Lifecycle.initial()

  // Latest events. `state` and `setupItems` are replaced whole on each event.
  property bool helloReceived: false
  property var capabilities: ({})
  property var agentState: null
  property var setupItems: []
  property var devices: []
  property var sessions: []
  property var hooks: []
  property var hookLog: []
  property var settings: null
  // The keep-awake policy, whether the sleep lock is held and why (D11); null until the helper reports.
  property var awake: null
  // The Bar LEDs' colours, [r, g, b] per LED, or null when nothing is drawn (D6, D7), and each
  // device's colours by device id (the popup's device rows). Replaced on each frame that changed.
  property var barLeds: null
  property var deviceLeds: ({})
  // The Bar LEDs' copies that want the band, in the order they asked: the first per bar window holds it.
  property var bandClaims: []
  // The widget copies whose popup is open (popup.mjs). The helper gets their OR as set-popup, so device
  // frames flow only while a popup shows the device rows. popupSent is what it last got; null until the
  // current helper has said hello.
  property var openPopups: []
  property var popupSent: null
  property var errors: ({})
  property var lastResult: null
  property bool notInstalled: false
  property string lastStderr: ""

  function register(pythonOverride) {
    if (pythonOverride !== undefined && pythonOverride !== null) pythonPath = pythonOverride
    apply(Lifecycle.register(life, proc.running))
  }

  function unregister() {
    apply(Lifecycle.unregister(life))
  }

  function apply(step) {
    life = step.state
    for (const action of step.actions) {
      if (action.type === "start") proc.running = true
      else if (action.type === "stop") proc.running = false
      else if (action.type === "scheduleStop") {
        stopTimer.interval = action.ms
        stopTimer.restart()
      } else if (action.type === "cancelStop") stopTimer.stop()
      else if (action.type === "scheduleRestart") {
        restartTimer.interval = action.ms
        restartTimer.restart()
      } else if (action.type === "cancelRestart") restartTimer.stop()
      else console.warn("HelperProcess: unknown lifecycle action", action.type)
    }
  }

  // The helper ended, stopped or crashed: drop what it said, then let the
  // lifecycle decide whether to restart it.
  function processEnded() {
    helloReceived = false
    capabilities = ({})
    agentState = null
    devices = []
    sessions = []
    hooks = []
    hookLog = []
    settings = null
    awake = null
    barLeds = null
    deviceLeds = ({})
    popupSent = null  // the next helper is told again
    errors = {}  // the next helper re-reports what still fails
    apply(Lifecycle.processEnded(life))
  }

  function send(command) {
    if (proc.running) proc.write(JSON.stringify(command) + "\n")
  }

  function refresh() { send({ cmd: "refresh" }) }
  function startService() { send({ cmd: "start-service" }) }
  // The widget's full map of device id to display (Agent, Battery, Off; D9).
  function setDisplays(displays) { send({ cmd: "set-displays", displays: displays }) }
  function setBrightness(deviceId, percent) { send({ cmd: "set-brightness", device: deviceId, percent: percent }) }
  // Focus, locate or resume one listed session (D8); the answer is a `result`.
  function openSession(session) { send({ cmd: "open-session", provider: session.provider, session_id: session.session_id }) }
  // Settings (D13): hooks per agent, the timeouts, the Bar LEDs switch and the Ask notice (D20).
  function installHooks(provider) { send({ cmd: "hooks-install", provider: provider }) }
  function uninstallHooks(provider) { send({ cmd: "hooks-uninstall", provider: provider }) }
  // `timing` holds idle_timeout_seconds and/or recent_session_retention_seconds.
  function setAgentList(timing) { send(Object.assign({ cmd: "set-agent-list" }, timing)) }
  function setBarLeds(enabled) { send({ cmd: "set-bar-leds", enabled: enabled }) }
  function setNotify(enabled) { send({ cmd: "set-notify", enabled: enabled }) }
  // Keep awake (D11): never, agents (when agents work) or always; saved through upstream's settings.
  function setPolicy(policy) { send({ cmd: "set-policy", policy: policy }) }

  // A Bar LEDs copy asks for the band of its bar window (`wants`), or drops its ask (`wants` false,
  // on destruction). The first copy that asked for a window holds it; when it leaves, the next holds it.
  function wantBand(owner, window, wants) {
    const others = bandClaims.filter(function (claim) { return claim.owner !== owner })
    bandClaims = wants && window ? others.concat([{ owner: owner, window: window }]) : others
  }

  // A widget copy's popup opened (isOpen) or closed, or the copy went away (false). Sends the OR on change.
  function setPopupOpen(owner, isOpen) {
    openPopups = Popup.withOpenPopup(openPopups, owner, isOpen)
    sendPopup()
  }

  function sendPopup() {
    const open = Popup.anyPopupOpen(openPopups)
    if (!helloReceived || open === popupSent) return
    popupSent = open
    send({ cmd: "set-popup", open: open })
  }

  // The copy holding the band of this window, or null.
  function bandHolder(window) {
    for (let i = 0; i < bandClaims.length; i++) {
      if (bandClaims[i].window === window) return bandClaims[i].owner
    }
    return null
  }

  function applyFrame(event) {
    if (event.target === "bar") {
      barLeds = event.leds
    } else if (event.target === "device") {
      const next = Object.assign({}, deviceLeds)
      next[event.device] = event.leds
      deviceLeds = next
    }
  }

  function handleLine(line) {
    if (line.trim() === "") return
    let event = null
    try {
      event = JSON.parse(line)
    } catch (e) {
      errors = Object.assign({}, errors, { protocol: "not JSON from the helper: " + line })
      return
    }
    if (event.type === "hello") {
      helloReceived = true
      capabilities = event.capabilities
      popupSent = null  // a fresh helper starts closed: tell it what the popups are
      sendPopup()
      apply(Lifecycle.hello(life))
    } else if (event.type === "state") {
      agentState = event
    } else if (event.type === "setup") {
      setupItems = event.items
    } else if (event.type === "devices") {
      devices = event.items
    } else if (event.type === "sessions") {
      sessions = event.items
    } else if (event.type === "hooks") {
      hooks = event.providers
    } else if (event.type === "log") {
      hookLog = event.rows
    } else if (event.type === "settings") {
      settings = event
    } else if (event.type === "awake") {
      awake = event
    } else if (event.type === "frame") {
      applyFrame(event)
    } else if (event.type === "result") {
      lastResult = event
    } else if (event.type === "error") {
      errors = Object.assign({}, errors, { [event.feature]: event.message })
    } else if (event.type === "clear") {
      const remaining = Object.assign({}, errors)
      delete remaining[event.feature]
      errors = remaining
    }
  }

  // Both intervals are set by the action that schedules them.
  Timer {
    id: stopTimer
    onTriggered: root.apply(Lifecycle.stopDue(root.life, proc.running))
  }

  Timer {
    id: restartTimer
    onTriggered: root.apply(Lifecycle.restartDue(root.life, proc.running))
  }

  // The shell prints a marker instead of starting the helper when the venv's
  // python is not there. Quickshell's exit signal is not used: qmllint cannot
  // type its exit status, so any onExited handler fails the lint.
  Process {
    id: proc
    command: [
      "/bin/sh", "-c",
      "if [ -x \"$1\" ]; then exec \"$1\" -I \"$2\"; fi; echo " + root.notInstalledMarker + " >&2",
      "sidepulse-omarchy", root.python, root.helperPath
    ]
    stdinEnabled: true

    stdout: SplitParser {
      splitMarker: "\n"
      onRead: function (line) { root.handleLine(line) }
    }

    stderr: SplitParser {
      splitMarker: "\n"
      onRead: function (line) {
        if (line === root.notInstalledMarker) root.notInstalled = true
        else root.lastStderr = line
      }
    }

    onStarted: root.notInstalled = false

    // Both a stop (no copies left) and a crash end here; the lifecycle restarts
    // only while copies are registered.
    onRunningChanged: if (!running) root.processEnded()
  }
}
