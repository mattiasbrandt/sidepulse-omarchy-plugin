pragma Singleton

import QtQuick
import Quickshell
import Quickshell.Io

// The one helper process for the whole shell (D5). Each widget copy registers
// on creation and unregisters on destruction. The helper runs while a copy is
// registered and stops once the count has stayed at zero for stopDebounceMs,
// so a plugin reload (down to zero and back) keeps the same process.
//
// This file owns the process, parses the NDJSON lines and exposes the latest
// event of each kind. Logic lives in sidepulse_omarchy.py beside it. Edits here
// do not hot-reload: `omarchy restart shell` picks them up.
Item {
  id: root

  // Holds the Timers and the Process below; an Item takes children, a QtObject does not.
  visible: false

  readonly property int stopDebounceMs: 1500
  readonly property int restartMinMs: 1000
  readonly property int restartMaxMs: 30000
  readonly property string notInstalledMarker: "SIDEPULSE_NOT_INSTALLED"

  // Empty means SidePulse's own venv, the one the official installer makes.
  property string pythonPath: ""
  readonly property string python: pythonPath !== ""
    ? pythonPath
    : Quickshell.env("HOME") + "/.local/share/sidepulse/venv/bin/python"
  readonly property string helperPath: decodeURIComponent(
    Qt.resolvedUrl("sidepulse_omarchy.py").toString().replace("file://", ""))

  property int refs: 0
  property int backoffMs: restartMinMs

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
    refs += 1
    stopTimer.stop()
    ensureRunning()
  }

  function unregister() {
    refs = Math.max(0, refs - 1)
    if (refs === 0) stopTimer.restart()
  }

  function ensureRunning() {
    if (!proc.running) proc.running = true
  }

  // The helper ended. Registered copies mean it died on its own: restart after
  // a backoff that doubles up to restartMaxMs and resets on the next hello.
  function processEnded() {
    helloReceived = false
    agentState = null
    if (refs > 0) {
      restartTimer.interval = backoffMs
      backoffMs = Math.min(backoffMs * 2, restartMaxMs)
      restartTimer.restart()
    }
  }

  function send(command) {
    if (proc.running) proc.write(JSON.stringify(command) + "\n")
  }

  function refresh() { send({ cmd: "refresh" }) }
  function startService() { send({ cmd: "start-service" }) }

  function handleLine(line) {
    if (line.trim() === "") return
    var event = null
    try {
      event = JSON.parse(line)
    } catch (e) {
      errors = Object.assign({}, errors, { protocol: "not JSON from the helper: " + line })
      return
    }
    if (event.type === "hello") {
      helloReceived = true
      backoffMs = restartMinMs
    } else if (event.type === "state") {
      agentState = event
    } else if (event.type === "setup") {
      setupItems = event.items
    } else if (event.type === "result") {
      lastResult = event
    } else if (event.type === "error") {
      errors = Object.assign({}, errors, { [event.feature]: event.message })
    }
  }

  Timer {
    id: stopTimer
    interval: root.stopDebounceMs
    onTriggered: proc.running = false
  }

  // Set to backoffMs when scheduled; doubles after each unexpected exit.
  Timer {
    id: restartTimer
    onTriggered: root.ensureRunning()
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

    // Both a stop (no copies left) and a crash end here; processEnded restarts
    // only while copies are registered.
    onRunningChanged: if (!running) root.processEnded()
  }
}
