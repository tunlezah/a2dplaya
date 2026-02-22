"""
Bluetooth manager for A2DPlaya.

Manages BlueZ D-Bus interactions for acting as a Bluetooth A2DP audio sink.
Handles adapter configuration, device discovery, pairing, and A2DP audio
transport setup. Feeds received audio data into the AudioPipeline.

Requires:
    - BlueZ 5.x with D-Bus API
    - dbus-python or pydbus
    - PulseAudio or PipeWire with bluetooth module
"""

import logging
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional

from a2dplaya.config import AppConfig

logger = logging.getLogger(__name__)

# BlueZ D-Bus constants
BLUEZ_SERVICE = "org.bluez"
BLUEZ_ADAPTER_IFACE = "org.bluez.Adapter1"
BLUEZ_DEVICE_IFACE = "org.bluez.Device1"
BLUEZ_MEDIA_IFACE = "org.bluez.Media1"
BLUEZ_MEDIA_TRANSPORT_IFACE = "org.bluez.MediaTransport1"
BLUEZ_AGENT_IFACE = "org.bluez.Agent1"
BLUEZ_AGENT_MANAGER_IFACE = "org.bluez.AgentManager1"

A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"
A2DP_SOURCE_UUID = "0000110a-0000-1000-8000-00805f9b34fb"


class BluetoothDevice:
    """Represents a discovered or connected Bluetooth device."""

    def __init__(self, address: str, name: str = "", path: str = ""):
        self.address = address
        self.name = name or address
        self.path = path
        self.paired = False
        self.connected = False
        self.trusted = False
        self.audio_connected = False

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "name": self.name,
            "paired": self.paired,
            "connected": self.connected,
            "trusted": self.trusted,
            "audio_connected": self.audio_connected,
        }


class BluetoothManager:
    """
    Manages Bluetooth A2DP sink functionality via BlueZ D-Bus API.

    When a Bluetooth device connects and streams A2DP audio, this manager
    captures the audio and feeds it to the AudioPipeline via a callback.
    Falls back to command-line bluetoothctl if D-Bus is not available.
    """

    def __init__(self, config: AppConfig, audio_callback: Optional[Callable] = None):
        self._config = config.bluetooth
        self._audio_callback = audio_callback
        self._lock = threading.RLock()

        # Known devices
        self._devices: Dict[str, BluetoothDevice] = {}

        # State
        self._adapter_path: Optional[str] = None
        self._adapter_address: Optional[str] = None
        self._discoverable = False
        self._scanning = False
        self._running = False

        # D-Bus connection (lazy init)
        self._bus = None
        self._dbus_available = False

        # Audio transport
        self._transport_fd: Optional[int] = None
        self._audio_thread: Optional[threading.Thread] = None

    def start(self):
        """Initialize Bluetooth subsystem and register as A2DP sink."""
        self._running = True

        # Try D-Bus first, fall back to bluetoothctl
        try:
            import dbus
            self._bus = dbus.SystemBus()
            self._dbus_available = True
            self._setup_adapter_dbus()
            logger.info("Bluetooth manager started with D-Bus API")
        except (ImportError, Exception) as e:
            logger.warning("D-Bus not available (%s), using bluetoothctl fallback", e)
            self._dbus_available = False
            self._setup_adapter_cli()

    def stop(self):
        """Stop Bluetooth manager and clean up resources."""
        self._running = False
        self._scanning = False

        if self._audio_thread and self._audio_thread.is_alive():
            self._audio_thread.join(timeout=5.0)

        if self._transport_fd is not None:
            try:
                import os
                os.close(self._transport_fd)
            except OSError:
                pass
            self._transport_fd = None

        logger.info("Bluetooth manager stopped")

    def get_devices(self) -> List[dict]:
        """Return list of known Bluetooth devices."""
        with self._lock:
            return [d.to_dict() for d in self._devices.values()]

    def get_connected_device(self) -> Optional[dict]:
        """Return the currently connected audio device, if any."""
        with self._lock:
            for dev in self._devices.values():
                if dev.audio_connected:
                    return dev.to_dict()
        return None

    def start_discovery(self):
        """Start scanning for nearby Bluetooth devices."""
        if self._scanning:
            return

        self._scanning = True
        thread = threading.Thread(
            target=self._discovery_worker,
            name="BT-Discovery",
            daemon=True
        )
        thread.start()
        logger.info("Bluetooth discovery started")

    def stop_discovery(self):
        """Stop scanning for devices."""
        self._scanning = False
        if self._dbus_available:
            try:
                import dbus
                adapter = dbus.Interface(
                    self._bus.get_object(BLUEZ_SERVICE, self._adapter_path),
                    BLUEZ_ADAPTER_IFACE
                )
                adapter.StopDiscovery()
            except Exception:
                pass
        logger.info("Bluetooth discovery stopped")

    def set_discoverable(self, discoverable: bool):
        """Make this device discoverable/not discoverable to other BT devices."""
        self._discoverable = discoverable

        if self._dbus_available:
            try:
                import dbus
                props = dbus.Interface(
                    self._bus.get_object(BLUEZ_SERVICE, self._adapter_path),
                    "org.freedesktop.DBus.Properties"
                )
                props.Set(BLUEZ_ADAPTER_IFACE, "Discoverable", dbus.Boolean(discoverable))
                props.Set(BLUEZ_ADAPTER_IFACE, "Pairable", dbus.Boolean(True))
                logger.info("Discoverable set to %s", discoverable)
            except Exception as e:
                logger.error("Failed to set discoverable: %s", e)
        else:
            try:
                cmd = "discoverable on" if discoverable else "discoverable off"
                subprocess.run(
                    ["bluetoothctl", cmd],
                    capture_output=True, timeout=5
                )
            except Exception as e:
                logger.error("bluetoothctl discoverable failed: %s", e)

    def pair_device(self, address: str) -> bool:
        """Initiate pairing with a device."""
        if self._dbus_available:
            return self._pair_device_dbus(address)
        return self._pair_device_cli(address)

    def connect_device(self, address: str) -> bool:
        """Connect to a paired device."""
        if self._dbus_available:
            return self._connect_device_dbus(address)
        return self._connect_device_cli(address)

    def disconnect_device(self, address: str) -> bool:
        """Disconnect a device."""
        if self._dbus_available:
            return self._disconnect_device_dbus(address)
        return self._disconnect_device_cli(address)

    def trust_device(self, address: str) -> bool:
        """Mark a device as trusted for auto-reconnection."""
        if self._dbus_available:
            return self._trust_device_dbus(address)
        return self._trust_device_cli(address)

    def is_adapter_available(self) -> bool:
        """Check if a Bluetooth adapter is available."""
        return self._adapter_path is not None or self._adapter_address is not None

    def get_adapter_info(self) -> dict:
        """Return information about the Bluetooth adapter."""
        return {
            "available": self.is_adapter_available(),
            "address": self._adapter_address or "unknown",
            "discoverable": self._discoverable,
            "scanning": self._scanning,
            "dbus_mode": self._dbus_available,
        }

    # --- D-Bus implementation ---

    def _setup_adapter_dbus(self):
        """Configure the Bluetooth adapter via D-Bus."""
        import dbus

        manager = dbus.Interface(
            self._bus.get_object(BLUEZ_SERVICE, "/"),
            "org.freedesktop.DBus.ObjectManager"
        )
        objects = manager.GetManagedObjects()

        # Find adapter
        for path, interfaces in objects.items():
            if BLUEZ_ADAPTER_IFACE in interfaces:
                preferred = self._config.preferred_adapter
                if preferred and preferred not in path:
                    continue
                self._adapter_path = path
                props = interfaces[BLUEZ_ADAPTER_IFACE]
                self._adapter_address = str(props.get("Address", ""))
                break

        if not self._adapter_path:
            raise RuntimeError("No Bluetooth adapter found")

        # Configure adapter
        props = dbus.Interface(
            self._bus.get_object(BLUEZ_SERVICE, self._adapter_path),
            "org.freedesktop.DBus.Properties"
        )
        props.Set(BLUEZ_ADAPTER_IFACE, "Powered", dbus.Boolean(True))
        props.Set(BLUEZ_ADAPTER_IFACE, "Alias", dbus.String("A2DPlaya"))

        logger.info(
            "Bluetooth adapter configured: %s (%s)",
            self._adapter_path, self._adapter_address
        )

    def _setup_adapter_cli(self):
        """Configure the Bluetooth adapter via bluetoothctl."""
        try:
            result = subprocess.run(
                ["bluetoothctl", "show"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("Controller"):
                    parts = line.split()
                    if len(parts) >= 2:
                        self._adapter_address = parts[1]
                        self._adapter_path = "/cli"

            # Power on and set alias
            subprocess.run(
                ["bluetoothctl", "power", "on"],
                capture_output=True, timeout=5
            )
            subprocess.run(
                ["bluetoothctl", "system-alias", "A2DPlaya"],
                capture_output=True, timeout=5
            )
            logger.info("Bluetooth adapter configured via CLI: %s", self._adapter_address)
        except Exception as e:
            logger.error("Failed to setup Bluetooth adapter: %s", e)

    def _discovery_worker(self):
        """Background worker for device discovery."""
        if self._dbus_available:
            self._discovery_dbus()
        else:
            self._discovery_cli()

    def _discovery_dbus(self):
        """Run discovery via D-Bus."""
        import dbus

        try:
            adapter = dbus.Interface(
                self._bus.get_object(BLUEZ_SERVICE, self._adapter_path),
                BLUEZ_ADAPTER_IFACE
            )
            adapter.StartDiscovery()

            timeout = self._config.discovery_timeout
            start = time.time()
            while self._scanning and (time.time() - start) < timeout:
                self._refresh_devices_dbus()
                time.sleep(1.0)

            try:
                adapter.StopDiscovery()
            except Exception:
                pass
        except Exception as e:
            logger.error("D-Bus discovery error: %s", e)
        finally:
            self._scanning = False

    def _discovery_cli(self):
        """Run discovery via bluetoothctl."""
        try:
            proc = subprocess.Popen(
                ["bluetoothctl", "scan", "on"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            timeout = self._config.discovery_timeout
            start = time.time()
            while self._scanning and (time.time() - start) < timeout:
                time.sleep(1.0)
                self._refresh_devices_cli()

            proc.terminate()
            subprocess.run(
                ["bluetoothctl", "scan", "off"],
                capture_output=True, timeout=5
            )
        except Exception as e:
            logger.error("CLI discovery error: %s", e)
        finally:
            self._scanning = False

    def _refresh_devices_dbus(self):
        """Refresh device list from D-Bus."""
        import dbus

        try:
            manager = dbus.Interface(
                self._bus.get_object(BLUEZ_SERVICE, "/"),
                "org.freedesktop.DBus.ObjectManager"
            )
            objects = manager.GetManagedObjects()

            with self._lock:
                for path, interfaces in objects.items():
                    if BLUEZ_DEVICE_IFACE not in interfaces:
                        continue

                    props = interfaces[BLUEZ_DEVICE_IFACE]
                    address = str(props.get("Address", ""))
                    if not address:
                        continue

                    name = str(props.get("Name", props.get("Alias", address)))
                    if address not in self._devices:
                        self._devices[address] = BluetoothDevice(address, name, path)

                    dev = self._devices[address]
                    dev.name = name
                    dev.path = path
                    dev.paired = bool(props.get("Paired", False))
                    dev.connected = bool(props.get("Connected", False))
                    dev.trusted = bool(props.get("Trusted", False))

                    # Check for audio connection
                    uuids = [str(u) for u in props.get("UUIDs", [])]
                    dev.audio_connected = (
                        dev.connected and
                        (A2DP_SINK_UUID in uuids or A2DP_SOURCE_UUID in uuids)
                    )

        except Exception as e:
            logger.error("Failed to refresh devices: %s", e)

    def _refresh_devices_cli(self):
        """Refresh device list from bluetoothctl."""
        try:
            result = subprocess.run(
                ["bluetoothctl", "devices"],
                capture_output=True, text=True, timeout=5
            )
            with self._lock:
                for line in result.stdout.splitlines():
                    parts = line.strip().split(None, 2)
                    if len(parts) >= 3 and parts[0] == "Device":
                        address = parts[1]
                        name = parts[2]
                        if address not in self._devices:
                            self._devices[address] = BluetoothDevice(address, name)
                        self._devices[address].name = name
        except Exception as e:
            logger.error("Failed to refresh devices via CLI: %s", e)

    def _pair_device_dbus(self, address: str) -> bool:
        """Pair with device via D-Bus."""
        import dbus

        dev = self._devices.get(address)
        if not dev or not dev.path:
            return False

        try:
            device = dbus.Interface(
                self._bus.get_object(BLUEZ_SERVICE, dev.path),
                BLUEZ_DEVICE_IFACE
            )
            device.Pair()
            dev.paired = True
            logger.info("Paired with %s (%s)", dev.name, address)
            return True
        except Exception as e:
            logger.error("Failed to pair with %s: %s", address, e)
            return False

    def _pair_device_cli(self, address: str) -> bool:
        """Pair with device via bluetoothctl."""
        try:
            result = subprocess.run(
                ["bluetoothctl", "pair", address],
                capture_output=True, text=True, timeout=30
            )
            success = "Pairing successful" in result.stdout
            if success and address in self._devices:
                self._devices[address].paired = True
            return success
        except Exception as e:
            logger.error("CLI pair failed: %s", e)
            return False

    def _connect_device_dbus(self, address: str) -> bool:
        """Connect to device via D-Bus."""
        import dbus

        dev = self._devices.get(address)
        if not dev or not dev.path:
            return False

        try:
            device = dbus.Interface(
                self._bus.get_object(BLUEZ_SERVICE, dev.path),
                BLUEZ_DEVICE_IFACE
            )
            device.Connect()
            dev.connected = True
            logger.info("Connected to %s (%s)", dev.name, address)
            return True
        except Exception as e:
            logger.error("Failed to connect to %s: %s", address, e)
            return False

    def _connect_device_cli(self, address: str) -> bool:
        """Connect to device via bluetoothctl."""
        try:
            result = subprocess.run(
                ["bluetoothctl", "connect", address],
                capture_output=True, text=True, timeout=30
            )
            success = "Connection successful" in result.stdout
            if success and address in self._devices:
                self._devices[address].connected = True
            return success
        except Exception as e:
            logger.error("CLI connect failed: %s", e)
            return False

    def _disconnect_device_dbus(self, address: str) -> bool:
        """Disconnect device via D-Bus."""
        import dbus

        dev = self._devices.get(address)
        if not dev or not dev.path:
            return False

        try:
            device = dbus.Interface(
                self._bus.get_object(BLUEZ_SERVICE, dev.path),
                BLUEZ_DEVICE_IFACE
            )
            device.Disconnect()
            dev.connected = False
            dev.audio_connected = False
            logger.info("Disconnected %s (%s)", dev.name, address)
            return True
        except Exception as e:
            logger.error("Failed to disconnect %s: %s", address, e)
            return False

    def _disconnect_device_cli(self, address: str) -> bool:
        """Disconnect device via bluetoothctl."""
        try:
            result = subprocess.run(
                ["bluetoothctl", "disconnect", address],
                capture_output=True, text=True, timeout=10
            )
            success = "Successful disconnected" in result.stdout
            if success and address in self._devices:
                self._devices[address].connected = False
                self._devices[address].audio_connected = False
            return success
        except Exception as e:
            logger.error("CLI disconnect failed: %s", e)
            return False

    def _trust_device_dbus(self, address: str) -> bool:
        """Trust device via D-Bus."""
        import dbus

        dev = self._devices.get(address)
        if not dev or not dev.path:
            return False

        try:
            props = dbus.Interface(
                self._bus.get_object(BLUEZ_SERVICE, dev.path),
                "org.freedesktop.DBus.Properties"
            )
            props.Set(BLUEZ_DEVICE_IFACE, "Trusted", dbus.Boolean(True))
            dev.trusted = True
            logger.info("Trusted %s (%s)", dev.name, address)
            return True
        except Exception as e:
            logger.error("Failed to trust %s: %s", address, e)
            return False

    def _trust_device_cli(self, address: str) -> bool:
        """Trust device via bluetoothctl."""
        try:
            result = subprocess.run(
                ["bluetoothctl", "trust", address],
                capture_output=True, text=True, timeout=10
            )
            success = "trust succeeded" in result.stdout.lower()
            if success and address in self._devices:
                self._devices[address].trusted = True
            return success
        except Exception as e:
            logger.error("CLI trust failed: %s", e)
            return False
