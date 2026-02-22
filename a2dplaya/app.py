"""
Main application controller for A2DPlaya.

Orchestrates all subsystems: Bluetooth, AudioPipeline, Chromecast,
AirPlay, Line-In, and the Web Server. Handles lifecycle management,
signal handling, and inter-component wiring.
"""

import logging
import signal
import sys
import threading
import time
from typing import Optional

from a2dplaya.config import AppConfig
from a2dplaya.audio.pipeline import AudioPipeline
from a2dplaya.audio.line_in import LineInCapture
from a2dplaya.bluetooth.manager import BluetoothManager
from a2dplaya.cast.manager import CastManager
from a2dplaya.airplay.manager import AirPlayManager
from a2dplaya.web.server import WebServer

logger = logging.getLogger(__name__)


class AppController:
    """
    Main application controller that wires all components together.

    Manages the lifecycle of all subsystems and routes audio data
    from input sources through the pipeline to output targets.
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig.load()
        self._running = False

        # Core components
        self.pipeline: Optional[AudioPipeline] = None
        self.bluetooth: Optional[BluetoothManager] = None
        self.cast: Optional[CastManager] = None
        self.airplay: Optional[AirPlayManager] = None
        self.line_in: Optional[LineInCapture] = None
        self.web: Optional[WebServer] = None

        # Shutdown event
        self._shutdown_event = threading.Event()

    def start(self):
        """Initialize and start all subsystems."""
        if self._running:
            return

        logger.info("A2DPlaya starting up...")
        self._running = True

        # Configure logging
        logging.basicConfig(
            level=getattr(logging, self.config.log_level, logging.INFO),
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S"
        )

        # 1. Audio Pipeline (central hub)
        self.pipeline = AudioPipeline(self.config)
        self.pipeline.start()
        logger.info("Audio pipeline initialized")

        # 2. Bluetooth Manager
        try:
            self.bluetooth = BluetoothManager(
                self.config,
                audio_callback=self._bt_audio_callback
            )
            self.bluetooth.start()
            logger.info("Bluetooth manager initialized")
        except Exception as e:
            logger.warning("Bluetooth initialization failed: %s", e)
            self.bluetooth = None

        # 3. Chromecast Manager
        try:
            self.cast = CastManager(self.config)
            self.cast.start()
            # Register as audio consumer
            self.pipeline.register_consumer("chromecast", self.cast.audio_consumer)
            logger.info("Chromecast manager initialized")
        except Exception as e:
            logger.warning("Chromecast initialization failed: %s", e)
            self.cast = None

        # 4. AirPlay Manager
        try:
            self.airplay = AirPlayManager(self.config)
            self.airplay.start()
            self.pipeline.register_consumer("airplay", self.airplay.audio_consumer)
            logger.info("AirPlay manager initialized")
        except Exception as e:
            logger.warning("AirPlay initialization failed: %s", e)
            self.airplay = None

        # 5. Line-In Capture
        try:
            self.line_in = LineInCapture(
                self.config,
                audio_callback=self._linein_audio_callback
            )
            # Don't auto-start unless configured
            if self.config.audio.enable_line_in:
                self.line_in.start()
            logger.info("Line-in capture initialized")
        except Exception as e:
            logger.warning("Line-in initialization failed: %s", e)
            self.line_in = None

        # 6. Web Server (last, after all other components)
        try:
            self.web = WebServer(self.config, app_controller=self)
            self.pipeline.register_consumer("webui", self.web.audio_consumer)
            self.web.start()
            logger.info("Web server initialized on port %d", self.config.web.port)
        except Exception as e:
            logger.warning("Web server initialization failed: %s", e)
            self.web = None

        # Register signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        logger.info(
            "A2DPlaya ready! Web UI: http://0.0.0.0:%d",
            self.config.web.port
        )

    def stop(self):
        """Gracefully shut down all subsystems."""
        if not self._running:
            return

        logger.info("A2DPlaya shutting down...")
        self._running = False

        # Stop in reverse order
        if self.web:
            self.web.stop()

        if self.line_in:
            self.line_in.stop()

        if self.airplay:
            self.airplay.stop()

        if self.cast:
            self.cast.stop()

        if self.bluetooth:
            self.bluetooth.stop()

        if self.pipeline:
            self.pipeline.stop()

        self._shutdown_event.set()
        logger.info("A2DPlaya stopped")

    def run_forever(self):
        """Start the application and block until shutdown."""
        self.start()
        try:
            self._shutdown_event.wait()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def _bt_audio_callback(self, pcm_data: bytes, sample_rate: int, channels: int, sample_width: int):
        """Callback for Bluetooth audio data -> pipeline."""
        if self.pipeline:
            self.pipeline.current_source = "bluetooth"
            self.pipeline.feed_audio(pcm_data, sample_rate, channels, sample_width)

    def _linein_audio_callback(self, pcm_data: bytes, sample_rate: int, channels: int, sample_width: int):
        """Callback for line-in audio data -> pipeline."""
        if self.pipeline:
            self.pipeline.current_source = "line-in"
            self.pipeline.feed_audio(pcm_data, sample_rate, channels, sample_width)

    def _signal_handler(self, signum, frame):
        """Handle SIGINT/SIGTERM for graceful shutdown."""
        logger.info("Received signal %d, shutting down...", signum)
        self.stop()
