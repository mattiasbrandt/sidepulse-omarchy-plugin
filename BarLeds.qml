pragma ComponentBehavior: Bound

import QtQuick
import "helper"
import "helper/barleds.mjs" as Band

// The Bar LEDs (D7, D21): the band of LEDs along the bar's inner edge, drawn
// from the helper's frames with upstream's blend (helper/barleds.mjs). The
// item reparents itself into the bar window's contentItem, so the bar's own
// slots cannot clip it. It sits below the bar's widgets (negative z, above the
// window's own background colour), so marks the bar draws on the same edge, such
// as the open-popup line, stay on top of it. Every widget copy
// asks for its window's band, and the first copy per window holds it
// (HelperProcess). The position comes from the window, so which copy
// holds it does not show. A top or bottom bar's band runs across the bar; a
// left or right bar's runs down it (Band.bandLayout).
Item {
  id: band

  // The bar (for its edge) and its window.
  property var bar: null
  property var barWindow: null

  readonly property string edge: bar ? String(bar.position) : "top"
  readonly property var leds: HelperProcess.barLeds
  readonly property bool wants: barWindow !== null && leds !== null
  readonly property bool holds: wants && HelperProcess.bandHolder(barWindow) === band
  readonly property var layout: Band.bandLayout(edge, parent ? parent.width : 0, parent ? parent.height : 0)

  parent: barWindow ? barWindow.contentItem : null
  z: -1
  visible: holds
  width: layout.width
  height: layout.height
  x: layout.x
  y: layout.y

  function reclaim() { HelperProcess.wantBand(band, barWindow, wants) }

  onWantsChanged: reclaim()
  onBarWindowChanged: reclaim()
  onHoldsChanged: if (holds) canvas.requestPaint()
  onLedsChanged: if (holds) canvas.requestPaint()
  onLayoutChanged: if (holds) canvas.requestPaint()
  Component.onDestruction: HelperProcess.wantBand(band, null, false)

  Canvas {
    id: canvas
    anchors.fill: parent
    onPaint: {
      const context = getContext("2d")
      // The band is painted in its horizontal frame; a vertical bar's canvas is turned to run along it.
      context.save()
      context.translate(band.layout.originX, band.layout.originY)
      context.rotate(band.layout.rotation)
      Band.paintBand(context, Band.ledColours(band.leds || []), band.layout.length, band.layout.outerEdgeIsBottom)
      context.restore()
    }
  }
}
