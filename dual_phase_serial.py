"""Two-phase serial helper for bed control units.

This script establishes a COM/serial connection, waits for the periodically
streamed "Okamzite hodnoty" frames, and then optionally issues additional
queries (e.g., statistics or settings) while staying on the same link.
Captured frames are printed as comma-separated hexadecimal bytes
(e.g., "7E, 14, 17, 00, ...").

Key behavior:
- Uses the baud rate and timing defaults from the existing HDLC reader
  (38400 baud, modest timeouts suitable for 1-second periodic frames).
- Phase 1 listens passively for a configurable duration to collect the
  automatically emitted values.
- Phase 2 sends one or more user-supplied request frames and collects the
  responses.

Example:
    python dual_phase_serial.py COM5 \
        --initial-seconds 5 \
        --request "7E 14 17 00" \
        --request "7E 15 01 00"
"""

from __future__ import annotations

import argparse
import time
from typing import List, Sequence

import serial
import sys

from pdu_reader import (
    DEFAULT_BAUDRATE,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    INTERBYTE_TIMEOUT,
    ReadConfig,
    _read_one_frame_from_serial,
)


def _format_bytes(data: bytes) -> str:
    """Render bytes as "AA, BB, CC" (uppercase hex with leading zeroes)."""

    return ", ".join(f"{b:02X}" for b in data)


def _parse_hex_sequence(text: str) -> bytes:
    """Convert a string like "7E 14,17 00" or "0x7E,0x14,0x17,0x00" to bytes.

    Whitespace, commas, and semicolons are treated as separators. Prefixes such
    as ``0x`` or ``0X`` are allowed. Raises ``ValueError`` if any token fails to
    parse as a base-0 integer or is out of byte range.
    """

    if not text:
        raise ValueError("Empty request cannot be converted to bytes")

    tokens: List[str] = []
    for part in text.replace(",", " ").replace(";", " ").split():
        if part:
            tokens.append(part)
    if not tokens:
        raise ValueError("No hex tokens found in request string")

    values: List[int] = []
    for token in tokens:
        try:
            value = int(token, 0)
        except ValueError as exc:  # pragma: no cover - explicit message preferred
            raise ValueError(f"Could not parse '{token}' as a hex byte") from exc
        if not 0 <= value <= 0xFF:
            raise ValueError(f"Value {value} from '{token}' is outside byte range")
        values.append(value)

    return bytes(values)


def _read_frames(
    ser: serial.Serial,
    cfg: ReadConfig,
    count: int,
    *,
    label: str,
    after_write_delay: float | None = None,
) -> None:
    """Read ``count`` frames from an open serial link and print them."""

    if after_write_delay:
        time.sleep(after_write_delay)

    for idx in range(1, count + 1):
        frame_with_flags, _ = _read_one_frame_from_serial(ser, cfg)
        print(f"{label} {idx}: {_format_bytes(frame_with_flags)}")


def _listen_for_initial_values(
    ser: serial.Serial,
    cfg: ReadConfig,
    initial_seconds: float,
) -> None:
    """Collect automatically streamed frames for a time budget."""

    deadline = time.time() + initial_seconds
    observed = 0
    while time.time() < deadline:
        try:
            frame_with_flags, _ = _read_one_frame_from_serial(ser, cfg)
        except TimeoutError:
            # If nothing arrives within the timeout, keep looping until deadline
            if time.time() >= deadline:
                break
            continue
        observed += 1
        print(f"Okamzite frame {observed}: {_format_bytes(frame_with_flags)}")

    if observed == 0:
        print("No Okamzite hodnoty frames captured within the initial window.")


def _send_requests_and_collect(
    ser: serial.Serial,
    cfg: ReadConfig,
    requests: Sequence[bytes],
    responses_per_request: int,
    post_write_delay: float,
) -> None:
    """Transmit each request and gather the specified number of replies."""

    for req_idx, request in enumerate(requests, start=1):
        ser.write(request)
        ser.flush()
        print(f"Sent request {req_idx}: {_format_bytes(request)}")
        _read_frames(
            ser,
            cfg,
            responses_per_request,
            label=f"Response to request {req_idx}",
            after_write_delay=post_write_delay,
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    port_help = "COM/tty port connected to the control unit"
    if DEFAULT_PORT:
        port_help += f" [default: {DEFAULT_PORT}]"

    parser.add_argument("port", nargs="?", default=DEFAULT_PORT, help=port_help)
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE, help="Serial baud rate")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="Overall timeout while waiting for bytes",
    )
    parser.add_argument(
        "--interbyte-timeout",
        type=float,
        default=INTERBYTE_TIMEOUT,
        help="Maximum tolerated gap between bytes inside a frame",
    )
    parser.add_argument(
        "--initial-seconds",
        type=float,
        default=5.0,
        help="How long to passively listen for Okamzite hodnoty frames",
    )
    parser.add_argument(
        "--request",
        action="append",
        dest="requests",
        default=[],
        help=(
            "Hex bytes to transmit for a query (e.g., '7E 14 17 00'). "
            "Repeat the flag to send multiple requests."
        ),
    )
    parser.add_argument(
        "--responses-per-request",
        type=int,
        default=1,
        help="How many response frames to collect after each request",
    )
    parser.add_argument(
        "--post-write-delay",
        type=float,
        default=0.2,
        help="Seconds to wait after writing before reading responses",
    )

    args = parser.parse_args(argv)
    if args.port is None:
        parser.error("Serial port is required (e.g., COM3 or /dev/ttyUSB0)")

    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    requests: List[bytes] = []
    for raw in args.requests:
        try:
            requests.append(_parse_hex_sequence(raw))
        except ValueError as exc:
            print(f"Could not parse request '{raw}': {exc}", file=sys.stderr)
            return 2

    cfg = ReadConfig(
        baudrate=args.baudrate,
        timeout=args.timeout,
        interbyte_timeout=args.interbyte_timeout,
        crc_mode="none",
        remove_crc=True,
    )

    print(
        "Opening {port} at {baud} baud (timeout={timeout}s, interbyte={inter}s)".format(
            port=args.port,
            baud=cfg.baudrate,
            timeout=cfg.timeout,
            inter=cfg.interbyte_timeout,
        )
    )

    with serial.Serial(
        port=args.port,
        baudrate=cfg.baudrate,
        timeout=cfg.timeout,
        inter_byte_timeout=cfg.interbyte_timeout,
    ) as ser:
        _listen_for_initial_values(ser, cfg, args.initial_seconds)

        if requests:
            _send_requests_and_collect(
                ser,
                cfg,
                requests,
                responses_per_request=args.responses_per_request,
                post_write_delay=args.post_write_delay,
            )
        else:
            print("No additional requests provided; exiting after initial capture.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
