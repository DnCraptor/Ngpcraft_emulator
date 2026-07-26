"""GRAPHICS FILTER PLUGINS — scalers the emulator does not ship, and cannot.

The good upscalers of the emulation scene (2xSaI, SuperEagle, HQx, xBRZ) exist as
COPYLEFT implementations. This emulator is MIT, so bundling them is not an option —
an algorithm cannot be owned, but an implementation can, and theirs are not ours to
redistribute. Rewriting HQx's 256-rule table honestly is a project of its own.

So the emulator loads them instead of shipping them. You point the setting at a folder
you own, drop a file in it, and it appears in the filter list. A plugin you downloaded
under any licence is yours to run; nothing about it is distributed by us.

⚠️ NO FOLDER IS CREATED FOR THIS. The path is empty by default and the feature simply
stays off -- the app already writes enough directories next to itself without adding an
empty one for a feature most users will never enable.

THE CONTRACT — one Python file, three names:

    NAME  = "Scale2x"        # what the filter list shows
    SCALE = 2                # how much bigger the output is
    def apply(rgb):          # (h, w, 3) uint8  ->  (h*SCALE, w*SCALE, 3) uint8
        ...

Nothing else is required and nothing else is called. `apply` receives the picture AFTER
the colour profile and BEFORE the scanline/LCD/CRT filters, so those still work on top.

WHAT WE CHECK, AND WHY WE CHECK IT BY RUNNING IT
------------------------------------------------
Declaring the three names is not evidence that the thing works: a plugin that returns
the wrong shape, the wrong dtype, or raises on the first frame would take the picture
down with it. So every plugin is SMOKE-TESTED on a small array at discovery time and
only offered if it came back with exactly what it promised. A plugin that fails later,
in the middle of a game, is dropped for the session rather than allowed to kill frames.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# What we import from a plugin folder. `.pyw` and package folders are deliberately not
# supported: one file, one filter, no import side-effects to reason about.
PLUGIN_GLOB = "*.py"
# A plugin may not blow the frame up: 8x is already 1280x1216 from a 160x152 screen.
MAX_SCALE = 8
# The smoke test. Small enough to cost nothing, big enough that a scaler has neighbours
# to look at (a 1x1 array makes every edge rule trivially true).
_PROBE = np.arange(4 * 5 * 3, dtype=np.uint8).reshape(4, 5, 3)


@dataclass(frozen=True)
class FilterPlugin:
    """One loaded, verified filter. `apply` is only ever the plugin's own function."""

    ident: str          # "plugin:<file stem>" -- what the setting stores
    name: str           # what the UI shows
    scale: int
    apply: object
    source: Path

    def __call__(self, rgb: np.ndarray) -> np.ndarray:
        return self.apply(rgb)


@dataclass(frozen=True)
class FilterRejection:
    """A file in the folder that is not a usable filter, and the reason in one line.
    Reported rather than swallowed: a plugin that silently does not appear is a bug
    report we would receive as "your filter list is broken"."""

    source: Path
    reason: str


def _load_module(path: Path):
    """Import one file under a private name, so two plugins can share a module name
    and neither can shadow anything the emulator imports."""
    spec = importlib.util.spec_from_file_location(f"_ngpc_filter_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError("not importable")
    module = importlib.util.module_from_spec(spec)
    # Not registered in sys.modules permanently: a re-scan must pick up an edited file,
    # and a plugin is not something anything else should be able to import by name.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def _verify(module, path: Path) -> "FilterPlugin | FilterRejection":
    name = getattr(module, "NAME", None)
    scale = getattr(module, "SCALE", None)
    fn = getattr(module, "apply", None)
    if not isinstance(name, str) or not name.strip():
        return FilterRejection(path, "no NAME")
    if not isinstance(scale, int) or isinstance(scale, bool) or not 1 <= scale <= MAX_SCALE:
        return FilterRejection(path, f"SCALE must be an int in 1..{MAX_SCALE}")
    if not callable(fn):
        return FilterRejection(path, "no apply(rgb) function")
    # RUN IT. Everything above is a promise; this is the evidence.
    try:
        out = fn(_PROBE.copy())
    except Exception as exc:
        return FilterRejection(path, f"apply() raised {type(exc).__name__}: {exc}")
    if not isinstance(out, np.ndarray):
        return FilterRejection(path, "apply() did not return a numpy array")
    want = (_PROBE.shape[0] * scale, _PROBE.shape[1] * scale, 3)
    if out.shape != want:
        return FilterRejection(path, f"apply() returned {out.shape}, expected {want}")
    if out.dtype != np.uint8:
        return FilterRejection(path, f"apply() returned {out.dtype}, expected uint8")
    return FilterPlugin(ident=f"plugin:{path.stem}", name=name.strip(),
                        scale=scale, apply=fn, source=path)


def discover(folder: "str | Path | None") -> "tuple[list[FilterPlugin], list[FilterRejection]]":
    """Every usable filter in `folder`, and every file that tried and failed.

    Never raises: an unreadable folder, a syntax error, a plugin that imports something
    it does not have -- all of it comes back as a rejection with a reason, because the
    alternative is an emulator that will not start because of a file it was handed.
    """
    if not folder:
        return [], []
    root = Path(folder)
    try:
        files = sorted(p for p in root.glob(PLUGIN_GLOB) if p.is_file())
    except OSError as exc:
        return [], [FilterRejection(root, f"cannot read the folder: {exc}")]
    ok: list[FilterPlugin] = []
    bad: list[FilterRejection] = []
    for path in files:
        try:
            module = _load_module(path)
        except Exception as exc:
            bad.append(FilterRejection(path, f"{type(exc).__name__}: {exc}"))
            continue
        result = _verify(module, path)
        (ok if isinstance(result, FilterPlugin) else bad).append(result)
    return ok, bad


class FilterRegistry:
    """The discovered plugins, and the one thing the render path needs from them.

    Rescans only when the folder CHANGES (or when asked): discovery imports files and
    runs each plugin once, which is not something to do sixty times a second.
    """

    def __init__(self) -> None:
        self._folder: str = ""
        self.plugins: list[FilterPlugin] = []
        self.rejected: list[FilterRejection] = []
        self._disabled: dict[str, str] = {}     # ident -> why it was dropped mid-game

    def sync(self, folder: "str | None", force: bool = False) -> None:
        folder = str(folder or "")
        if folder == self._folder and not force:
            return
        self._folder = folder
        self.plugins, self.rejected = discover(folder)
        self._disabled.clear()

    def get(self, ident: str) -> "FilterPlugin | None":
        if not ident or not ident.startswith("plugin:") or ident in self._disabled:
            return None
        for p in self.plugins:
            if p.ident == ident:
                return p
        return None

    def disabled_reason(self, ident: str) -> "str | None":
        return self._disabled.get(ident)

    def run(self, ident: str, rgb: np.ndarray) -> "np.ndarray | None":
        """Apply a plugin to one frame, or return None and drop it for the session.

        A plugin verified at discovery can still fail on a real picture (an assumption
        about the size, a stray edge case). That must cost the FILTER, never the frame:
        we fall back to the plain picture and remember why, so the panel can say it.
        """
        plugin = self.get(ident)
        if plugin is None:
            return None
        try:
            out = plugin(rgb)
            h, w = rgb.shape[:2]
            if (not isinstance(out, np.ndarray) or out.dtype != np.uint8
                    or out.shape != (h * plugin.scale, w * plugin.scale, 3)):
                raise ValueError(f"returned {getattr(out, 'shape', type(out).__name__)}")
            return out
        except Exception as exc:
            self._disabled[ident] = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
            return None
