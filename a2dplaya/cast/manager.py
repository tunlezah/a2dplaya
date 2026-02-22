"""
Chromecast manager for A2DPlaya.

Discovers Chromecast devices via mDNS/Zeroconf and streams audio to them.
Audio is served as an HTTP MP3 stream that the Chromecast fetches from this
device. Uses pychromecast for device control when available, with a
socket-based fallback for basic operation.

Audio flow:
    AudioPipeline -> MP3 encoder -> HTTP stream server -> Chromecast fetches URL
"""

import io
import logging
import socket
import struct
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Callable, Dict, List, Optional

from a2dplaya.config import AppConfig

logger = logging.getLogger(__name__)


class CastDevice:
    """Represents a discovered Chromecast device."""

    def __init__(self, name: str, host: str, port: int, model: str = ""):
        self.name = name
        self.host = host
        self.port = port
        self.model = model
        self.uuid = ""
        self.status = "idle"  # idle, connecting, playing, error

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "model": self.model,
            "uuid": self.uuid,
            "status": self.status,
        }


class AudioStreamHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves the live audio stream to Chromecast."""

    # Class-level reference to the stream buffer (set by CastManager)
    stream_manager = None

    def do_GET(self):
        if self.path == "/stream.mp3":
            self._serve_mp3_stream()
        elif self.path == "/stream.wav":
            self._serve_wav_stream()
        else:
            self.send_error(404)

    def _serve_mp3_stream(self):
        """Serve a continuous MP3 audio stream."""
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "close")
        self.send_header("icy-name", "A2DPlaya")
        self.end_headers()

        if not self.stream_manager:
            return

        try:
            while self.stream_manager.is_streaming():
                chunk = self.stream_manager.get_stream_chunk()
                if chunk:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                else:
                    time.sleep(0.01)
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("Stream client disconnected")
        except Exception as e:
            logger.error("Stream handler error: %s", e)

    def _serve_wav_stream(self):
        """Serve a continuous WAV audio stream (PCM)."""
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        if not self.stream_manager:
            return

        # Write WAV header with max size (streaming)
        sample_rate = 44100
        channels = 2
        bits = 16
        byte_rate = sample_rate * channels * (bits // 8)
        block_align = channels * (bits // 8)

        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 0xFFFFFFFF - 8, b"WAVE",
            b"fmt ", 16, 1, channels,
            sample_rate, byte_rate, block_align, bits,
            b"data", 0xFFFFFFFF - 44
        )
        self.wfile.write(header)

        try:
            while self.stream_manager.is_streaming():
                chunk = self.stream_manager.get_pcm_chunk()
                if chunk:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                else:
                    time.sleep(0.01)
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("WAV stream client disconnected")

    def log_message(self, format, *args):
        """Suppress default HTTP logging."""
        logger.debug("StreamHTTP: %s", format % args)


class CastManager:
    """
    Manages Chromecast device discovery and audio streaming.

    Discovers devices via Zeroconf/mDNS, encodes audio to MP3 format,
    serves it via an HTTP stream, and tells the Chromecast to play the URL.
    """

    def __init__(self, config: AppConfig):
        self._config = config.cast
        self._app_config = config
        self._lock = threading.RLock()

        # Discovered devices
        self._devices: Dict[str, CastDevice] = {}

        # Streaming state
        self._streaming = False
        self._stream_buffer = bytearray()
        self._pcm_buffer = bytearray()
        self._buffer_lock = threading.Lock()
        self._max_buffer = 1024 * 1024  # 1MB buffer

        # HTTP stream server
        self._http_server: Optional[HTTPServer] = None
        self._http_thread: Optional[threading.Thread] = None
        self._stream_port = config.cast.stream_port

        # Active cast connection
        self._active_device: Optional[str] = None
        self._cast_connection = None  # pychromecast CastBrowser or socket

        # MP3 encoder (lazy init)
        self._mp3_encoder = None

    def start(self):
        """Start the Cast manager and HTTP stream server."""
        self._start_http_server()
        logger.info("Cast manager started, stream server on port %d", self._stream_port)

    def stop(self):
        """Stop casting and clean up resources."""
        self.stop_casting()
        self._stop_http_server()
        logger.info("Cast manager stopped")

    def discover_devices(self, timeout: Optional[int] = None) -> List[dict]:
        """
        Discover Chromecast devices on the local network.

        Uses Zeroconf/mDNS to find _googlecast._tcp services.
        Falls back to pychromecast if available.
        """
        timeout = timeout or self._config.discovery_timeout

        # Try pychromecast first
        try:
            return self._discover_pychromecast(timeout)
        except ImportError:
            pass

        # Fall back to zeroconf
        try:
            return self._discover_zeroconf(timeout)
        except ImportError:
            pass

        logger.warning("No Chromecast discovery library available")
        return []

    def get_devices(self) -> List[dict]:
        """Return list of discovered Chromecast devices."""
        with self._lock:
            return [d.to_dict() for d in self._devices.values()]

    def cast_to(self, device_name: str) -> bool:
        """
        Start casting audio to the specified Chromecast device.

        Tells the Chromecast to play the HTTP audio stream URL
        served by this application.
        """
        with self._lock:
            device = self._devices.get(device_name)
            if not device:
                logger.error("Unknown cast device: %s", device_name)
                return False

        device.status = "connecting"
        self._streaming = True

        # Get our local IP that can reach the Chromecast
        local_ip = self._get_local_ip(device.host)
        stream_url = f"http://{local_ip}:{self._stream_port}/stream.wav"

        # Try pychromecast
        try:
            return self._cast_pychromecast(device, stream_url)
        except ImportError:
            pass

        # Fall back to basic DIAL/REST
        try:
            return self._cast_dial(device, stream_url)
        except Exception as e:
            logger.error("Failed to cast to %s: %s", device_name, e)
            device.status = "error"
            return False

    def stop_casting(self):
        """Stop the active cast session."""
        self._streaming = False
        self._active_device = None

        if self._cast_connection:
            try:
                if hasattr(self._cast_connection, "quit_app"):
                    self._cast_connection.quit_app()
                elif hasattr(self._cast_connection, "disconnect"):
                    self._cast_connection.disconnect()
            except Exception as e:
                logger.error("Error stopping cast: %s", e)
            self._cast_connection = None

        with self._lock:
            for dev in self._devices.values():
                dev.status = "idle"

        with self._buffer_lock:
            self._stream_buffer.clear()
            self._pcm_buffer.clear()

        logger.info("Casting stopped")

    def is_streaming(self) -> bool:
        """Check if currently streaming audio."""
        return self._streaming

    def audio_consumer(self, pcm_data: bytes, sample_rate: int, channels: int, sample_width: int):
        """
        AudioPipeline consumer callback.

        Receives processed PCM audio and buffers it for the HTTP stream.
        """
        if not self._streaming:
            return

        with self._buffer_lock:
            self._pcm_buffer.extend(pcm_data)
            # Trim buffer if too large
            if len(self._pcm_buffer) > self._max_buffer:
                self._pcm_buffer = self._pcm_buffer[-self._max_buffer:]

    def get_stream_chunk(self) -> Optional[bytes]:
        """Get the next chunk of encoded audio for streaming."""
        with self._buffer_lock:
            if not self._stream_buffer:
                return None
            chunk = bytes(self._stream_buffer[:4096])
            del self._stream_buffer[:4096]
            return chunk

    def get_pcm_chunk(self) -> Optional[bytes]:
        """Get the next chunk of raw PCM audio for WAV streaming."""
        with self._buffer_lock:
            if not self._pcm_buffer:
                return None
            chunk = bytes(self._pcm_buffer[:8192])
            del self._pcm_buffer[:8192]
            return chunk

    def get_active_device(self) -> Optional[dict]:
        """Return the device currently being cast to."""
        if self._active_device:
            with self._lock:
                dev = self._devices.get(self._active_device)
                if dev:
                    return dev.to_dict()
        return None

    # --- Internal methods ---

    def _start_http_server(self):
        """Start the HTTP audio stream server."""
        AudioStreamHandler.stream_manager = self

        try:
            self._http_server = HTTPServer(
                ("0.0.0.0", self._stream_port),
                AudioStreamHandler
            )
            self._http_server.timeout = 1
            self._http_thread = threading.Thread(
                target=self._http_server_loop,
                name="CastHTTP",
                daemon=True
            )
            self._http_thread.start()
        except Exception as e:
            logger.error("Failed to start HTTP stream server: %s", e)

    def _http_server_loop(self):
        """HTTP server main loop."""
        while self._http_server:
            try:
                self._http_server.handle_request()
            except Exception:
                break

    def _stop_http_server(self):
        """Stop the HTTP stream server."""
        if self._http_server:
            self._http_server.shutdown()
            self._http_server = None

    def _discover_pychromecast(self, timeout: int) -> List[dict]:
        """Discover devices using pychromecast library."""
        import pychromecast

        chromecasts, browser = pychromecast.get_chromecasts(timeout=timeout)
        browser.stop_discovery()

        with self._lock:
            for cc in chromecasts:
                name = cc.name
                self._devices[name] = CastDevice(
                    name=name,
                    host=cc.host,
                    port=cc.port,
                    model=cc.model_name or ""
                )
                self._devices[name].uuid = str(cc.uuid) if cc.uuid else ""

        return self.get_devices()

    def _discover_zeroconf(self, timeout: int) -> List[dict]:
        """Discover devices using zeroconf/mDNS directly."""
        from zeroconf import ServiceBrowser, Zeroconf

        zc = Zeroconf()
        found = []

        class Listener:
            def add_service(self, zc_ref, type_, name):
                info = zc_ref.get_service_info(type_, name)
                if info:
                    host = socket.inet_ntoa(info.addresses[0]) if info.addresses else ""
                    friendly = info.properties.get(b"fn", b"").decode("utf-8", errors="replace")
                    model = info.properties.get(b"md", b"").decode("utf-8", errors="replace")
                    found.append((friendly or name, host, info.port, model))

            def remove_service(self, zc_ref, type_, name):
                pass

            def update_service(self, zc_ref, type_, name):
                pass

        browser = ServiceBrowser(zc, "_googlecast._tcp.local.", Listener())
        time.sleep(timeout)
        browser.cancel()
        zc.close()

        with self._lock:
            for name, host, port, model in found:
                self._devices[name] = CastDevice(name, host, port, model)

        return self.get_devices()

    def _cast_pychromecast(self, device: CastDevice, stream_url: str) -> bool:
        """Cast using pychromecast library."""
        import pychromecast

        chromecasts, browser = pychromecast.get_chromecasts()
        browser.stop_discovery()

        for cc in chromecasts:
            if cc.name == device.name:
                cc.wait()
                mc = cc.media_controller
                mc.play_media(stream_url, "audio/wav")
                mc.block_until_active()

                self._cast_connection = cc
                self._active_device = device.name
                device.status = "playing"
                logger.info("Casting to %s via pychromecast", device.name)
                return True

        device.status = "error"
        return False

    def _cast_dial(self, device: CastDevice, stream_url: str) -> bool:
        """Cast using DIAL REST protocol (basic fallback)."""
        import urllib.request

        # Try to launch the default media receiver via DIAL
        dial_url = f"http://{device.host}:{device.port}/apps/CC1AD845"

        try:
            data = f"""<?xml version="1.0" encoding="UTF-8"?>
            <play>
                <url>{stream_url}</url>
                <type>audio/wav</type>
            </play>""".encode()

            req = urllib.request.Request(dial_url, data=data, method="POST")
            req.add_header("Content-Type", "application/xml")
            urllib.request.urlopen(req, timeout=10)

            self._active_device = device.name
            device.status = "playing"
            logger.info("Cast initiated via DIAL to %s", device.name)
            return True
        except Exception as e:
            logger.error("DIAL cast failed: %s", e)
            device.status = "error"
            return False

    def _get_local_ip(self, target_host: str) -> str:
        """Determine the local IP that can reach the target host."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((target_host, 80))
            local_ip = s.getsockname()[0]
            s.close()
            return local_ip
        except Exception:
            return "127.0.0.1"
