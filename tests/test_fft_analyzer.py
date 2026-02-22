"""Tests for the FFT analyzer module."""

import math
import struct
import unittest

from a2dplaya.audio.fft_analyzer import FFTAnalyzer


class TestFFTAnalyzer(unittest.TestCase):
    """Test FFT-based EQ band analysis."""

    def setUp(self):
        self.analyzer = FFTAnalyzer(
            sample_rate=44100,
            channels=2,
            sample_width=2
        )

    def test_initial_levels_zero(self):
        """All band levels should start at zero."""
        levels = self.analyzer.get_band_levels()
        self.assertEqual(len(levels), 10)
        for level in levels:
            self.assertEqual(level, 0.0)

    def test_silence_stays_low(self):
        """Analyzing silence should produce near-zero levels."""
        silence = b"\x00" * (2048 * 4)  # 2048 stereo frames
        self.analyzer.analyze(silence)
        levels = self.analyzer.get_band_levels()
        for level in levels:
            self.assertLessEqual(level, 0.1)

    def test_band_count(self):
        """Should always return exactly 10 bands."""
        levels = self.analyzer.get_band_levels()
        self.assertEqual(len(levels), 10)

    def test_band_ranges_computed(self):
        """Band ranges should be computed for all 10 bands."""
        ranges = self.analyzer._band_ranges
        self.assertEqual(len(ranges), 10)
        for low, high in ranges:
            self.assertGreaterEqual(low, 0)
            self.assertGreater(high, low)

    def test_levels_clamped(self):
        """Band levels should be clamped to 0.0 - 1.0."""
        # Generate a loud signal
        samples = []
        for i in range(4096):
            val = int(32000 * math.sin(2 * math.pi * 1000 * i / 44100))
            samples.append(val)
            samples.append(val)

        data = struct.pack(f"<{len(samples)}h", *samples)
        self.analyzer.analyze(data)
        levels = self.analyzer.get_band_levels()

        for level in levels:
            self.assertGreaterEqual(level, 0.0)
            self.assertLessEqual(level, 1.0)

    def test_hann_window(self):
        """Hann window should taper edges to zero."""
        samples = [1.0] * 100
        windowed = self.analyzer._apply_hann_window(samples)
        self.assertAlmostEqual(windowed[0], 0.0, places=5)
        self.assertAlmostEqual(windowed[-1], 0.0, places=5)
        # Middle should be near 1.0
        self.assertGreater(windowed[50], 0.9)


class TestFFTAnalyzerMonoExtraction(unittest.TestCase):
    """Test mono sample extraction from PCM data."""

    def test_stereo_to_mono(self):
        analyzer = FFTAnalyzer(sample_rate=44100, channels=2, sample_width=2)
        # 2 stereo frames: L=1000, R=3000, L=2000, R=4000
        data = struct.pack("<4h", 1000, 3000, 2000, 4000)
        mono = analyzer._extract_mono_samples(data)
        self.assertEqual(len(mono), 2)

    def test_mono_input(self):
        analyzer = FFTAnalyzer(sample_rate=44100, channels=1, sample_width=2)
        data = struct.pack("<2h", 1000, 2000)
        mono = analyzer._extract_mono_samples(data)
        self.assertEqual(len(mono), 2)

    def test_empty_data(self):
        analyzer = FFTAnalyzer(sample_rate=44100, channels=2, sample_width=2)
        mono = analyzer._extract_mono_samples(b"")
        self.assertEqual(len(mono), 0)


if __name__ == "__main__":
    unittest.main()
