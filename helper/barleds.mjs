// The Bar LEDs' drawing (D7): how each LED's colour spreads along the band and
// how it is tone-mapped, as upstream draws the SidePulse Notch (virtual_device.py:
// blended_led_color_at_x, tone_mapped_led_color and the LED_* constants). The
// helper sends the colours; these pure functions turn them into the band's
// columns. tests/test_bar_leds.py checks the constants against upstream's source
// and tests/barleds.test.mjs checks the functions against upstream's outputs.

// Upstream's constants (virtual_device.py). Changing one means changing upstream's too.
export const LED_BLEND_RADIUS_LEDS = 1.5;
export const BLEND_COLUMN_WIDTH = 4.0;
export const LED_GAMMA = 0.86;
export const LED_CORE_BOOST = 1.22;
export const LED_HOTLINE_BOOST = 1.46;
export const LED_BAND_HEIGHT = 5.0;
// The band's width for eight LEDs; the Bar LEDs use it, centred, on every bar (D7).
export const WINDOW_WIDTH = 220.0;

// A frame's LEDs, as the helper sends them: [red, green, blue], each 0-255. Upstream
// turns a wasm frame into (r, g, b, alpha) with alpha the largest channel; so does this.
export function ledColours(leds) {
  return leds.map(([red, green, blue]) => [
    red / 255,
    green / 255,
    blue / 255,
    Math.max(red, green, blue) / 255,
  ]);
}

// Blend every LED into one x position across ledWidth-wide slots: each LED
// contributes with a cosine falloff out to LED_BLEND_RADIUS_LEDS slots. The
// result is clamped to 0..1 per channel.
export function blendedColourAt(colours, x, ledWidth) {
  if (ledWidth <= 0) return [0, 0, 0, 0];
  const radius = ledWidth * LED_BLEND_RADIUS_LEDS;
  const totals = [0, 0, 0, 0];
  colours.forEach((colour, index) => {
    const centre = (index + 0.5) * ledWidth;
    const distance = Math.abs(x - centre);
    if (distance > radius) return;
    const phase = distance / radius;
    const weight = 0.5 + 0.5 * Math.cos(Math.PI * phase);
    for (let channel = 0; channel < 4; channel += 1) {
      totals[channel] += colour[channel] * weight;
    }
  });
  return totals.map((value) => Math.min(1, Math.max(0, value)));
}

// Gamma and a boost on each colour channel; the alpha only scales. Zero stays zero.
export function toneMappedColour(red, green, blue, alpha, boost = 1, alphaScale = 1) {
  const channel = (value) => (value <= 0 ? 0 : Math.min(1, Math.pow(value, LED_GAMMA) * boost));
  return [
    channel(red),
    channel(green),
    channel(blue),
    Math.min(1, Math.max(0, alpha * alphaScale)),
  ];
}

// The band as columns of BLEND_COLUMN_WIDTH across bandWidth: each column's core
// colour (the band) and hotline colour (its outer edge), both tone-mapped as
// upstream draws them. Columns whose colour is black are returned as null, so
// the painter skips them, as upstream's fill_rect_with_cg does.
export function bandColumns(colours, bandWidth) {
  const ledWidth = bandWidth / colours.length;
  const columns = [];
  for (let x = 0; x < bandWidth; x += BLEND_COLUMN_WIDTH) {
    const width = Math.min(BLEND_COLUMN_WIDTH, bandWidth - x);
    const [red, green, blue, alpha] = blendedColourAt(colours, x + width / 2, ledWidth);
    const lit = Math.max(red, green, blue, alpha) > 0.001;
    columns.push({
      x,
      width,
      core: lit ? toneMappedColour(red, green, blue, alpha, LED_CORE_BOOST, 0.92) : null,
      hotline: lit ? toneMappedColour(red, green, blue, alpha, LED_HOTLINE_BOOST, 0.72) : null,
    });
  }
  return columns;
}

// Where the band sits in its bar window, and how its canvas is turned (D7). A top or bottom bar's band is
// LED_BAND_HEIGHT tall across the window, centred, on the inner edge. A left or right bar's band is
// LED_BAND_HEIGHT wide down the inner edge (the right of a left bar, the left of a right bar), centred.
// For a vertical bar the band is painted in its horizontal frame and turned by a quarter turn, so the
// LEDs run top to bottom: the canvas is translated to originX/originY and rotated by `rotation`. The
// hotline (the edge facing the desktop) is the band's outer edge: the bottom of a top bar, the top of a
// bottom bar, the right of a left bar and the left of a right bar.
export function bandLayout(edge, windowWidth, windowHeight) {
  const vertical = edge === "left" || edge === "right"
  const length = Math.min(WINDOW_WIDTH, vertical ? windowHeight : windowWidth)
  const outerEdgeIsBottom = edge === "top" || edge === "right"
  if (vertical) {
    return {
      vertical,
      x: edge === "left" ? windowWidth - LED_BAND_HEIGHT : 0,
      y: Math.round((windowHeight - length) / 2),
      width: LED_BAND_HEIGHT,
      height: length,
      length,
      outerEdgeIsBottom,
      originX: LED_BAND_HEIGHT,
      originY: 0,
      rotation: Math.PI / 2,
    }
  }
  return {
    vertical,
    x: Math.round((windowWidth - length) / 2),
    y: edge === "top" ? windowHeight - LED_BAND_HEIGHT : 0,
    width: length,
    height: LED_BAND_HEIGHT,
    length,
    outerEdgeIsBottom,
    originX: 0,
    originY: 0,
    rotation: 0,
  }
}

// Paint the band into a 2D canvas that is bandWidth by LED_BAND_HEIGHT and sits
// on the bar's inner edge. The hotline is the band's outer edge, 1.15 px, as
// upstream draws it: the bottom edge when the outer edge is the bottom (a top bar).
export function paintBand(ctx, colours, bandWidth, outerEdgeIsBottom) {
  const hotHeight = 1.15;
  const rgba = ([red, green, blue, alpha]) =>
    `rgba(${Math.round(red * 255)}, ${Math.round(green * 255)}, ${Math.round(blue * 255)}, ${alpha})`;
  ctx.clearRect(0, 0, bandWidth, LED_BAND_HEIGHT);
  for (const column of bandColumns(colours, bandWidth)) {
    if (column.core === null) continue;
    ctx.fillStyle = rgba(column.core);
    ctx.fillRect(column.x, 0, column.width, LED_BAND_HEIGHT);
    ctx.fillStyle = rgba(column.hotline);
    ctx.fillRect(column.x, outerEdgeIsBottom ? LED_BAND_HEIGHT - hotHeight : 0, column.width, hotHeight);
  }
}
