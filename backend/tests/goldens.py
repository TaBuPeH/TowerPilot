"""The GOLDEN profile the regression tests compile: a real, fully populated
account profile frozen under tests/fixtures/, not the shipped starter.

profiles/default.yaml used to be both the running account's profile and the
tests' fixture. The shipped default is now a generic starter (unverified
abilities, no rescue policies bound), so the byte-for-byte compiler locks
would no longer describe a farming account. The fixture keeps that account:
every rescue policy armed, every v29 preset named, the constant-era plan.
"""
from pathlib import Path

import yaml

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "golden_profile.yaml"


def load_golden() -> dict:
    """Same contract as playerprofile.load(): the mapping, stamped with
    `_name`/`_path` so attestation logs can name the file."""
    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    data["_name"] = "golden_profile"
    data["_path"] = str(FIXTURE)
    return data
