"""Tests for the audio resampler module."""

import struct
import unittest

from a2dplaya.audio.resampler import Resampler


class TestResampler(unittest.TestCase):
    """Test audio format conversion."""

    def setUp(self):
        self.resampler = Resampler(
            target_rate=44100,
            target_channels=2,
            target_width=2
        )

    def test_passthrough_same_format(self):
        """Audio matching target format should pass through unchanged."""
        data = struct.pack("<4h", 1000, -1000, 2000, -2000)
        result = self.resampler.resample(data, 44100, 2, 2)
        self.assertEqual(result, data)

    def test_empty_data(self):
        result = self.resampler.resample(b"", 44100, 2, 2)
        self.assertEqual(result, b"")

    def test_mono_to_stereo(self):
        """Mono input should be duplicated to stereo."""
        mono_data = struct.pack("<2h", 1000, 2000)  # 2 mono samples
        result = self.resampler.resample(mono_data, 44100, 1, 2)
        # Should produce 4 samples (2 stereo frames)
        samples = struct.unpack(f"<{len(result)//2}h", result)
        self.assertEqual(len(samples), 4)

    def test_stereo_to_mono_and_back(self):
        """Stereo -> mono resampler should average channels."""
        mono_resampler = Resampler(target_rate=44100, target_channels=1, target_width=2)
        stereo_data = struct.pack("<4h", 1000, 3000, 2000, 4000)  # 2 stereo frames
        result = mono_resampler.resample(stereo_data, 44100, 2, 2)
        samples = struct.unpack(f"<{len(result)//2}h", result)
        self.assertEqual(len(samples), 2)  # 2 mono samples

    def test_width_conversion_16_to_16(self):
        """Same width should not change data."""
        data = struct.pack("<2h", 16000, -16000)
        result = self.resampler.resample(data, 44100, 2, 2)
        self.assertEqual(result, data)

    def test_sample_rate_conversion(self):
        """Upsampling should produce more samples."""
        # 2 stereo frames at 22050 Hz
        data = struct.pack("<4h", 1000, 1000, 2000, 2000)
        result = self.resampler.resample(data, 22050, 2, 2)
        # Should produce roughly 2x the frames (44100/22050 = 2.0)
        out_samples = len(result) // 2
        # At least 3 stereo frames (6 samples)
        self.assertGreaterEqual(out_samples, 6)

    def test_decode_encode_roundtrip_16bit(self):
        """16-bit decode/encode should be nearly lossless."""
        original = struct.pack("<4h", 100, -200, 300, -400)
        samples = self.resampler._decode_samples(original, 2)
        result = self.resampler._encode_samples(samples, 2)
        # Allow +-1 due to float precision
        orig_vals = struct.unpack("<4h", original)
        result_vals = struct.unpack("<4h", result)
        for o, r in zip(orig_vals, result_vals):
            self.assertAlmostEqual(o, r, delta=1)


class TestResamplerEdgeCases(unittest.TestCase):
    """Test edge cases in resampling."""

    def test_single_sample(self):
        resampler = Resampler(target_rate=44100, target_channels=1, target_width=2)
        data = struct.pack("<h", 1000)
        result = resampler.resample(data, 44100, 1, 2)
        self.assertEqual(len(result), 2)

    def test_silence(self):
        resampler = Resampler(target_rate=44100, target_channels=2, target_width=2)
        data = b"\x00" * 100
        result = resampler.resample(data, 44100, 2, 2)
        # All samples should be zero or near-zero
        samples = struct.unpack(f"<{len(result)//2}h", result)
        for s in samples:
            self.assertEqual(s, 0)


if __name__ == "__main__":
    unittest.main()
