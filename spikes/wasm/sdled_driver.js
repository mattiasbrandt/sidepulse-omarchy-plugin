// Deno port of the JS in src/sidepulse/led_wasm.py (_javascript_controller),
// run as an independent second driver. Reads a JSON payload on stdin:
//   { wasm_base64, cases: [{ name, led_count, ops: [["parse", text, ms] | ["step", ms]] }] }
// Each case gets a fresh module instance (like a fresh JSContext upstream).
// Writes JSON to stdout: [{ name, results: [parseResult | frame(array of bytes)] }].
const ERROR_NAMES = [
  "ok", "null-input", "too-long", "too-many-lines", "too-many-animation-lines",
  "syntax", "bad-color", "bad-index", "bad-time", "bad-brightness", "bad-repeat",
  "trailing-input",
];

function b64ToBytes(base64) {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=";
  const bytes = [];
  let buffer = 0;
  let bits = 0;
  for (let i = 0; i < base64.length; i += 1) {
    const c = base64.charAt(i);
    if (c === "=") break;
    const value = chars.indexOf(c);
    if (value < 0) continue;
    buffer = (buffer << 6) | value;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      bytes.push((buffer >> bits) & 255);
    }
  }
  return new Uint8Array(bytes);
}

function createController(wasmBase64) {
  const module = new WebAssembly.Module(b64ToBytes(wasmBase64));
  const instance = new WebAssembly.Instance(module, {});
  const exports = instance.exports;
  const memory = exports.memory;
  const inputPtr = exports.sdled_input_ptr();
  const outputPtr = exports.sdled_output_ptr();
  const initialized = {};

  function normalizeLedCount(ledCount) {
    return Number(ledCount) === 2 ? 2 : 8;
  }

  function ensureInitialized(ledCount, nowMs) {
    const count = normalizeLedCount(ledCount);
    if (!initialized[count]) {
      exports.sdled_reset(count, Math.floor(nowMs));
      initialized[count] = true;
    }
    return count;
  }

  function decodeParseResult(packed) {
    const error = (packed >>> 8) & 255;
    return {
      ok: (packed & 1) === 1,
      error: error,
      errorName: ERROR_NAMES[error] || ("error-" + error),
      line: (packed >>> 16) & 255,
      column: (packed >>> 24) & 255,
    };
  }

  function writeInput(text) {
    const input = new Uint8Array(memory.buffer, inputPtr, 512);
    for (let i = 0; i < input.length; i += 1) input[i] = 0;
    let length = 0;
    for (let j = 0; j < text.length; j += 1) {
      const code = text.charCodeAt(j);
      if (code <= 127) {
        if (length < 512) input[length] = code;
        length += 1;
      } else if (code <= 2047) {
        if (length < 512) input[length] = 192 | (code >> 6);
        if (length + 1 < 512) input[length + 1] = 128 | (code & 63);
        length += 2;
      } else {
        if (length < 512) input[length] = 224 | (code >> 12);
        if (length + 1 < 512) input[length + 1] = 128 | ((code >> 6) & 63);
        if (length + 2 < 512) input[length + 2] = 128 | (code & 63);
        length += 3;
      }
    }
    return length;
  }

  return {
    reset(ledCount, nowMs) {
      const count = normalizeLedCount(ledCount);
      exports.sdled_reset(count, Math.floor(nowMs));
      initialized[count] = true;
    },
    parse(ledCount, text, nowMs) {
      const count = ensureInitialized(ledCount, nowMs);
      const length = writeInput(String(text));
      const packed = exports.sdled_parse(count, length > 512 ? 513 : length, Math.floor(nowMs));
      return decodeParseResult(packed);
    },
    step(ledCount, nowMs) {
      const count = ensureInitialized(ledCount, nowMs);
      exports.sdled_step(count, Math.floor(nowMs));
      const output = new Uint8Array(memory.buffer, outputPtr, count * 3);
      return Array.from(output);
    },
  };
}

const payload = JSON.parse(await new Response(Deno.stdin.readable).text());
const results = [];
for (const c of payload.cases) {
  const ctl = createController(payload.wasm_base64);
  const out = [];
  for (const op of c.ops) {
    if (op[0] === "parse") out.push(ctl.parse(c.led_count, op[1], op[2]));
    else out.push(ctl.step(c.led_count, op[1]));
  }
  results.push({ name: c.name, results: out });
}
console.log(JSON.stringify(results));
