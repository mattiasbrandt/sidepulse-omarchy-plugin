pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "helper"

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

  // Idle is muted; the other three states use upstream's colours.
  readonly property color glyphColour: !agentState || agentState.state === "idle" || agentState.state === "unavailable"
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

  readonly property var sectionRows: ["sessions", "devices", "keep-awake", "settings"]

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
  Component.onDestruction: HelperProcess.unregister()
  onPythonSettingChanged: HelperProcess.pythonPath = pythonSetting

  onOpenedChanged: if (opened) {
    cursorActive = false
    cursorIndex = 0
    if (panelFlick) panelFlick.contentY = 0
    HelperProcess.refresh()
    Qt.callLater(function () { keyCatcher.forceActiveFocus() })
  }

  // Our own shapes (D22): a device outline with one LED. The setup look is the
  // same glyph at half opacity with a dot in the urgent colour.
  readonly property Component glyphComponent: Component {
    Item {
      opacity: root.needsSetup ? 0.5 : 1.0

      Rectangle {
        anchors.centerIn: parent
        width: Style.space(12)
        height: Style.space(8)
        radius: height / 2
        color: "transparent"
        border.width: Math.max(1, Style.space(1))
        border.color: root.glyphColour
      }

      Rectangle {
        anchors.centerIn: parent
        width: Style.space(3)
        height: width
        radius: width / 2
        color: root.glyphColour
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

            PlaceholderRow {
              width: parent.width
              rowName: "sessions"
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

            PlaceholderRow {
              width: parent.width
              rowName: "devices"
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

  // An empty section: one navigable row until its slice fills the section.
  component PlaceholderRow: CursorSurface {
    id: placeholder
    property string rowName: ""

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
      text: "Nothing here yet."
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
  }
}
