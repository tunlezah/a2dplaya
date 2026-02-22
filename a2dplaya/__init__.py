"""
A2DPlaya - Bluetooth A2DP Sink to Chromecast/AirPlay Audio Bridge

A self-contained application that acts as a Bluetooth A2DP audio sink,
captures audio from connected Bluetooth devices (phones, record players,
tape players, etc.), and streams it to Google Chromecast and Apple AirPlay
endpoints. Includes a web interface for control, visualization, and
browser-based audio playback.

Architecture:
    - BluetoothManager: Manages BlueZ D-Bus interactions for A2DP sink
    - AudioPipeline: Captures, resamples, and routes audio data
    - CastManager: Discovers and streams to Chromecast devices
    - AirPlayManager: Discovers and streams to AirPlay devices
    - WebServer: Flask-based web interface and API
    - EQVisualizer: FFT-based 10-band equalizer visualization
"""

__version__ = "1.0.0"
__author__ = "A2DPlaya Project"
