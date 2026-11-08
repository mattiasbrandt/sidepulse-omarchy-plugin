# Third-party notices

This plugin is an unofficial hobby project. It contains code from
[SidePulse](https://github.com/inteliwear/sidepulse), the official project behind
the [SidePulse](https://sidepulse.io) devices, written by Peter Kuhar and the
SidePulse contributors. The code below stays under SidePulse's MIT license and
keeps its copyright notice. The rest of the plugin imports SidePulse's own code
from the user's install at run time, and SidePulse itself is not distributed
here.

## Code from SidePulse

Taken from upstream commit
[`6d55225`](https://github.com/inteliwear/sidepulse/commit/6d55225fed20a82521b23d86a4df2c4a15dff295).
Paths under `src/sidepulse/` are upstream's.

| File here | Code here | Upstream source | Relation |
|---|---|---|---|
| `helper/sidepulse_omarchy.py` | `awake_policy_should_hold`, `sleep_prevention_battery_safeguard`, `format_percent_value`, `lid_closed_led_state_should_hold`, `lid_close_animation_should_run`, `menu_status_sort_key` | `src/sidepulse/status_bar.py` | copied |
| `helper/sidepulse_omarchy.py` | `recent_statuses` | `src/sidepulse/status_bar.py` | adapted |
| `helper/sidepulse_omarchy.py` | `MODE_STATES`, `STATE_LABELS`, `STATE_COLOURS` | `src/sidepulse/status_bar.py` | copied mapping and values |
| `helper/sidepulse_omarchy.py` | `_engine_input` | `writeInput` in `src/sidepulse/led_wasm.py` | ported to Python |
| `helper/sidepulse_omarchy.py` | `SdLedWasm.parse` (the over-long length and the packed result's decoding) | `sdledParse` and `decodeParseResult` in `src/sidepulse/led_wasm.py` | ported to Python |
| `helper/barleds.mjs` | `blendedColourAt`, `toneMappedColour`, `bandColumns`, `paintBand`, the `LED_*` and `BLEND_COLUMN_WIDTH` constants | `src/sidepulse/virtual_device.py` | ported to JavaScript |
| `helper/sessions.mjs` | `formatAge` | `format_age` in `src/sidepulse/status_bar.py` | ported to JavaScript |
| `tests/barleds_vectors.json` | test vectors | output of `src/sidepulse/virtual_device.py` | generated |
| `tests/bar_leds_reference.py` | `upstream_vector_cases` (programs and expected frames) | `tests/test_sidepulse.py::test_led_wasm_controller_*` | copied |

## SidePulse's license

MIT License

Copyright (c) 2026 Peter Kuhar

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
