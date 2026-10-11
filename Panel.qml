pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "helper"
import "helper/sessions.mjs" as Sessions
import "helper/settings.mjs" as Settings
import "helper/status.mjs" as Status

// SidePulse bar widget. The helper (HelperProcess, shared by every copy of
// this widget) reports the agents' state, which colours the icon (D12). The
// popup holds Setup (D19), Sessions, Devices and the collapsible Settings
// section (D13), which also holds Keep awake (D11, D18). The Bar LEDs (D7)
// are drawn in the bar window by BarLeds.
Panel {
  id: root

  moduleName: "io.github.mattiasbrandt.sidepulse"

  // The bar window this copy sits in: the Bar LEDs are drawn into it (D7).
  readonly property var barWindow: QsWindow.window

  readonly property string pythonSetting: setting("sidepulsePython", "")
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property var agentState: HelperProcess.agentState
  readonly property var installRow: ({
    id: "install",
    title: "SidePulse is not installed",
    detail: "Install it with: curl -fsSL https://sidepulse.io/setup.sh | bash",
    action: null
  })
  readonly property var setupRows: HelperProcess.notInstalled ? [installRow] : HelperProcess.setupItems
  readonly property bool needsSetup: setupRows.length > 0

  // The colour a connected device's LEDs show now (a SidePulse Pro wins, D21);
  // empty when none is lit. Upstream's device programs and its menu-bar legend
  // differ (Done is cyan on the device, green in the legend), and the icon
  // follows the device the user can see (D12).
  readonly property string deviceColour: {
    let found = ""
    for (let i = 0; i < HelperProcess.devices.length; i++) {
      const device = HelperProcess.devices[i]
      if (!device.colour || displayOf(device.id) === "off") continue
      if (device.kind === "pro") return device.colour
      if (found === "") found = device.colour
    }
    return found
  }

  // Without a lit device: upstream's legend colours, Idle muted.
  readonly property color glyphColour: deviceColour !== ""
    ? deviceColour
    : !agentState || agentState.state === "idle" || agentState.state === "unavailable"
      ? dim
      : agentState.colour

  // The tooltip ends in "· click for details", the left click's only action (helper/status.mjs).
  readonly property string tooltipText: Status.tooltipText({
    notInstalled: HelperProcess.notInstalled,
    agentState: agentState,
    needsSetup: needsSetup
  })

  readonly property string heroMeta: {
    if (HelperProcess.notInstalled) return "Not installed"
    if (!agentState) return "Starting"
    return agentState.label + (agentState.active_count > 0 ? " · " + agentState.active_count + " active" : "")
  }

  // Problems the helper reported, one per feature: capabilities that are off and
  // failures (D14). Each is a title and the reason, so the reason reads as a problem.
  readonly property var problemLines: {
    const lines = []
    const keys = Object.keys(HelperProcess.errors)
    for (let i = 0; i < keys.length; i++) {
      lines.push({ title: Settings.problemTitle(keys[i]), message: HelperProcess.errors[keys[i]] })
    }
    // A helper that died before saying hello only leaves its last stderr line.
    if (!HelperProcess.helloReceived && !HelperProcess.notInstalled && HelperProcess.lastStderr !== "") {
      lines.push({ title: Settings.problemTitle("helper"), message: HelperProcess.lastStderr })
    }
    return lines
  }

  // Settings (D13). The section is collapsed until opened; its rows exist only while open.
  property bool settingsOpen: false
  property string hookBusy: ""
  // The agents the helper lists, in its order (upstream's list, not a copy here).
  readonly property var hookProviders: HelperProcess.hooks.map(function (entry) { return entry.provider })
  readonly property var settingRowNames: settingsOpen
    ? hookProviders.map(function (provider) { return "hook:" + provider })
      .concat(["bar-leds", "idle", "retention", "notify", "keep-awake"])
    : []
  // Upstream's values, as the helper last reported them; empty until the first report.
  readonly property bool barLedsOn: HelperProcess.settings !== null && HelperProcess.settings.bar_leds === true
  readonly property string barLedsDetail: {
    const cap = HelperProcess.capabilities ? HelperProcess.capabilities.bar_leds : undefined
    if (cap && cap.ok === false) return "Unavailable until the problem above is fixed."
    return "SidePulse's setting. The LEDs show along the bar's inner edge."
  }
  readonly property real idleSeconds: HelperProcess.settings !== null ? HelperProcess.settings.idle_timeout_seconds : 0
  readonly property real retentionSeconds: HelperProcess.settings !== null
    ? HelperProcess.settings.recent_session_retention_seconds : 0
  readonly property bool notifyOnAsk: setting("notifyOnAsk", false)

  function hookEntry(provider) {
    for (let i = 0; i < HelperProcess.hooks.length; i++) {
      if (HelperProcess.hooks[i].provider === provider) return HelperProcess.hooks[i]
    }
    return undefined
  }

  function toggleHook(provider) {
    if (hookBusy !== "") return
    const entry = hookEntry(provider)
    hookBusy = provider
    // "Installed" is SidePulse's own hooks, so a switch on means remove them, and off means install.
    if (entry && entry.installed === true) HelperProcess.uninstallHooks(provider)
    else HelperProcess.installHooks(provider)
  }

  function toggleBarLeds() {
    HelperProcess.setBarLeds(!barLedsOn)
  }

  // Steps the idle timeout or the retention to the next preset in direction dx.
  function stepTiming(rowName, dx) {
    if (HelperProcess.settings === null) return
    if (rowName === "idle") {
      HelperProcess.setAgentList({ idle_timeout_seconds: Settings.nextPreset(Settings.IDLE_PRESETS, idleSeconds, dx) })
    } else if (rowName === "retention") {
      HelperProcess.setAgentList({
        recent_session_retention_seconds: Settings.nextPreset(Settings.RETENTION_PRESETS, retentionSeconds, dx)
      })
    }
  }

  // The one sender of the notice setting to the helper is onNotifyOnAskChanged: saving
  // updates notifyOnAsk synchronously, so reading it again here would see the new value.
  function toggleNotify() {
    saveEntry("notifyOnAsk", !notifyOnAsk)
  }

  // Steps the keep-awake policy one choice in direction dx; the helper saves it through upstream (D11).
  function stepPolicy(dx) {
    if (HelperProcess.awake === null) return
    const next = Settings.nextPolicy(HelperProcess.awake.policy, dx)
    if (next !== HelperProcess.awake.policy) HelperProcess.setPolicy(next)
  }

  // The Display each device was last set to, keyed by device id (D9). Stored in
  // this widget's shell.json entry; the helper re-derives upstream's value from it.
  readonly property var deviceDisplays: setting("deviceDisplays", ({}))
  readonly property var deviceRowNames: HelperProcess.devices.length > 0
    ? HelperProcess.devices.map(function (device) { return "device:" + device.id })
    : ["devices"]
  // The Sessions rows: one per listed session, or one placeholder while there are none.
  readonly property var sessionRowNames: HelperProcess.sessions.length > 0
    ? HelperProcess.sessions.map(function (session, index) { return Sessions.sessionRow(index) })
    : ["sessions"]
  readonly property var sectionRows: sessionRowNames.concat(deviceRowNames, ["settings"], settingRowNames)
  // Battery is offered only where the helper finds a battery (D9); without one a remembered Battery means Agent.
  readonly property bool batteryOffered: HelperProcess.power !== null && HelperProcess.power.battery_offered === true
  readonly property var displayOrder: batteryOffered ? ["agent", "battery", "off"] : ["agent", "off"]
  readonly property var displayChoices: displayOrder.map(function (value) {
    return { value: value, label: value === "agent" ? "Agent" : value === "battery" ? "Battery" : "Off" }
  })

  // The product drawn for the icon: Pro wins when both are plugged in (D21).
  readonly property int iconLedCount: {
    if (HelperProcess.devices.length === 0) return 1
    for (let i = 0; i < HelperProcess.devices.length; i++) {
      if (HelperProcess.devices[i].kind === "pro") return 8
    }
    return 2
  }

  // A value not offered here (none remembered, or Battery without a battery)
  // means Agent, as the helper treats it.
  function displayOf(deviceId) {
    const value = deviceDisplays[deviceId]
    return displayOrder.indexOf(value) >= 0 ? value : "agent"
  }

  // Session ages advance while the popup is open; the rows read this clock.
  property real nowMs: Date.now()
  Timer {
    id: ageTimer
    interval: 1000
    repeat: true
    running: root.opened
    onTriggered: root.nowMs = Date.now()
  }

  // Enter or a click on a listed session (D8). A session without an id cannot be looked up.
  function openSession(index) {
    const session = HelperProcess.sessions[index]
    if (session && session.resumable) HelperProcess.openSession(session)
  }

  function deviceById(deviceId) {
    for (let i = 0; i < HelperProcess.devices.length; i++) {
      if (HelperProcess.devices[i].id === deviceId) return HelperProcess.devices[i]
    }
    return null
  }

  // Writes one key into this widget's shell.json entry, keeping the rest of the
  // entry as it is (the same way the Tailscale panel does). Only presentation
  // settings go here (D13): device displays and the Ask notice.
  function saveEntry(key, value) {
    if (!bar || !bar.shell || typeof bar.shell.updateEntryInline !== "function") return
    const entry = { id: root.moduleName }
    for (const name in settings) if (name !== "id") entry[name] = settings[name]
    entry[key] = value
    bar.shell.updateEntryInline(root.moduleName, entry)
  }

  function setDisplay(deviceId, display) {
    const displays = Object.assign({}, deviceDisplays)
    displays[deviceId] = display
    saveEntry("deviceDisplays", displays)
    HelperProcess.setDisplays(displays)
  }

  function cycleDisplay(deviceId) {
    const at = displayOrder.indexOf(displayOf(deviceId))
    setDisplay(deviceId, displayOrder[(at + 1) % displayOrder.length])
  }

  function nudgeBrightness(deviceId, steps) {
    const device = deviceById(deviceId)
    if (!device) return
    HelperProcess.setBrightness(deviceId, Math.max(0, Math.min(100, device.percent + 5 * steps)))
  }

  // Keyboard cursor. Setup rows with an action come first, then the sections.
  property int cursorIndex: 0
  property bool cursorActive: false

  readonly property var cursorRows: {
    const rows = []
    for (let i = 0; i < setupRows.length; i++) {
      if (setupRows[i].action) rows.push("setup:" + setupRows[i].id)
    }
    return rows.concat(sectionRows)
  }

  readonly property string cursorRow: cursorRows.length === 0
    ? ""
    : cursorRows[Math.max(0, Math.min(cursorIndex, cursorRows.length - 1))]

  function rowHasCursor(name) {
    return cursorActive && cursorRow === name
  }

  function moveCursor(dy) {
    cursorActive = true
    if (cursorRows.length === 0) return
    cursorIndex = Math.max(0, Math.min(cursorRows.length - 1, cursorIndex + dy))
  }

  function focusRow(name) {
    const at = cursorRows.indexOf(name)
    if (at < 0) return
    cursorActive = true
    cursorIndex = at
  }

  // Keeps a cursor row on screen, the way Omarchy's own panels do (dropbox Panel.qml).
  function scrollItemIntoView(item) {
    if (!panelFlick || !item) return
    Qt.callLater(function () {
      if (!item) return
      const margin = Style.space(6)
      const top = item.mapToItem(panelFlick.contentItem, 0, 0).y
      const bottom = top + item.height
      const maxY = Math.max(0, panelFlick.contentHeight - panelFlick.height)
      if (top < panelFlick.contentY + margin) panelFlick.contentY = Math.max(0, top - margin)
      else if (bottom > panelFlick.contentY + panelFlick.height - margin)
        panelFlick.contentY = Math.min(maxY, bottom + margin - panelFlick.height)
    })
  }

  // Opening Settings scrolls its header to the top, so the rows it reveals are on
  // screen instead of below the fold. The rows and the popup's own height grow
  // after settingsOpen flips, so the scroll is applied again on each change of
  // either until revealTimer ends it (or the user scrolls).
  property bool revealPending: false

  function revealSettings() {
    if (!revealPending || !panelFlick || !settingsHeader) return
    const top = settingsHeader.mapToItem(panelFlick.contentItem, 0, 0).y - Style.space(6)
    const maxY = Math.max(0, panelFlick.contentHeight - panelFlick.height)
    panelFlick.contentY = Math.max(0, Math.min(maxY, top))
  }

  Timer {
    id: revealTimer
    interval: 400
    onTriggered: {
      root.revealSettings()
      root.revealPending = false
    }
  }

  function runSetupAction(row) {
    if (row.action) HelperProcess.send({ cmd: row.action })
  }

  // Enter, or a click, on the row with this cursor name.
  function activateRow(name) {
    if (name.indexOf("session:") === 0) {
      openSession(parseInt(name.substring(8), 10))
    } else if (name.indexOf("device:") === 0) {
      cycleDisplay(name.substring(7))
    } else if (name.indexOf("setup:") === 0) {
      const id = name.substring(6)
      for (let i = 0; i < setupRows.length; i++) {
        if (setupRows[i].id === id) runSetupAction(setupRows[i])
      }
    } else if (name === "settings") {
      settingsOpen = !settingsOpen
    } else if (name.indexOf("hook:") === 0) {
      toggleHook(name.substring(5))
    } else if (name === "bar-leds") {
      toggleBarLeds()
    } else if (name === "idle" || name === "retention") {
      stepTiming(name, 1)
    } else if (name === "notify") {
      toggleNotify()
    } else if (name === "keep-awake") {
      stepPolicy(1)
    }
  }

  function activateCursor() {
    activateRow(cursorRow)
  }

  // Left and right on a row step its value: a device's brightness or a timeout.
  function stepRow(name, dx) {
    if (name.indexOf("device:") === 0) nudgeBrightness(name.substring(7), dx)
    else if (name === "idle" || name === "retention") stepTiming(name, dx)
    else if (name === "keep-awake") stepPolicy(dx)
  }

  // The bar's ModuleSlot holding this copy (the parent of the Loader that loads it). A copy is drawn when
  // the slot is visible and has size, as BarModel.js isDrawnSlot decides; the center section's zero-size
  // placeholder is not (Bar.qml, "a zero-size placeholder"). Without a slot, the copy's own size decides.
  readonly property var slot: parent && parent.parent ? parent.parent : null
  readonly property bool drawn: slot !== null
    ? slot.visible === true && slot.width > 0 && slot.height > 0
    : visible && width > 0 && height > 0

  Component.onCompleted: {
    HelperProcess.pythonPath = pythonSetting
    HelperProcess.register()
    HelperProcess.claimIpc(root, drawn)
  }

  onDrawnChanged: HelperProcess.claimIpc(root, drawn)

  // A helper that has just started knows no displays or notice setting yet: send
  // what was remembered, and send again whenever the remembered values change
  // (also when edited in shell.json).
  Connections {
    target: HelperProcess
    function onHelloReceivedChanged() {
      if (!HelperProcess.helloReceived) return
      HelperProcess.setDisplays(root.deviceDisplays)
      HelperProcess.setNotify(root.notifyOnAsk)
    }
  }

  onDeviceDisplaysChanged: if (HelperProcess.helloReceived) HelperProcess.setDisplays(deviceDisplays)
  onNotifyOnAskChanged: if (HelperProcess.helloReceived) HelperProcess.setNotify(notifyOnAsk)
  // Focusing or resuming a session closes the popup; a failure stays on show with its reason.
  // A hook command's answer ends its busy switch, whatever it says (the hooks event shows the truth).
  Connections {
    target: HelperProcess
    function onLastResultChanged() {
      const result = HelperProcess.lastResult
      if (result && result.command === "open-session" && result.ok === true) root.close()
      if (result && String(result.command).indexOf("hooks-") === 0) root.hookBusy = ""
    }
  }

  Component.onDestruction: {
    HelperProcess.setPopupOpen(root, false)
    HelperProcess.releaseIpc(root)
    HelperProcess.unregister()
  }

  // The IPC target is the plugin id (IPC targets are global in the shell). Only the owning copy
  // registers it (HelperProcess.ipcOwner), so the anchor placeholder never answers. The base Panel's
  // handler stays off (no ipcTarget); this one keeps its five calls and adds status() (docs/spec.md).
  IpcHandler {
    enabled: HelperProcess.ipcOwner() === root
    target: root.moduleName

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function status(): string {
      return Status.statusJson({
        notInstalled: HelperProcess.notInstalled,
        agentState: root.agentState,
        needsSetup: root.needsSetup
      })
    }
  }
  onPythonSettingChanged: HelperProcess.pythonPath = pythonSetting

  // A result from an earlier open is stale once the popup opens again.
  onSettingsOpenChanged: {
    revealPending = settingsOpen
    if (settingsOpen) {
      revealTimer.restart()
      Qt.callLater(revealSettings)
    }
  }

  // The helper streams the device rows' frames only while a popup is open (set-popup, D21).
  onOpenedChanged: {
    HelperProcess.setPopupOpen(root, opened)
    if (opened) {
      cursorActive = false
      cursorIndex = 0
      nowMs = Date.now()
      HelperProcess.lastResult = null
      hookBusy = ""
      if (panelFlick) panelFlick.contentY = 0
      HelperProcess.refresh()
      Qt.callLater(function () { keyCatcher.forceActiveFocus() })
    }
  }

  // Our own shapes (D22): a device outline with its LEDs (D21: one neutral LED
  // with no device, 2 for a SidePulse Dot, 8 for a SidePulse Pro). The setup
  // look is the same glyph at half opacity with a dot in the urgent colour.
  // The popup's hero draws the glyph across; the bar icon on a left or right bar draws it down (D22, vertical bars).
  readonly property Component glyphComponent: Component {
    GlyphShape {}
  }
  readonly property Component barGlyphComponent: Component {
    GlyphShape { vertical: root.bar ? root.bar.vertical === true : false }
  }

  component GlyphShape: Item {
    id: glyph
    property bool vertical: false
    opacity: root.needsSetup ? 0.5 : 1.0
    readonly property real ledSize: Style.space(2)
    readonly property real ledGap: Style.space(1)
    readonly property real ledsLength: root.iconLedCount * ledSize + (root.iconLedCount - 1) * ledGap
    readonly property real pillLength: Math.max(Style.space(12), ledsLength + Style.space(4))

    Rectangle {
      anchors.centerIn: parent
      width: glyph.vertical ? Style.space(8) : glyph.pillLength
      height: glyph.vertical ? glyph.pillLength : Style.space(8)
      radius: Math.min(width, height) / 2
      color: "transparent"
      border.width: Math.max(1, Style.space(1))
      border.color: root.glyphColour
    }

    Grid {
      anchors.centerIn: parent
      spacing: glyph.ledGap
      columns: glyph.vertical ? 1 : root.iconLedCount

      Repeater {
        model: root.iconLedCount

        Rectangle {
          required property int index
          // The device's live frame (D12), as its popup row draws it; without one, the State's colour.
          readonly property var led: HelperProcess.iconLeds ? HelperProcess.iconLeds[index] : null
          width: glyph.ledSize
          height: width
          radius: width / 2
          color: led ? Qt.rgba(led[0] / 255, led[1] / 255, led[2] / 255, 1) : root.glyphColour
        }
      }
    }

    Rectangle {
      visible: root.needsSetup
      anchors.right: parent.right
      anchors.top: parent.top
      width: Style.space(3)
      height: width
      radius: width / 2
      color: root.urgent
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: root.tooltipText
    // Ask lights the icon with the urgent highlight (D12).
    active: root.agentState !== null && root.agentState.urgent === true
    iconComponent: root.barGlyphComponent

    onPressed: function (buttonCode) {
      if (buttonCode === Qt.LeftButton) root.toggle()
    }
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // The Bar LEDs (D7): this copy asks for the band of its bar window; the first copy per window holds it.
  BarLeds {
    bar: root.bar
    barWindow: root.barWindow
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(520))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function (dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        if (dy !== 0) root.moveCursor(dy)
        else if (dx !== 0) root.stepRow(root.cursorRow, dx)
      }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function (direction) { root.switchPanel(direction) }
      onTextKey: function (t) {
        if (String(t).toLowerCase() === "r") HelperProcess.refresh()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        onContentHeightChanged: root.revealSettings()
        onHeightChanged: root.revealSettings()
        onMovementStarted: root.revealPending = false
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          PanelHero {
            width: parent.width
            title: "SidePulse"
            meta: root.heroMeta
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconOpacity: root.agentState !== null ? 1.0 : 0.5
            iconComponent: root.glyphComponent
          }

          Text {
            textFormat: Text.PlainText
            visible: HelperProcess.lastResult !== null && HelperProcess.lastResult.ok !== true
            width: parent.width
            text: HelperProcess.lastResult !== null ? HelperProcess.lastResult.message : ""
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          Column {
            visible: root.needsSetup
            width: parent.width
            spacing: Style.space(6)

            PanelSectionHeader {
              text: "SETUP"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Repeater {
              model: root.setupRows

              SetupRow {
                required property var modelData
                width: parent.width
                row: modelData
              }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(6)

            PanelSectionHeader {
              text: "SESSIONS"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            // Keyed on the count, as the devices are: each sessions event replaces the list.
            Repeater {
              model: HelperProcess.sessions.length

              SessionRow {
                required property int index
                width: parent.width
                session: HelperProcess.sessions[index] || ({})
                rowIndex: index
              }
            }

            PlaceholderRow {
              visible: HelperProcess.sessions.length === 0
              width: parent.width
              rowName: "sessions"
              label: "No recent sessions"
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(6)

            PanelSectionHeader {
              text: "DEVICES"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            // Keyed on the count, not the list: each devices event replaces the
            // list, and a list model would rebuild every row, ending a slider
            // drag at its first step.
            Repeater {
              model: HelperProcess.devices.length

              DeviceRow {
                required property int index
                width: parent.width
                device: HelperProcess.devices[index] || ({})
              }
            }

            PlaceholderRow {
              visible: HelperProcess.devices.length === 0
              width: parent.width
              rowName: "devices"
              label: "No SidePulse device connected"
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(6)

            // Problems stay on show when the section is collapsed: a capability
            // that is off must not hide behind a closed header (D14).
            Column {
              visible: root.problemLines.length > 0
              width: parent.width
              spacing: Style.space(6)

              SubHeading { text: "PROBLEMS" }

              Repeater {
                model: root.problemLines.length

                ProblemRow {
                  required property int index
                  width: parent.width
                  problem: root.problemLines[index] || ({})
                }
              }
            }

            SettingsToggle {
              id: settingsHeader
              width: parent.width
            }

            Column {
              visible: root.settingsOpen
              width: parent.width
              spacing: Style.space(6)

              SubHeading { text: "HOOKS" }

              // One row per agent the helper lists (upstream's HOOK_PROVIDERS). Keyed on
              // the count, so an event that replaces the list does not rebuild the rows.
              Repeater {
                model: HelperProcess.hooks.length

                SettingRow {
                  required property int index
                  readonly property var entry: HelperProcess.hooks[index] || ({})
                  width: parent.width
                  rowName: "hook:" + entry.provider
                  settingLabel: Settings.hookName(entry.provider)
                  settingDetail: Settings.hookStatus(entry)
                  // An unreadable config has no switch: flipping it would act on a config we could not read.
                  showSwitch: entry.error === undefined
                  checked: entry.installed === true
                  busy: root.hookBusy === entry.provider
                }
              }

              SettingRow {
                width: parent.width
                rowName: "bar-leds"
                settingLabel: "Bar LEDs"
                settingDetail: root.barLedsDetail
                showSwitch: true
                checked: root.barLedsOn
              }

              SettingRow {
                width: parent.width
                rowName: "idle"
                stepper: true
                settingLabel: "Idle timeout"
                settingDetail: "A session with no hook for this long goes stale."
                settingValue: HelperProcess.settings !== null ? Settings.durationLabel(root.idleSeconds) : ""
              }

              SettingRow {
                width: parent.width
                rowName: "retention"
                stepper: true
                settingLabel: "Keep finished sessions"
                settingDetail: "Done sessions stay in the list for this long."
                settingValue: HelperProcess.settings !== null ? Settings.durationLabel(root.retentionSeconds) : ""
              }

              SettingRow {
                width: parent.width
                rowName: "notify"
                settingLabel: "Notify when a session asks"
                settingDetail: "Off by default. Clicking the notice opens the session."
                showSwitch: true
                checked: root.notifyOnAsk
              }

              SubHeading { text: "KEEP AWAKE" }

              // A stepped value, like the timeouts: the chevrons and the wheel step the policy both ways (D11).
              // The detail says whether the sleep lock is held now and why; a failure to hold shows under PROBLEMS (D14).
              SettingRow {
                width: parent.width
                rowName: "keep-awake"
                stepper: true
                settingLabel: "Policy"
                settingDetail: Settings.awakeText(HelperProcess.awake)
                settingValue: HelperProcess.awake !== null ? Settings.policyLabel(HelperProcess.awake.policy) : ""
              }

              SubHeading { text: "HOOK LOG" }

              Repeater {
                model: HelperProcess.hookLog.length

                LogRow {
                  required property int index
                  width: parent.width
                  entry: HelperProcess.hookLog[index] || ({})
                }
              }

              Text {
                visible: HelperProcess.hookLog.length === 0
                width: parent.width
                textFormat: Text.PlainText
                text: "No hook decisions yet."
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
            }
          }
        }
      }
    }
  }

  component SetupRow: CursorSurface {
    id: setupRow
    onHasCursorChanged: if (hasCursor) root.scrollItemIntoView(setupRow)
    required property var row

    readonly property bool actionable: row.action !== null && row.action !== undefined
    readonly property string rowName: "setup:" + row.id

    hasCursor: actionable && root.rowHasCursor(rowName)
    foreground: root.foreground
    implicitHeight: setupColumn.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      enabled: setupRow.actionable
      hoverEnabled: true
      cursorShape: setupRow.actionable ? Qt.PointingHandCursor : Qt.ArrowCursor
      onEntered: if (setupRow.actionable) root.focusRow(setupRow.rowName)
      onClicked: root.runSetupAction(setupRow.row)
    }

    Column {
      id: setupColumn
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(2)

      Text {
        textFormat: Text.PlainText
        width: parent.width
        text: setupRow.row.title
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        wrapMode: Text.WordWrap
      }

      Text {
        textFormat: Text.PlainText
        width: parent.width
        text: setupRow.row.detail
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }

      Text {
        textFormat: Text.PlainText
        visible: setupRow.actionable
        text: setupRow.row.action_label !== undefined ? setupRow.row.action_label : ""
        // An action reads as one: accent colour and an underline, not plain body text.
        color: setupRow.accent
        font.underline: true
        font.bold: true
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }
    }
  }

  // One listed session (D13): provider, project, state, last message and age.
  // Enter or a click opens it (D8); a row without a session id only shows.
  component SessionRow: CursorSurface {
    id: sessionRow
    onHasCursorChanged: if (hasCursor) root.scrollItemIntoView(sessionRow)
    required property var session
    required property int rowIndex

    readonly property string rowName: Sessions.sessionRow(rowIndex)
    readonly property string age: Sessions.formatAge(Sessions.ageSeconds(session.updated_ms, root.nowMs))
    readonly property color stateColour: session.colour ? session.colour : root.dim

    hasCursor: root.rowHasCursor(rowName)
    foreground: root.foreground
    implicitHeight: sessionColumn.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: sessionRow.session.resumable ? Qt.PointingHandCursor : Qt.ArrowCursor
      onEntered: root.focusRow(sessionRow.rowName)
      onClicked: root.openSession(sessionRow.rowIndex)
    }

    Column {
      id: sessionColumn
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(2)

      Row {
        width: parent.width
        spacing: Style.space(6)

        Text {
          textFormat: Text.PlainText
          text: Sessions.providerLabel(sessionRow.session.provider)
          color: sessionRow.stateColour
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }

        Text {
          id: projectText
          textFormat: Text.PlainText
          width: Math.max(0, parent.width - stateText.implicitWidth - ageText.implicitWidth - Style.space(24))
          text: sessionRow.session.project || sessionRow.session.name || ""
          elide: Text.ElideRight
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }

        Text {
          id: stateText
          textFormat: Text.PlainText
          text: sessionRow.session.label || ""
          color: sessionRow.stateColour
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }

        Text {
          id: ageText
          textFormat: Text.PlainText
          text: sessionRow.age
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }
      }

      Text {
        textFormat: Text.PlainText
        visible: text !== ""
        width: parent.width
        text: Sessions.oneLine(sessionRow.session.message)
        elide: Text.ElideRight
        maximumLineCount: 1
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
    }
  }

  // One connected device (D13, D21, D22): its product drawn with its LEDs, the
  // Display (Agent / Battery / Off, D9) and brightness.
  component DeviceRow: CursorSurface {
    id: deviceRow
    onHasCursorChanged: if (hasCursor) root.scrollItemIntoView(deviceRow)
    required property var device

    readonly property string rowName: "device:" + device.id
    readonly property string display: root.displayOf(device.id)
    // The device's LEDs as its program plays them now (the helper's frames, D21). Without frames
    // (the Bar LEDs' engine is unavailable, PROBLEMS says why) they show the program's colour, as
    // before the frames; Off shows dim. Agent and Battery both show what the device holds (D9, D10).
    readonly property var leds: display !== "off" ? (HelperProcess.deviceLeds[device.id] || null) : null
    readonly property color staticColour: display !== "off" && device.colour ? device.colour : root.dim

    hasCursor: root.rowHasCursor(rowName)
    foreground: root.foreground
    implicitHeight: deviceColumn.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      onEntered: root.focusRow(deviceRow.rowName)
    }

    Column {
      id: deviceColumn
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(6)

      Row {
        spacing: Style.space(10)

        ProductArt {
          anchors.verticalCenter: parent.verticalCenter
          count: deviceRow.device.led_count
          colours: deviceRow.leds
          colour: deviceRow.staticColour
        }

        Column {
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(1)

          Text {
            textFormat: Text.PlainText
            text: deviceRow.device.product
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          Text {
            textFormat: Text.PlainText
            text: deviceRow.device.name
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }
      }

      Text {
        textFormat: Text.PlainText
        text: "Display"
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }

      ButtonGroup {
        width: parent.width
        options: root.displayChoices
        value: deviceRow.display
        focusable: false
        onChanged: function (value) { root.setDisplay(deviceRow.device.id, value) }
      }

      Row {
        width: parent.width
        spacing: Style.space(8)

        PanelSlider {
          bar: root.bar
          width: parent.width - brightnessText.width - Style.space(8)
          anchors.verticalCenter: parent.verticalCenter
          minimum: 0
          maximum: 100
          step: 5
          integer: true
          value: deviceRow.device.percent
          onMoved: function (value) { HelperProcess.setBrightness(deviceRow.device.id, value) }
        }

        Text {
          id: brightnessText
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: deviceRow.device.percent + "%"
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }
      }
    }
  }

  // A device outline with one LED per light (the same shapes as the icon, D22).
  component ProductArt: Item {
    id: art
    property int count: 2
    // Each LED's [red, green, blue], 0-255, as the helper's frame gives it; null when there are none.
    property var colours: null
    // The colour drawn when there are no frames: the device program's first colour (#5), or dim.
    property color colour: root.dim
    readonly property real ledSize: Style.space(4)
    readonly property real ledGap: Style.space(3)

    // Read by each LED's delegate: a new frame changes the colours in place, the delegates stay.
    function ledColour(index) {
      const led = art.colours ? art.colours[index] : null
      return led ? Qt.rgba(led[0] / 255, led[1] / 255, led[2] / 255, 1) : art.colour
    }

    width: Math.max(Style.space(24), count * ledSize + (count - 1) * ledGap + Style.space(10))
    height: Style.space(12)

    Rectangle {
      anchors.fill: parent
      radius: height / 2
      color: "transparent"
      border.width: Math.max(1, Style.space(1))
      border.color: root.dim
    }

    Row {
      anchors.centerIn: parent
      spacing: art.ledGap

      Repeater {
        model: art.count

        Rectangle {
          required property int index
          width: art.ledSize
          height: width
          radius: width / 2
          color: art.ledColour(index)
        }
      }
    }
  }

  // The Settings section's header: Enter or a click opens and shuts it (D13).
  component SettingsToggle: CursorSurface {
    id: settingsToggle
    onHasCursorChanged: if (hasCursor) root.scrollItemIntoView(settingsToggle)
    readonly property string rowName: "settings"

    hasCursor: root.rowHasCursor(rowName)
    foreground: root.foreground
    implicitHeight: toggleRow.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onEntered: root.focusRow(settingsToggle.rowName)
      onClicked: root.activateRow(settingsToggle.rowName)
    }

    Row {
      id: toggleRow
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(8)

      PanelSectionHeader {
        text: "SETTINGS"
        foreground: root.foreground
        fontFamily: root.fontFamily
      }

      Text {
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: root.settingsOpen ? "Hide" : "Show"
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
    }
  }

  // One setting: its label, what it says now, and a switch or a value (D13).
  // Enter or a click on the row does the change; the switch is drawn only.
  component SettingRow: CursorSurface {
    id: settingRow
    onHasCursorChanged: if (hasCursor) root.scrollItemIntoView(settingRow)
    required property string rowName
    property string settingLabel: ""
    property string settingDetail: ""
    property string settingValue: ""
    property bool showSwitch: false
    property bool checked: false
    property bool busy: false
    // A timeout row: ‹ value › chevrons and the wheel step it both ways (Omarchy's
    // clock panel steps its month the same way); a click on the row does nothing.
    property bool stepper: false

    hasCursor: root.rowHasCursor(rowName)
    foreground: root.foreground
    implicitHeight: settingColumn.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onEntered: root.focusRow(settingRow.rowName)
      onClicked: if (!settingRow.stepper) root.activateRow(settingRow.rowName)
    }

    WheelHandler {
      enabled: settingRow.stepper
      onWheel: function (event) {
        // Side-scrolls report y === 0; they must not read as a step.
        if (event.angleDelta.y === 0) return
        root.stepRow(settingRow.rowName, event.angleDelta.y > 0 ? 1 : -1)
      }
    }

    Column {
      id: settingColumn
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(2)

      Row {
        width: parent.width
        spacing: Style.space(8)

        Text {
          textFormat: Text.PlainText
          width: Math.max(0, parent.width - valueLabel.implicitWidth
            - (switchItem.visible ? switchItem.width : 0)
            - (settingRow.stepper ? lessButton.width + moreButton.width + Style.space(16) : 0)
            - Style.space(16))
          text: settingRow.settingLabel
          elide: Text.ElideRight
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }

        PanelActionButton {
          id: lessButton
          visible: settingRow.stepper
          anchors.verticalCenter: parent.verticalCenter
          iconText: "󰅁"
          tooltipText: "Shorter"
          foreground: root.foreground
          fontFamily: root.fontFamily
          onClicked: root.stepRow(settingRow.rowName, -1)
        }

        Text {
          id: valueLabel
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: settingRow.settingValue
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        PanelActionButton {
          id: moreButton
          visible: settingRow.stepper
          anchors.verticalCenter: parent.verticalCenter
          iconText: "󰅂"
          tooltipText: "Longer"
          foreground: root.foreground
          fontFamily: root.fontFamily
          onClicked: root.stepRow(settingRow.rowName, 1)
        }

        ToggleSwitch {
          id: switchItem
          visible: settingRow.showSwitch
          anchors.verticalCenter: parent.verticalCenter
          checked: settingRow.checked
          busy: settingRow.busy
          interactive: false
          hasCursor: settingRow.hasCursor
          foreground: root.foreground
        }
      }

      Text {
        textFormat: Text.PlainText
        visible: text !== ""
        width: parent.width
        text: settingRow.settingDetail
        wrapMode: Text.WordWrap
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
    }
  }

  // A problem the helper reported: the feature in the urgent colour, then the reason (D14).
  component ProblemRow: Column {
    id: problemRow
    required property var problem
    spacing: Style.space(1)

    Text {
      textFormat: Text.PlainText
      width: parent.width
      text: problemRow.problem.title || ""
      color: root.urgent
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.bold: true
      wrapMode: Text.WordWrap
    }

    Text {
      textFormat: Text.PlainText
      width: parent.width
      text: problemRow.problem.message || ""
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.WordWrap
    }
  }

  // One hook decision (D13): when, which agent and event, and the State it became.
  component LogRow: Row {
    id: logRow
    required property var entry
    readonly property var line: Settings.logText(entry)
    spacing: Style.space(8)

    Text {
      id: logTime
      textFormat: Text.PlainText
      text: logRow.line.time
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }

    Text {
      textFormat: Text.PlainText
      width: Math.max(0, logRow.width - logTime.implicitWidth - stateText.implicitWidth - Style.space(24))
      text: logRow.line.what
      elide: Text.ElideRight
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }

    Text {
      id: stateText
      textFormat: Text.PlainText
      text: logRow.line.state
      color: logRow.entry.colour ? logRow.entry.colour : root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }
  }

  component SubHeading: Text {
    textFormat: Text.PlainText
    color: root.dim
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
  }

  // An empty section: one navigable row until its slice fills the section.
  component PlaceholderRow: CursorSurface {
    id: placeholder
    onHasCursorChanged: if (hasCursor) root.scrollItemIntoView(placeholder)
    property string rowName: ""
    property string label: "Nothing here yet."

    hasCursor: root.rowHasCursor(rowName)
    foreground: root.foreground
    implicitHeight: placeholderText.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      onEntered: root.focusRow(placeholder.rowName)
    }

    Text {
      id: placeholderText
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      textFormat: Text.PlainText
      text: placeholder.label
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
  }
}
