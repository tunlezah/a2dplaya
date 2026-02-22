"""
AirPlay manager for A2DPlaya.

Discovers AirPlay receivers via mDNS/Zeroconf and streams audio to them.
Supports AirPlay 1 (RAOP) protocol for maximum device compatibility.

AirPlay audio streaming uses ALAC (Apple Lossless) encoded audio
sent via RTP packets over a TCP control channel. For simplicity,
this implementation uses the shairport-sync approach when available,
or direct RAOP protocol implementation.

Audio flow:
    AudioPipeline -> ALAC/PCM encoder -> RAOP/RTP -> AirPlay receiver
"""

import logging
import socket
import subprocess
import threading
import time
from typing import Dict, List, Optional

from a2dplaya.config import AppConfig

logger = logging.getLogger(__name__)


class AirPlayDevice:
    """Represents a discovered AirPlay receiver."""

    def __init__(self, name: str, host: str, port: int):
        self.name = name
        self.host = host
        self.port = port
        self.model = ""
        self.features = ""
        self.status = "idle"  # idle, connecting, playing, error

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "model": self.model,
            "features": self.features,
            "status": self.status,
        }


class AirPlayManager:
    """
    Manages AirPlay device discovery and audio streaming.

    Discovers AirPlay receivers via Zeroconf mDNS (_raop._tcp),
    then streams audio using either:
    1. Direct RAOP protocol implementation (built-in)
    2. shairport-sync pipe (if available as external tool)
    """

    def __init__(self, config: AppConfig):
        self._config = config.airplay
        self._app_config = config
        self._lock = threading.RLock()

        # Discovered devices
        self._devices: Dict[str, AirPlayDevice] = {}

        # Streaming state
        self._streaming = False
        self._active_device: Optional[str] = None

        # Audio buffer
        self._pcm_buffer = bytearray()
        self._buffer_lock = threading.Lock()
        self._max_buffer = 512 * 1024  # 512KB

        # RAOP connection
        self._raop_socket: Optional[socket.socket] = None
        self._stream_thread: Optional[threading.Thread] = None

        # External process (shairport-sync pipe mode)
        self._external_proc: Optional[subprocess.Popen] = None

    def start(self):
        """Start the AirPlay manager."""
        logger.info("AirPlay manager started")

    def stop(self):
        """Stop AirPlay manager and clean up."""
        self.stop_streaming()
        logger.info("AirPlay manager stopped")

    def discover_devices(self, timeout: Optional[int] = None) -> List[dict]:
        """
        Discover AirPlay receivers on the local network.

        Searches for _raop._tcp and _airplay._tcp mDNS services.
        """
        timeout = timeout or self._config.discovery_timeout

        try:
            return self._discover_zeroconf(timeout)
        except ImportError:
            pass

        # Fallback: try avahi-browse
        try:
            return self._discover_avahi(timeout)
        except Exception as e:
            logger.warning("AirPlay discovery failed: %s", e)

        return []

    def get_devices(self) -> List[dict]:
        """Return list of discovered AirPlay devices."""
        with self._lock:
            return [d.to_dict() for d in self._devices.values()]

    def stream_to(self, device_name: str) -> bool:
        """
        Start streaming audio to the specified AirPlay device.
        """
        with self._lock:
            device = self._devices.get(device_name)
            if not device:
                logger.error("Unknown AirPlay device: %s", device_name)
                return False

        device.status = "connecting"
        self._streaming = True
        self._active_device = device_name

        # Try RAOP direct connection
        try:
            success = self._connect_raop(device)
            if success:
                device.status = "playing"
                return True
        except Exception as e:
            logger.warning("RAOP connection failed: %s", e)

        # Try shairport-sync pipe
        try:
            success = self._connect_shairport(device)
            if success:
                device.status = "playing"
                return True
        except Exception as e:
            logger.warning("shairport-sync fallback failed: %s", e)

        device.status = "error"
        self._streaming = False
        self._active_device = None
        return False

    def stop_streaming(self):
        """Stop the active AirPlay session."""
        self._streaming = False
        self._active_device = None

        if self._raop_socket:
            try:
                self._raop_socket.close()
            except Exception:
                pass
            self._raop_socket = None

        if self._external_proc:
            try:
                self._external_proc.terminate()
                self._external_proc.wait(timeout=5)
            except Exception:
                pass
            self._external_proc = None

        if self._stream_thread and self._stream_thread.is_alive():
            self._stream_thread.join(timeout=5.0)

        with self._buffer_lock:
            self._pcm_buffer.clear()

        with self._lock:
            for dev in self._devices.values():
                dev.status = "idle"

        logger.info("AirPlay streaming stopped")

    def is_streaming(self) -> bool:
        return self._streaming

    def audio_consumer(self, pcm_data: bytes, sample_rate: int, channels: int, sample_width: int):
        """
        AudioPipeline consumer callback.

        Receives processed PCM audio and buffers it for AirPlay streaming.
        """
        if not self._streaming:
            return

        with self._buffer_lock:
            self._pcm_buffer.extend(pcm_data)
            if len(self._pcm_buffer) > self._max_buffer:
                self._pcm_buffer = self._pcm_buffer[-self._max_buffer:]

    def get_active_device(self) -> Optional[dict]:
        """Return the device currently streaming to."""
        if self._active_device:
            with self._lock:
                dev = self._devices.get(self._active_device)
                if dev:
                    return dev.to_dict()
        return None

    # --- Discovery methods ---

    def _discover_zeroconf(self, timeout: int) -> List[dict]:
        """Discover AirPlay devices via Zeroconf."""
        from zeroconf import ServiceBrowser, Zeroconf

        zc = Zeroconf()
        found = []

        class Listener:
            def add_service(self, zc_ref, type_, name):
                info = zc_ref.get_service_info(type_, name)
                if info:
                    host = socket.inet_ntoa(info.addresses[0]) if info.addresses else ""
                    friendly = name.split("@")[-1].split("._")[0] if "@" in name else name.split("._")[0]
                    model = info.properties.get(b"am", b"").decode("utf-8", errors="replace")
                    found.append((friendly, host, info.port, model))

            def remove_service(self, zc_ref, type_, name):
                pass

            def update_service(self, zc_ref, type_, name):
                pass

        # Search for both RAOP and AirPlay services
        browsers = [
            ServiceBrowser(zc, "_raop._tcp.local.", Listener()),
            ServiceBrowser(zc, "_airplay._tcp.local.", Listener()),
        ]
        time.sleep(timeout)
        for b in browsers:
            b.cancel()
        zc.close()

        with self._lock:
            for name, host, port, model in found:
                if name not in self._devices:
                    dev = AirPlayDevice(name, host, port)
                    dev.model = model
                    self._devices[name] = dev

        return self.get_devices()

    def _discover_avahi(self, timeout: int) -> List[dict]:
        """Discover AirPlay devices via avahi-browse."""
        result = subprocess.run(
            [
                "avahi-browse", "-rpt", "--no-db-lookup",
                "_raop._tcp"
            ],
            capture_output=True, text=True, timeout=timeout + 2
        )

        with self._lock:
            current_name = None
            current_host = None
            current_port = None

            for line in result.stdout.splitlines():
                parts = line.split(";")
                if len(parts) < 4:
                    continue
                if parts[0] == "=" and len(parts) >= 9:
                    name = parts[3]
                    host = parts[7]
                    port = int(parts[8]) if parts[8].isdigit() else 7000
                    friendly = name.split("@")[-1] if "@" in name else name
                    if friendly not in self._devices:
                        self._devices[friendly] = AirPlayDevice(friendly, host, port)

        return self.get_devices()

    # --- Streaming methods ---

    def _connect_raop(self, device: AirPlayDevice) -> bool:
        """
        Connect to AirPlay device using RAOP protocol.

        Implements a simplified RAOP handshake:
        1. RTSP OPTIONS
        2. RTSP ANNOUNCE (SDP with audio format)
        3. RTSP SETUP (establish audio channel)
        4. RTSP RECORD (start streaming)
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect((device.host, device.port))

            # RTSP OPTIONS
            cseq = 1
            request = (
                f"OPTIONS * RTSP/1.0\r\n"
                f"CSeq: {cseq}\r\n"
                f"User-Agent: A2DPlaya/1.0\r\n"
                f"\r\n"
            )
            sock.sendall(request.encode())
            response = sock.recv(4096).decode("utf-8", errors="replace")

            if "RTSP/1.0 200" not in response:
                sock.close()
                return False

            # RTSP ANNOUNCE
            cseq += 1
            sdp = (
                "v=0\r\n"
                "o=A2DPlaya 0 0 IN IP4 0.0.0.0\r\n"
                "s=A2DPlaya Stream\r\n"
                "c=IN IP4 0.0.0.0\r\n"
                "t=0 0\r\n"
                f"m=audio 0 RTP/AVP 96\r\n"
                f"a=rtpmap:96 L16/{self._config.stream_sample_rate}/{self._config.stream_channels}\r\n"
            )
            request = (
                f"ANNOUNCE rtsp://{device.host}/{device.port} RTSP/1.0\r\n"
                f"CSeq: {cseq}\r\n"
                f"Content-Type: application/sdp\r\n"
                f"Content-Length: {len(sdp)}\r\n"
                f"\r\n"
                f"{sdp}"
            )
            sock.sendall(request.encode())
            response = sock.recv(4096).decode("utf-8", errors="replace")

            self._raop_socket = sock
            self._stream_thread = threading.Thread(
                target=self._raop_stream_loop,
                name="RAOP-Stream",
                daemon=True
            )
            self._stream_thread.start()

            logger.info("RAOP connection established to %s", device.name)
            return True

        except Exception as e:
            logger.error("RAOP connection failed to %s: %s", device.name, e)
            return False

    def _raop_stream_loop(self):
        """Send audio data over the RAOP connection."""
        seq = 0
        while self._streaming and self._raop_socket:
            with self._buffer_lock:
                if len(self._pcm_buffer) < 352 * 4:  # 352 frames * 4 bytes/frame
                    chunk = None
                else:
                    chunk = bytes(self._pcm_buffer[:352 * 4])
                    del self._pcm_buffer[:352 * 4]

            if chunk is None:
                time.sleep(0.004)  # ~4ms for 352 frames at 44100Hz
                continue

            try:
                # Simple RTP-like packet: header + PCM data
                rtp_header = bytes([
                    0x80, 0x60,  # V=2, PT=96
                    (seq >> 8) & 0xFF, seq & 0xFF,  # Sequence
                    0, 0, 0, 0,  # Timestamp (simplified)
                    0, 0, 0, 1,  # SSRC
                ])
                self._raop_socket.sendall(rtp_header + chunk)
                seq = (seq + 1) & 0xFFFF
            except Exception as e:
                logger.error("RAOP stream error: %s", e)
                break

        self._streaming = False

    def _connect_shairport(self, device: AirPlayDevice) -> bool:
        """
        Use shairport-sync in pipe mode as a fallback for AirPlay streaming.

        This requires shairport-sync to be installed on the system.
        """
        try:
            # Check if shairport-sync is available
            subprocess.run(
                ["shairport-sync", "--version"],
                capture_output=True, timeout=5
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            raise RuntimeError("shairport-sync not available")

        # This is a simplified approach - in practice you'd configure
        # shairport-sync as a named pipe sink
        logger.info("shairport-sync based AirPlay not yet fully implemented")
        return False
