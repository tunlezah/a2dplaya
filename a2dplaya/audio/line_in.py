"""
Line-in audio capture for A2DPlaya.

Captures audio from an ALSA line-in device (e.g., record player,
tape player connected via 3.5mm/RCA input) and feeds it to the
AudioPipeline. Uses pyalsaaudio when available, with a subprocess
fallback using arecord.
"""

import logging
import subprocess
import threading
import time
from typing import Callable, Optional

from a2dplaya.config import AppConfig, PIPELINE_SAMPLE_RATE, PIPELINE_CHANNELS, PIPELINE_SAMPLE_WIDTH

logger = logging.getLogger(__name__)


class LineInCapture:
    """
    Captures audio from an ALSA line-in device.

    Supports both pyalsaaudio (preferred) and arecord (fallback)
    for audio capture. Captured audio is passed to the AudioPipeline
    via a callback function.
    """

    def __init__(self, config: AppConfig, audio_callback: Optional[Callable] = None):
        self._config = config.audio
        self._audio_callback = audio_callback
        self._running = False
        self._capture_thread: Optional[threading.Thread] = None
        self._device_name = config.audio.line_in_device or "default"

        # Capture format
        self._sample_rate = PIPELINE_SAMPLE_RATE
        self._channels = PIPELINE_CHANNELS
        self._sample_width = PIPELINE_SAMPLE_WIDTH
        self._chunk_size = config.audio.chunk_size

        # External process (arecord fallback)
        self._proc: Optional[subprocess.Popen] = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def device_name(self) -> str:
        return self._device_name

    @device_name.setter
    def device_name(self, name: str):
        was_running = self._running
        if was_running:
            self.stop()
        self._device_name = name
        if was_running:
            self.start()

    def start(self):
        """Start capturing audio from line-in."""
        if self._running:
            return

        if not self._config.enable_line_in:
            logger.info("Line-in capture disabled in config")
            return

        self._running = True
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="LineIn-Capture",
            daemon=True
        )
        self._capture_thread.start()
        logger.info("Line-in capture started on device: %s", self._device_name)

    def stop(self):
        """Stop capturing audio."""
        self._running = False

        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                pass
            self._proc = None

        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=5.0)

        logger.info("Line-in capture stopped")

    def list_devices(self) -> list:
        """List available ALSA capture devices."""
        devices = []

        # Try pyalsaaudio
        try:
            import alsaaudio
            for card_idx in alsaaudio.card_indexes():
                name, longname = alsaaudio.card_name(card_idx)
                devices.append({
                    "id": f"hw:{card_idx},0",
                    "name": name,
                    "description": longname,
                })
            return devices
        except ImportError:
            pass

        # Fallback: parse arecord -l
        try:
            result = subprocess.run(
                ["arecord", "-l"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if line.startswith("card "):
                    parts = line.split(":")
                    if len(parts) >= 2:
                        card_num = line.split()[1].rstrip(":")
                        name = parts[1].strip().split("[")[0].strip()
                        devices.append({
                            "id": f"hw:{card_num},0",
                            "name": name,
                            "description": line.strip(),
                        })
        except Exception as e:
            logger.warning("Failed to list audio devices: %s", e)

        return devices

    def _capture_loop(self):
        """Main capture loop - tries pyalsaaudio then arecord."""
        try:
            self._capture_alsa()
        except ImportError:
            logger.info("pyalsaaudio not available, falling back to arecord")
            self._capture_arecord()
        except Exception as e:
            logger.error("ALSA capture failed: %s, trying arecord", e)
            self._capture_arecord()

    def _capture_alsa(self):
        """Capture audio using pyalsaaudio."""
        import alsaaudio

        inp = alsaaudio.PCM(
            type=alsaaudio.PCM_CAPTURE,
            mode=alsaaudio.PCM_NORMAL,
            device=self._device_name
        )
        inp.setchannels(self._channels)
        inp.setrate(self._sample_rate)
        inp.setformat(alsaaudio.PCM_FORMAT_S16_LE)
        inp.setperiodsize(self._chunk_size)

        logger.info("ALSA capture started: %dHz, %dch, 16-bit", self._sample_rate, self._channels)

        while self._running:
            try:
                length, data = inp.read()
                if length > 0 and data and self._audio_callback:
                    self._audio_callback(
                        data,
                        self._sample_rate,
                        self._channels,
                        self._sample_width
                    )
            except Exception as e:
                logger.error("ALSA read error: %s", e)
                time.sleep(0.1)

        inp.close()

    def _capture_arecord(self):
        """Capture audio using arecord subprocess."""
        cmd = [
            "arecord",
            "-D", self._device_name,
            "-f", "S16_LE",
            "-r", str(self._sample_rate),
            "-c", str(self._channels),
            "-t", "raw",
            "--buffer-size", str(self._chunk_size * 4),
        ]

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            logger.info("arecord capture started: %s", " ".join(cmd))

            chunk_bytes = self._chunk_size * self._channels * self._sample_width

            while self._running and self._proc.poll() is None:
                data = self._proc.stdout.read(chunk_bytes)
                if data and self._audio_callback:
                    self._audio_callback(
                        data,
                        self._sample_rate,
                        self._channels,
                        self._sample_width
                    )

        except FileNotFoundError:
            logger.error("arecord not found. Install alsa-utils.")
        except Exception as e:
            logger.error("arecord capture error: %s", e)
        finally:
            if self._proc:
                self._proc.terminate()
