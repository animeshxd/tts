import ctypes
import ctypes.util
from typing import Optional, Union

# PulseAudio sample formats and constants
PA_SAMPLE_U8 = 0
PA_SAMPLE_ALAW = 1
PA_SAMPLE_ULAW = 2
PA_SAMPLE_S16LE = 3
PA_SAMPLE_S16BE = 4
PA_SAMPLE_FLOAT32LE = 5
PA_SAMPLE_FLOAT32BE = 6
PA_SAMPLE_S32LE = 7
PA_SAMPLE_S32BE = 8
PA_SAMPLE_S24LE = 9
PA_SAMPLE_S24BE = 10
PA_SAMPLE_S24_32LE = 11
PA_SAMPLE_S24_32BE = 12

PA_STREAM_NODIRECTION = 0
PA_STREAM_PLAYBACK = 1
PA_STREAM_RECORD = 2
PA_STREAM_UPLOAD = 3


class pa_sample_spec(ctypes.Structure):
    _fields_ = [
        ("format", ctypes.c_int),
        ("rate", ctypes.c_uint32),
        ("channels", ctypes.c_uint8),
    ]


def _load_pulse_libraries():
    simple_lib_name = ctypes.util.find_library("pulse-simple") or "libpulse-simple.so.0"
    pulse_lib_name = ctypes.util.find_library("pulse") or "libpulse.so.0"

    libpulse_simple = ctypes.CDLL(simple_lib_name)
    libpulse = ctypes.CDLL(pulse_lib_name)

    # pa_simple_new
    libpulse_simple.pa_simple_new.argtypes = [
        ctypes.c_char_p,  # server
        ctypes.c_char_p,  # name
        ctypes.c_int,     # dir
        ctypes.c_char_p,  # dev
        ctypes.c_char_p,  # stream_name
        ctypes.POINTER(pa_sample_spec),  # ss
        ctypes.c_void_p,  # map
        ctypes.c_void_p,  # attr
        ctypes.POINTER(ctypes.c_int),  # error
    ]
    libpulse_simple.pa_simple_new.restype = ctypes.c_void_p

    # pa_simple_free
    libpulse_simple.pa_simple_free.argtypes = [ctypes.c_void_p]
    libpulse_simple.pa_simple_free.restype = None

    # pa_simple_write
    libpulse_simple.pa_simple_write.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_int),
    ]
    libpulse_simple.pa_simple_write.restype = ctypes.c_int

    # pa_simple_drain
    libpulse_simple.pa_simple_drain.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    libpulse_simple.pa_simple_drain.restype = ctypes.c_int

    # pa_simple_flush
    libpulse_simple.pa_simple_flush.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    libpulse_simple.pa_simple_flush.restype = ctypes.c_int

    # pa_strerror
    libpulse.pa_strerror.argtypes = [ctypes.c_int]
    libpulse.pa_strerror.restype = ctypes.c_char_p

    return libpulse_simple, libpulse


_libpulse_simple, _libpulse = _load_pulse_libraries()


def pa_strerror(error_code: int) -> str:
    """Return a human readable error message for the given PulseAudio error code."""
    res = _libpulse.pa_strerror(error_code)
    return res.decode("utf-8", errors="replace") if res else f"Unknown error ({error_code})"


class PulseAudioPlayback:
    """
    PulseAudio Simple API wrapper for audio playback using ctypes FFI.
    """

    def __init__(
        self,
        device: Optional[str] = None,
        rate: int = 24000,
        channels: int = 1,
        format: int = PA_SAMPLE_FLOAT32LE,
        app_name: str = "tts",
        stream_name: str = "playback",
    ):
        self.device = device
        self.rate = rate
        self.channels = channels
        self.format = format
        self.app_name = app_name
        self.stream_name = stream_name
        self._handle = None

        dev_bytes: Optional[bytes] = None
        if device and device.strip() and device.strip().lower() not in ("default", "pipewire", "pulse"):
            dev_bytes = device.strip().encode("utf-8")

        ss = pa_sample_spec(format=format, rate=rate, channels=channels)
        err = ctypes.c_int(0)

        self._handle = _libpulse_simple.pa_simple_new(
            None,
            app_name.encode("utf-8"),
            PA_STREAM_PLAYBACK,
            dev_bytes,
            stream_name.encode("utf-8"),
            ctypes.byref(ss),
            None,
            None,
            ctypes.byref(err),
        )

        if not self._handle:
            raise RuntimeError(f"Failed to open PulseAudio stream: {pa_strerror(err.value)}")

    def write(self, data: Union[bytes, bytearray, memoryview]) -> None:
        """Write audio byte buffer to the stream."""
        if not self._handle:
            raise RuntimeError("PulseAudio stream is closed.")

        if not isinstance(data, (bytes, bytearray, memoryview)):
            data = bytes(data)

        err = ctypes.c_int(0)
        ret = _libpulse_simple.pa_simple_write(
            self._handle,
            data,
            len(data),
            ctypes.byref(err),
        )
        if ret < 0:
            raise RuntimeError(f"pa_simple_write failed: {pa_strerror(err.value)}")

    def drain(self) -> None:
        """Wait until all written data has finished playing."""
        if not self._handle:
            return
        err = ctypes.c_int(0)
        ret = _libpulse_simple.pa_simple_drain(self._handle, ctypes.byref(err))
        if ret < 0:
            raise RuntimeError(f"pa_simple_drain failed: {pa_strerror(err.value)}")

    def flush(self) -> None:
        """Flush playback buffer discarding unsent audio data."""
        if not self._handle:
            return
        err = ctypes.c_int(0)
        ret = _libpulse_simple.pa_simple_flush(self._handle, ctypes.byref(err))
        if ret < 0:
            raise RuntimeError(f"pa_simple_flush failed: {pa_strerror(err.value)}")

    def close(self) -> None:
        """Close the stream and free PulseAudio resources."""
        if self._handle:
            _libpulse_simple.pa_simple_free(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        self.close()
