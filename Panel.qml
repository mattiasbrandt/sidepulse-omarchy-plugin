pragma ComponentBehavior: Bound

import QtQuick
import qs.Commons
import qs.Ui

// SidePulse bar widget. This scaffold draws a neutral device glyph and an
// empty popup; agent sessions, devices and Bar LEDs arrive in later slices.
Panel {
  id: root

  moduleName: "io.github.mattiasbrandt.sidepulse"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: "SidePulse"

    // Our own shapes (D22): a device outline with one LED, in the bar's
    // foreground colour. No SidePulse brand artwork.
    iconComponent: Component {
      Item {
        Rectangle {
          anchors.centerIn: parent
          width: Style.space(12)
          height: Style.space(8)
          radius: height / 2
          color: "transparent"
          border.width: Math.max(1, Style.space(1))
          border.color: root.barForeground
        }

        Rectangle {
          anchors.centerIn: parent
          width: Style.space(3)
          height: width
          radius: width / 2
          color: root.barForeground
        }
      }
    }

    onPressed: function (buttonCode) {
      if (buttonCode === Qt.LeftButton) root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
    }
  }
}
