"""
FFT-based audio analyzer for A2DPlaya EQ visualization.

Performs real-time FFT analysis on audio data and maps the frequency
spectrum to 10 EQ bands for visualization in the web UI.
"""

import math
import struct
import threading
import time
from typing import List, Optional

from a2dplaya.config import EQ_BANDS, EQ_FFT_SIZE, EQ_UPDATE_RATE_HZ, EQ_BAND_FREQUENCIES


class FFTAnalyzer:
    """
    Real-time FFT analyzer producing 10-band EQ levels.

    Uses a simple DFT implementation to avoid external numpy dependency.
    Maintains smoothed band levels for visually appealing animation.
    """

    def __init__(self, sample_rate: int, channels: int, sample_width: int):
        self._sample_rate = sample_rate
        self._channels = channels
        self._sample_width = sample_width
        self._fft_size = EQ_FFT_SIZE
        self._lock = threading.Lock()

        # Current smoothed band levels (0.0 - 1.0)
        self._band_levels: List[float] = [0.0] * EQ_BANDS

        # Smoothing parameters
        self._attack = 0.3   # How fast levels rise
        self._decay = 0.05   # How fast levels fall

        # Pre-compute band frequency ranges
        self._band_ranges = self._compute_band_ranges()

        # Minimum update interval
        self._min_interval = 1.0 / EQ_UPDATE_RATE_HZ
        self._last_update = 0.0

    def _compute_band_ranges(self) -> List[tuple]:
        """
        Compute frequency bin ranges for each EQ band.

        Returns list of (low_bin, high_bin) tuples for each band.
        """
        bin_hz = self._sample_rate / self._fft_size
        ranges = []

        for i, center in enumerate(EQ_BAND_FREQUENCIES):
            if i == 0:
                low = 0
            else:
                low = int(math.sqrt(EQ_BAND_FREQUENCIES[i - 1] * center))

            if i == len(EQ_BAND_FREQUENCIES) - 1:
                high = self._sample_rate // 2
            else:
                high = int(math.sqrt(center * EQ_BAND_FREQUENCIES[i + 1]))

            low_bin = max(1, int(low / bin_hz))
            high_bin = min(self._fft_size // 2, int(high / bin_hz) + 1)
            ranges.append((low_bin, high_bin))

        return ranges

    def analyze(self, pcm_data: bytes):
        """
        Analyze a chunk of PCM audio data and update band levels.

        Args:
            pcm_data: Raw 16-bit signed PCM audio data
        """
        now = time.time()
        if now - self._last_update < self._min_interval:
            return

        self._last_update = now

        # Extract mono samples from PCM data
        samples = self._extract_mono_samples(pcm_data)
        if len(samples) < self._fft_size:
            # Pad with zeros if not enough samples
            samples.extend([0.0] * (self._fft_size - len(samples)))

        # Use only the last fft_size samples
        samples = samples[-self._fft_size:]

        # Apply Hann window
        windowed = self._apply_hann_window(samples)

        # Compute magnitude spectrum using DFT for the needed bins
        magnitudes = self._compute_magnitudes(windowed)

        # Map to EQ bands
        new_levels = self._map_to_bands(magnitudes)

        # Apply smoothing
        with self._lock:
            for i in range(EQ_BANDS):
                if new_levels[i] > self._band_levels[i]:
                    self._band_levels[i] += (
                        (new_levels[i] - self._band_levels[i]) * self._attack
                    )
                else:
                    self._band_levels[i] += (
                        (new_levels[i] - self._band_levels[i]) * self._decay
                    )
                self._band_levels[i] = max(0.0, min(1.0, self._band_levels[i]))

    def get_band_levels(self) -> List[float]:
        """
        Get current EQ band levels for visualization.

        Returns:
            List of 10 float values, each 0.0 - 1.0
        """
        with self._lock:
            return list(self._band_levels)

    def _extract_mono_samples(self, pcm_data: bytes) -> list:
        """Extract mono float samples from PCM data."""
        if self._sample_width != 2:
            return []

        n_samples = len(pcm_data) // 2
        if n_samples == 0:
            return []

        fmt = f"<{n_samples}h"
        try:
            raw = struct.unpack(fmt, pcm_data[:n_samples * 2])
        except struct.error:
            return []

        # Convert to mono if stereo
        if self._channels == 2:
            mono = []
            for i in range(0, len(raw) - 1, 2):
                mono.append((raw[i] + raw[i + 1]) / 65536.0)
            return mono
        else:
            return [s / 32768.0 for s in raw]

    def _apply_hann_window(self, samples: list) -> list:
        """Apply Hann window function to reduce spectral leakage."""
        n = len(samples)
        return [
            samples[i] * 0.5 * (1.0 - math.cos(2.0 * math.pi * i / (n - 1)))
            for i in range(n)
        ]

    def _compute_magnitudes(self, samples: list) -> list:
        """
        Compute magnitude spectrum using DFT for needed frequency bins.

        Only computes bins that are actually needed for the EQ bands,
        which is much faster than a full FFT for visualization purposes.
        """
        n = len(samples)
        max_bin = max(high for _, high in self._band_ranges) + 1
        max_bin = min(max_bin, n // 2)

        magnitudes = [0.0] * (max_bin + 1)

        for k in range(1, max_bin + 1):
            real = 0.0
            imag = 0.0
            freq = 2.0 * math.pi * k / n
            for i in range(n):
                angle = freq * i
                real += samples[i] * math.cos(angle)
                imag -= samples[i] * math.sin(angle)
            magnitudes[k] = math.sqrt(real * real + imag * imag) / n

        return magnitudes

    def _map_to_bands(self, magnitudes: list) -> List[float]:
        """Map frequency magnitudes to EQ band levels."""
        levels = []
        max_mag = len(magnitudes)

        for low_bin, high_bin in self._band_ranges:
            if low_bin >= max_mag:
                levels.append(0.0)
                continue

            high_bin = min(high_bin, max_mag)
            if low_bin >= high_bin:
                levels.append(0.0)
                continue

            # Average magnitude in this band
            band_sum = sum(magnitudes[low_bin:high_bin])
            band_avg = band_sum / (high_bin - low_bin)

            # Scale to 0.0-1.0 range (empirical scaling factor)
            level = min(1.0, band_avg * 15.0)
            levels.append(level)

        return levels
