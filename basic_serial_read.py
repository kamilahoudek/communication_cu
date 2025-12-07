"""Minimal script to open the control-unit serial port and read raw bytes."""

import argparse
import sys
import time
from typing import Iterable

import serial

from pdu_reader import DEFAULT_BAUDRATE, DEFAULT_PORT, DEFAULT_TIMEOUT, list_serial_ports


def render_bytes(data: Iterable[int]) -> str:
    """Render a short chunk of bytes as a space-separated hex string."""

    return " ".join(f"{b:02X}" for b in data)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Open the control-unit serial port and dump raw bytes as they arrive. "
            "No parsing or framing is performed; this helper only establishes communication "
            "and shows what the device transmits."
        )
    )

    port_help = "Serial port connected to the unit (e.g. COM3 or /dev/ttyUSB0)"
    if DEFAULT_PORT:
        port_help += f" [default: {DEFAULT_PORT}]"

    parser.add_argument("port", nargs="?", default=DEFAULT_PORT, help=port_help)
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE, help="Serial baud rate")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Read timeout in seconds")
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help=(
            "Optional duration in seconds to listen before exiting. "
            "If omitted or zero, the script runs until interrupted."
        ),
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=128,
        help="Maximum number of bytes to read per serial read call",
    )

    args = parser.parse_args(argv)
    if args.port is None:
        ports = list_serial_ports()
        if ports:
            detected = "\n".join(f"  - {name}" for name in ports)
            hint = (
                "No port provided. Detected serial ports:\n"
                f"{detected}\n\n"
                "Re-run the command with one of the ports above, for example:\n"
                f"    python basic_serial_read.py {ports[0]}"
            )
        else:
            hint = (
                "No port provided and no serial ports were detected automatically."
                " Ensure the device is connected or set the BED_CONTROL_PORT environment variable."
            )
        parser.error(hint)

    return args


def read_forever(args: argparse.Namespace) -> None:
    """Open the serial port and stream raw bytes until interrupted or duration elapses."""

    with serial.Serial(
        port=args.port,
        baudrate=args.baudrate,
        timeout=args.timeout,
    ) as ser:
        print(f"Opened {args.port} at {args.baudrate} baud; waiting for data...", file=sys.stderr)
        if args.duration > 0:
            stop_at = time.monotonic() + args.duration
        else:
            stop_at = None

        try:
            while True:
                if stop_at is not None and time.monotonic() >= stop_at:
                    break

                chunk = ser.read(args.chunk_size)
                if chunk:
                    print(render_bytes(chunk))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    read_forever(parse_args())
