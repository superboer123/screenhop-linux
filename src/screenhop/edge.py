"""Pointer-edge detection backends for GNOME/Wayland.

The preferred backend is the bundled GNOME Shell extension. An XWayland
override-redirect edge-window backend keeps crossing usable after package
installation, before the next GNOME login loads the extension.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import threading
import time

import dbus


BUS_NAME = "com.screenhop.Pointer"
OBJ_PATH = "/com/screenhop/Pointer"
IFACE = "com.screenhop.Pointer"


class EdgeClient:
    def __init__(self, callback):
        self.bus = dbus.SessionBus()
        self.callback = callback
        self.fallback = X11EdgeWatcher(callback)
        self.bus.add_signal_receiver(
            self._edge, signal_name="EdgeReached", dbus_interface=IFACE,
            bus_name=BUS_NAME, path=OBJ_PATH)
        # GNOME may unmap XWayland override-redirect windows while the session
        # is locked. Recreate their mapped state as soon as the session resumes.
        self.bus.add_signal_receiver(
            self._screen_state_changed,
            signal_name="ActiveChanged",
            dbus_interface="org.gnome.ScreenSaver",
            path="/org/gnome/ScreenSaver",
        )
        try:
            self.system_bus = dbus.SystemBus()
            self.system_bus.add_signal_receiver(
                self._sleep_state_changed,
                signal_name="PrepareForSleep",
                dbus_interface="org.freedesktop.login1.Manager",
                path="/org/freedesktop/login1",
            )
        except dbus.DBusException:
            self.system_bus = None

    def _extension_available(self) -> bool:
        try:
            return bool(self.bus.name_has_owner(BUS_NAME))
        except dbus.DBusException:
            return False

    def available(self) -> bool:
        self.fallback.ensure_running()
        return self._extension_available() or self.fallback.available

    def configure(self, enabled: bool, threshold: int, edges: list[int]) -> None:
        if self._extension_available():
            obj = self.bus.get_object(BUS_NAME, OBJ_PATH)
            iface = dbus.Interface(obj, IFACE)
            iface.Configure(bool(enabled), dbus.UInt32(threshold),
                            dbus.Array(edges, signature="u"))
            self.fallback.configure(False, threshold, [])
        else:
            self.fallback.configure(enabled, threshold, edges)

    def backend_name(self) -> str:
        if self._extension_available():
            return "GNOME Shell"
        if self.fallback.available:
            return "XWayland 边缘窗口"
        return "未就绪"

    def mark_inbound(self) -> None:
        """Re-arm the fallback after the pointer has returned to Linux."""
        self.fallback.rearm_on_motion(0.7, "inbound")

    def rearm_after_failure(self) -> None:
        self.fallback.rearm_on_motion(0.35, "request-failed")

    def _screen_state_changed(self, active) -> None:
        if not bool(active):
            self.fallback.reset("screen-unlocked")

    def _sleep_state_changed(self, sleeping) -> None:
        if not bool(sleeping):
            self.fallback.reset("system-resumed")

    def _edge(self, direction, x, y):
        self.callback(int(direction), int(x), int(y))


class X11EdgeWatcher:
    """Detect pointer entry through thin XWayland edge surfaces.

    InputOnly X windows are not promoted to Wayland surfaces by Mutter, so a
    two-pixel InputOutput surface is used.  The preferred Shell backend takes
    over automatically after the extension is loaded.
    """

    INPUT_OUTPUT = 1
    ENTER_NOTIFY = 7
    MOTION_NOTIFY = 6
    CW_OVERRIDE_REDIRECT = 1 << 9
    CW_EVENT_MASK = 1 << 11
    CW_BACK_PIXEL = 1 << 1
    ENTER_WINDOW_MASK = 1 << 4
    POINTER_MOTION_MASK = 1 << 6

    class Attributes(ctypes.Structure):
        _fields_ = [
            ("background_pixmap", ctypes.c_ulong), ("background_pixel", ctypes.c_ulong),
            ("border_pixmap", ctypes.c_ulong), ("border_pixel", ctypes.c_ulong),
            ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int),
            ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
            ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int),
            ("event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
            ("override_redirect", ctypes.c_int), ("colormap", ctypes.c_ulong),
            ("cursor", ctypes.c_ulong),
        ]

    class AnyEvent(ctypes.Structure):
        _fields_ = [
            ("type", ctypes.c_int), ("serial", ctypes.c_ulong),
            ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
            ("window", ctypes.c_ulong),
        ]

    def __init__(self, callback):
        self.callback = callback
        self.enabled = False
        self.edges: set[int] = set()
        self.threshold = 2
        self._lock = threading.Lock()
        self._last = 0.0
        self._armed = True
        self._arm_at = 0.0
        self._motion_can_cross = False
        self._last_pointer: tuple[int, int] | None = None
        self._last_motion_event = 0.0
        self._reset_requested = False
        self._library = ctypes.util.find_library("X11")
        self.available = bool(os.environ.get("DISPLAY") and self._library)
        self._thread = None
        if self.available:
            self.ensure_running()

    def ensure_running(self) -> None:
        if not os.environ.get("DISPLAY") or not self._library:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self.available = True
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="screenhop-xedge",
            )
            self._thread.start()

    def configure(self, enabled, threshold, edges):
        with self._lock:
            self.enabled = bool(enabled)
            self.threshold = max(1, min(12, int(threshold)))
            self.edges = {int(x) for x in edges}

    def rearm_on_motion(self, delay: float, reason: str) -> None:
        with self._lock:
            self._armed = False
            self._arm_at = time.monotonic() + max(0.0, float(delay))
            self._motion_can_cross = True
            self._last_pointer = None
            self._last_motion_event = time.monotonic()
        logging.info("XWayland edge scheduled re-arm: %s", reason)

    def reset(self, reason: str) -> None:
        """Remap all edge surfaces after unlock/resume and re-arm safely."""
        with self._lock:
            self._reset_requested = True
            self._armed = False
            self._arm_at = time.monotonic() + 0.35
            self._motion_can_cross = True
            self._last_pointer = None
        logging.info("XWayland edge reset requested: %s", reason)
        self.ensure_running()

    @staticmethod
    def _bind_xlib(x11):
        void_p = ctypes.c_void_p
        ulong = ctypes.c_ulong
        uint = ctypes.c_uint
        int_p = ctypes.POINTER(ctypes.c_int)
        ulong_p = ctypes.POINTER(ulong)
        uint_p = ctypes.POINTER(uint)

        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = void_p
        x11.XDefaultScreen.argtypes = [void_p]
        x11.XDefaultScreen.restype = ctypes.c_int
        x11.XDisplayWidth.argtypes = [void_p, ctypes.c_int]
        x11.XDisplayWidth.restype = ctypes.c_int
        x11.XDisplayHeight.argtypes = [void_p, ctypes.c_int]
        x11.XDisplayHeight.restype = ctypes.c_int
        x11.XDefaultRootWindow.argtypes = [void_p]
        x11.XDefaultRootWindow.restype = ulong
        x11.XCreateWindow.argtypes = [
            void_p, ulong, ctypes.c_int, ctypes.c_int, uint, uint, uint,
            ctypes.c_int, uint, void_p, ulong,
            ctypes.POINTER(X11EdgeWatcher.Attributes),
        ]
        x11.XCreateWindow.restype = ulong
        x11.XMoveResizeWindow.argtypes = [void_p, ulong, ctypes.c_int,
                                          ctypes.c_int, uint, uint]
        x11.XMapWindow.argtypes = [void_p, ulong]
        x11.XUnmapWindow.argtypes = [void_p, ulong]
        x11.XFlush.argtypes = [void_p]
        x11.XPending.argtypes = [void_p]
        x11.XPending.restype = ctypes.c_int
        x11.XNextEvent.argtypes = [void_p, void_p]
        x11.XQueryPointer.argtypes = [void_p, ulong, ulong_p, ulong_p,
                                      int_p, int_p, int_p, int_p, uint_p]
        x11.XQueryPointer.restype = ctypes.c_int

    def _run(self):
        try:
            x11 = ctypes.CDLL(self._library)
            self._bind_xlib(x11)
            display = x11.XOpenDisplay(None)
            if not display:
                self.available = False
                return

            screen = x11.XDefaultScreen(display)
            width = x11.XDisplayWidth(display, screen)
            height = x11.XDisplayHeight(display, screen)
            root = x11.XDefaultRootWindow(display)
            windows: dict[int, int] = {}
            mapped: set[int] = set()
            last_geometry: dict[int, tuple[int, int, int, int]] = {}

            for direction in (1, 2, 3, 4):
                attrs = self.Attributes()
                attrs.override_redirect = 1
                attrs.event_mask = self.ENTER_WINDOW_MASK | self.POINTER_MOTION_MASK
                win = x11.XCreateWindow(
                    display, root, 0, 0, 1, 1, 0, 0, self.INPUT_OUTPUT, None,
                    self.CW_BACK_PIXEL | self.CW_OVERRIDE_REDIRECT | self.CW_EVENT_MASK,
                    ctypes.byref(attrs))
                windows[int(win)] = direction

            event = ctypes.create_string_buffer(192)
            root_return = ctypes.c_ulong()
            child_return = ctypes.c_ulong()
            root_x = ctypes.c_int()
            root_y = ctypes.c_int()
            win_x = ctypes.c_int()
            win_y = ctypes.c_int()
            mask = ctypes.c_uint()

            while True:
                with self._lock:
                    threshold = self.threshold
                    wanted = set(self.edges) if self.enabled else set()
                    reset_requested = self._reset_requested
                    self._reset_requested = False

                if reset_requested:
                    for win in tuple(mapped):
                        x11.XUnmapWindow(display, win)
                    mapped.clear()
                    last_geometry.clear()
                    x11.XFlush(display)
                    logging.info("XWayland edge surfaces remapped")

                geometries = {
                    1: (0, 0, width, threshold),
                    2: (0, max(0, height - threshold), width, threshold),
                    3: (0, 0, threshold, height),
                    4: (max(0, width - threshold), 0, threshold, height),
                }
                changed = False
                for win, direction in windows.items():
                    if direction in wanted:
                        x, y, w, h = geometries[direction]
                        geometry = (x, y, w, h)
                        # Do not enqueue an XMoveResizeWindow request on every
                        # 10 ms poll. The old loop produced hundreds of
                        # thousands of pending X requests and eventually
                        # corrupted the process allocator during long sessions.
                        if last_geometry.get(win) != geometry:
                            x11.XMoveResizeWindow(display, win, x, y, w, h)
                            last_geometry[win] = geometry
                            changed = True
                        if win not in mapped:
                            x11.XMapWindow(display, win)
                            mapped.add(win)
                            changed = True
                    elif win in mapped:
                        x11.XUnmapWindow(display, win)
                        mapped.remove(win)
                        last_geometry.pop(win, None)
                        changed = True
                if changed:
                    x11.XFlush(display)

                with self._lock:
                    should_arm = (not self._armed and self._arm_at and
                                  time.monotonic() >= self._arm_at)
                if should_arm and x11.XQueryPointer(
                        display, root, ctypes.byref(root_return),
                        ctypes.byref(child_return), ctypes.byref(root_x),
                        ctypes.byref(root_y), ctypes.byref(win_x),
                        ctypes.byref(win_y), ctypes.byref(mask)):
                    px, py = root_x.value, root_y.value
                    on_enabled_edge = (
                        (1 in wanted and py < threshold) or
                        (2 in wanted and py >= height - threshold) or
                        (3 in wanted and px < threshold) or
                        (4 in wanted and px >= width - threshold)
                    )
                    with self._lock:
                        if on_enabled_edge:
                            self._arm_at = time.monotonic() + 0.12
                        else:
                            self._armed = True
                            self._arm_at = 0.0
                            self._last_pointer = (px, py)
                    if not on_enabled_edge:
                        logging.info("XWayland edge re-armed at %d,%d", px, py)

                if not x11.XPending(display):
                    time.sleep(0.01)
                    continue
                x11.XNextEvent(display, event)
                any_event = ctypes.cast(
                    event, ctypes.POINTER(self.AnyEvent)).contents
                if any_event.type not in (self.ENTER_NOTIFY, self.MOTION_NOTIFY):
                    continue
                direction = windows.get(int(any_event.window), 0)
                with self._lock:
                    if any_event.type == self.MOTION_NOTIFY:
                        self._last_motion_event = time.monotonic()
                    armed = self._armed
                    motion_can_cross = self._motion_can_cross
                    previous = self._last_pointer
                if (direction not in wanted or not armed or
                        time.monotonic() - self._last < 0.8):
                    continue
                if not x11.XQueryPointer(
                        display, root, ctypes.byref(root_return),
                        ctypes.byref(child_return), ctypes.byref(root_x),
                        ctypes.byref(root_y), ctypes.byref(win_x),
                        ctypes.byref(win_y), ctypes.byref(mask)):
                    continue
                current = (root_x.value, root_y.value)
                if any_event.type == self.MOTION_NOTIFY:
                    outward = bool(previous) and {
                        1: current[1] <= previous[1],
                        2: current[1] >= previous[1],
                        3: current[0] <= previous[0],
                        4: current[0] >= previous[0],
                    }.get(direction, False)
                    with self._lock:
                        self._last_pointer = current
                    if not motion_can_cross or not outward:
                        continue
                nx = round(root_x.value * 1000 / max(1, width - 1))
                ny = round(root_y.value * 1000 / max(1, height - 1))
                if direction == 1:
                    ny = 995
                elif direction == 2:
                    ny = 5
                elif direction == 3:
                    nx = 995
                elif direction == 4:
                    nx = 5
                self._last = time.monotonic()
                with self._lock:
                    self._armed = False
                    self._arm_at = 0.0
                    self._motion_can_cross = False
                    self._last_pointer = current
                logging.info("XWayland edge trigger direction=%d position=%d,%d",
                             direction, nx, ny)
                self.callback(direction, max(0, min(1000, nx)),
                              max(0, min(1000, ny)))
        except Exception:
            logging.exception("XWayland edge watcher stopped")
            self.available = False

