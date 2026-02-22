"""
Configuration management for A2DPlaya.

Handles loading, validation, and persistence of application configuration.
All ports, paths, and runtime settings are configurable through the config
file or environment variables. Environment variables take precedence over
config file values.
"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default configuration directory
DEFAULT_CONFIG_DIR = os.path.expanduser("~/.config/a2dplaya")
DEFAULT_CONFIG_FILE = os.path.join(DEFAULT_CONFIG_DIR, "config.json")

# Audio constants - Chromecast compatible formats
# Chromecast supports: MP3 (up to 320kbps), AAC-LC, FLAC, WAV, Opus, Vorbis
# Preferred sample rates: 44100, 48000 Hz
# Preferred bit depths: 16-bit, 24-bit
CHROMECAST_SAMPLE_RATE = 44100
CHROMECAST_CHANNELS = 2
CHROMECAST_SAMPLE_WIDTH = 2  # 16-bit = 2 bytes
CHROMECAST_FORMAT = "mp3"  # Most compatible format

# AirPlay audio specs
# AirPlay supports: ALAC (Apple Lossless), AAC, PCM
# Standard: 44100 Hz, 16-bit, stereo
AIRPLAY_SAMPLE_RATE = 44100
AIRPLAY_CHANNELS = 2
AIRPLAY_SAMPLE_WIDTH = 2  # 16-bit

# Internal pipeline audio format (common format for processing)
PIPELINE_SAMPLE_RATE = 44100
PIPELINE_CHANNELS = 2
PIPELINE_SAMPLE_WIDTH = 2  # 16-bit PCM
PIPELINE_CHUNK_SIZE = 4096  # samples per chunk

# FFT / EQ visualization
EQ_BANDS = 10
EQ_FFT_SIZE = 2048
EQ_UPDATE_RATE_HZ = 30  # Visualization update rate

# EQ band center frequencies (Hz) for 10-band equalizer
EQ_BAND_FREQUENCIES = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]

# Bluetooth constants
BT_DISCOVERY_TIMEOUT = 15  # seconds
BT_RECONNECT_DELAY = 3  # seconds between reconnection attempts
BT_RECONNECT_MAX_RETRIES = 10
BT_AGENT_CAPABILITY = "NoInputNoOutput"  # Auto-accept pairing


@dataclass
class BluetoothConfig:
    """Bluetooth-specific configuration."""
    discovery_timeout: int = BT_DISCOVERY_TIMEOUT
    reconnect_delay: int = BT_RECONNECT_DELAY
    reconnect_max_retries: int = BT_RECONNECT_MAX_RETRIES
    preferred_adapter: Optional[str] = None  # e.g., "hci0"
    auto_accept_pairing: bool = True
    agent_capability: str = BT_AGENT_CAPABILITY


@dataclass
class AudioConfig:
    """Audio pipeline configuration."""
    sample_rate: int = PIPELINE_SAMPLE_RATE
    channels: int = PIPELINE_CHANNELS
    sample_width: int = PIPELINE_SAMPLE_WIDTH
    chunk_size: int = PIPELINE_CHUNK_SIZE
    line_in_device: Optional[str] = None  # ALSA device name, e.g., "hw:1,0"
    enable_line_in: bool = False
    volume: float = 1.0  # 0.0 to 1.0
    eq_enabled: bool = True


@dataclass
class CastConfig:
    """Chromecast-specific configuration."""
    stream_port: int = 8099  # Port for serving audio stream to Chromecast
    discovery_timeout: int = 10  # seconds
    stream_format: str = CHROMECAST_FORMAT
    stream_sample_rate: int = CHROMECAST_SAMPLE_RATE
    stream_channels: int = CHROMECAST_CHANNELS


@dataclass
class AirPlayConfig:
    """AirPlay-specific configuration."""
    discovery_timeout: int = 10  # seconds
    stream_sample_rate: int = AIRPLAY_SAMPLE_RATE
    stream_channels: int = AIRPLAY_CHANNELS
    stream_buffer_size: int = 4096


@dataclass
class WebConfig:
    """Web server configuration."""
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False
    secret_key: str = ""  # Generated at install time


@dataclass
class AppConfig:
    """Main application configuration container."""
    bluetooth: BluetoothConfig = field(default_factory=BluetoothConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    cast: CastConfig = field(default_factory=CastConfig)
    airplay: AirPlayConfig = field(default_factory=AirPlayConfig)
    web: WebConfig = field(default_factory=WebConfig)
    log_level: str = "INFO"
    data_dir: str = DEFAULT_CONFIG_DIR

    def save(self, path: Optional[str] = None):
        """Persist configuration to disk as JSON."""
        config_path = path or DEFAULT_CONFIG_FILE
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            json.dump(asdict(self), f, indent=2)
        logger.info("Configuration saved to %s", config_path)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "AppConfig":
        """
        Load configuration from file, with environment variable overrides.

        Environment variables follow the pattern A2DPLAYA_SECTION_KEY,
        e.g., A2DPLAYA_WEB_PORT=9090 overrides web.port.
        """
        config_path = path or DEFAULT_CONFIG_FILE
        config = cls()

        # Load from file if it exists
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    data = json.load(f)
                config = cls._from_dict(data)
                logger.info("Configuration loaded from %s", config_path)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(
                    "Failed to load config from %s: %s. Using defaults.",
                    config_path, e
                )

        # Apply environment variable overrides
        config._apply_env_overrides()

        # Generate secret key if not set
        if not config.web.secret_key:
            config.web.secret_key = os.urandom(32).hex()

        return config

    @classmethod
    def _from_dict(cls, data: dict) -> "AppConfig":
        """Construct AppConfig from a dictionary, handling nested dataclasses."""
        config = cls()
        if "bluetooth" in data:
            config.bluetooth = BluetoothConfig(**{
                k: v for k, v in data["bluetooth"].items()
                if k in BluetoothConfig.__dataclass_fields__
            })
        if "audio" in data:
            config.audio = AudioConfig(**{
                k: v for k, v in data["audio"].items()
                if k in AudioConfig.__dataclass_fields__
            })
        if "cast" in data:
            config.cast = CastConfig(**{
                k: v for k, v in data["cast"].items()
                if k in CastConfig.__dataclass_fields__
            })
        if "airplay" in data:
            config.airplay = AirPlayConfig(**{
                k: v for k, v in data["airplay"].items()
                if k in AirPlayConfig.__dataclass_fields__
            })
        if "web" in data:
            config.web = WebConfig(**{
                k: v for k, v in data["web"].items()
                if k in WebConfig.__dataclass_fields__
            })
        if "log_level" in data:
            config.log_level = data["log_level"]
        if "data_dir" in data:
            config.data_dir = data["data_dir"]
        return config

    def _apply_env_overrides(self):
        """
        Override configuration values from environment variables.

        Format: A2DPLAYA_<SECTION>_<KEY>=<value>
        Example: A2DPLAYA_WEB_PORT=9090
        """
        env_mappings = {
            "A2DPLAYA_WEB_PORT": ("web", "port", int),
            "A2DPLAYA_WEB_HOST": ("web", "host", str),
            "A2DPLAYA_WEB_DEBUG": ("web", "debug", lambda x: x.lower() == "true"),
            "A2DPLAYA_WEB_SECRET_KEY": ("web", "secret_key", str),
            "A2DPLAYA_CAST_STREAM_PORT": ("cast", "stream_port", int),
            "A2DPLAYA_AUDIO_SAMPLE_RATE": ("audio", "sample_rate", int),
            "A2DPLAYA_AUDIO_VOLUME": ("audio", "volume", float),
            "A2DPLAYA_BT_ADAPTER": ("bluetooth", "preferred_adapter", str),
            "A2DPLAYA_LOG_LEVEL": (None, "log_level", str),
        }
        for env_key, (section, attr, type_fn) in env_mappings.items():
            value = os.environ.get(env_key)
            if value is not None:
                try:
                    converted = type_fn(value)
                    if section:
                        setattr(getattr(self, section), attr, converted)
                    else:
                        setattr(self, attr, converted)
                    logger.info("Config override from env: %s=%s", env_key, value)
                except (ValueError, TypeError) as e:
                    logger.warning(
                        "Invalid env override %s=%s: %s", env_key, value, e
                    )
