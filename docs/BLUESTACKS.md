# Running Tower Pilot on BlueStacks

> **Status:** Active
> **Type:** Knowledge
> **Created:** 2026-09-05
> **Updated:** 2026-09-05
> **Tags:** bluestacks, emulator, setup

The autopilot was built against MuMu Player. Everything it needs from an
emulator is generic - an adb daemon and a 1080 x 2560 portrait display -
but BlueStacks ships with different defaults for all of it. This is the
list of what to change, in order, and what the code does differently on
BlueStacks.

## The reference display

The templates were cut on an emulator reporting these values over adb
(read on 2026-09-05 with `wm size` / `wm density`):

| Property | Value |
|---|---|
| Resolution | 1080 x 2560, portrait |
| Density | 360 dpi |
| Android | 15 |

The capture layer refuses any other resolution outright
(`device/capture.py`, "resolution lock violated"). Density is not checked
by code - a wrong dpi renders the HUD at another size and every template
simply scores low. Match both.

BlueStacks reaches that frame the other way round: its panel stays
**landscape, 2560 x 1080 at 360 dpi**, and when The Tower (portrait-only)
comes to the front BlueStacks rotates the display. `wm size` keeps saying
`2560x1080`, but `screencap` delivers the rotated 1080 x 2560 frame and the
home screen matched at 1.0 (seen 2026-09-05). A portrait panel of
1080 x 2560 reads right in `wm size` but Unity never draws a frame on it:
blank window, all-zero SurfaceFlinger frame table. **Prepare** writes the
landscape values for that reason.

---

## BlueStacks 5 settings

1. **Display**: a custom resolution of **2560 x 1080 landscape** and a pixel
   density of **360** (see above for why not portrait). The Setup page's
   **Prepare** button writes both into `bluestacks.conf` while BlueStacks is
   closed and keeps a timestamped backup; by hand it is Settings > Display.
   Restart the instance afterwards (BlueStacks applies display changes on
   restart).
2. **Advanced > Android Debug Bridge**: switch it **on**. Note the port it
   shows (the default instance uses 5555). While it is off the device
   still shows up in `adb devices`, but every shell command is refused
   with `error: closed` (seen 2026-09-05 through HD-Adb.exe itself), so
   the boot pipeline waits on its adb stage until it times out. The
   Setup page marks the row "ADB switch off in BlueStacks". In
   `%ProgramData%\BlueStacks_nxt\bluestacks.conf` the switch is
   `bst.enable_adb_access` and the port is `bst.instance.<key>.adb_port`;
   the wizard reads both.
3. Install The Tower from the Play Store inside the instance and sign in
   yourself. The autopilot never handles credentials.
4. **Do not run MuMu at the same time.** BlueStacks' `HD-Adb.exe` and
   MuMu's `adb.exe` speak different adb server protocol versions; each
   `devices` call kills the other's daemon and the transport flaps to
   `offline` (seen live, 2026-08-18).

---

## Connecting the autopilot

1. Start `python frontend/dashboard.py`, open <http://127.0.0.1:8620/>.
2. On **Setup**, scan. BlueStacks appears with its instance name, the adb
   port from `bluestacks.conf`, and whether it is running (the wizard
   looks for `HD-Player.exe --instance <key>`).
3. When the row says "ADB switch off in BlueStacks" or "display ...,
   needs 2560x1080@360 landscape", close BlueStacks and press **Prepare**:
   it edits `bluestacks.conf` (ADB on, panel 2560 x 1080 @ 360, custom
   resolution on) with a backup next to it, and refuses while any
   `HD-Player.exe` runs because BlueStacks rewrites the file on exit.
4. Press **Start it**. That runs the same command as BlueStacks' own
   shortcut (`HD-Player.exe --instance <key>`), starts the adb daemon with
   `HD-Adb.exe`, and, when the configured instance has no serial yet,
   points it at `127.0.0.1:<port>` with `adb.exe = ...\HD-Adb.exe`. It
   then hands off to the boot pipeline (`device/boot.py`): wait for adb
   and Android, dismiss overlays, launch The Tower by package name, and
   verify a known screen. No icon is tapped - the game is started through
   `am start`, so which BlueStacks tab is in front does not matter. The
   pipeline gives adb four minutes; with ADB still off in BlueStacks it
   times out, and Start can simply be pressed again.
5. An instance that already points somewhere is not repointed by Start;
   **Use this one** does that explicitly.
6. The resolution check reads the `screencap` header - the frame the
   vision layer gets - and must say 1080 x 2560. Run it after Start has
   brought the game up: only then has BlueStacks rotated the panel.
   BlueStacks' Windows-side Home tab (the one with the adverts) is not the
   Android screen: the pipeline starts The Tower by package name over
   adb, and BlueStacks opens it in its own tab. Nothing needs to be
   clicked in the BlueStacks window.
7. Leave the instance's `display` and `input_display` keys **absent**
   (the template config has none). They exist for MuMu's secondary game
   display; without them the capture and input paths use the default
   display and skip the MuMu-only display refresh entirely.

> [!warning] Ad overlays on first boot
> `device/overlays.py` closes known emulator ad windows and REFUSES an
> unknown one (it logs the package name and a screenshot instead of
> tapping). BlueStacks' own Home tab and launcher apps are allowed by the
> `com.bluestacks.` prefix; its ad windows are not known yet. If `boot.py`
> stops on an overlay, read the package from `backend/logs/<instance>/`
> and add it to `AD_PACKAGES`.

---

## First run: read-only

`config.example.yaml` ships with `allow_taps: false`, so nothing is
tapped until you say so. Check what the eyes see first:

```bash
python backend/vision/screen.py --instance main -v
```

It prints the screen name and every template score for one capture. On
the game's home screen it must say `home`; in a run it must say `battle`
with the wave number. Then open the Calibrate page and work through the
*Required for your account* list with the cropper. When the scores are
clean, set `allow_taps: true` in Configuration.

---

## If a shipped template scores low on BlueStacks

BlueStacks renders the same layout, but anti-aliasing and scaling can
move a fixed-scale match from 1.0 to 0.8 and under the 0.90 gate used by
the menu flows. The codebase's answer is "one control, several looks":

- `interactions/presets.py` `PICKER_ICONS` and
  `interactions/tourney.py` `BATTLE_BUTTONS` are tuples of alternative
  cuts, tried in order - add a BlueStacks cut next to the existing one.
- `templates/floaters/gem_*.png` are picked up by glob - drop in a new gem
  cut, no code change.
- Everything else: recut the template with the cropper (tick *overwrite*)
  and keep the original under another name if you also run MuMu.

A good cut matches itself at 1.0 and the cropper reports the next-best
match on the same frame; anything above 0.9 there means the box is not
distinctive enough.
