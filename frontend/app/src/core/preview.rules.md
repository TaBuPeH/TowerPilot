# Preview rules
> **Status:** Active
> **Created:** 2026-10-23
> **Updated:** 2026-10-23

`preview.ts` owns viewer preferences, presets, session URLs, status interpretation,
source identity, effective FPS and freshness verdicts (F6-13 PV1–PV8).
Availability describes prerequisites; a current confirmed transport is required
separately. Auto is explicit in the URL. ADB caps effective FPS at four.

Receiving requires a decoded advancing successful sequence and a matching fresh
active status with a positive successful count and no error. The displayed
part identifies its source. Receipt age is measured locally; source age is the
estimate at server encode, and server report age is separate. Missing values
remain unknown. Freshness expires after `max(3, 2 / target_fps)` seconds.

The session store owns the session. It attaches by itself while the viewer is open and the
status read reports the emulator online (the same connected signal as the header chip); it never
starts, boots, wakes or reconnects the emulator, MuMu or adb. Collapse, hidden document, offline,
route leave, context change and unmount abort all work, clear pixels and revoke object URLs, and
the session reattaches when the condition clears. The person's Stop is the one choice that holds:
nothing restarts until the person presses Start, for this page session (memory only; a reload
starts again). A failed session is retried with backoff (3 s, doubling to 60 s, reset by a frame
or by the device coming back) at most once per backoff window, driven by dashboard reads and
never by its own timer, and only while the device reads online. A context epoch and a new stream
ID reject late answers. Preferences persist in local storage; the running state never does.
There is no still-image fallback.
