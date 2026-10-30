"""Create backend/config.yaml from the shipped config.example.yaml.

The one rule behind settings.seed_config and the dashboard's seed_config; the
rules are config_seed.rules.md. Import-safe: standard library only, no
settings, no yaml, no side effect at import.
"""
from pathlib import Path


def seed(path: str | Path, example: str | Path) -> bool:
    """Copy `example` to `path` byte for byte when `path` does not exist.

    Returns True when the file was created, False when it was already there
    (including one a concurrent starter created first; it is never replaced).
    Raises FileNotFoundError when both are missing. A copy that fails midway
    leaves no half-written file behind.
    """
    path, example = Path(path), Path(example)
    if path.exists():
        return False
    if not example.exists():
        raise FileNotFoundError(f"{path} is missing and so is {example}")
    data = example.read_bytes()
    try:
        handle = open(path, "xb")
    except FileExistsError:
        return False
    try:
        with handle:
            handle.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return True
