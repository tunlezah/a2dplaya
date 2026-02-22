# A2DPlaya

Bluetooth A2DP Sink to Chromecast/AirPlay Audio Bridge.

A self-contained application that acts as a Bluetooth A2DP audio sink, captures audio from connected Bluetooth devices (phones, record players, tape players, etc.), and streams it to Google Chromecast and Apple AirPlay endpoints. Includes a web interface for control, visualization, and browser-based audio playback.

## Features

- **Bluetooth A2DP Sink** - Receive audio from any Bluetooth device
- **Chromecast Output** - Stream to any Chromecast/Google Home device
- **AirPlay Output** - Stream to AirPlay receivers (HomePod, Apple TV, etc.)
- **Line-In Input** - Capture from 3.5mm/RCA input (record players, tape decks)
- **Web UI** - Mobile-friendly control panel with 10-band EQ visualizer
- **Browser Playback** - Listen directly in the browser via Web Audio API
- **REST API** - Full API for integration and automation

## Architecture

```
Input Sources          Pipeline              Output Targets
┌─────────────┐    ┌──────────────┐    ┌─────────────────┐
│  Bluetooth   │───>│              │───>│   Chromecast    │
│  A2DP Sink   │    │   Audio      │    │   (HTTP Stream) │
└─────────────┘    │   Pipeline   │    ├─────────────────┤
┌─────────────┐    │              │───>│   AirPlay       │
│  Line-In     │───>│  Resample    │    │   (RAOP/RTP)    │
│  (ALSA)      │    │  Volume      │    ├─────────────────┤
└─────────────┘    │  FFT/EQ      │───>│   Web Browser   │
                   │              │    │   (WebSocket)    │
                   └──────────────┘    └─────────────────┘
                          │
                   ┌──────────────┐
                   │   Web Server  │
                   │   (Flask)     │
                   │   REST API    │
                   │   EQ Viz SSE  │
                   └──────────────┘
```

## Quick Start

```bash
# Install
chmod +x install.sh
./install.sh

# Run
source venv/bin/activate
python -m a2dplaya

# Open web UI
# http://<device-ip>:8080
```

## Usage

```bash
# Basic usage
python -m a2dplaya

# Custom port
python -m a2dplaya --port 9090

# With line-in input
python -m a2dplaya --line-in hw:1,0

# Debug mode
python -m a2dplaya --debug

# Specific Bluetooth adapter
python -m a2dplaya --bt-adapter hci1
```

## Configuration

Configuration is stored in `~/.config/a2dplaya/config.json`. Environment variables override config file values:

| Variable | Description | Default |
|---|---|---|
| `A2DPLAYA_WEB_PORT` | Web server port | 8080 |
| `A2DPLAYA_WEB_HOST` | Web server bind address | 0.0.0.0 |
| `A2DPLAYA_AUDIO_VOLUME` | Default volume (0.0-1.0) | 1.0 |
| `A2DPLAYA_CAST_STREAM_PORT` | Chromecast stream port | 8099 |
| `A2DPLAYA_BT_ADAPTER` | Bluetooth adapter | auto |
| `A2DPLAYA_LOG_LEVEL` | Log level | INFO |

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/status` | Full system status |
| GET/POST | `/api/volume` | Get/set volume |
| GET | `/api/bluetooth/devices` | List BT devices |
| POST | `/api/bluetooth/scan` | Start BT scan |
| POST | `/api/bluetooth/pair` | Pair with device |
| POST | `/api/bluetooth/connect` | Connect to device |
| POST | `/api/cast/discover` | Find Chromecasts |
| POST | `/api/cast/play` | Start casting |
| POST | `/api/cast/stop` | Stop casting |
| POST | `/api/airplay/discover` | Find AirPlay devices |
| POST | `/api/airplay/play` | Start AirPlay stream |
| GET | `/api/eq/stream` | SSE EQ data (30fps) |

## Requirements

- Python 3.8+
- Linux with BlueZ 5.x
- Bluetooth adapter
- Optional: PulseAudio/PipeWire, ALSA

## License

MIT
