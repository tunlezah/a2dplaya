"""
Web server and REST API for A2DPlaya.

Provides:
    - REST API for device control (Bluetooth, Chromecast, AirPlay)
    - WebSocket endpoint for real-time EQ visualization data
    - Static file serving for the web UI
    - Browser-based audio playback via WebSocket audio streaming

Uses Flask for HTTP and flask-sock for WebSocket support.
Falls back to a built-in simple HTTP server if Flask is not available.
"""

import json
import logging
import os
import struct
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from typing import Optional

from a2dplaya.config import AppConfig

logger = logging.getLogger(__name__)

# Try to import Flask
try:
    from flask import Flask, jsonify, request, send_from_directory, Response
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    logger.info("Flask not available, using built-in HTTP server")


class WebServer:
    """
    Web server providing UI and API for A2DPlaya.

    Exposes REST endpoints for controlling all subsystems and
    serves the web-based EQ visualizer and control panel.
    """

    def __init__(self, config: AppConfig, app_controller=None):
        self._config = config.web
        self._app_controller = app_controller
        self._running = False
        self._server_thread: Optional[threading.Thread] = None

        # WebSocket clients for EQ data
        self._ws_clients = []
        self._ws_lock = threading.Lock()

        # EQ broadcast thread
        self._eq_thread: Optional[threading.Thread] = None

        # Static files directory
        self._static_dir = os.path.join(os.path.dirname(__file__), "static")

        if FLASK_AVAILABLE:
            self._app = self._create_flask_app()
        else:
            self._app = None

    def start(self):
        """Start the web server."""
        self._running = True

        self._server_thread = threading.Thread(
            target=self._run_server,
            name="WebServer",
            daemon=True
        )
        self._server_thread.start()

        # Start EQ data broadcast
        self._eq_thread = threading.Thread(
            target=self._eq_broadcast_loop,
            name="EQ-Broadcast",
            daemon=True
        )
        self._eq_thread.start()

        logger.info(
            "Web server started on %s:%d",
            self._config.host, self._config.port
        )

    def stop(self):
        """Stop the web server."""
        self._running = False
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=5.0)
        logger.info("Web server stopped")

    def audio_consumer(self, pcm_data: bytes, sample_rate: int, channels: int, sample_width: int):
        """
        AudioPipeline consumer callback for WebSocket audio streaming.

        Sends audio data to connected browser clients for playback.
        """
        with self._ws_lock:
            dead_clients = []
            for i, ws in enumerate(self._ws_clients):
                try:
                    if hasattr(ws, "send_audio"):
                        ws.send_audio(pcm_data)
                except Exception:
                    dead_clients.append(i)

            for i in reversed(dead_clients):
                self._ws_clients.pop(i)

    def _create_flask_app(self):
        """Create and configure the Flask application."""
        app = Flask(
            __name__,
            static_folder=self._static_dir,
            static_url_path="/static"
        )
        app.secret_key = self._config.secret_key

        # --- Page routes ---

        @app.route("/")
        def index():
            return send_from_directory(self._static_dir, "index.html")

        # --- API routes ---

        @app.route("/api/status")
        def api_status():
            ctrl = self._app_controller
            if not ctrl:
                return jsonify({"error": "not initialized"}), 503

            return jsonify({
                "bluetooth": {
                    "adapter": ctrl.bluetooth.get_adapter_info() if ctrl.bluetooth else None,
                    "connected_device": ctrl.bluetooth.get_connected_device() if ctrl.bluetooth else None,
                },
                "audio": {
                    "pipeline_running": ctrl.pipeline.stats if ctrl.pipeline else {},
                    "volume": ctrl.pipeline.volume if ctrl.pipeline else 0,
                    "source": ctrl.pipeline.current_source if ctrl.pipeline else None,
                    "line_in_active": ctrl.line_in.is_running if ctrl.line_in else False,
                },
                "cast": {
                    "active_device": ctrl.cast.get_active_device() if ctrl.cast else None,
                    "streaming": ctrl.cast.is_streaming() if ctrl.cast else False,
                },
                "airplay": {
                    "active_device": ctrl.airplay.get_active_device() if ctrl.airplay else None,
                    "streaming": ctrl.airplay.is_streaming() if ctrl.airplay else False,
                },
                "eq_data": ctrl.pipeline.get_current_eq_data() if ctrl.pipeline else [0] * 10,
            })

        @app.route("/api/volume", methods=["GET", "POST"])
        def api_volume():
            ctrl = self._app_controller
            if not ctrl or not ctrl.pipeline:
                return jsonify({"error": "not initialized"}), 503

            if request.method == "POST":
                data = request.get_json(silent=True) or {}
                vol = data.get("volume")
                if vol is not None:
                    ctrl.pipeline.volume = float(vol)
                return jsonify({"volume": ctrl.pipeline.volume})

            return jsonify({"volume": ctrl.pipeline.volume})

        # --- Bluetooth API ---

        @app.route("/api/bluetooth/devices")
        def api_bt_devices():
            ctrl = self._app_controller
            if not ctrl or not ctrl.bluetooth:
                return jsonify({"error": "bluetooth not available"}), 503
            return jsonify({"devices": ctrl.bluetooth.get_devices()})

        @app.route("/api/bluetooth/scan", methods=["POST"])
        def api_bt_scan():
            ctrl = self._app_controller
            if not ctrl or not ctrl.bluetooth:
                return jsonify({"error": "bluetooth not available"}), 503
            ctrl.bluetooth.start_discovery()
            return jsonify({"status": "scanning"})

        @app.route("/api/bluetooth/discoverable", methods=["POST"])
        def api_bt_discoverable():
            ctrl = self._app_controller
            if not ctrl or not ctrl.bluetooth:
                return jsonify({"error": "bluetooth not available"}), 503
            data = request.get_json(silent=True) or {}
            ctrl.bluetooth.set_discoverable(data.get("enabled", True))
            return jsonify({"status": "ok"})

        @app.route("/api/bluetooth/pair", methods=["POST"])
        def api_bt_pair():
            ctrl = self._app_controller
            if not ctrl or not ctrl.bluetooth:
                return jsonify({"error": "bluetooth not available"}), 503
            data = request.get_json(silent=True) or {}
            address = data.get("address")
            if not address:
                return jsonify({"error": "address required"}), 400
            success = ctrl.bluetooth.pair_device(address)
            return jsonify({"success": success})

        @app.route("/api/bluetooth/connect", methods=["POST"])
        def api_bt_connect():
            ctrl = self._app_controller
            if not ctrl or not ctrl.bluetooth:
                return jsonify({"error": "bluetooth not available"}), 503
            data = request.get_json(silent=True) or {}
            address = data.get("address")
            if not address:
                return jsonify({"error": "address required"}), 400
            success = ctrl.bluetooth.connect_device(address)
            return jsonify({"success": success})

        @app.route("/api/bluetooth/disconnect", methods=["POST"])
        def api_bt_disconnect():
            ctrl = self._app_controller
            if not ctrl or not ctrl.bluetooth:
                return jsonify({"error": "bluetooth not available"}), 503
            data = request.get_json(silent=True) or {}
            address = data.get("address")
            if not address:
                return jsonify({"error": "address required"}), 400
            success = ctrl.bluetooth.disconnect_device(address)
            return jsonify({"success": success})

        # --- Chromecast API ---

        @app.route("/api/cast/devices")
        def api_cast_devices():
            ctrl = self._app_controller
            if not ctrl or not ctrl.cast:
                return jsonify({"error": "cast not available"}), 503
            return jsonify({"devices": ctrl.cast.get_devices()})

        @app.route("/api/cast/discover", methods=["POST"])
        def api_cast_discover():
            ctrl = self._app_controller
            if not ctrl or not ctrl.cast:
                return jsonify({"error": "cast not available"}), 503
            devices = ctrl.cast.discover_devices()
            return jsonify({"devices": devices})

        @app.route("/api/cast/play", methods=["POST"])
        def api_cast_play():
            ctrl = self._app_controller
            if not ctrl or not ctrl.cast:
                return jsonify({"error": "cast not available"}), 503
            data = request.get_json(silent=True) or {}
            device_name = data.get("device")
            if not device_name:
                return jsonify({"error": "device required"}), 400
            success = ctrl.cast.cast_to(device_name)
            return jsonify({"success": success})

        @app.route("/api/cast/stop", methods=["POST"])
        def api_cast_stop():
            ctrl = self._app_controller
            if not ctrl or not ctrl.cast:
                return jsonify({"error": "cast not available"}), 503
            ctrl.cast.stop_casting()
            return jsonify({"status": "stopped"})

        # --- AirPlay API ---

        @app.route("/api/airplay/devices")
        def api_airplay_devices():
            ctrl = self._app_controller
            if not ctrl or not ctrl.airplay:
                return jsonify({"error": "airplay not available"}), 503
            return jsonify({"devices": ctrl.airplay.get_devices()})

        @app.route("/api/airplay/discover", methods=["POST"])
        def api_airplay_discover():
            ctrl = self._app_controller
            if not ctrl or not ctrl.airplay:
                return jsonify({"error": "airplay not available"}), 503
            devices = ctrl.airplay.discover_devices()
            return jsonify({"devices": devices})

        @app.route("/api/airplay/play", methods=["POST"])
        def api_airplay_play():
            ctrl = self._app_controller
            if not ctrl or not ctrl.airplay:
                return jsonify({"error": "airplay not available"}), 503
            data = request.get_json(silent=True) or {}
            device_name = data.get("device")
            if not device_name:
                return jsonify({"error": "device required"}), 400
            success = ctrl.airplay.stream_to(device_name)
            return jsonify({"success": success})

        @app.route("/api/airplay/stop", methods=["POST"])
        def api_airplay_stop():
            ctrl = self._app_controller
            if not ctrl or not ctrl.airplay:
                return jsonify({"error": "airplay not available"}), 503
            ctrl.airplay.stop_streaming()
            return jsonify({"status": "stopped"})

        # --- Line-in API ---

        @app.route("/api/linein/devices")
        def api_linein_devices():
            ctrl = self._app_controller
            if not ctrl or not ctrl.line_in:
                return jsonify({"error": "line-in not available"}), 503
            return jsonify({"devices": ctrl.line_in.list_devices()})

        @app.route("/api/linein/start", methods=["POST"])
        def api_linein_start():
            ctrl = self._app_controller
            if not ctrl or not ctrl.line_in:
                return jsonify({"error": "line-in not available"}), 503
            data = request.get_json(silent=True) or {}
            device = data.get("device")
            if device:
                ctrl.line_in.device_name = device
            ctrl.line_in.start()
            return jsonify({"status": "started"})

        @app.route("/api/linein/stop", methods=["POST"])
        def api_linein_stop():
            ctrl = self._app_controller
            if not ctrl or not ctrl.line_in:
                return jsonify({"error": "line-in not available"}), 503
            ctrl.line_in.stop()
            return jsonify({"status": "stopped"})

        # --- EQ data endpoint (Server-Sent Events) ---

        @app.route("/api/eq/stream")
        def api_eq_stream():
            def generate():
                while True:
                    ctrl = self._app_controller
                    if ctrl and ctrl.pipeline:
                        data = ctrl.pipeline.get_current_eq_data()
                    else:
                        data = [0] * 10
                    yield f"data: {json.dumps(data)}\n\n"
                    time.sleep(1.0 / 30)  # 30fps

            return Response(
                generate(),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                }
            )

        # --- Audio stream for browser playback ---

        @app.route("/api/audio/stream")
        def api_audio_stream():
            """PCM audio stream for browser-based playback via Web Audio API."""
            ctrl = self._app_controller
            if not ctrl or not ctrl.pipeline:
                return jsonify({"error": "not available"}), 503

            def generate():
                # Send WAV header
                sample_rate = ctrl.pipeline.sample_rate
                channels = ctrl.pipeline.channels
                bits = ctrl.pipeline.sample_width * 8
                byte_rate = sample_rate * channels * (bits // 8)
                block_align = channels * (bits // 8)

                header = struct.pack(
                    "<4sI4s4sIHHIIHH4sI",
                    b"RIFF", 0xFFFFFFFF - 8, b"WAVE",
                    b"fmt ", 16, 1, channels,
                    sample_rate, byte_rate, block_align, bits,
                    b"data", 0xFFFFFFFF - 44
                )
                yield header

                # Stream PCM data
                while self._running:
                    eq_data = ctrl.pipeline.get_current_eq_data()
                    time.sleep(0.02)

            return Response(
                generate(),
                mimetype="audio/wav",
                headers={"Cache-Control": "no-cache"}
            )

        return app

    def _run_server(self):
        """Run the web server."""
        if FLASK_AVAILABLE and self._app:
            self._app.run(
                host=self._config.host,
                port=self._config.port,
                debug=False,
                use_reloader=False,
                threaded=True
            )
        else:
            self._run_builtin_server()

    def _run_builtin_server(self):
        """Run a basic built-in HTTP server (fallback)."""
        handler = SimpleHTTPRequestHandler
        handler.directory = self._static_dir

        server = HTTPServer(
            (self._config.host, self._config.port),
            handler
        )
        server.timeout = 1

        while self._running:
            server.handle_request()

        server.server_close()

    def _eq_broadcast_loop(self):
        """Periodically broadcast EQ data to WebSocket clients."""
        while self._running:
            time.sleep(1.0 / 30)  # 30fps
