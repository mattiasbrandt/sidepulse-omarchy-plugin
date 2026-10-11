pragma Singleton

import QtQuick
import Quickshell
import Quickshell.Io
import "lifecycle.mjs" as Lifecycle

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
  property var agentState: null
  property var setupItems: []
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
    agentState = null
    errors = {}  // the next helper re-reports what still fails
    apply(Lifecycle.processEnded(life))
  }

  function send(command) {
    if (proc.running) proc.write(JSON.stringify(command) + "\n")
  }

  function refresh() { send({ cmd: "refresh" }) }
  function startService() { send({ cmd: "start-service" }) }

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
      apply(Lifecycle.hello(life))
    } else if (event.type === "state") {
      agentState = event
    } else if (event.type === "setup") {
      setupItems = event.items
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
