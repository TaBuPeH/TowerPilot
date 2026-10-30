"""The config seed rule (runtime/config_seed.py): one byte copy of config.example.yaml, shared by settings.seed_config
and the dashboard's seed_config. Offline; every file is under tmp_path; no git, no process other than one Python
child that checks the module is import-safe."""
import ast
import importlib.util
import subprocess
import sys

import paths as test_paths
import pytest

from runtime import config_seed

BACKEND = test_paths.BACKEND
CRLF = b"adb: {exe: adb}\r\ninstances:\r\n  main: {serial: ''}\r\n"
LF = b"adb: {exe: adb}\ninstances:\n  main: {serial: ''}\nnote: caf\xc3\xa9\n"


@pytest.fixture()
def dash():
    spec = importlib.util.spec_from_file_location("tp_dashboard_seed", str(test_paths.DASHBOARD))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("content", [LF, CRLF, b"", b"\n\n", b"\xef\xbb\xbfbom: true\n", bytes(range(256))])
def test_the_copy_is_byte_for_byte(tmp_path, content):
    example, cfg = tmp_path / "config.example.yaml", tmp_path / "config.yaml"
    example.write_bytes(content)
    assert config_seed.seed(cfg, example) is True
    assert cfg.read_bytes() == content


def test_an_existing_config_is_never_replaced(tmp_path):
    example, cfg = tmp_path / "config.example.yaml", tmp_path / "config.yaml"
    example.write_bytes(LF)
    cfg.write_bytes(b"edited: true\n")
    assert config_seed.seed(cfg, example) is False
    assert cfg.read_bytes() == b"edited: true\n"
    cfg.write_bytes(b"")                       # an empty file is still the player's file
    assert config_seed.seed(cfg, example) is False and cfg.read_bytes() == b""


def test_both_files_missing_raises_naming_both(tmp_path):
    cfg, example = tmp_path / "config.yaml", tmp_path / "nope.yaml"
    with pytest.raises(FileNotFoundError) as failure:
        config_seed.seed(cfg, example)
    assert str(cfg) in str(failure.value) and str(example) in str(failure.value)
    assert not cfg.exists()


def test_a_missing_example_with_an_existing_config_is_not_an_error(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_bytes(b"x: 1\n")
    assert config_seed.seed(cfg, tmp_path / "nope.yaml") is False


def test_a_starter_that_loses_the_race_leaves_the_winners_file(tmp_path, monkeypatch):
    """The other starter creates config.yaml between our check and our write: ours is dropped, theirs stays."""
    example, cfg = tmp_path / "config.example.yaml", tmp_path / "config.yaml"
    example.write_bytes(LF)
    real_read = type(example).read_bytes

    def read_then_lose(self):
        data = real_read(self)
        cfg.write_bytes(b"winner: true\n")
        return data

    monkeypatch.setattr(type(example), "read_bytes", read_then_lose)
    assert config_seed.seed(cfg, example) is False
    assert cfg.read_bytes() == b"winner: true\n"


def test_a_failed_write_leaves_no_half_file_so_a_retry_seeds_again(tmp_path, monkeypatch):
    example, cfg = tmp_path / "config.example.yaml", tmp_path / "config.yaml"
    example.write_bytes(LF)
    real_open = open

    class Failing:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

        def write(self, data):
            self.handle.write(data[:5])
            raise OSError("disk full")

    monkeypatch.setattr("builtins.open", lambda path, mode="r", *a, **k: Failing(real_open(path, mode, *a, **k))
                        if str(path) == str(cfg) else real_open(path, mode, *a, **k))
    with pytest.raises(OSError, match="disk full"):
        config_seed.seed(cfg, example)
    monkeypatch.undo()
    assert not cfg.exists()
    assert config_seed.seed(cfg, example) is True and cfg.read_bytes() == LF


def test_the_module_is_import_safe():
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import runtime.config_seed; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in {'settings', 'yaml', 'flask', 'cv2', 'numpy', 'device', 'player'}))")
    done = subprocess.run([sys.executable, "-E", "-s", "-c", code, str(BACKEND)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "[]", done.stdout + done.stderr


def test_settings_delegates_with_its_own_arguments(tmp_path, monkeypatch):
    import settings
    calls = []
    monkeypatch.setattr(config_seed, "seed", lambda path, example: calls.append((path, example)) or "answer")
    assert settings.seed_config(tmp_path / "a.yaml", tmp_path / "b.yaml") == "answer"
    assert settings.seed_config() == "answer"
    assert calls == [(tmp_path / "a.yaml", tmp_path / "b.yaml"), (settings.CONFIG_PATH, settings.CONFIG_EXAMPLE)]


def test_settings_keeps_its_contract(tmp_path):
    import settings
    example, cfg = tmp_path / "config.example.yaml", tmp_path / "config.yaml"
    example.write_bytes(CRLF)
    assert settings.seed_config(cfg, example) is True and cfg.read_bytes() == CRLF
    assert settings.seed_config(cfg, example) is False
    with pytest.raises(FileNotFoundError):
        settings.seed_config(tmp_path / "other.yaml", tmp_path / "nope.yaml")


def test_the_dashboard_delegates_with_its_module_paths(dash, tmp_path, monkeypatch):
    """The dashboard reads CONFIG_PATH / CONFIG_EXAMPLE when it seeds (tests and the contract worlds repoint them)."""
    calls = []
    monkeypatch.setattr(dash.config_seed, "seed", lambda path, example: calls.append((path, example)) or True)
    monkeypatch.setattr(dash, "CONFIG_PATH", str(tmp_path / "c.yaml"))
    monkeypatch.setattr(dash, "CONFIG_EXAMPLE", str(tmp_path / "e.yaml"))
    assert dash.seed_config() is True
    assert calls == [(str(tmp_path / "c.yaml"), str(tmp_path / "e.yaml"))]


def test_the_dashboard_seed_is_a_byte_copy_and_keeps_its_errors(dash, tmp_path, monkeypatch):
    example = tmp_path / "config.example.yaml"
    example.write_bytes(LF)
    monkeypatch.setattr(dash, "CONFIG_PATH", str(tmp_path / "config.yaml"))
    monkeypatch.setattr(dash, "CONFIG_EXAMPLE", str(example))
    assert dash.seed_config() is True and (tmp_path / "config.yaml").read_bytes() == LF
    assert dash.seed_config() is False
    monkeypatch.setattr(dash, "CONFIG_PATH", str(tmp_path / "second.yaml"))
    monkeypatch.setattr(dash, "CONFIG_EXAMPLE", str(tmp_path / "nope.yaml"))
    with pytest.raises(FileNotFoundError):
        dash.seed_config()


def test_the_dashboard_and_settings_hold_no_second_copy_of_the_rule():
    """The rule exists once: neither caller opens the example or writes the config itself, and the dashboard still does not import settings at load."""
    for path in (test_paths.DASHBOARD, BACKEND / "settings.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        seed = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "seed_config")
        names = {n.attr if isinstance(n, ast.Attribute) else n.id for n in ast.walk(seed) if isinstance(n, (ast.Attribute, ast.Name))}
        assert "config_seed" in names and not {"open", "write_text", "read_text", "write", "copyfile"} & names, path.name
    top = ast.parse(test_paths.DASHBOARD.read_text(encoding="utf-8")).body      # module level only: one function imports settings lazily, for another purpose
    imported = {alias.name.split(".")[0] for n in top if isinstance(n, ast.Import) for alias in n.names}
    imported |= {(n.module or "").split(".")[0] for n in top if isinstance(n, ast.ImportFrom)}
    assert "settings" not in imported
