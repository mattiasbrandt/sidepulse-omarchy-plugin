pragma ComponentBehavior: Bound

import QtQuick
import "helper"
import "helper/barleds.mjs" as Band

// The Bar LEDs (D7, D21): the band of LEDs along the bar's inner edge, drawn
// from the helper's frames with upstream's blend (helper/barleds.mjs). The
// item reparents itself into the bar window's contentItem, so the bar's own
// slots cannot clip it and high z keeps it above the widgets. Every widget copy
// asks for its window's band, and the first copy per window holds it
// (HelperProcess). The position comes from the window, so which copy holds it
// does not show.
Item {
  id: band

  // The bar (for its edge) and its window.
  property var bar: null
  property var barWindow: null

  readonly property string edge: bar ? String(bar.position) : "top"
  readonly property bool horizontal: edge === "top" || edge === "bottom"
  readonly property var leds: HelperProcess.barLeds
  readonly property bool wants: horizontal && barWindow !== null && leds !== null
  readonly property bool holds: wants && HelperProcess.bandHolder(barWindow) === band

  parent: barWindow ? barWindow.contentItem : null
  z: 1000
  visible: holds
  width: parent ? Math.min(Band.WINDOW_WIDTH, parent.width) : 0
  height: Band.LED_BAND_HEIGHT
  x: parent ? Math.round((parent.width - width) / 2) : 0
  // The inner edge: the bottom of a top bar, the top of a bottom bar.
  y: parent ? (edge === "top" ? parent.height - height : 0) : 0

  function reclaim() { HelperProcess.wantBand(band, barWindow, wants) }

  onWantsChanged: reclaim()
  onBarWindowChanged: reclaim()
  onHoldsChanged: if (holds) canvas.requestPaint()
  onLedsChanged: if (holds) canvas.requestPaint()
  Component.onDestruction: HelperProcess.wantBand(band, null, false)

  Canvas {
    id: canvas
    anchors.fill: parent
    onPaint: {
      const context = getContext("2d")
      Band.paintBand(context, Band.ledColours(band.leds || []), width, band.edge === "top")
    }
  }
}
