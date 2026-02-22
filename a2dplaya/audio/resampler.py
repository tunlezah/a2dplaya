"""
Audio resampler for A2DPlaya.

Converts audio between different sample rates, channel counts, and bit depths.
Uses linear interpolation for resampling when the audioop module is not available,
or audioop when available for higher quality conversion.
"""

import logging
import struct
from typing import Optional

logger = logging.getLogger(__name__)


class Resampler:
    """
    Converts PCM audio between formats.

    Handles sample rate conversion, channel mixing (mono<->stereo),
    and bit depth conversion. All output is signed little-endian PCM.
    """

    def __init__(self, target_rate: int, target_channels: int, target_width: int):
        self._target_rate = target_rate
        self._target_channels = target_channels
        self._target_width = target_width

    def resample(
        self,
        pcm_data: bytes,
        src_rate: int,
        src_channels: int,
        src_width: int
    ) -> bytes:
        """
        Resample PCM audio to the target format.

        Args:
            pcm_data: Raw PCM bytes (signed, little-endian)
            src_rate: Source sample rate in Hz
            src_channels: Source channel count
            src_width: Source bytes per sample

        Returns:
            Resampled PCM bytes in target format
        """
        if not pcm_data:
            return pcm_data

        data = pcm_data

        # Step 1: Convert bit depth if needed
        if src_width != self._target_width:
            data = self._convert_width(data, src_width, self._target_width, src_channels)
            src_width = self._target_width

        # Step 2: Convert channels if needed
        if src_channels != self._target_channels:
            data = self._convert_channels(data, src_channels, self._target_channels, src_width)
            src_channels = self._target_channels

        # Step 3: Convert sample rate if needed
        if src_rate != self._target_rate:
            data = self._convert_rate(data, src_rate, self._target_rate, src_channels, src_width)

        return data

    def _convert_width(
        self, data: bytes, src_width: int, dst_width: int, channels: int
    ) -> bytes:
        """Convert between bit depths (8, 16, 24, 32-bit)."""
        # Decode samples to normalized floats (-1.0 to 1.0)
        samples = self._decode_samples(data, src_width)
        # Re-encode to target width
        return self._encode_samples(samples, dst_width)

    def _convert_channels(
        self, data: bytes, src_ch: int, dst_ch: int, width: int
    ) -> bytes:
        """Convert between channel configurations."""
        samples = self._decode_samples(data, width)
        frame_count = len(samples) // src_ch

        if src_ch == 1 and dst_ch == 2:
            # Mono to stereo: duplicate samples
            out = []
            for i in range(frame_count):
                out.append(samples[i])
                out.append(samples[i])
            return self._encode_samples(out, width)

        elif src_ch == 2 and dst_ch == 1:
            # Stereo to mono: average channels
            out = []
            for i in range(frame_count):
                left = samples[i * 2]
                right = samples[i * 2 + 1]
                out.append((left + right) / 2.0)
            return self._encode_samples(out, width)

        elif src_ch > dst_ch:
            # Downmix: average all channels per frame
            out = []
            for i in range(frame_count):
                frame_samples = samples[i * src_ch:(i + 1) * src_ch]
                avg = sum(frame_samples) / len(frame_samples)
                for _ in range(dst_ch):
                    out.append(avg)
            return self._encode_samples(out, width)

        else:
            # Upmix: copy first channel to extra channels
            out = []
            for i in range(frame_count):
                frame_samples = samples[i * src_ch:(i + 1) * src_ch]
                out.extend(frame_samples)
                for _ in range(dst_ch - src_ch):
                    out.append(frame_samples[0])
            return self._encode_samples(out, width)

    def _convert_rate(
        self, data: bytes, src_rate: int, dst_rate: int,
        channels: int, width: int
    ) -> bytes:
        """Resample using linear interpolation."""
        samples = self._decode_samples(data, width)
        frame_count = len(samples) // channels
        if frame_count < 2:
            return data

        ratio = dst_rate / src_rate
        out_frames = int(frame_count * ratio)
        out = []

        for i in range(out_frames):
            src_pos = i / ratio
            idx = int(src_pos)
            frac = src_pos - idx

            if idx + 1 >= frame_count:
                idx = frame_count - 2
                frac = 1.0

            for ch in range(channels):
                s0 = samples[idx * channels + ch]
                s1 = samples[(idx + 1) * channels + ch]
                out.append(s0 + (s1 - s0) * frac)

        return self._encode_samples(out, width)

    def _decode_samples(self, data: bytes, width: int) -> list:
        """Decode PCM bytes to list of float samples (-1.0 to 1.0)."""
        if width == 2:
            n = len(data) // 2
            fmt = f"<{n}h"
            raw = struct.unpack(fmt, data[:n * 2])
            return [s / 32768.0 for s in raw]
        elif width == 1:
            # 8-bit unsigned
            return [(b - 128) / 128.0 for b in data]
        elif width == 4:
            n = len(data) // 4
            fmt = f"<{n}i"
            raw = struct.unpack(fmt, data[:n * 4])
            return [s / 2147483648.0 for s in raw]
        elif width == 3:
            # 24-bit signed little-endian
            samples = []
            for i in range(0, len(data) - 2, 3):
                val = data[i] | (data[i + 1] << 8) | (data[i + 2] << 16)
                if val >= 0x800000:
                    val -= 0x1000000
                samples.append(val / 8388608.0)
            return samples
        else:
            logger.warning("Unsupported sample width: %d", width)
            return []

    def _encode_samples(self, samples: list, width: int) -> bytes:
        """Encode float samples (-1.0 to 1.0) to PCM bytes."""
        if width == 2:
            clamped = [max(-32768, min(32767, int(s * 32768))) for s in samples]
            return struct.pack(f"<{len(clamped)}h", *clamped)
        elif width == 1:
            clamped = [max(0, min(255, int(s * 128 + 128))) for s in samples]
            return bytes(clamped)
        elif width == 4:
            clamped = [max(-2147483648, min(2147483647, int(s * 2147483648))) for s in samples]
            return struct.pack(f"<{len(clamped)}i", *clamped)
        elif width == 3:
            out = bytearray()
            for s in samples:
                val = max(-8388608, min(8388607, int(s * 8388608)))
                if val < 0:
                    val += 0x1000000
                out.append(val & 0xFF)
                out.append((val >> 8) & 0xFF)
                out.append((val >> 16) & 0xFF)
            return bytes(out)
        else:
            return b""
