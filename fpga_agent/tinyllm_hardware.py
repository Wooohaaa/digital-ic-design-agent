"""Physical UART deployment and acceptance gate for TinyStories260K."""
from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evidence import implementation_valid, sha, simulation_valid
from .project import load_manifest
from .terminal import write_json
from .tinyllm import ASSETS, Reference


MODEL_BYTES = 1_056_540
BAUD = 115_200
PAGE_BYTES = 256
LINE_TIMEOUT_SECONDS = 240.0
PAGE_ACK_TIMEOUT_SECONDS = 30.0
INFERENCE_TIMEOUT_SECONDS = 30.0 * 60.0


class _UART:
    """Small transcript-aware line and binary protocol helper."""

    def __init__(self, port: Any, transcript: list[str]):
        self.port = port
        self.transcript = transcript

    def note(self, direction: str, message: str) -> None:
        stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        self.transcript.append(f"{stamp} {direction} {message}")

    def send(self, payload: bytes, description: str) -> None:
        view = memoryview(payload)
        sent = 0
        while sent < len(view):
            count = self.port.write(bytes(view[sent:]))
            if not count:
                raise TimeoutError(f"UART write stalled while sending {description}")
            sent += count
        self.port.flush()
        detail = f"data={payload.hex()}" if len(payload) <= 64 else f"sha256={hashlib.sha256(payload).hexdigest()}"
        self.note("TX", f"{description} ({len(payload)} bytes, {detail})")

    def read_line(self, description: str, *, timeout: float = LINE_TIMEOUT_SECONDS,
                  total_deadline: float | None = None) -> str:
        deadline = time.monotonic() + timeout
        if total_deadline is not None:
            deadline = min(deadline, total_deadline)
        data = bytearray()
        while time.monotonic() < deadline:
            byte = self.port.read(1)
            if not byte:
                continue
            if byte == b"\n":
                line = data.rstrip(b"\r").decode("ascii", errors="replace")
                self.note("RX", f"{description}: {line}")
                if "ERROR" in line.upper():
                    raise RuntimeError(f"Board reported {line!r} while {description}")
                return line
            data.extend(byte)
        partial = data.rstrip(b"\r").decode("ascii", errors="replace")
        if partial:
            self.note("RX", f"{description} partial before timeout: {partial}")
        raise TimeoutError(f"Timed out waiting for {description}; partial={partial!r}")

    def read_nonempty_line(self, description: str, *, timeout: float = LINE_TIMEOUT_SECONDS,
                           total_deadline: float | None = None) -> str:
        deadline = time.monotonic() + timeout
        if total_deadline is not None:
            deadline = min(deadline, total_deadline)
        while time.monotonic() < deadline:
            line = self.read_line(description, timeout=deadline-time.monotonic(),
                                  total_deadline=total_deadline)
            if line:
                return line
        raise TimeoutError(f"Timed out waiting for nonempty {description}")

    def read_page_ack(self, page_index: int) -> None:
        deadline = time.monotonic() + PAGE_ACK_TIMEOUT_SECONDS
        pending = bytearray()
        while time.monotonic() < deadline:
            byte = self.port.read(1)
            if not byte:
                continue
            if byte == b"+":
                if pending:
                    text = pending.decode("ascii", errors="replace").strip()
                    self.note("RX", f"page {page_index} pre-ack: {text}")
                    if "ERROR" in text.upper():
                        raise RuntimeError(f"Board reported {text!r} while uploading page {page_index}")
                self.note("RX", f"page {page_index}: +")
                return
            if byte == b"\n":
                text = pending.rstrip(b"\r").decode("ascii", errors="replace")
                self.note("RX", f"page {page_index}: {text}")
                if "ERROR" in text.upper():
                    raise RuntimeError(f"Board reported {text!r} while uploading page {page_index}")
                pending.clear()
            else:
                pending.extend(byte)
        partial = pending.decode("ascii", errors="replace")
        if partial:
            self.note("RX", f"page {page_index} partial before timeout: {partial}")
        raise TimeoutError(f"Timed out waiting for page {page_index} acknowledgement")


class _Tokenizer:
    """Tokenizer for the llama2.c tok512.bin score/piece table."""

    def __init__(self, path: Path):
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != ASSETS["tok512.bin"]:
            raise ValueError(f"tok512.bin SHA-256 mismatch: {digest}")
        if len(raw) < 4:
            raise ValueError("tok512.bin is truncated")
        self.max_piece_bytes = struct.unpack_from("<i", raw, 0)[0]
        if self.max_piece_bytes <= 0:
            raise ValueError("tok512.bin has invalid maximum piece length")
        offset = 4
        self.scores: list[float] = []
        self.pieces: list[bytes] = []
        self.lookup: dict[bytes, int] = {}
        for token_id in range(512):
            if offset + 8 > len(raw):
                raise ValueError(f"tok512.bin ends before vocabulary entry {token_id}")
            score, piece_length = struct.unpack_from("<fi", raw, offset)
            offset += 8
            if piece_length < 0 or piece_length > self.max_piece_bytes or offset + piece_length > len(raw):
                raise ValueError(f"tok512.bin entry {token_id} has invalid piece length")
            piece = raw[offset:offset + piece_length]
            offset += piece_length
            if not math.isfinite(score):
                raise ValueError(f"tok512.bin entry {token_id} has non-finite score")
            self.scores.append(float(score))
            self.pieces.append(piece)
            self.lookup.setdefault(piece, token_id)
        if offset != len(raw):
            raise ValueError(f"tok512.bin has {len(raw)-offset} trailing bytes")
        self.sha256 = digest

    @staticmethod
    def _utf8_character_length(lead: int) -> int:
        if lead < 0x80:
            return 1
        if 0xC2 <= lead <= 0xDF:
            return 2
        if 0xE0 <= lead <= 0xEF:
            return 3
        if 0xF0 <= lead <= 0xF4:
            return 4
        return 1

    def encode(self, text: str) -> list[int]:
        data = (" " + text).encode("utf-8")
        tokens: list[int] = []
        cursor = 0
        while cursor < len(data):
            width = self._utf8_character_length(data[cursor])
            piece = data[cursor:cursor + width]
            token_id = self.lookup.get(piece)
            if token_id is None:
                tokens.extend(3 + value for value in piece)
            else:
                tokens.append(token_id)
            cursor += width

        while len(tokens) > 1:
            best_score = -math.inf
            best_index = -1
            best_id = -1
            for index in range(len(tokens) - 1):
                merged = self.pieces[tokens[index]] + self.pieces[tokens[index + 1]]
                candidate = self.lookup.get(merged)
                if candidate is not None and self.scores[candidate] > best_score:
                    best_score = self.scores[candidate]
                    best_index = index
                    best_id = candidate
            if best_index < 0:
                break
            tokens[best_index] = best_id
            del tokens[best_index + 1]
        return [1, *tokens]

    def decode_generated(self, token_ids: list[int]) -> str:
        output = bytearray()
        for token_id in token_ids:
            if token_id == 2:
                break
            if token_id == 0:
                output.extend(b"<unk>")
            elif token_id == 1:
                continue
            elif 3 <= token_id < 259:
                output.append(token_id - 3)
            elif 259 <= token_id < len(self.pieces):
                output.extend(self.pieces[token_id])
            else:
                raise ValueError(f"Generated token ID out of range: {token_id}")
        return output.decode("utf-8", errors="replace")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Cannot read required evidence {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Evidence is not a JSON object: {path}")
    return value


def _preflight(project: Path) -> tuple[dict[str, Any], dict[str, str], Path, Path, int]:
    manifest = load_manifest(project)
    sim = _read_json(project / "reports/simulation.json")
    td = _read_json(project / "reports/td.json")
    programming = _read_json(project / "reports/programming.json")

    valid, reason = simulation_valid(project, manifest, sim)
    if not valid:
        raise RuntimeError(f"Simulation evidence is invalid: {reason}")
    valid, reason = implementation_valid(project, manifest, td)
    if not valid:
        raise RuntimeError(f"TD implementation evidence is invalid: {reason}")
    if programming.get("status") != "pass":
        raise RuntimeError("reports/programming.json does not report status=pass")
    td_hashes = td.get("bitstream_hashes")
    programming_hashes = programming.get("bitstream_hashes")
    if not isinstance(td_hashes, dict) or not td_hashes or programming_hashes != td_hashes:
        raise RuntimeError("Programming bitstream_hashes do not exactly match the validated TD report")

    model = project / "model/stories260K.bin"
    tokenizer = project / "model/tok512.bin"
    for name, path in (("stories260K.bin", model), ("tok512.bin", tokenizer)):
        actual = sha(path)
        if actual != ASSETS[name]:
            raise RuntimeError(f"Verified model asset changed: {name} SHA-256 {actual}")
    model_bytes = model.read_bytes()
    if len(model_bytes) != MODEL_BYTES:
        raise RuntimeError(f"Checkpoint length is {len(model_bytes)}, expected {MODEL_BYTES}")
    model_crc = zlib.crc32(model_bytes) & 0xFFFFFFFF
    header = project / "firmware/model_contract.h"
    try:
        contract = header.read_text(encoding="ascii")
    except OSError as exc:
        raise RuntimeError(f"Cannot read firmware model CRC contract: {exc}") from exc
    match = re.search(r"#define\s+MODEL_CRC32\s+(0x[0-9a-fA-F]+)u", contract)
    if not match or int(match.group(1), 16) != model_crc:
        raise RuntimeError("Firmware MODEL_CRC32 does not match the verified checkpoint")
    return td_hashes, programming, model, tokenizer, model_crc


def _reference_logits(reference: Reference, token: int, position: int):
    values = reference.forward(token, position)
    np = reference.np
    if not np.all(np.isfinite(values)):
        raise RuntimeError(f"Independent Reference produced non-finite logits at position {position}")
    return values, int(np.argmax(values))


def _expect_crc_line(uart: _UART, label: str, expected_crc: int) -> int:
    line = uart.read_nonempty_line(label)
    match = re.fullmatch(r"CRC\s+([0-9a-fA-F]{8})", line.strip())
    if not match:
        raise RuntimeError(f"Expected CRC line during {label}, received {line!r}")
    actual = int(match.group(1), 16)
    if actual != expected_crc:
        raise RuntimeError(f"Flash CRC mismatch during {label}: {actual:08x} != {expected_crc:08x}")
    return actual


def _upload_model(uart: _UART, model: Path, model_crc: int, total_bytes: int) -> int:
    uart.send(b"W" + struct.pack("<II", total_bytes, model_crc), "W + length/CRC header")
    response = uart.read_nonempty_line("UPLOAD prompt")
    if response.strip() != "UPLOAD":
        raise RuntimeError(f"Expected UPLOAD prompt, received {response!r}")

    data = model.read_bytes()
    page_count = (len(data) + PAGE_BYTES - 1) // PAGE_BYTES
    with model.open("rb") as stream:
        for page_index in range(page_count):
            page = stream.read(PAGE_BYTES)
            uart.send(page, f"model page {page_index + 1}/{page_count}")
            uart.read_page_ack(page_index + 1)
            if (page_index + 1) % 64 == 0 or page_index + 1 == page_count:
                print(f"TinyStories upload: {page_index + 1}/{page_count} pages", flush=True)

    actual = _expect_crc_line(uart, "upload readback CRC", model_crc)
    done = uart.read_nonempty_line("upload completion")
    if done.strip() != "DONE":
        raise RuntimeError(f"Expected upload DONE, received {done!r}")
    return actual


def _run_diagnostic(uart: _UART, model: Path, total_deadline: float) -> tuple[int, float, list[int]]:
    import numpy as np

    request = b"T\x03\x02" + struct.pack("<HH", 1, 382)
    uart.send(request, "T diagnostic prompt [1,382], steps=3")
    reference = Reference(model)
    inputs = [1, 382]
    next_token = 1
    argmax_tokens: list[int] = []
    max_error = 0.0
    comparisons = 0

    for position in range(3):
        token = inputs[position] if position < len(inputs) else next_token
        expected_logits, expected_best = _reference_logits(reference, token, position)
        next_token = expected_best
        argmax_tokens.append(expected_best)

        line = uart.read_line(f"T logits position {position}", total_deadline=total_deadline)
        fields = line.strip().split()
        if len(fields) != 514 or fields[0] != "LOGITS" or fields[1] != str(position):
            raise RuntimeError(f"Malformed logits frame at position {position}: {line[:160]!r}")
        try:
            actual = np.asarray([struct.unpack("<f", struct.pack("<I", int(word, 16)))[0]
                                 for word in fields[2:]], dtype=np.float32)
        except (ValueError, struct.error) as exc:
            raise RuntimeError(f"Invalid logits bit pattern at position {position}") from exc
        if not np.all(np.isfinite(actual)):
            raise RuntimeError(f"Board returned non-finite logits at position {position}")
        error = float(np.max(np.abs(actual - expected_logits)))
        max_error = max(max_error, error)
        if error > 0.002:
            raise RuntimeError(f"Logits mismatch at position {position}: max abs error {error}")
        actual_best = int(np.argmax(actual))
        if actual_best != expected_best:
            raise RuntimeError(f"Logits argmax mismatch at position {position}: {actual_best} != {expected_best}")
        comparisons += int(actual.size)
        if position >= len(inputs) - 1:
            token_line = uart.read_line(f"T predicted token at position {position}",
                                        total_deadline=total_deadline)
            match = re.fullmatch(r"TOKEN\s+(\d+)\s+(\d+)", token_line.strip())
            if not match:
                raise RuntimeError(f"Malformed diagnostic TOKEN line: {token_line!r}")
            reported_position, reported_token = map(int, match.groups())
            expected_token = expected_best
            if reported_position != position + 1 or reported_token != expected_token:
                raise RuntimeError("Diagnostic TOKEN differs from independent Reference: "
                                   f"got ({reported_position},{reported_token}), "
                                   f"expected ({position+1},{expected_token})")

    done = uart.read_line("T diagnostic completion", total_deadline=total_deadline)
    if done.strip() != "DONE":
        raise RuntimeError(f"Expected diagnostic DONE, received {done!r}")
    if comparisons != 1536:
        raise RuntimeError(f"Compared {comparisons} logits, expected 1536")
    return comparisons, max_error, argmax_tokens


def _expected_generation(reference: Reference, prompt_tokens: list[int], steps: int) -> list[tuple[int, int]]:
    expected: list[tuple[int, int]] = []
    next_token = 1
    for position in range(steps):
        token = prompt_tokens[position] if position < len(prompt_tokens) else next_token
        _, next_token = _reference_logits(reference, token, position)
        if position >= len(prompt_tokens) - 1:
            expected.append((position + 1, next_token))
            if next_token == 2:
                break
    return expected


def _run_generation(uart: _UART, model: Path, prompt: str, prompt_tokens: list[int],
                    new_tokens: int, total_deadline: float) -> tuple[str, list[int]]:
    import numpy as np  # noqa: F401 -- confirms the independent verifier runtime is available.

    steps = len(prompt_tokens) + new_tokens - 1
    expected = _expected_generation(Reference(model), prompt_tokens, steps)
    if time.monotonic() >= total_deadline:
        raise TimeoutError("Independent generation reference exceeded the inference deadline")
    frame = b"G" + bytes((steps, len(prompt_tokens))) + b"".join(
        struct.pack("<H", token) for token in prompt_tokens)
    uart.send(frame, f"G prompt with {len(prompt_tokens)} tokens, {new_tokens} new tokens")
    actual: list[tuple[int, int]] = []
    while time.monotonic() < total_deadline:
        line = uart.read_line("G generation output", total_deadline=total_deadline)
        if line.strip() == "DONE":
            break
        match = re.fullmatch(r"TOKEN\s+(\d+)\s+(\d+)", line.strip())
        if not match:
            raise RuntimeError(f"Unexpected generation response: {line!r}")
        reported_position, reported_token = map(int, match.groups())
        if not 0 <= reported_token < 512:
            raise RuntimeError(f"Board returned token ID out of range: {reported_token}")
        actual.append((reported_position, reported_token))
    else:
        raise TimeoutError("FPGA generation exceeded the 30-minute total deadline")

    if actual != expected:
        raise RuntimeError(f"FPGA token sequence differs from independent Reference: "
                           f"actual={actual}, expected={expected}")
    generated_ids = [token for _, token in actual]
    tokenizer = _Tokenizer(model.parent / "tok512.bin")
    generated_text = tokenizer.decode_generated(generated_ids)
    return prompt + generated_text, generated_ids


def hardware_run(project: Path, port: str, upload: bool = False,
                 prompt: str = "Once upon a time", new_tokens: int = 8) -> dict[str, Any]:
    """Verify and exercise a programmed TinyStories260K FPGA over UART.

    The FPGA performs every forward pass. Host computation is limited to the
    independent NumPy reference check, BPE tokenization, and text decoding.
    """
    if isinstance(new_tokens, bool) or not isinstance(new_tokens, int) or not 1 <= new_tokens <= 64:
        raise ValueError("new_tokens must be an integer from 1 through 64")
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    if not isinstance(upload, bool):
        raise ValueError("upload must be a bool")
    if not isinstance(port, str) or not port.strip():
        raise ValueError("port must be a nonempty serial port name")

    project = Path(project).resolve()
    transcript: list[str] = []
    result: dict[str, Any] = {
        "status": "fail",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "port": port,
        "upload_requested": bool(upload),
        "prompt": prompt,
        "new_tokens_requested": new_tokens,
    }
    serial_port = None
    transcript_path = project / "reports/hardware-uart.txt"
    report_path = project / "reports/hardware.json"
    project.joinpath("reports").mkdir(parents=True, exist_ok=True)
    write_json(report_path, result)
    try:
        model = project / "model/stories260K.bin"
        tokenizer_path = project / "model/tok512.bin"
        tokenizer = _Tokenizer(tokenizer_path)
        prompt_tokens = tokenizer.encode(prompt)
        if not prompt_tokens or len(prompt_tokens) > 64:
            raise ValueError(f"Prompt encodes to {len(prompt_tokens)} tokens; context capacity is 64")
        steps = len(prompt_tokens) + new_tokens - 1
        if steps > 64:
            raise ValueError(f"Prompt ({len(prompt_tokens)}) plus requested generation exceeds ctx64")
        result["prompt_token_ids"] = prompt_tokens
        result["steps"] = steps

        bitstream_hashes, programming, model, tokenizer_path, model_crc = _preflight(project)
        result["bitstream_hashes"] = bitstream_hashes
        result["model_sha256"] = sha(model)
        result["model_crc32"] = f"{model_crc:08x}"
        result["tokenizer_sha256"] = sha(tokenizer_path)
        result["programming_record"] = programming.get("programming_id", programming.get("completed_at"))
        result["verifier_sha256"] = {
            "tinyllm.py": sha(Path(__file__).with_name("tinyllm.py")),
            "tinyllm_hardware.py": sha(Path(__file__)),
        }

        # Only connect after independent simulation, TD, programmer, and asset evidence passed.
        if port.startswith("ftdi://"):
            from .ftdi import FtdiSerial

            serial_port = FtdiSerial(port=port, baudrate=BAUD, timeout=0.2,
                                     write_timeout=LINE_TIMEOUT_SECONDS)
            serial_number = port.removeprefix("ftdi://")
            info = {
                "port": port,
                "vid": 0x0403,
                "pid": 0x6042,
                "vid_hex": "0403",
                "pid_hex": "6042",
                "serial_number": serial_number,
                "manufacturer": "FTDI",
                "product": "USB-JTAG Cable B (D2XX)",
                "location": None,
                "hwid": "D2XX",
            }
        else:
            try:
                import serial
                from serial.tools import list_ports
            except ImportError as exc:
                raise RuntimeError("pyserial is required for COM/tty ports; install the optional tinyllm dependency") from exc
            serial_port = serial.Serial(port=port, baudrate=BAUD, bytesize=serial.EIGHTBITS,
                                        parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                                        timeout=0.2, write_timeout=LINE_TIMEOUT_SECONDS)
            try:
                serial_port.dtr = False
                serial_port.rts = False
            except (AttributeError, OSError):
                pass
            usb_info = next((item for item in list_ports.comports()
                             if str(getattr(item, "device", "")).casefold() == port.casefold()), None)
            source = usb_info if usb_info is not None else serial_port
            info = {
                "port": getattr(serial_port, "port", port),
                "vid": getattr(source, "vid", None),
                "pid": getattr(source, "pid", None),
                "vid_hex": f"{source.vid:04x}" if isinstance(getattr(source, "vid", None), int) else None,
                "pid_hex": f"{source.pid:04x}" if isinstance(getattr(source, "pid", None), int) else None,
                "serial_number": getattr(source, "serial_number", None),
                "manufacturer": getattr(source, "manufacturer", None),
                "product": getattr(source, "product", None),
                "location": getattr(source, "location", None),
                "hwid": getattr(source, "hwid", None),
            }
        result["serial_device"] = info
        uart = _UART(serial_port, transcript)

        uart.send(b"I", "JEDEC ID command")
        handshake_deadline = time.monotonic() + LINE_TIMEOUT_SECONDS
        jedec_line = ""
        while time.monotonic() < handshake_deadline:
            line = uart.read_line("startup banner / JEDEC", timeout=handshake_deadline-time.monotonic())
            if line.strip().startswith("JEDEC "):
                jedec_line = line
                break
        else:
            raise TimeoutError("No JEDEC response after sending I; firmware may not be running")
        match = re.fullmatch(r"JEDEC\s+([0-9a-fA-F]{8})", jedec_line.strip())
        if not match:
            raise RuntimeError(f"Malformed JEDEC response: {jedec_line!r}")
        jedec = int(match.group(1), 16)
        if jedec != 0x20BA18:
            raise RuntimeError(f"Unexpected user Flash JEDEC ID: {jedec:08x}")
        result["jedec_id"] = f"{jedec:08x}"

        if upload:
            result["upload_crc32"] = f"{_upload_model(uart, model, model_crc, MODEL_BYTES):08x}"

        uart.send(b"V", "full-image CRC command")
        actual_crc = _expect_crc_line(uart, "full-image CRC", model_crc)
        result["flash_crc32"] = f"{actual_crc:08x}"

        inference_deadline = time.monotonic() + INFERENCE_TIMEOUT_SECONDS
        diagnostic_count, max_error, diagnostic_tokens = _run_diagnostic(uart, model, inference_deadline)
        result["diagnostic_logits_count"] = diagnostic_count
        result["diagnostic_max_absolute_error"] = max_error
        result["diagnostic_argmax_tokens"] = diagnostic_tokens

        story, generated_ids = _run_generation(uart, model, prompt, prompt_tokens, new_tokens,
                                               inference_deadline)
        result["story"] = story
        result["generated_token_ids"] = generated_ids
        result["status"] = "pass"
        result["completed_at"] = datetime.now(timezone.utc).isoformat()
        return result
    except BaseException as exc:
        result["status"] = "fail"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["completed_at"] = datetime.now(timezone.utc).isoformat()
        raise
    finally:
        active_exception = sys.exc_info()[0] is not None
        if serial_port is not None:
            try:
                serial_port.close()
            except Exception as exc:
                transcript.append(f"{datetime.now(timezone.utc).isoformat()} CLOSE error: {exc}")
        try:
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            transcript_path.write_text("\n".join(transcript) + ("\n" if transcript else ""),
                                       encoding="utf-8")
            result["uart_transcript"] = "reports/hardware-uart.txt"
            result["uart_transcript_sha256"] = sha(transcript_path)
            result["host_verifier_sha256"] = sha(Path(__file__))
            result.setdefault("completed_at", datetime.now(timezone.utc).isoformat())
            write_json(report_path, result)
        except Exception as exc:
            result["status"] = "fail"
            result["error"] = result.get("error", f"Could not persist hardware evidence: {exc}")
            try:
                write_json(report_path, result)
            except Exception:
                pass
            if not active_exception:
                raise

