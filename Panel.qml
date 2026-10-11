pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "helper"
import "helper/sessions.mjs" as Sessions

// SidePulse bar widget. The helper (HelperProcess, shared by every copy of
// this widget) reports the agents' state, which colours the icon (D12). The
// popup is the section frame (D18): Setup lists what is missing (D19), and the
// four sections below are empty until their slices fill them.
Panel {
  id: root

  moduleName: "io.github.mattiasbrandt.sidepulse"

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
      if (!device.colour || displayOf(device.id) !== "agent") continue
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

  readonly property string tooltipText: {
    if (HelperProcess.notInstalled) return "SidePulse: not installed"
    if (!agentState) return "SidePulse: starting"
    let text = "SidePulse: " + agentState.label
    if (agentState.active_count > 0) text += ", " + agentState.active_count + " active"
    if (agentState.top) text += ", " + agentState.top.display_name
    if (needsSetup) text += " (setup needed)"
    return text
  }

  readonly property string heroMeta: {
    if (HelperProcess.notInstalled) return "Not installed"
    if (!agentState) return "Starting"
    return agentState.label + (agentState.active_count > 0 ? " · " + agentState.active_count + " active" : "")
  }

  // Problems the helper reported, one line each: capabilities that are off and failures.
  readonly property var problemLines: {
    const lines = []
    const keys = Object.keys(HelperProcess.errors)
    for (let i = 0; i < keys.length; i++) lines.push(keys[i] + ": " + HelperProcess.errors[keys[i]])
    // A helper that died before saying hello only leaves its last stderr line.
    if (!HelperProcess.helloReceived && !HelperProcess.notInstalled && HelperProcess.lastStderr !== "") {
      lines.push("helper: " + HelperProcess.lastStderr)
    }
    return lines
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
  readonly property var sectionRows: sessionRowNames.concat(deviceRowNames, ["keep-awake", "settings"])
  // Battery joins with #11, offered only where there is a battery.
  readonly property var displayOrder: ["agent", "off"]
  readonly property var displayChoices: [
    { value: "agent", label: "Agent" },
    { value: "off", label: "Off" }
  ]

  // The product drawn for the icon: Pro wins when both are plugged in (D21).
  readonly property int iconLedCount: {
    if (HelperProcess.devices.length === 0) return 1
    for (let i = 0; i < HelperProcess.devices.length; i++) {
      if (HelperProcess.devices[i].kind === "pro") return 8
    }
    return 2
  }

  // A value this version does not offer (none remembered, or a later Battery)
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

  // Writes the remembered displays into this widget's shell.json entry, keeping
  // the rest of the entry as it is (the same way the Tailscale panel does).
  function saveDeviceDisplays(displays) {
    if (!bar || !bar.shell || typeof bar.shell.updateEntryInline !== "function") return
    const entry = { id: root.moduleName }
    for (const key in settings) if (key !== "id") entry[key] = settings[key]
    entry.deviceDisplays = displays
    bar.shell.updateEntryInline(root.moduleName, entry)
  }

  function setDisplay(deviceId, display) {
    const displays = Object.assign({}, deviceDisplays)
    displays[deviceId] = display
    saveDeviceDisplays(displays)
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

  function runSetupAction(row) {
    if (row.action) HelperProcess.send({ cmd: row.action })
  }

  function activateCursor() {
    const name = cursorRow
    if (name.indexOf("session:") === 0) {
      openSession(parseInt(name.substring(8), 10))
      return
    }
    if (name.indexOf("device:") === 0) {
      cycleDisplay(name.substring(7))
      return
    }
    if (name.indexOf("setup:") !== 0) return
    const id = name.substring(6)
    for (let i = 0; i < setupRows.length; i++) {
      if (setupRows[i].id === id) runSetupAction(setupRows[i])
    }
  }

  Component.onCompleted: {
    HelperProcess.pythonPath = pythonSetting
    HelperProcess.register()
  }

  // A helper that has just started knows no displays yet: send what was remembered,
  // and send again whenever the remembered map changes (also when edited in shell.json).
  Connections {
    target: HelperProcess
    function onHelloReceivedChanged() {
      if (HelperProcess.helloReceived) HelperProcess.setDisplays(root.deviceDisplays)
    }
  }

  onDeviceDisplaysChanged: if (HelperProcess.helloReceived) HelperProcess.setDisplays(deviceDisplays)
  // Focusing or resuming a session closes the popup; a failure stays on show with its reason.
  Connections {
    target: HelperProcess
    function onLastResultChanged() {
      const result = HelperProcess.lastResult
      if (result && result.command === "open-session" && result.ok === true) root.close()
    }
  }

  Component.onDestruction: HelperProcess.unregister()
  onPythonSettingChanged: HelperProcess.pythonPath = pythonSetting

  // A result from an earlier open is stale once the popup opens again.
  onOpenedChanged: if (opened) {
    cursorActive = false
    cursorIndex = 0
    nowMs = Date.now()
    HelperProcess.lastResult = null
    if (panelFlick) panelFlick.contentY = 0
    HelperProcess.refresh()
    Qt.callLater(function () { keyCatcher.forceActiveFocus() })
  }

  // Our own shapes (D22): a device outline with its LEDs (D21: one neutral LED
  // with no device, 2 for a SidePulse Dot, 8 for a SidePulse Pro). The setup
  // look is the same glyph at half opacity with a dot in the urgent colour.
  readonly property Component glyphComponent: Component {
    Item {
      id: glyph
      opacity: root.needsSetup ? 0.5 : 1.0
      readonly property real ledSize: Style.space(2)
      readonly property real ledGap: Style.space(1)
      readonly property real ledsWidth: root.iconLedCount * ledSize + (root.iconLedCount - 1) * ledGap

      Rectangle {
        anchors.centerIn: parent
        width: Math.max(Style.space(12), glyph.ledsWidth + Style.space(4))
        height: Style.space(8)
        radius: height / 2
        color: "transparent"
        border.width: Math.max(1, Style.space(1))
        border.color: root.glyphColour
      }

      Row {
        anchors.centerIn: parent
        spacing: glyph.ledGap

        Repeater {
          model: root.iconLedCount

          Rectangle {
            required property int index
            width: glyph.ledSize
            height: width
            radius: width / 2
            color: root.glyphColour
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
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: root.tooltipText
    // Ask lights the icon with the urgent highlight (D12).
    active: root.agentState !== null && root.agentState.urgent === true
    iconComponent: root.glyphComponent

    onPressed: function (buttonCode) {
      if (buttonCode === Qt.LeftButton) root.toggle()
    }
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

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
        else if (dx !== 0 && root.cursorRow.indexOf("device:") === 0) root.nudgeBrightness(root.cursorRow.substring(7), dx)
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

            PanelSectionHeader {
              text: "KEEP AWAKE"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            PlaceholderRow {
              width: parent.width
              rowName: "keep-awake"
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(6)

            PanelSectionHeader {
              text: "SETTINGS"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Repeater {
              model: root.problemLines

              Text {
                required property var modelData
                textFormat: Text.PlainText
                width: parent.width
                text: modelData
                color: root.urgent
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.WordWrap
              }
            }

            PlaceholderRow {
              width: parent.width
              rowName: "settings"
            }
          }
        }
      }
    }
  }

  component SetupRow: CursorSurface {
    id: setupRow
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
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }
    }
  }

  // One listed session (D13): provider, project, state, last message and age.
  // Enter or a click opens it (D8); a row without a session id only shows.
  component SessionRow: CursorSurface {
    id: sessionRow
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
  // Display (Agent / Off, D9) and brightness.
  component DeviceRow: CursorSurface {
    id: deviceRow
    required property var device

    readonly property string rowName: "device:" + device.id
    readonly property string display: root.displayOf(device.id)
    // The colour the device's own LEDs show now (the animation frames come with
    // the Bar LEDs, #8); Off and dark programs leave them dim.
    readonly property color ledColour: display === "agent" && device.colour
      ? device.colour
      : root.dim

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
          colour: deviceRow.ledColour
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
    property color colour: root.dim
    readonly property real ledSize: Style.space(4)
    readonly property real ledGap: Style.space(3)

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
          color: art.colour
        }
      }
    }
  }

  // An empty section: one navigable row until its slice fills the section.
  component PlaceholderRow: CursorSurface {
    id: placeholder
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
