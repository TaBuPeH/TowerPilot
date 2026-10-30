"""The wave and screen readings of ONE frame, each taken at most once, for the owners that look at the same frame
in one pass of the gem collector (FOCUS #11 step 2).

A reading is reused only for the identical frame object: the loop may carry a newer frame after a guard or a fresh
capture, and any other frame is read directly, as before. No reading is changed here, only repeated work is skipped."""
from vision import screen, wave_reader

_UNREAD = object()


class FrameReads:
    def __init__(self, frame):
        self.frame = frame
        self._wave = self._screen = _UNREAD

    def wave(self, frame):
        """`wave_reader.read_wave(frame)`, read once when `frame` is the frame these reads belong to."""
        if frame is not self.frame:
            return wave_reader.read_wave(frame)
        if self._wave is _UNREAD:
            self._wave = wave_reader.read_wave(frame)
        return self._wave

    def screen(self, frame):
        """`screen.identify(frame)`, read once when `frame` is the frame these reads belong to."""
        if frame is not self.frame:
            return screen.identify(frame)
        if self._screen is _UNREAD:
            self._screen = screen.identify(frame)
        return self._screen
