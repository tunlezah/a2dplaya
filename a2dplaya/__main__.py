"""
A2DPlaya entry point.

Usage:
    python -m a2dplaya [--config PATH] [--port PORT] [--debug]
"""

import argparse
import logging
import sys

from a2dplaya.config import AppConfig
from a2dplaya.app import AppController


def main():
    parser = argparse.ArgumentParser(
        description="A2DPlaya - Bluetooth A2DP Audio Bridge to Chromecast/AirPlay"
    )
    parser.add_argument(
        "--config", "-c",
        help="Path to configuration file",
        default=None
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        help="Web server port (default: 8080)",
        default=None
    )
    parser.add_argument(
        "--debug", "-d",
        action="store_true",
        help="Enable debug logging"
    )
    parser.add_argument(
        "--bt-adapter",
        help="Bluetooth adapter to use (e.g., hci0)",
        default=None
    )
    parser.add_argument(
        "--no-bluetooth",
        action="store_true",
        help="Disable Bluetooth (line-in only mode)"
    )
    parser.add_argument(
        "--line-in",
        help="ALSA device for line-in capture (e.g., hw:1,0)",
        default=None
    )

    args = parser.parse_args()

    # Load config
    config = AppConfig.load(args.config)

    # Apply CLI overrides
    if args.port:
        config.web.port = args.port
    if args.debug:
        config.log_level = "DEBUG"
        config.web.debug = True
    if args.bt_adapter:
        config.bluetooth.preferred_adapter = args.bt_adapter
    if args.line_in:
        config.audio.line_in_device = args.line_in
        config.audio.enable_line_in = True

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S"
    )

    # Start application
    app = AppController(config)

    if args.no_bluetooth:
        # Disable Bluetooth by preventing its initialization
        config.bluetooth.preferred_adapter = "__disabled__"

    print(f"A2DPlaya v1.0.0 - Bluetooth Audio Bridge")
    print(f"Web UI: http://0.0.0.0:{config.web.port}")
    print(f"Press Ctrl+C to stop")
    print()

    app.run_forever()


if __name__ == "__main__":
    main()
