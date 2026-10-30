"""Hidden Android windows must not block collection; visible overlays must."""
import pytest

from device import overlays

GAME = overlays.GAME_PKG + "/Activity"


def _dump(rows):
    return "\n".join(
        f"  Window #{i} Window{{ab{i} u0 {title}}}:\n    {state}"
        for i, (title, state) in enumerate(rows)
    ).encode()


@pytest.mark.parametrize("cover", [
    "NotificationShade", "InputMethod", "ShellDropTarget",
    "app.lawnchair/Launcher", "com.bluestacks.appmart/Activity",
])
@pytest.mark.parametrize("visible", [True, False])
def test_input_guard_uses_visibility_not_system_app_allowlist(monkeypatch, cover, visible):
    state = str(visible).lower()
    dump = _dump([(cover, f"isOnScreen={state}\n    isVisible={state}"),
                  (GAME, "isOnScreen=true\n    isVisible=true")])
    monkeypatch.setattr(overlays.adbclient, "shell", lambda *a: dump)
    # Existing cleanup still sees the complete inventory.
    assert overlays.windows("device") == [cover, GAME]
    wins = overlays.windows("device", visible_only=True)
    expected = "in_front:" + cover.split("/")[0] if visible else None
    assert overlays.foreground_blocker(wins) == expected


def test_hidden_game_cannot_authorize_input(monkeypatch):
    dump = _dump([(GAME, "isOnScreen=false\n    isVisible=false")])
    monkeypatch.setattr(overlays.adbclient, "shell", lambda *a: dump)
    assert overlays.foreground_blocker(overlays.windows("device", visible_only=True)) == "no_windows"


def test_transitioning_overlay_still_blocks(monkeypatch):
    dump = _dump([("NotificationShade", "isOnScreen=true\n    isVisible=false"),
                  (GAME, "isOnScreen=true\n    isVisible=true")])
    monkeypatch.setattr(overlays.adbclient, "shell", lambda *a: dump)
    assert overlays.foreground_blocker(overlays.windows("device", visible_only=True)) == "in_front:NotificationShade"


def test_unknown_dump_format_is_not_permission(monkeypatch):
    dump = _dump([("NotificationShade", "unrecognized format"),
                  (GAME, "isOnScreen=true")])
    monkeypatch.setattr(overlays.adbclient, "shell", lambda *a: dump)
    with pytest.raises(ValueError, match="visibility unavailable"):
        overlays.windows("device", visible_only=True)
