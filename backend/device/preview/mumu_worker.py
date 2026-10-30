"""Owned MuMu preview helper: vendor DLL failures cannot crash the dashboard."""

from __future__ import annotations

import ctypes
import json
from pathlib import Path
import socket
import struct
import sys

import cv2
import numpy as np
import numpy.typing as npt

from device.preview.mumu import _dimensions, _dll_path


class NativeCapture:
    """Four read-only renderer exports, using the public caller-owned-buffer ABI."""

    def __init__(self, install_dir: str, index: int, package: str, display_id: int | None) -> None:
        self.handle = 0
        self.dll = ctypes.CDLL(str(_dll_path(Path(install_dir))))
        self.dll.nemu_connect.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
        self.dll.nemu_connect.restype = ctypes.c_int
        self.dll.nemu_get_display_id.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        self.dll.nemu_get_display_id.restype = ctypes.c_int
        self.dll.nemu_capture_display.argtypes = [
            ctypes.c_int, ctypes.c_uint, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ubyte),
        ]
        self.dll.nemu_capture_display.restype = ctypes.c_int
        self.dll.nemu_disconnect.argtypes = [ctypes.c_int]
        self.dll.nemu_disconnect.restype = None
        self.width = ctypes.c_int()
        self.height = ctypes.c_int()
        try:
            self.handle = self.dll.nemu_connect(install_dir, index)
            if self.handle <= 0:
                raise RuntimeError(f"nemu_connect returned {self.handle}")
            self.display_id = display_id
            if self.display_id is None:
                self.display_id = self.dll.nemu_get_display_id(self.handle, package.encode("utf-8"), 0)
            if self.display_id < 0:
                raise RuntimeError(f"nemu_get_display_id returned {self.display_id}")
            result = self.dll.nemu_capture_display(
                self.handle, self.display_id, 0, ctypes.byref(self.width), ctypes.byref(self.height), None,
            )
            if result != 0:
                raise RuntimeError(f"MuMu dimension query returned {result}")
            self.dimensions = _dimensions(self.width.value, self.height.value)
            width, height = self.dimensions
            self.buffer_size = width * height * 4
            self.buffer = (ctypes.c_ubyte * self.buffer_size)()
            self.rgba = np.ctypeslib.as_array(self.buffer).reshape(height, width, 4)
            self.bgr = np.empty((height, width, 3), dtype=np.uint8)
        except Exception:
            self.close()
            raise

    def read(self) -> npt.NDArray[np.uint8]:
        result = self.dll.nemu_capture_display(
            self.handle, self.display_id, self.buffer_size,
            ctypes.byref(self.width), ctypes.byref(self.height), self.buffer,
        )
        if result != 0:
            raise RuntimeError(f"nemu_capture_display returned {result}")
        if (self.width.value, self.height.value) != self.dimensions:
            raise RuntimeError("MuMu capture resolution changed; reconnect preview")
        cv2.cvtColor(self.rgba, cv2.COLOR_RGBA2BGR, dst=self.bgr)
        cv2.flip(self.bgr, 0, dst=self.bgr)
        return self.bgr

    def close(self) -> None:
        if self.handle > 0:
            handle, self.handle = self.handle, 0
            self.dll.nemu_disconnect(handle)


def _send_message(connection: socket.socket, message: dict) -> None:
    payload = json.dumps(message).encode("utf-8")
    connection.sendall(struct.pack("!I", len(payload)) + payload)


def serve(connection: socket.socket, token: str, install_dir: str, index: int,
          package: str, display_id: int | None) -> None:
    native = None
    initialized = False
    try:
        native = NativeCapture(install_dir, index, package, display_id)
        width, height = native.dimensions
        _send_message(connection, {"token": token, "width": width, "height": height})
        initialized = True
        while True:
            request = connection.recv(1)
            if not request:
                return
            if request != b"F":
                raise ValueError("invalid MuMu preview request")
            frame = native.read()
            connection.sendall(b"F")
            connection.sendall(memoryview(frame).cast("B"))
    except Exception as exc:
        try:
            if initialized:
                connection.sendall(b"E")
            _send_message(connection, {"token": token, "error": f"{type(exc).__name__}: {exc}"})
        except OSError:
            # The owner may already have closed its timed-out connection.
            pass
    finally:
        connection.close()
        if native is not None:
            native.close()


def main() -> None:
    if sys.platform == "win32":
        # Apply only inside this owned helper: vendor crashes must not open
        # Windows error dialogs or leave the dashboard waiting for a person.
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    port, token, install_dir, index, package, *display = sys.argv[1:]
    with socket.create_connection(("127.0.0.1", int(port)), timeout=8.0) as connection:
        # No idle deadline: an inactive preview owns no work and waits for its owner.
        connection.settimeout(None)
        serve(connection, token, install_dir, int(index), package, int(display[0]) if display else None)


if __name__ == "__main__":
    main()
