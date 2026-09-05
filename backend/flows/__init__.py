"""The flow registry - one file in this folder per type of run.

A FLOW is a declarative spec: it names the run kind, which script drives it,
and how a blueprint's fields become that script's command line. The
scheduler (combo.py), the profile compiler (playerprofile.py), the tray and
the dashboard all read THIS registry instead of keeping their own tables -
adding a new flow file here is all it takes for a new run type to become
schedulable, compilable and visible everywhere.

HOW TO ADD A FLOW (full guide: flows/README.md):

1. Create `flows/<name>.py` with a module-level literal dict:

       FLOW = {
           "kind": "my_kind",            # blueprint `kind` that selects it
           "label": "My farming mode",   # human name for menus/UIs
           "runner": "flows/<name>.py",  # its own script - or None when the
                                         # generic orchestrator engine runs
                                         # the compiled preset directly
           "handoff": "none",            # screen prep the scheduler owes it:
                                         # "loadout" (equip + set tier),
                                         # "home_only", or "none" (the flow
                                         # does its own setup end to end)
           "count_arg": None,            # e.g. "--loops": receives the
                                         # scheduler's remaining daily quota
           "blueprint_args": [],         # [{"flag": "--x", "fields": [...],
                                         #   "default": ...}] - argv derived
                                         # from blueprint fields
           "blueprint_fields": (),       # extra legal blueprint fields for
                                         # kinds the compiler doesn't know
       }

2. If `runner` is a script, make it runnable from the backend root and give
   it the same CLI contract as the existing flows (`--instance`, and
   `--preset bp_<name>` accepting ONLY compiled blueprints of its kind).

The spec must be a PURE LITERAL: it is read with `ast.literal_eval`, never
imported, so broken or heavyweight flow modules cannot take down the
dashboard or the compiler just by being discovered.
"""
import ast
from pathlib import Path

_FLOWS_DIR = Path(__file__).resolve().parent

# The generic observe-decide-act engine. Flows with `runner: None` are run BY
# it: their whole variance lives in the compiled preset the engine reads.
ENGINE = "orchestrator.py"

_REQUIRED = ("kind", "label")
_DEFAULTS = {
    "runner": None,
    "handoff": "loadout",
    "count_arg": None,
    "count_unbounded": "0",
    "blueprint_args": (),
    "blueprint_fields": (),
    "legacy_preset": None,
    # Flag the scheduler appends when its "loadout" handoff has ALREADY put
    # the block's battle on screen: the runner must adopt that battle, not
    # walk Home and end it. None = the runner has no such mode.
    "adopt_arg": None,
}
_HANDOFFS = ("loadout", "home_only", "none")


class FlowError(RuntimeError):
    """A flow file is broken or a kind is unknown. Always names the file or
    the kind - a registry problem must be actionable, never a shrug."""


def _parse_spec(path: Path) -> dict:
    """The FLOW literal out of one flow file, WITHOUT importing it.

    ast-only on purpose: flow files import capture/act/settings at module
    level, and discovery runs inside the dashboard and the compiler, where
    importing a runner (or executing a broken one) is exactly what must not
    happen. A FLOW that is not a pure literal is refused, not evaluated.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:
        raise FlowError(f"{path.name}: does not parse ({e})") from e
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "FLOW"
                for t in node.targets):
            try:
                spec = ast.literal_eval(node.value)
            except ValueError as e:
                raise FlowError(
                    f"{path.name}: FLOW must be a pure literal dict "
                    f"(no names, calls or f-strings)") from e
            if not isinstance(spec, dict):
                raise FlowError(f"{path.name}: FLOW is a "
                                f"{type(spec).__name__}, not a dict")
            return spec
    raise FlowError(f"{path.name}: no module-level `FLOW = {{...}}` found - "
                    f"every file in flows/ must declare one "
                    f"(prefix the filename with _ to opt out)")


def _validate(spec: dict, name: str) -> dict:
    for key in _REQUIRED:
        if not spec.get(key):
            raise FlowError(f"{name}: FLOW is missing required key {key!r}")
    out = dict(_DEFAULTS)
    out.update(spec)
    if out["handoff"] not in _HANDOFFS:
        raise FlowError(f"{name}: handoff {out['handoff']!r} is not one of "
                        f"{_HANDOFFS}")
    for arg in out["blueprint_args"]:
        if not (isinstance(arg, dict) and arg.get("flag")
                and arg.get("fields")):
            raise FlowError(f"{name}: every blueprint_args entry needs "
                            f"'flag' and 'fields' (got {arg!r})")
    return out


_cache: dict[str, dict] | None = None


def flows(refresh: bool = False) -> dict[str, dict]:
    """kind -> validated spec, for every file in this folder.

    Cached after the first walk: consumers call this on hot paths (adoption
    scans, preset compiles) and the folder does not change under a running
    process - a deploy is a restart here like everywhere else.
    """
    global _cache
    if _cache is not None and not refresh:
        return _cache
    out: dict[str, dict] = {}
    for path in sorted(_FLOWS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        spec = _validate(_parse_spec(path), path.name)
        kind = spec["kind"]
        if kind in out:
            raise FlowError(f"{path.name}: kind {kind!r} is already declared "
                            f"by {out[kind]['file']}")
        spec["file"] = f"flows/{path.name}"
        out[kind] = spec
    if not out:
        raise FlowError(f"no flows found in {_FLOWS_DIR}")
    _cache = out
    return out


def flow(kind: str) -> dict:
    all_flows = flows()
    if kind not in all_flows:
        raise FlowError(f"no flow for kind {kind!r} "
                        f"(have: {', '.join(sorted(all_flows))})")
    return all_flows[kind]


def kinds() -> tuple[str, ...]:
    return tuple(flows())


def script(kind: str) -> str:
    """What to spawn (and match running processes against) for this kind."""
    return flow(kind)["runner"] or ENGINE


def extra_argv(kind: str, body: dict, remaining: int | str | None = None) -> list[str]:
    """argv derived from a blueprint/preset body, per the flow's spec.

    THE ONE ARGV BUILDER. The compiler (runner_args at compile time) and the
    scheduler (plan-block spawns) both call this, so a flow's command line
    cannot drift between the two.

    `remaining` is the scheduler's remaining quota for counted flows; None
    means compile time, where the body's own `count` (or 0 = unbounded) is
    the number. Values are matched on `is not None`, never truthiness - a
    legitimate 0 must survive to the command line.
    """
    spec = flow(kind)
    args: list[str] = []
    if spec["count_arg"]:
        n = remaining if remaining is not None else (body.get("count") or 0)
        args += [spec["count_arg"], str(n)]
    for a in spec["blueprint_args"]:
        val = next((body[f] for f in a["fields"] if body.get(f) is not None),
                   a.get("default"))
        if val is not None:
            args += [a["flag"], str(val)]
    return args


def run_main(flow_name: str, main) -> None:
    """Entry wrapper for flow scripts: log any terminal exception as an event.

    Runners live under pythonw, where an unhandled traceback has no console -
    a died-silent process reads as "runner failed" in the UI with no why
    (the v29 guardian-screen Abort, 2026-08-27). The event log IS the
    console; the exception still propagates so the exit code stays honest.
    """
    try:
        main()
    except SystemExit:
        raise
    except BaseException as e:      # noqa: BLE001 - logged, then re-raised
        try:
            from runtime import logger
            logger.event("runner_crashed", flow=flow_name,
                         error=f"{type(e).__name__}: {e}")
        except Exception:           # noqa: BLE001 - never mask the original
            pass
        raise
