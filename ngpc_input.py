"""Gamepad reading and turbo (autofire), both feeding the same joypad mask.

Two features that look unrelated but are the same problem: something other than
a key-down/key-up pair decides what the console sees in its 0xB0 register. So
they live together, behind one call the player makes per frame.

GAMEPAD. Qt6 dropped QtGamepad and the project ships only PyQt6 + numpy, so
rather than pull in SDL/pygame (a new dependency, and one more thing for the
PyInstaller build to get wrong) this reads Windows' XInput directly through
ctypes -- the API every Xbox-style pad speaks natively. Off Windows, or with no
DLL present, `XInputPad` reports itself unavailable and the shell simply keeps
running on the keyboard; nothing else has to know.

TURBO. A held button that the console must see as a rapid press/release train.
That only works if 0xB0 is written EVERY emulated frame -- the shell used to
write it once per timer tick (a batch of frames), which would make an autofire
stutter at the batch rate instead of the rate you asked for.
"""

from __future__ import annotations

import ctypes
import sys
import time

# NGPC joypad bits, mirrored from ngpc_settings.JOYPAD_BUTTONS (kept here as
# plain ints so this module stays importable without Qt).
UP, DOWN, LEFT, RIGHT = 0x01, 0x02, 0x04, 0x08
A, B, OPTION = 0x10, 0x20, 0x40


# --------------------------------------------------------------- gamepad
class _XInputState(ctypes.Structure):
    class _Gamepad(ctypes.Structure):
        _fields_ = [
            ("wButtons", ctypes.c_ushort),
            ("bLeftTrigger", ctypes.c_ubyte),
            ("bRightTrigger", ctypes.c_ubyte),
            ("sThumbLX", ctypes.c_short),
            ("sThumbLY", ctypes.c_short),
            ("sThumbRX", ctypes.c_short),
            ("sThumbRY", ctypes.c_short),
        ]

    _fields_ = [("dwPacketNumber", ctypes.c_uint), ("Gamepad", _Gamepad)]


# XInput button bits (xinput.h)
_XI_DPAD_UP, _XI_DPAD_DOWN, _XI_DPAD_LEFT, _XI_DPAD_RIGHT = 0x0001, 0x0002, 0x0004, 0x0008
_XI_START, _XI_BACK = 0x0010, 0x0020
_XI_A, _XI_B, _XI_X, _XI_Y = 0x1000, 0x2000, 0x4000, 0x8000

# The NGPC has two face buttons; a modern pad has four. Map BOTH diagonal pairs so
# either grip works without a settings trip: A/X -> NGPC A, B/Y -> NGPC B.
_FACE_TO_NGPC = (
    (_XI_A, A), (_XI_X, A),
    (_XI_B, B), (_XI_Y, B),
    (_XI_START, OPTION), (_XI_BACK, OPTION),
)
_DPAD_TO_NGPC = (
    (_XI_DPAD_UP, UP), (_XI_DPAD_DOWN, DOWN),
    (_XI_DPAD_LEFT, LEFT), (_XI_DPAD_RIGHT, RIGHT),
)

# Past this much stick deflection a direction counts as pressed. XInput's own
# resting deadzone is 7849; well above it, so a centred stick never drifts into
# a held direction on a worn pad.
_STICK_THRESHOLD = 16000

_DLLS = ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll")

# ⚠️ Querying an EMPTY controller slot is expensive -- XInput goes and looks for
# the device, and the call can cost the better part of a millisecond. Polling an
# unplugged pad at frame rate is a well-known way to lose frames for nothing. So
# a slot that answered "not connected" is only re-probed every few seconds; a
# connected one is free to poll as often as the caller likes.
_RETRY_SECONDS = 2.0


class XInputPad:
    """Polls one XInput controller (0-3, default 0). Safe to call every frame:
    see `_RETRY_SECONDS` for how the disconnected case is kept cheap. Two players
    each get their own pad by constructing with index 0 and 1."""

    def __init__(self, index: int = 0) -> None:
        self._index = max(0, min(3, int(index)))
        self._dll = None
        self._connected = False
        self._next_probe = 0.0
        self._mask = 0
        if not sys.platform.startswith("win"):
            return
        for name in _DLLS:
            try:
                self._dll = ctypes.windll.LoadLibrary(name)
                break
            except OSError:
                continue

    @property
    def available(self) -> bool:
        """True when the XInput API itself is usable -- NOT that a pad is plugged
        in. A pad can be connected at any moment, so the poll must keep trying."""
        return self._dll is not None

    @property
    def connected(self) -> bool:
        """Whether the last poll actually saw a controller."""
        return self._connected

    def poll(self) -> int:
        """Current pad state as an NGPC joypad mask (0 if no pad / no XInput)."""
        if self._dll is None:
            return 0
        if not self._connected and time.monotonic() < self._next_probe:
            return 0                    # still unplugged as far as we know -- see above
        state = _XInputState()
        try:
            # ERROR_SUCCESS(0) = a pad is there; ERROR_DEVICE_NOT_CONNECTED(1167)
            # is the normal answer with nothing plugged in, not a failure.
            if self._dll.XInputGetState(self._index, ctypes.byref(state)) != 0:
                self._connected = False
                self._next_probe = time.monotonic() + _RETRY_SECONDS
                self._mask = 0
                return 0
        except OSError:
            self._connected = False
            self._next_probe = time.monotonic() + _RETRY_SECONDS
            self._mask = 0
            return 0
        self._connected = True
        pad = state.Gamepad
        mask = 0
        for xi_bit, ngpc_bit in _DPAD_TO_NGPC:
            if pad.wButtons & xi_bit:
                mask |= ngpc_bit
        for xi_bit, ngpc_bit in _FACE_TO_NGPC:
            if pad.wButtons & xi_bit:
                mask |= ngpc_bit
        # Left stick doubles as the d-pad: most pads' sticks are more comfortable
        # than their d-pad, and a lot of NGPC games are stick-friendly anyway.
        if pad.sThumbLX <= -_STICK_THRESHOLD:
            mask |= LEFT
        elif pad.sThumbLX >= _STICK_THRESHOLD:
            mask |= RIGHT
        if pad.sThumbLY <= -_STICK_THRESHOLD:
            mask |= DOWN
        elif pad.sThumbLY >= _STICK_THRESHOLD:
            mask |= UP
        # Opposite directions at once is electrically impossible on the real
        # d-pad, and games do not all handle it. Stick + d-pad can produce it.
        if mask & LEFT and mask & RIGHT:
            mask &= ~(LEFT | RIGHT)
        if mask & UP and mask & DOWN:
            mask &= ~(UP | DOWN)
        self._mask = mask
        return mask


# --------------------------------------------------------------- SDL2 pad
# XInput is Windows + Xbox-only. SDL2 (via pygame) is the cross-platform standard
# and, through its GameController mapping database, handles Xbox, PlayStation,
# Nintendo and generic pads on Windows, macOS and Linux alike. So this is the
# PREFERRED backend everywhere (see make_pad); XInput stays as a fallback.
_sdl_state = {"tried": False, "ok": False}


def _sdl_init() -> bool:
    """Initialise SDL's joystick/controller subsystems ONCE, without a window and
    WITHOUT grabbing the audio device (Qt owns audio). Returns True on success."""
    if _sdl_state["tried"]:
        return _sdl_state["ok"]
    _sdl_state["tried"] = True
    try:
        import os
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")   # no window
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")   # do not take the sound card
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")   # no banner on import
        import pygame
        pygame.display.init()          # the event pump needs a (dummy) video system
        pygame.joystick.init()
        from pygame._sdl2 import controller
        controller.init()
        _sdl_state["ok"] = True
    except Exception:
        _sdl_state["ok"] = False
    return _sdl_state["ok"]


class SdlPad:
    """One SDL game controller (index 0-3), same interface as XInputPad. Works on
    Windows/macOS/Linux and with most controller brands. Falls back to raw
    joystick buttons if a device has no standard controller mapping.

    ⚠️ BETA / NOT YET HARDWARE-VALIDATED: init, detection and graceful no-pad
    fallback are covered by tests, but the actual button->NGPC mapping has not
    been exercised on a physical controller. It may need adjusting per model.
    See README "Known issues"."""

    def __init__(self, index: int = 0) -> None:
        self._index = max(0, int(index))
        self._ok = _sdl_init()
        self._ctrl = None
        self._js = None                # raw-joystick fallback
        self._connected = False
        self._next_probe = 0.0

    @property
    def available(self) -> bool:
        return self._ok

    @property
    def connected(self) -> bool:
        return self._connected

    def _open(self) -> bool:
        import pygame
        from pygame._sdl2 import controller
        try:
            if controller.get_count() > self._index:
                if controller.is_controller(self._index):
                    self._ctrl = controller.Controller(self._index)
                    self._connected = True
                    return True
                # no standard mapping -> raw joystick fallback
                self._js = pygame.joystick.Joystick(self._index)
                self._js.init()
                self._connected = True
                return True
        except Exception:
            pass
        self._connected = False
        self._next_probe = time.monotonic() + _RETRY_SECONDS
        return False

    def poll(self) -> int:
        if not self._ok:
            return 0
        import pygame
        try:
            pygame.event.pump()
        except Exception:
            return 0
        if self._ctrl is None and self._js is None:
            if not self._connected and time.monotonic() < self._next_probe:
                return 0
            if not self._open():
                return 0
        try:
            mask = (self._read_controller() if self._ctrl is not None
                    else self._read_joystick())
        except Exception:                       # unplugged mid-poll
            self._ctrl = self._js = None
            self._connected = False
            self._next_probe = time.monotonic() + _RETRY_SECONDS
            return 0
        # opposite directions can't coexist on a real d-pad
        if mask & LEFT and mask & RIGHT:
            mask &= ~(LEFT | RIGHT)
        if mask & UP and mask & DOWN:
            mask &= ~(UP | DOWN)
        return mask

    def _read_controller(self) -> int:
        import pygame as pg
        c = self._ctrl
        mask = 0
        if c.get_button(pg.CONTROLLER_BUTTON_DPAD_UP):    mask |= UP
        if c.get_button(pg.CONTROLLER_BUTTON_DPAD_DOWN):  mask |= DOWN
        if c.get_button(pg.CONTROLLER_BUTTON_DPAD_LEFT):  mask |= LEFT
        if c.get_button(pg.CONTROLLER_BUTTON_DPAD_RIGHT): mask |= RIGHT
        # NGPC has two face buttons; map both diagonal pairs so either grip works.
        if c.get_button(pg.CONTROLLER_BUTTON_A) or c.get_button(pg.CONTROLLER_BUTTON_X):
            mask |= A
        if c.get_button(pg.CONTROLLER_BUTTON_B) or c.get_button(pg.CONTROLLER_BUTTON_Y):
            mask |= B
        if c.get_button(pg.CONTROLLER_BUTTON_START) or c.get_button(pg.CONTROLLER_BUTTON_BACK):
            mask |= OPTION
        lx = c.get_axis(pg.CONTROLLER_AXIS_LEFTX)
        ly = c.get_axis(pg.CONTROLLER_AXIS_LEFTY)
        if lx <= -_STICK_THRESHOLD:   mask |= LEFT
        elif lx >= _STICK_THRESHOLD:  mask |= RIGHT
        if ly <= -_STICK_THRESHOLD:   mask |= UP
        elif ly >= _STICK_THRESHOLD:  mask |= DOWN
        return mask

    def _read_joystick(self) -> int:
        """Fallback for a pad with no standard mapping: a sensible generic layout
        (hat 0 = d-pad, buttons 0/1 = A/B, 2/3 = A/B alt, 6/7/9 = Option)."""
        j = self._js
        mask = 0
        if j.get_numhats() > 0:
            hx, hy = j.get_hat(0)
            if hx < 0: mask |= LEFT
            elif hx > 0: mask |= RIGHT
            if hy > 0: mask |= UP
            elif hy < 0: mask |= DOWN
        nb = j.get_numbuttons()

        def btn(i):
            return i < nb and j.get_button(i)

        if btn(0) or btn(2): mask |= A
        if btn(1) or btn(3): mask |= B
        if btn(6) or btn(7) or btn(9): mask |= OPTION
        if j.get_numaxes() >= 2:
            ax = j.get_axis(0) * 32767
            ay = j.get_axis(1) * 32767
            if ax <= -_STICK_THRESHOLD:   mask |= LEFT
            elif ax >= _STICK_THRESHOLD:  mask |= RIGHT
            if ay <= -_STICK_THRESHOLD:   mask |= UP
            elif ay >= _STICK_THRESHOLD:  mask |= DOWN
        return mask


def make_pad(index: int = 0):
    """The controller for player `index` (0 = P1, 1 = P2). Prefers SDL2 (all OSes,
    most brands); falls back to XInput on Windows, and to an inert pad otherwise."""
    p = SdlPad(index)
    if p.available:
        return p
    return XInputPad(index)


# ----------------------------------------------------------------- turbo
def apply_turbo(held: int, turbo_mask: int, frame: int, hz: int) -> int:
    """Chop the turbo-flagged buttons of `held` into an on/off train at `hz`.

    `frame` is a free-running count of EMULATED frames (not host frames), so the
    autofire rate is the same whether the emulator is fast-forwarding or crawling
    -- it is the console's own 60 Hz that the game counts, not the wall clock.

    Buttons without a turbo flag pass through untouched.
    """
    plain = held & ~turbo_mask
    fire = held & turbo_mask
    if not fire:
        return plain
    # A full press+release cycle at `hz`, held for the first half of it. Clamped
    # so 30 Hz (period 2) still alternates instead of collapsing to always-on.
    period = max(2, round(60 / max(1, hz)))
    if (frame % period) < (period // 2):
        return plain | fire
    return plain
