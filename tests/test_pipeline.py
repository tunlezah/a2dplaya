"""Tests for the audio pipeline module."""

import struct
import threading
import time
import unittest

from a2dplaya.config import AppConfig
from a2dplaya.audio.pipeline import AudioPipeline


class TestAudioPipeline(unittest.TestCase):
    """Test the central audio processing pipeline."""

    def setUp(self):
        self.config = AppConfig()
        self.pipeline = AudioPipeline(self.config)

    def tearDown(self):
        self.pipeline.stop()

    def test_start_stop(self):
        self.pipeline.start()
        self.assertTrue(self.pipeline._running)
        self.pipeline.stop()
        self.assertFalse(self.pipeline._running)

    def test_volume_property(self):
        self.pipeline.volume = 0.5
        self.assertEqual(self.pipeline.volume, 0.5)

    def test_volume_clamped(self):
        self.pipeline.volume = 1.5
        self.assertEqual(self.pipeline.volume, 1.0)
        self.pipeline.volume = -0.5
        self.assertEqual(self.pipeline.volume, 0.0)

    def test_register_unregister_consumer(self):
        def dummy_consumer(data, sr, ch, sw):
            pass

        self.pipeline.register_consumer("test", dummy_consumer)
        self.assertIn("test", self.pipeline.get_consumers())

        self.pipeline.unregister_consumer("test")
        self.assertNotIn("test", self.pipeline.get_consumers())

    def test_consumer_receives_audio(self):
        received = []

        def consumer(data, sr, ch, sw):
            received.append(data)

        self.pipeline.register_consumer("test", consumer)
        self.pipeline.start()

        # Feed some audio data (matching pipeline format)
        test_data = struct.pack("<4h", 1000, -1000, 2000, -2000)
        self.pipeline.feed_audio(
            test_data,
            self.pipeline.sample_rate,
            self.pipeline.channels,
            self.pipeline.sample_width
        )

        # Wait for processing - poll with retries for CI reliability
        for _ in range(50):
            if received:
                break
            time.sleep(0.05)

        self.assertGreater(len(received), 0)

    def test_feed_audio_when_stopped(self):
        """Feeding audio when pipeline is stopped should be a no-op."""
        test_data = struct.pack("<4h", 1000, -1000, 2000, -2000)
        # Should not raise
        self.pipeline.feed_audio(test_data, 44100, 2, 2)

    def test_apply_volume_unity(self):
        """Volume at 1.0 should return data unchanged."""
        self.pipeline.volume = 1.0
        data = struct.pack("<4h", 1000, -1000, 2000, -2000)
        result = self.pipeline._apply_volume(data)
        self.assertEqual(result, data)

    def test_apply_volume_zero(self):
        """Volume at 0.0 should return silence."""
        self.pipeline.volume = 0.0
        data = struct.pack("<4h", 1000, -1000, 2000, -2000)
        result = self.pipeline._apply_volume(data)
        self.assertEqual(result, b"\x00" * len(data))

    def test_apply_volume_half(self):
        """Volume at 0.5 should halve sample values."""
        self.pipeline.volume = 0.5
        data = struct.pack("<4h", 10000, -10000, 20000, -20000)
        result = self.pipeline._apply_volume(data)
        samples = struct.unpack("<4h", result)
        self.assertAlmostEqual(samples[0], 5000, delta=1)
        self.assertAlmostEqual(samples[1], -5000, delta=1)

    def test_eq_data(self):
        """EQ data should return 10 bands."""
        data = self.pipeline.get_current_eq_data()
        self.assertEqual(len(data), 10)

    def test_stats(self):
        """Stats should be a dict with expected keys."""
        stats = self.pipeline.stats
        self.assertIn("chunks_processed", stats)
        self.assertIn("bytes_processed", stats)
        self.assertIn("underruns", stats)

    def test_current_source(self):
        self.assertIsNone(self.pipeline.current_source)
        self.pipeline.current_source = "bluetooth"
        self.assertEqual(self.pipeline.current_source, "bluetooth")


if __name__ == "__main__":
    unittest.main()
