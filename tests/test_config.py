"""Tests for A2DPlaya configuration module."""

import json
import os
import tempfile
import unittest

from a2dplaya.config import (
    AppConfig, BluetoothConfig, AudioConfig, CastConfig,
    AirPlayConfig, WebConfig,
    PIPELINE_SAMPLE_RATE, PIPELINE_CHANNELS, PIPELINE_SAMPLE_WIDTH,
    EQ_BANDS, EQ_BAND_FREQUENCIES
)


class TestAppConfigDefaults(unittest.TestCase):
    """Test default configuration values."""

    def test_default_audio_config(self):
        config = AppConfig()
        self.assertEqual(config.audio.sample_rate, PIPELINE_SAMPLE_RATE)
        self.assertEqual(config.audio.channels, PIPELINE_CHANNELS)
        self.assertEqual(config.audio.sample_width, PIPELINE_SAMPLE_WIDTH)
        self.assertEqual(config.audio.volume, 1.0)

    def test_default_web_config(self):
        config = AppConfig()
        self.assertEqual(config.web.host, "0.0.0.0")
        self.assertEqual(config.web.port, 8080)
        self.assertFalse(config.web.debug)

    def test_default_bluetooth_config(self):
        config = AppConfig()
        self.assertTrue(config.bluetooth.auto_accept_pairing)
        self.assertIsNone(config.bluetooth.preferred_adapter)

    def test_default_cast_config(self):
        config = AppConfig()
        self.assertEqual(config.cast.stream_port, 8099)
        self.assertEqual(config.cast.stream_format, "mp3")

    def test_eq_constants(self):
        self.assertEqual(EQ_BANDS, 10)
        self.assertEqual(len(EQ_BAND_FREQUENCIES), 10)
        # Frequencies should be monotonically increasing
        for i in range(1, len(EQ_BAND_FREQUENCIES)):
            self.assertGreater(EQ_BAND_FREQUENCIES[i], EQ_BAND_FREQUENCIES[i - 1])


class TestAppConfigPersistence(unittest.TestCase):
    """Test config save/load cycle."""

    def test_save_and_load(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        try:
            config = AppConfig()
            config.web.port = 9999
            config.audio.volume = 0.5
            config.log_level = "DEBUG"
            config.save(path)

            loaded = AppConfig.load(path)
            self.assertEqual(loaded.web.port, 9999)
            self.assertEqual(loaded.audio.volume, 0.5)
            self.assertEqual(loaded.log_level, "DEBUG")
        finally:
            os.unlink(path)

    def test_load_nonexistent_returns_defaults(self):
        config = AppConfig.load("/tmp/nonexistent_a2dplaya_config.json")
        self.assertEqual(config.web.port, 8080)
        self.assertEqual(config.audio.sample_rate, PIPELINE_SAMPLE_RATE)

    def test_load_invalid_json(self):
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False
        ) as f:
            f.write("not valid json{{{")
            path = f.name

        try:
            config = AppConfig.load(path)
            # Should fall back to defaults
            self.assertEqual(config.web.port, 8080)
        finally:
            os.unlink(path)


class TestAppConfigEnvOverrides(unittest.TestCase):
    """Test environment variable configuration overrides."""

    def test_env_web_port(self):
        os.environ["A2DPLAYA_WEB_PORT"] = "9090"
        try:
            config = AppConfig.load("/tmp/nonexistent.json")
            self.assertEqual(config.web.port, 9090)
        finally:
            del os.environ["A2DPLAYA_WEB_PORT"]

    def test_env_volume(self):
        os.environ["A2DPLAYA_AUDIO_VOLUME"] = "0.75"
        try:
            config = AppConfig.load("/tmp/nonexistent.json")
            self.assertEqual(config.audio.volume, 0.75)
        finally:
            del os.environ["A2DPLAYA_AUDIO_VOLUME"]

    def test_env_log_level(self):
        os.environ["A2DPLAYA_LOG_LEVEL"] = "DEBUG"
        try:
            config = AppConfig.load("/tmp/nonexistent.json")
            self.assertEqual(config.log_level, "DEBUG")
        finally:
            del os.environ["A2DPLAYA_LOG_LEVEL"]

    def test_env_invalid_value_ignored(self):
        os.environ["A2DPLAYA_WEB_PORT"] = "not_a_number"
        try:
            config = AppConfig.load("/tmp/nonexistent.json")
            # Should keep default
            self.assertEqual(config.web.port, 8080)
        finally:
            del os.environ["A2DPLAYA_WEB_PORT"]


class TestConfigFromDict(unittest.TestCase):
    """Test constructing config from dictionary."""

    def test_partial_dict(self):
        data = {
            "web": {"port": 3000},
            "audio": {"volume": 0.3},
        }
        config = AppConfig._from_dict(data)
        self.assertEqual(config.web.port, 3000)
        self.assertEqual(config.audio.volume, 0.3)
        # Other fields keep defaults
        self.assertEqual(config.web.host, "0.0.0.0")
        self.assertEqual(config.audio.sample_rate, PIPELINE_SAMPLE_RATE)

    def test_unknown_keys_ignored(self):
        data = {
            "web": {"port": 5000, "unknown_key": "value"},
        }
        config = AppConfig._from_dict(data)
        self.assertEqual(config.web.port, 5000)


if __name__ == "__main__":
    unittest.main()
