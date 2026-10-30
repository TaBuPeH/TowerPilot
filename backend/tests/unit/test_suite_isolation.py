"""The suite never writes into the real per-instance log directory, and never
runs on this machine's own config.yaml.

runtime.logger names its file lazily under backend/logs/<instance>/ and the
dashboard shows the newest events_*.jsonl there as live activity, so a test
whose code path reaches an unpatched logger.event() used to drop fixture
records ("unit/blob.png", a 300x200 frame) into the user's real log.
conftest.py's autouse fixture moves that sink into each test's tmp_path.

settings.CONFIG is loaded at import from the git-ignored backend/config.yaml,
so results used to depend on each install (a bound account, a calibrated ROI,
a `fleet.by_tier` override). conftest.py replaces it with the shipped
config.example.yaml before any test module imports settings."""

import yaml

import settings
from runtime import logger
from settings import CONFIG, ROOT


def test_event_log_resolves_under_the_tests_tmp_path(tmp_path):
    """Checked BEFORE the write, so a regression fails without polluting."""
    real = ROOT / CONFIG["logging"]["dir"]
    assert not logger.run_file().is_relative_to(real)
    assert logger.run_file().is_relative_to(tmp_path)
    logger.event("suite_isolation_probe")
    assert "suite_isolation_probe" in logger.run_file().read_text(encoding="utf-8")


def test_bound_main_instance_is_the_shipped_example_not_this_machine():
    """bind_device() rebuilds the overridable blocks from the snapshots settings
    took at import, so those must come from the example too, not only CONFIG."""
    example = yaml.safe_load(settings.CONFIG_EXAMPLE.read_text(encoding="utf-8"))
    settings.bind_device("main")
    assert settings.instance("main") == example["instances"]["main"]
    for key in settings._OVERRIDABLE:
        assert CONFIG.get(key) == example.get(key), key
