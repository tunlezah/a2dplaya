"""
Core audio pipeline for A2DPlaya.

The AudioPipeline is the central audio routing and processing engine.
It receives raw PCM audio from input sources (Bluetooth A2DP, line-in),
performs resampling if needed, computes FFT data for the EQ visualizer,
applies volume control, and distributes audio to registered output
consumers (Chromecast stream, AirPlay stream, WebSocket browser stream).

Audio flow:
    Input Source -> Resample -> Volume -> FFT Analysis -> Output Consumers

All audio is normalized to the pipeline's internal format:
    - 44100 Hz sample rate
    - 16-bit signed PCM (little-endian)
    - Stereo (2 channels)
"""

import logging
import struct
import threading
import time
from collections import deque
from typing import Callable, Dict, List, Optional, Tuple

from a2dplaya.audio.resampler import Resampler
from a2dplaya.audio.fft_analyzer import FFTAnalyzer
from a2dplaya.config import AppConfig, PIPELINE_SAMPLE_RATE, PIPELINE_CHANNELS, PIPELINE_SAMPLE_WIDTH

logger = logging.getLogger(__name__)

# Type alias for audio consumer callbacks
# Consumers receive (pcm_data: bytes, sample_rate: int, channels: int, sample_width: int)
AudioConsumer = Callable[[bytes, int, int, int], None]


class AudioPipeline:
    """
    Central audio routing and processing engine.

    Receives PCM audio from input sources, processes it (resample, volume,
    FFT analysis), and distributes to registered consumers. Thread-safe
    for concurrent input/output operations.
    """

    def __init__(self, config: AppConfig):
        self._config = config
        self._lock = threading.RLock()
        self._running = False

        # Internal audio format
        self._sample_rate = config.audio.sample_rate or PIPELINE_SAMPLE_RATE
        self._channels = config.audio.channels or PIPELINE_CHANNELS
        self._sample_width = config.audio.sample_width or PIPELINE_SAMPLE_WIDTH

        # Volume control (0.0 - 1.0)
        self._volume = config.audio.volume

        # Resampler for input format conversion
        self._resampler = Resampler(
            target_rate=self._sample_rate,
            target_channels=self._channels,
            target_width=self._sample_width
        )

        # FFT analyzer for EQ visualization
        self._fft_analyzer = FFTAnalyzer(
            sample_rate=self._sample_rate,
            channels=self._channels,
            sample_width=self._sample_width
        )

        # Registered output consumers: name -> callback
        self._consumers: Dict[str, AudioConsumer] = {}

        # Audio buffer for smoothing out input irregularities
        self._buffer = deque(maxlen=100)  # Max ~100 chunks buffered

        # Current input source name (for UI display)
        self._current_source: Optional[str] = None

        # Statistics
        self._stats = {
            "chunks_processed": 0,
            "bytes_processed": 0,
            "underruns": 0,
            "overruns": 0,
            "last_chunk_time": 0.0,
        }

        # Processing thread
        self._process_thread: Optional[threading.Thread] = None

    @property
    def volume(self) -> float:
        """Current volume level (0.0 - 1.0)."""
        return self._volume

    @volume.setter
    def volume(self, value: float):
        """Set volume level, clamped to 0.0 - 1.0."""
        self._volume = max(0.0, min(1.0, float(value)))
        logger.debug("Volume set to %.2f", self._volume)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def sample_width(self) -> int:
        return self._sample_width

    @property
    def current_source(self) -> Optional[str]:
        return self._current_source

    @current_source.setter
    def current_source(self, name: Optional[str]):
        self._current_source = name

    @property
    def fft_analyzer(self) -> FFTAnalyzer:
        return self._fft_analyzer

    @property
    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    def start(self):
        """Start the audio processing pipeline."""
        if self._running:
            return
        self._running = True
        self._process_thread = threading.Thread(
            target=self._process_loop,
            name="AudioPipeline",
            daemon=True
        )
        self._process_thread.start()
        logger.info(
            "Audio pipeline started: %dHz, %dch, %d-bit",
            self._sample_rate, self._channels, self._sample_width * 8
        )

    def stop(self):
        """Stop the audio processing pipeline and release resources."""
        self._running = False
        if self._process_thread and self._process_thread.is_alive():
            self._process_thread.join(timeout=5.0)
        self._buffer.clear()
        logger.info("Audio pipeline stopped")

    def register_consumer(self, name: str, callback: AudioConsumer):
        """
        Register an output consumer to receive processed audio.

        Args:
            name: Unique identifier for this consumer (e.g., "chromecast", "webui")
            callback: Function called with (pcm_data, sample_rate, channels, sample_width)
        """
        with self._lock:
            self._consumers[name] = callback
            logger.info("Registered audio consumer: %s", name)

    def unregister_consumer(self, name: str):
        """Remove a registered audio consumer."""
        with self._lock:
            if name in self._consumers:
                del self._consumers[name]
                logger.info("Unregistered audio consumer: %s", name)

    def get_consumers(self) -> List[str]:
        """Return list of registered consumer names."""
        with self._lock:
            return list(self._consumers.keys())

    def feed_audio(
        self,
        pcm_data: bytes,
        sample_rate: int,
        channels: int,
        sample_width: int
    ):
        """
        Feed raw PCM audio data into the pipeline from an input source.

        The audio will be resampled to the pipeline's internal format if
        the input format doesn't match. This method is thread-safe and
        can be called from any source thread.

        Args:
            pcm_data: Raw PCM audio bytes (signed, little-endian)
            sample_rate: Input sample rate in Hz
            channels: Number of input channels
            sample_width: Bytes per sample (1=8-bit, 2=16-bit, 3=24-bit, 4=32-bit)
        """
        if not self._running or not pcm_data:
            return

        # Resample if input format doesn't match pipeline format
        if (sample_rate != self._sample_rate or
                channels != self._channels or
                sample_width != self._sample_width):
            pcm_data = self._resampler.resample(
                pcm_data, sample_rate, channels, sample_width
            )

        # Add to processing buffer
        try:
            self._buffer.append(pcm_data)
        except Exception:
            with self._lock:
                self._stats["overruns"] += 1

    def _process_loop(self):
        """
        Main processing loop running in a dedicated thread.

        Reads audio chunks from the buffer, applies volume control,
        runs FFT analysis, and distributes to all registered consumers.
        """
        chunk_duration = self._config.audio.chunk_size / self._sample_rate
        min_sleep = 0.001  # 1ms minimum sleep to prevent busy-waiting

        while self._running:
            if not self._buffer:
                time.sleep(min_sleep)
                continue

            try:
                # Get next chunk from buffer
                chunk = self._buffer.popleft()
            except IndexError:
                time.sleep(min_sleep)
                continue

            try:
                # Apply volume control
                chunk = self._apply_volume(chunk)

                # Run FFT analysis for EQ visualization
                self._fft_analyzer.analyze(chunk)

                # Distribute to all registered consumers
                with self._lock:
                    consumers = dict(self._consumers)

                for name, callback in consumers.items():
                    try:
                        callback(
                            chunk,
                            self._sample_rate,
                            self._channels,
                            self._sample_width
                        )
                    except Exception as e:
                        logger.error(
                            "Error in audio consumer '%s': %s", name, e
                        )

                # Update statistics
                with self._lock:
                    self._stats["chunks_processed"] += 1
                    self._stats["bytes_processed"] += len(chunk)
                    self._stats["last_chunk_time"] = time.time()

            except Exception as e:
                logger.error("Audio processing error: %s", e)

    def _apply_volume(self, pcm_data: bytes) -> bytes:
        """
        Apply volume scaling to PCM audio data.

        Operates on 16-bit signed little-endian PCM samples.
        Volume of 1.0 returns data unchanged for efficiency.

        Args:
            pcm_data: Raw PCM bytes

        Returns:
            Volume-adjusted PCM bytes
        """
        if self._volume >= 0.999:
            return pcm_data

        if self._volume <= 0.001:
            return b'\x00' * len(pcm_data)

        # Unpack 16-bit signed samples, apply volume, repack
        num_samples = len(pcm_data) // 2
        fmt = f"<{num_samples}h"
        try:
            samples = struct.unpack(fmt, pcm_data)
            scaled = struct.pack(
                fmt,
                *(max(-32768, min(32767, int(s * self._volume))) for s in samples)
            )
            return scaled
        except struct.error:
            return pcm_data

    def get_current_eq_data(self) -> List[float]:
        """
        Get current EQ band levels for visualization.

        Returns:
            List of 10 band levels, each 0.0 - 1.0
        """
        return self._fft_analyzer.get_band_levels()
