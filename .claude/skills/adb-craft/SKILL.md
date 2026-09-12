---
name: adb-craft
description: ADB device control for the autopilot — connection/state recovery, wait-for-device and boot gating, input injection (tap/swipe/text/keyevent), screenshots and UI dumps, reliability patterns, and a command quick-reference. Execution goes through autopilot/adbclient.py, never subprocess adb.exe.
---

<!-- imported 2026-08-18, scrubbed and merged from:
     https://raw.githubusercontent.com/skydoves/android-testing-skills/main/adb/devices/connecting-to-devices/SKILL.md
     https://raw.githubusercontent.com/skydoves/android-testing-skills/main/adb/control/injecting-input-and-state/SKILL.md
     https://raw.githubusercontent.com/skydoves/android-testing-skills/main/adb/automation/scripting-adb-for-ci/SKILL.md
     https://raw.githubusercontent.com/httprunner/skills/main/android-adb/SKILL.md -->

# ADB Craft

**Project rule: this repo does NOT shell out to `adb.exe`.** All device I/O goes through the adb-server socket client `autopilot/adbclient.py` — `shell(serial, cmd)`, `exec_out(serial, cmd)`, `alive(serial)`, `reconnect(serial)`. Anything below written as `adb -s SERIAL shell <cmd>` maps to `adbclient.shell(serial, "<cmd>")`; `adb exec-out <cmd>` maps to `adbclient.exec_out`. Host-side subcommands (forward, install, kill-server, wait-for-*) are not wrapped — extend adbclient if needed rather than spawning adb.

## Connection & state-waits

| State | Meaning | Recovery |
|---|---|---|
| `device` | Online. Does **not** imply boot complete — gate on `sys.boot_completed`. | n/a |
| `offline` | Transport up but adbd not talking (suspend/resume, hubs). | `adb reconnect offline` / `adbclient.reconnect(serial)`. |
| `unauthorized` | Host RSA key not accepted. | Unlock device, tap Allow. Never delete `~/.android/adbkey*` to "fix" it. |
| `connecting` | Transient TLS handshake (wireless). | Wait. |

- Selectors when multiple devices attached: `-s <serial>` (or `$ANDROID_SERIAL`), `-d` single USB, `-e` single TCP/emulator, `-t <transport_id>` from `adb devices -l`.
- **`-t` is transport ID, NOT a timeout.** adb has no per-command timeout flag at all.
- Wait syntax is `wait-for[-TRANSPORT]-STATE` with TRANSPORT in {usb, local, any} and STATE in {device, recovery, rescue, sideload, bootloader, disconnect}. `wait-for-device-online` does not exist.
- Transport-up ≠ booted. Boot gate:

```bash
adb wait-for-device
until [ "$(adb shell getprop sys.boot_completed | tr -d '\r')" = "1" ]; do sleep 1; done
```

- Reboot flow: `reboot` → `wait-for-disconnect` → `wait-for-device` → boot poll.
- Ready-device filter for scripts: `adb devices | awk '$2=="device"{print $1}'`.

## Input injection

```bash
input tap X Y
input swipe X1 Y1 X2 Y2 [ms]        # default ~300 ms
input swipe X Y X Y 1000            # long-press = swipe in place with duration
input draganddrop X1 Y1 X2 Y2 [ms]  # real drag gesture (DOWN→MOVE→UP), not same stream as swipe
input keyevent KEYCODE_BACK         # mnemonics or numeric codes
input text 'hello%sworld'           # spaces MUST be %s; metacharacters need double (host+device) escaping;
                                    # non-ASCII/unicode is unreliable through input text
```

- Confirm resolution before coordinate actions: `wm size`.
- Wake sequence: `input keyevent KEYCODE_WAKEUP` (224) then `input keyevent 82` to dismiss the AOSP keyguard.
- Common keycodes: HOME 3, BACK 4, POWER 26, TAB 61, ENTER 66, DEL 67, MENU 82, APP_SWITCH 187, SLEEP 223, WAKEUP 224.
- Deterministic UI — set all three animation scales to 0 (restore afterwards):
  `settings put global window_animation_scale 0`, same for `transition_animation_scale`, `animator_duration_scale`.
- App state: `pm clear <pkg>` kills the process AND wipes data/cache/runtime permissions; `am force-stop <pkg>` only kills (data intact — enough for a cold start, not a reset).
- Launch: `am start -S -W -n pkg/.Activity` (`-S` force-stop first, `-W` block until launch completes). Extras: `--es key str --ez key bool --ei key int`. Or by package: `monkey -p <pkg> -c android.intent.category.LAUNCHER 1`.
- `svc wifi/data/bluetooth` silently no-op on API 30+ (exit 0, nothing happens). Use `cmd wifi set-wifi-enabled enabled|disabled`, `cmd connectivity airplane-mode enable|disable`; verify with `cmd wifi status`.
- `wm size <WxH>` / `wm density <dpi>` overrides recreate the foreground Activity; treat as between-run knobs and always `wm size reset` / `wm density reset` afterwards.

## Capture & shell

```bash
adb exec-out screencap -p > screen.png            # PNG bytes straight to host — the autopilot's screenshot path
adb shell uiautomator dump /sdcard/window_dump.xml && adb pull /sdcard/window_dump.xml .   # UI hierarchy
adb shell dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'                             # foreground app
adb shell cmd package resolve-activity --brief -c android.intent.category.HOME             # launcher pkg (home detection)
adb shell pm list packages | grep <pkg>                                                    # installed?
```

## Reliability patterns

- **Timeouts:** no adb flag exists — wrap host commands with `timeout Ns` (exit 124 = timed out, 137 = SIGKILLed). Inside adbclient, socket timeouts cover this.
- **Transient errors** (`device not found`, `closed`, `protocol fault`, `device offline`): bounded retry (e.g. 3 attempts with short delay). `adb kill-server; adb start-server` between attempts is the classic bounce — in this project treat it as a last resort and prefer `adbclient.reconnect(serial)` first.
- **Exit codes:** `adb shell` propagates the device-side exit code only on platform-tools ≥ 24 with device API ≥ 24; don't build logic on `$?` below that.
- **Capture on failure:** grab a screenshot + `logcat -d -v threadtime` (and `-b crash`) *before* bailing out; namespace artifacts per serial when handling multiple devices.
- **Cleanup discipline:** every override (animation scales, `wm size`, port forwards) needs a matching reset in teardown; make each cleanup step non-fatal (`|| true`) so cleanup never masks the original failure.
- **Never blind-sleep** between inputs to "let the UI catch up" — block on a real condition: `am start -W`, the boot poll, or a detector/template confirmation of the resulting screen.

## Quick reference

| Task | Command |
|---|---|
| List devices + details | `adb devices -l` |
| Tap / swipe | `shell input tap X Y` / `shell input swipe X1 Y1 X2 Y2 [ms]` |
| Long press | `shell input swipe X Y X Y 1000` |
| Key event | `shell input keyevent KEYCODE_BACK` |
| Text (ASCII) | `shell input text 'a%sb'` (%s = space) |
| Screenshot | `exec-out screencap -p` |
| UI dump | `shell uiautomator dump /sdcard/window_dump.xml` |
| Foreground app | `shell dumpsys window \| grep mCurrentFocus` |
| Screen size | `shell wm size` |
| Launch activity | `shell am start -S -W -n pkg/.Activity` |
| Kill app (keep data) | `shell am force-stop <pkg>` |
| Full app reset | `shell pm clear <pkg>` |
| Boot complete? | `shell getprop sys.boot_completed` → `1` |
| Wi-Fi over TCP | `tcpip 5555` then `connect <ip>:5555` |
| Install / uninstall | `install -r app.apk` / `uninstall <pkg>` |
