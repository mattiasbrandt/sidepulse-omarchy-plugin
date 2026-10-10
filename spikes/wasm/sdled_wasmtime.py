"""wasmtime-py port of src/sidepulse/led_wasm.py (SdLedWasmController).

Same call sequence as the JavaScriptCore driver:
  - lazy sdled_reset(count, floor(nowMs)) on the first parse/step call
  - parse: write text into the 512-byte input buffer (UTF-16 code units
    encoded as ASCII/2-byte/3-byte UTF-8-style), call sdled_parse with
    length (513 if >512), decode packed result
  - step: sdled_step(count, nowMs), read count*3 RGB bytes at output ptr
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import wasmtime

ERROR_NAMES = (
    "ok",
    "null-input",
    "too-long",
    "too-many-lines",
    "too-many-animation-lines",
    "syntax",
    "bad-color",
    "bad-index",
    "bad-time",
    "bad-brightness",
    "bad-repeat",
    "trailing-input",
)
INPUT_SIZE = 512


def default_wasm_bytes() -> bytes:
    """The engine shipped inside the installed upstream sidepulse package."""
    from importlib.resources import files

    return files("sidepulse").joinpath("resources/sdled.wasm").read_bytes()


@dataclass(frozen=True)
class ParseResult:
    ok: bool
    error: int
    error_name: str
    line: int
    column: int


def _i32(value: int) -> int:
    """JS ToInt32 semantics for values passed into i32 wasm params."""
    return ((int(value) + 2**31) % 2**32) - 2**31


def _utf16_units(text: str) -> list[int]:
    data = text.encode("utf-16-le", "surrogatepass")
    return [data[i] | (data[i + 1] << 8) for i in range(0, len(data), 2)]


def encode_input(text: str) -> tuple[bytes, int]:
    """Port of writeInput(): returns (512-byte buffer, reported length)."""
    buf = bytearray(INPUT_SIZE)
    length = 0
    for code in _utf16_units(text):
        if code <= 127:
            if length < INPUT_SIZE:
                buf[length] = code
            length += 1
        elif code <= 2047:
            if length < INPUT_SIZE:
                buf[length] = (192 | (code >> 6)) & 0xFF
            if length + 1 < INPUT_SIZE:
                buf[length + 1] = (128 | (code & 63)) & 0xFF
            length += 2
        else:
            if length < INPUT_SIZE:
                buf[length] = (224 | (code >> 12)) & 0xFF
            if length + 1 < INPUT_SIZE:
                buf[length + 1] = (128 | ((code >> 6) & 63)) & 0xFF
            if length + 2 < INPUT_SIZE:
                buf[length + 2] = (128 | (code & 63)) & 0xFF
            length += 3
    return bytes(buf), length


def decode_parse_result(packed: int) -> ParseResult:
    packed &= 0xFFFFFFFF
    error = (packed >> 8) & 255
    return ParseResult(
        ok=(packed & 1) == 1,
        error=error,
        error_name=ERROR_NAMES[error] if error < len(ERROR_NAMES) else f"error-{error}",
        line=(packed >> 16) & 255,
        column=(packed >> 24) & 255,
    )


class SdLedWasm:
    def __init__(self, led_count: int = 8, wasm_bytes: bytes | None = None):
        self.led_count = 2 if int(led_count) == 2 else 8
        if wasm_bytes is None:
            wasm_bytes = default_wasm_bytes()
        self._engine = wasmtime.Engine()
        self._store = wasmtime.Store(self._engine)
        module = wasmtime.Module(self._engine, wasm_bytes)
        instance = wasmtime.Instance(self._store, module, [])
        exports = instance.exports(self._store)
        self._memory = exports["memory"]
        self._reset_fn = exports["sdled_reset"]
        self._parse_fn = exports["sdled_parse"]
        self._step_fn = exports["sdled_step"]
        self._input_ptr = exports["sdled_input_ptr"](self._store)
        self._output_ptr = exports["sdled_output_ptr"](self._store)
        self._initialized = False

    def _ensure_initialized(self, now_ms: int) -> None:
        if not self._initialized:
            self.reset(now_ms)

    def reset(self, now_ms: int) -> None:
        self._reset_fn(self._store, self.led_count, _i32(math.floor(now_ms)))
        self._initialized = True

    def parse(self, text: str, now_ms: int) -> ParseResult:
        self._ensure_initialized(now_ms)
        buf, length = encode_input(str(text))
        self._memory.write(self._store, buf, self._input_ptr)
        packed = self._parse_fn(
            self._store,
            self.led_count,
            513 if length > INPUT_SIZE else length,
            _i32(math.floor(now_ms)),
        )
        return decode_parse_result(packed)

    def step(self, now_ms: int) -> list[tuple[int, int, int]]:
        self._ensure_initialized(now_ms)
        self._step_fn(self._store, self.led_count, _i32(math.floor(now_ms)))
        count = self.led_count
        raw = bytes(self._memory.read(self._store, self._output_ptr, self._output_ptr + count * 3))
        return [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]
