#!/usr/bin/env python3
"""
companion.py-  Hornet desktop companion.
ESC to quit.  Left-click drag to throw.  Click on ground to sit / stand.
"""
import sys as _sys
# Expose system dist-packages so gi (GTK3) is reachable from the venv.
# Required on Ubuntu where python3-gi is a system package, not a pip package.
_sysdir = '/usr/lib/python3/dist-packages'
if _sysdir not in _sys.path:
    _sys.path.insert(0, _sysdir)

import os, sys, math, glob, platform, ctypes, ctypes.util, subprocess, re, random, threading, json, colorsys
from PIL import Image
from io import BytesIO

PLAT = platform.system()

def _resource(rel):
    """Resolve a bundled asset path (handles PyInstaller --onefile and normal runs)."""
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS if hasattr(sys, '_MEIPASS') else os.path.dirname(sys.executable)
        return os.path.join(base, rel)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)

def _user_data(filename):
    """Resolve a user-editable file next to the exe (stays writable outside the bundle)."""
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)

# ─────────────────────────────────────────────────────────────────────────────
# Session environment recovery (Linux)
# When launched outside a full login shell (desktop launchers, nohup, etc.)
# XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS and PULSE_SERVER are often absent.
# We scan /proc to borrow these vars from a running session process.
# ─────────────────────────────────────────────────────────────────────────────

def _restore_session_env():
    if PLAT != 'Linux':
        return
    _want = ('DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR',
             'PULSE_SERVER', 'PIPEWIRE_REMOTE')
    if all(k in os.environ for k in _want[:2]):   # already complete
        return
    uid = os.getuid()
    try:
        for env_path in glob.glob('/proc/*/environ'):
            try:
                if os.stat(env_path).st_uid != uid:
                    continue
                with open(env_path, 'rb') as f:
                    data = f.read()
                proc_env = {}
                for item in data.split(b'\x00'):
                    if b'=' in item:
                        k, v = item.split(b'=', 1)
                        try:
                            proc_env[k.decode()] = v.decode()
                        except Exception:
                            pass
                # Only use processes that are actually in a D-Bus session
                if 'DBUS_SESSION_BUS_ADDRESS' not in proc_env:
                    continue
                for key in _want:
                    if key not in os.environ and key in proc_env:
                        os.environ[key] = proc_env[key]
                if all(k in os.environ for k in _want[:2]):
                    return
            except Exception:
                continue
    except Exception:
        pass

_restore_session_env()

# ─────────────────────────────────────────────────────────────────────────────
# Linux pre-init: get PHYSICAL screen size and ARGB visual BEFORE pygame
# ─────────────────────────────────────────────────────────────────────────────

def _xrandr_screen_size():
    """Total virtual desktop size from xrandr-  ignores DPI scaling."""
    try:
        out = subprocess.check_output(['xrandr'], text=True, stderr=subprocess.DEVNULL)
        # "Screen 0: minimum 8 x 8, current 1920 x 1080, maximum ..."-  full desktop
        m = re.search(r'\bcurrent (\d+) x (\d+)', out)
        if m:
            return int(m.group(1)), int(m.group(2))
        # Fallback: first *-marked mode line
        m = re.search(r'(\d+)x(\d+)\s+[\d.]+\*', out)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return None, None


def _workarea_linux():
    """(x, y, w, h) of the work area (screen minus panels) via xprop."""
    try:
        out = subprocess.check_output(
            ['xprop', '-root', '_NET_WORKAREA'],
            text=True, stderr=subprocess.DEVNULL)
        nums = list(map(int, re.findall(r'\d+', out.split('=')[-1])))
        if len(nums) >= 4:
            return nums[0], nums[1], nums[2], nums[3]
    except Exception:
        pass
    return None


def _linux_enum_monitors(workarea):
    """Per-monitor bounds from `xrandr --listmonitors`, same tuple layout as
    _win_enum_monitors(). X11 only exposes one work area for the whole desktop,
    so each monitor's work area is its intersection with that rectangle."""
    try:
        out = subprocess.check_output(['xrandr', '--listmonitors'], text=True,
                                      stderr=subprocess.DEVNULL)
    except Exception:
        return []
    monitors = []
    # " 0: +*eDP-1 1920/344x1080/194+0+0  eDP-1"
    for m in re.finditer(r'(\d+)/\d+x(\d+)/\d+\+(-?\d+)\+(-?\d+)', out):
        w, h, x, y = map(int, m.groups())
        ml, mt, mr, mb = x, y, x + w, y + h
        wl, wt, wr, wb = ml, mt, mr, mb
        if workarea:
            ax, ay, aw, ah = workarea
            il, it = max(ml, ax), max(mt, ay)
            ir, ib = min(mr, ax + aw), min(mb, ay + ah)
            if ir > il and ib > it:
                wl, wt, wr, wb = il, it, ir, ib
        monitors.append((ml, mt, mr, mb, wl, wt, wr, wb))
    return monitors


def _linux_enum_monitors(workarea):
    """Per-monitor bounds from `xrandr --listmonitors`, same tuple layout as
    _win_enum_monitors. X11 only exposes one desktop-wide work area, so each
    monitor's work area is its rect clipped to that."""
    try:
        out = subprocess.check_output(['xrandr', '--listmonitors'],
                                      text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return []
    monitors = []
    # " 0: +*DP-1 2560/597x1440/336+0+0  DP-1"
    for m in re.finditer(r'(\d+)/\d+x(\d+)/\d+\+(-?\d+)\+(-?\d+)', out):
        w, h, x, y = map(int, m.groups())
        l, t, r, b = x, y, x + w, y + h
        wl, wt, wr, wb = l, t, r, b
        if workarea:
            ax, ay, aw, ah = workarea
            cl, ct = max(l, ax), max(t, ay)
            cr, cb = min(r, ax + aw), min(b, ay + ah)
            if cr > cl and cb > ct:
                wl, wt, wr, wb = cl, ct, cr, cb
        monitors.append((l, t, r, b, wl, wt, wr, wb))
    return monitors


def _linux_set_above(wid: int):
    """Send _NET_WM_STATE ClientMessage to add ABOVE (proper EWMH protocol for mapped windows)."""
    try:
        lib = ctypes.CDLL(ctypes.util.find_library('X11'))
        lib.XOpenDisplay.restype  = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XCloseDisplay.restype = ctypes.c_int
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        lib.XInternAtom.restype   = ctypes.c_ulong
        lib.XInternAtom.argtypes  = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.XDefaultRootWindow.restype  = ctypes.c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XFlush.restype  = ctypes.c_int
        lib.XFlush.argtypes = [ctypes.c_void_p]
        lib.XSendEvent.restype  = ctypes.c_int
        lib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                    ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
        dpy = lib.XOpenDisplay(None)
        if not dpy:
            return
        root        = lib.XDefaultRootWindow(dpy)
        wm_state    = lib.XInternAtom(dpy, b'_NET_WM_STATE', False)
        state_above = lib.XInternAtom(dpy, b'_NET_WM_STATE_ABOVE', False)

        class _Data(ctypes.Union):
            _fields_ = [('b', ctypes.c_char*20), ('s', ctypes.c_short*10),
                        ('l', ctypes.c_long*5)]
        class _CML(ctypes.Structure):
            _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong),
                        ('send_event', ctypes.c_int), ('display', ctypes.c_void_p),
                        ('window', ctypes.c_ulong), ('message_type', ctypes.c_ulong),
                        ('format', ctypes.c_int), ('data', _Data)]
        class _XEvt(ctypes.Union):
            _fields_ = [('xclient', _CML), ('pad', ctypes.c_long*24)]

        ev = _XEvt()
        ev.xclient.type         = 33          # ClientMessage
        ev.xclient.window       = wid
        ev.xclient.message_type = wm_state
        ev.xclient.format       = 32
        ev.xclient.data.l[0]    = 1           # _NET_WM_STATE_ADD
        ev.xclient.data.l[1]    = state_above
        ev.xclient.data.l[2]    = 0
        ev.xclient.data.l[3]    = 1           # source: application
        mask = 0x00020000 | 0x00080000        # SubstructureRedirect | SubstructureNotify
        lib.XSendEvent(dpy, root, False, mask, ctypes.byref(ev))
        lib.XFlush(dpy)
        lib.XCloseDisplay(dpy)
    except Exception:
        pass


def _find_argb_visual():
    """Return a 32-bit ARGB X11 visual id, or None."""
    try:
        xlib = ctypes.CDLL(ctypes.util.find_library('X11'))
        xlib.XOpenDisplay.restype  = ctypes.c_void_p
        xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        xlib.XCloseDisplay.restype  = ctypes.c_int
        xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xlib.XDefaultScreen.restype  = ctypes.c_int
        xlib.XDefaultScreen.argtypes = [ctypes.c_void_p]

        class XVisualInfo(ctypes.Structure):
            _fields_ = [('visual',ctypes.c_void_p),('visualid',ctypes.c_ulong),
                        ('screen',ctypes.c_int),('depth',ctypes.c_int),
                        ('class_',ctypes.c_int),('red_mask',ctypes.c_ulong),
                        ('green_mask',ctypes.c_ulong),('blue_mask',ctypes.c_ulong),
                        ('colormap_size',ctypes.c_int),('bits_per_rgb',ctypes.c_int)]

        xlib.XGetVisualInfo.restype  = ctypes.POINTER(XVisualInfo)
        xlib.XGetVisualInfo.argtypes = [ctypes.c_void_p, ctypes.c_long,
                                        ctypes.POINTER(XVisualInfo),
                                        ctypes.POINTER(ctypes.c_int)]
        xlib.XFree.argtypes = [ctypes.c_void_p]

        dpy = xlib.XOpenDisplay(None)
        if not dpy:
            return None
        scr = xlib.XDefaultScreen(dpy)
        tmpl = XVisualInfo(); tmpl.screen = scr; tmpl.depth = 32
        n = ctypes.c_int(0)
        vis = xlib.XGetVisualInfo(dpy, 0x2 | 0x8, ctypes.byref(tmpl), ctypes.byref(n))
        result = vis[0].visualid if (vis and n.value > 0) else None
        if vis:
            xlib.XFree(vis)
        xlib.XCloseDisplay(dpy)
        return result
    except Exception:
        return None


def _win_enum_monitors():
    """Per-monitor bounds via EnumDisplayMonitors, in virtual-desktop coordinates
    (origin may be negative when a monitor sits left of / above the primary one).
    Returns a list of (mon_left, mon_top, mon_right, mon_bottom,
                        work_left, work_top, work_right, work_bottom) tuples."""
    import ctypes.wintypes as wt

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_uint32), ('rcMonitor', wt.RECT),
                    ('rcWork', wt.RECT), ('dwFlags', ctypes.c_uint32)]

    monitors = []
    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(wt.RECT), wt.LPARAM)

    def _cb(hmon, hdc, lprc, data):
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            monitors.append((
                mi.rcMonitor.left, mi.rcMonitor.top, mi.rcMonitor.right, mi.rcMonitor.bottom,
                mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom,
            ))
        return 1

    try:
        u32 = ctypes.windll.user32
        u32.EnumDisplayMonitors.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                             MonitorEnumProc, wt.LPARAM]
        u32.EnumDisplayMonitors.restype = ctypes.c_int
        u32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)]
        u32.GetMonitorInfoW.restype = ctypes.c_int
        u32.EnumDisplayMonitors(None, None, MonitorEnumProc(_cb), 0)
    except Exception:
        pass
    return monitors


SCREEN_W = SCREEN_H = 0
USABLE_H = 0           # bottom of work area (where taskbar starts)
ARGB_MODE = False
# Full virtual desktop (spans every monitor; used both for multi-monitor physics
# bounds and to spawn Hornet off-screen for the walk-in entrance). VIRT_LEFT/TOP
# can be negative when a monitor is positioned left of / above the primary one.
VIRT_LEFT = VIRT_TOP = 0
VIRT_W = VIRT_H = 0
MONITORS = []           # list of per-monitor bound tuples, see _win_enum_monitors()

if PLAT == 'Linux':
    # --- screen size (physical pixels, not DPI-scaled) ---
    sw, sh = _xrandr_screen_size()
    if sw:
        SCREEN_W, SCREEN_H = sw, sh

    # --- work area (above taskbar) ---
    wa = _workarea_linux()
    USABLE_H = (wa[1] + wa[3]) if wa else SCREEN_H  # y + h = bottom of usable area

    # --- per-monitor bounds (the X screen spans all monitors from 0,0) ---
    MONITORS = _linux_enum_monitors(wa)
    if SCREEN_W:
        VIRT_W, VIRT_H = SCREEN_W, SCREEN_H
    if SCREEN_W:
        MONITORS = _linux_enum_monitors(wa)

    # --- ARGB visual (skip on Wayland) ---
    if not os.environ.get('WAYLAND_DISPLAY'):
        vis = _find_argb_visual()
        if vis:
            os.environ['SDL_VIDEO_X11_VISUAL_ID'] = str(vis)
            ARGB_MODE = True

elif PLAT == 'Windows':
    import ctypes.wintypes as _wt
    # Physical screen size (primary monitor -  used to center the sprite at startup)
    SCREEN_W = ctypes.windll.user32.GetSystemMetrics(0)   # SM_CXSCREEN
    SCREEN_H = ctypes.windll.user32.GetSystemMetrics(1)   # SM_CYSCREEN
    # Work area bottom = where taskbar starts (SPI_GETWORKAREA = 48)
    _rc = _wt.RECT()
    ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(_rc), 0)
    USABLE_H = _rc.bottom

    # Full virtual desktop bounds (spans every monitor) + per-monitor work areas,
    # so physics (floor/walls) can span and react correctly across all screens.
    VIRT_LEFT = ctypes.windll.user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    VIRT_TOP  = ctypes.windll.user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
    VIRT_W    = ctypes.windll.user32.GetSystemMetrics(78) or SCREEN_W  # SM_CXVIRTUALSCREEN
    VIRT_H    = ctypes.windll.user32.GetSystemMetrics(79) or SCREEN_H  # SM_CYVIRTUALSCREEN
    MONITORS  = _win_enum_monitors()

os.environ.setdefault('SDL_VIDEO_WINDOW_POS', '0,0')

import pygame
import numpy as np

CHROMA_KEY = (0, 0, 255)   # Win32 layered-window color key (blue screen fill → transparent)

MUSIC_FILE = _resource("assets/audio/needoline.mp3")
ICON_FILES = [
    _resource('assets/logo/logo-hdc.png'),
    _resource('assets/logo/logo-hdc.ico'),
]
CONFIG_PATH = _user_data('config.json')
APP_USER_MODEL_ID = 'HornetDesktopCompanion'
ICON_IMAGE = 1
ICON_SMALL = 0
ICON_BIG = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
WM_SETICON = 0x0080
# (start_seconds, end_seconds) for each segment
NEEDOLINE_SEGMENTS = [
    (  0,  46),   # default melody
    ( 47,  83),   # beastling call
    ( 84, 130),   # elegy of the deep
    (131, 159),   # conductor melody
    (160, 186),   # vaultkeeper melody
    (187, 213),   # architect melody
    (214, 253),   # trial end
]

# Song names for tray menu
SONG_NAMES = [
    'Default Melody',
    'Beastling Call',
    'Elegy of the Deep',
    'Conductor Melody',
    'Vaultkeeper Melody',
    'Architect Melody',
    'Trial End',
]

# Global tray control state
tray_globals = {
    'running': True,
    'topmost': True,
    'volume': 1.0,
    'current_song': -1,
    'auto_random_song': True,  # If False, always use current_song when sitting
    'hwnd': None,
    'sleep_z': True,           # Show floating Z's while sleeping
    'land_mode': 'bounce',     # 'bounce' | 'soft' | 'glide' (soft + umbrella glide on high falls)
    'drag_pendulum': True,     # Swing sprite around grip while dragged
    'wander': True,            # Walk around / read the map on her own while idle
    'window_platforms': True,  # Stand on / climb desktop windows (Windows, Linux X11)
    'window_platforms_ok': PLAT == 'Windows',  # Linux: set once the X11 scanner connects
    'cloak_color': 'default',  # Cloak hue: 'default' or '#RRGGBB'
    'spawn_mode': 'fall',      # 'fall' | 'walk_from_right' | 'walk_from_left'
}

# Global music state (modified by both main loop and tray)
has_music     = False
music_active  = False
music_seg     = 0
music_tick    = 0
music_dur_ms  = 0

# ─────────────────────────────────────────────────────────────────────────────
# X11 Shape Manager  (ONE connection for the lifetime of the app)
# ─────────────────────────────────────────────────────────────────────────────

class X11ShapeManager:
    """
    Gives pixel-perfect transparency via the SHAPE extension.
    Visual shape = sprite alpha mask (compositor not required).
    Input shape  = sprite bounding rect (easy to grab).
    All bitmaps are cached; only position changes each frame.
    """
    ShapeBounding = 0
    ShapeInput    = 2
    ShapeSet      = 0

    def __init__(self, input_only=False):
        self._input_only = input_only   # ARGB visual: compositor handles the look
        self._xlib   = None
        self._xext   = None
        self._dpy    = None
        self._win    = 0
        self._bitmaps = {}   # key -> depth-1 Pixmap

    def connect(self, win_id: int) -> bool:
        try:
            self._xlib = ctypes.CDLL(ctypes.util.find_library('X11'))
            self._xext = ctypes.CDLL(ctypes.util.find_library('Xext'))
            self._xlib.XOpenDisplay.restype  = ctypes.c_void_p
            self._xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
            self._dpy = self._xlib.XOpenDisplay(None)
            if not self._dpy:
                return False
            self._win = win_id
            return True
        except Exception:
            return False

    def pointer(self):
        """Global cursor position. pygame's goes stale while the pointer is
        outside the input shape (i.e. most of the time)."""
        if not self._dpy:
            return None
        x = self._xlib
        if not hasattr(self, '_qp_root'):
            x.XDefaultRootWindow.restype  = ctypes.c_ulong
            x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            x.XQueryPointer.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_uint)]
            self._qp_root = x.XDefaultRootWindow(self._dpy)
        root, child = ctypes.c_ulong(), ctypes.c_ulong()
        rx, ry, wx, wy, mask = (ctypes.c_int(), ctypes.c_int(), ctypes.c_int(),
                                ctypes.c_int(), ctypes.c_uint())
        if not x.XQueryPointer(self._dpy, self._qp_root, ctypes.byref(root),
                               ctypes.byref(child), ctypes.byref(rx), ctypes.byref(ry),
                               ctypes.byref(wx), ctypes.byref(wy), ctypes.byref(mask)):
            return None
        return rx.value, ry.value

    def disconnect(self):
        if self._dpy:
            for bm in self._bitmaps.values():
                self._xlib.XFreePixmap(self._dpy, bm)
            self._xlib.XCloseDisplay(self._dpy)
            self._dpy = None

    def _make_bitmap(self, surface: pygame.Surface) -> int:
        w, h = surface.get_size()
        ck = surface.get_colorkey()
        if ck is not None:
            arr = pygame.surfarray.array3d(surface)          # (w, h, 3) uint8
            ck_rgb = np.array(ck[:3], dtype=np.uint8)
            is_bg = np.all(arr == ck_rgb, axis=2)            # (w, h) True=chroma
            bits  = (~is_bg).T.astype(np.uint8)              # (h, w) 1=sprite
        else:
            alpha = pygame.surfarray.array_alpha(surface)    # (w, h)
            bits  = (alpha.T > 0).astype(np.uint8)           # (h, w) row-major
        row_bytes = (w + 7) // 8
        rows = []
        for row in bits:
            packed = np.packbits(row, bitorder='little')
            padded = np.zeros(row_bytes, dtype=np.uint8)
            padded[:len(packed)] = packed
            rows.append(bytes(padded))
        data = b''.join(rows)

        self._xlib.XCreateBitmapFromData.restype  = ctypes.c_ulong
        self._xlib.XCreateBitmapFromData.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_char_p,
            ctypes.c_uint, ctypes.c_uint]
        bm = self._xlib.XCreateBitmapFromData(self._dpy, self._win, data, w, h)
        return bm

    def update(self, x: int, y: int, surface: pygame.Surface, key):
        if not self._dpy:
            return
        w, h = surface.get_size()
        if self._input_only:
            self._set_input_rect(x, y, w, h)
            self._xlib.XFlush(self._dpy)
            return
        if key not in self._bitmaps:
            bm = self._make_bitmap(surface)
            if bm:
                self._bitmaps[key] = bm
            else:
                return

        bm = self._bitmaps[key]

        # Visual: pixel-perfect alpha mask
        self._xext.XShapeCombineMask.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_ulong, ctypes.c_int]
        self._xext.XShapeCombineMask(
            self._dpy, self._win, self.ShapeBounding,
            x, y, bm, self.ShapeSet)

        self._set_input_rect(x, y, w, h)
        self._xlib.XFlush(self._dpy)

    def _set_input_rect(self, x, y, w, h):
        # Input: bounding rectangle (easier to drag)
        class XRect(ctypes.Structure):
            _fields_ = [('x',ctypes.c_short),('y',ctypes.c_short),
                        ('width',ctypes.c_ushort),('height',ctypes.c_ushort)]
        rect = XRect(x, y, max(1,w), max(1,h))
        self._xext.XShapeCombineRectangles.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
            ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(XRect), ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self._xext.XShapeCombineRectangles(
            self._dpy, self._win, self.ShapeInput,
            0, 0, ctypes.byref(rect), 1, self.ShapeSet, 0)


# ─────────────────────────────────────────────────────────────────────────────
# Windows helpers
# ─────────────────────────────────────────────────────────────────────────────

class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint), ('flags', ctypes.c_uint),
                ('hwndActive', ctypes.c_void_p), ('hwndFocus', ctypes.c_void_p),
                ('hwndCapture', ctypes.c_void_p), ('hwndMenuOwner', ctypes.c_void_p),
                ('hwndMoveSize', ctypes.c_void_p), ('hwndCaret', ctypes.c_void_p),
                ('rcCaret', ctypes.c_int * 4)]
_GUI_INMENUMODE = 0x00000004


def _win_setup(hwnd):
    u = ctypes.windll.user32
    # hWndInsertAfter must be pointer-sized; without argtypes ctypes defaults to 32-bit
    # which truncates HWND_TOPMOST (-1) to 0xFFFFFFFF on 64-bit Windows
    u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_ssize_t,
                                ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    s = u.GetWindowLongW(hwnd, -20)
    u.SetWindowLongW(hwnd, -20, s | 0x00080000)          # WS_EX_LAYERED
    u.SetLayeredWindowAttributes(hwnd, 0x00FF0000, 0, 0x1) # LWA_COLORKEY, blue chroma
    u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)          # HWND_TOPMOST

def _win_assert_topmost(hwnd):
    # HWND_TOPMOST on an already-topmost window is a no-op for z-order reordering.
    # NOTOPMOST→TOPMOST cycle forces the window to the top of the topmost band.
    u = ctypes.windll.user32
    u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0013)  # HWND_NOTOPMOST
    u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)  # HWND_TOPMOST

class Platform:
    """Top edge of a desktop window Hornet can stand on. `segs` are the parts of
    that edge not hidden behind windows above it in z-order (where she can land)."""
    __slots__ = ('hwnd', 'l', 't', 'r', 'b', 'segs')

    def __init__(self, hwnd, l, t, r, b, segs):
        self.hwnd, self.l, self.t, self.r, self.b, self.segs = hwnd, l, t, r, b, segs


_PLATFORM_SKIP_CLASSES = {
    'Shell_TrayWnd', 'Shell_SecondaryTrayWnd', 'Windows.UI.Core.CoreWindow',
    'NotifyIconOverflowWindow', 'TopLevelWindowForOverflowXamlIsland',
    'XamlExplorerHostIslandWindow', 'Shell_InputSwitchTopLevelWindow',
    'TaskListThumbnailWnd',
}
_PLATFORM_MIN_W = 160      # narrower windows are not worth standing on
_scan_api = None


def _win_scan_api():
    import ctypes.wintypes as wt
    # Private DLL handles so these argtypes don't clash with the rest of the app
    u = ctypes.WinDLL('user32')
    d = ctypes.WinDLL('dwmapi')
    proto = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    u.EnumWindows.argtypes = [proto, wt.LPARAM]
    for name in ('IsWindowVisible', 'IsIconic', 'IsZoomed', 'GetWindowTextLengthW'):
        getattr(u, name).argtypes = [wt.HWND]
    u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    u.GetWindowLongW.restype = ctypes.c_long
    u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    d.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
    d.DwmGetWindowAttribute.restype = ctypes.c_long
    # DWM frame bounds are physical pixels, but this process is DPI-unaware and
    # everything else (cursor, monitors, our window) is in scaled pixels.
    g = ctypes.WinDLL('gdi32')
    u.GetDC.argtypes = [wt.HWND]
    u.GetDC.restype = wt.HDC
    u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    g.GetDeviceCaps.argtypes = [wt.HDC, ctypes.c_int]
    hdc = u.GetDC(None)
    logical, physical = g.GetDeviceCaps(hdc, 8), g.GetDeviceCaps(hdc, 118)  # HORZRES, DESKTOPHORZRES
    u.ReleaseDC(None, hdc)
    dwm_scale = logical / physical if logical and physical else 1.0
    return u, d, proto, wt, dwm_scale


def _subtract_span(segs, a, b):
    out = []
    for l, r in segs:
        if b <= l or a >= r:
            out.append((l, r))
            continue
        if a > l:
            out.append((l, a))
        if b < r:
            out.append((b, r))
    return out


def _win_scan_platforms():
    """Visible top-level windows as Platforms, topmost first. Never raises."""
    global _scan_api
    try:
        if _scan_api is None:
            _scan_api = _win_scan_api()
        u, d, proto, wt, dwm_scale = _scan_api
        own_pid = os.getpid()
        cls_buf = ctypes.create_unicode_buffer(128)
        wins = []

        def _cb(hwnd, _lp):
            if not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
                return True
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == own_pid:          # Hornet herself, her menus, color picker
                return True
            cloaked = ctypes.c_int(0)
            d.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)   # DWMWA_CLOAKED
            if cloaked.value:                 # other virtual desktop / hidden UWP shell
                return True
            r = wt.RECT()
            # Extended frame bounds = the visible frame (GetWindowRect adds invisible borders)
            if d.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) == 0:
                l, t = round(r.left * dwm_scale), round(r.top * dwm_scale)
                rr, bb = round(r.right * dwm_scale), round(r.bottom * dwm_scale)
            else:
                u.GetWindowRect(hwnd, ctypes.byref(r))
                l, t, rr, bb = r.left, r.top, r.right, r.bottom
            if rr - l < 4 or bb - t < 4:
                return True
            u.GetClassNameW(hwnd, cls_buf, 128)
            cls = cls_buf.value
            if cls in ('Progman', 'WorkerW'):  # the desktop itself
                return True
            ex = u.GetWindowLongW(hwnd, -20)
            if ex & 0x20:                      # WS_EX_TRANSPARENT overlays
                return True
            titled = u.GetWindowTextLengthW(hwnd) > 0
            is_plat = (titled and not (ex & 0x80) and cls not in _PLATFORM_SKIP_CLASSES
                       and not u.IsZoomed(hwnd) and rr - l >= _PLATFORM_MIN_W)
            wins.append((int(hwnd), l, t, rr, bb, is_plat))
            return True

        u.EnumWindows(proto(_cb), 0)
    except Exception as e:
        print(f"[platforms] scan failed: {e}")
        return []
    return _platforms_from_stack(wins)


def _platforms_from_stack(wins):
    """(id, l, t, r, b, is_plat) tuples, topmost first → Platforms whose top
    edges are clipped by the windows stacked above them."""
    plats = []
    for i, (h, l, t, r, b, is_plat) in enumerate(wins):
        if not is_plat:
            continue
        segs = [(l, r)]
        for (_h, l2, t2, r2, b2, _p) in wins[:i]:
            if t2 <= t < b2:                  # a window above covers this top edge
                segs = _subtract_span(segs, l2, r2)
                if not segs:
                    break
        segs = [(a, c) for a, c in segs if c - a >= 40]
        plats.append(Platform(h, l, t, r, b, segs))
    return plats


def _win_click_through(hwnd, enable: bool):
    u = ctypes.windll.user32
    s = u.GetWindowLongW(hwnd, -20)
    u.SetWindowLongW(hwnd, -20, (s | 0x20) if enable else (s & ~0x20))


# ─────────────────────────────────────────────────────────────────────────────
# Linux (X11) window platforms
# ─────────────────────────────────────────────────────────────────────────────

class X11PlatformScanner:
    """Linux counterpart of _win_scan_platforms: reads the EWMH stacking list
    (_NET_CLIENT_LIST_STACKING) on its own X connection. Needs an EWMH window
    manager (any mainstream X11 desktop has one)."""

    def __init__(self, own_wid=0):
        self._dpy = None
        self._own_wid = own_wid
        try:
            x = ctypes.CDLL(ctypes.util.find_library('X11'))
            x.XOpenDisplay.restype  = ctypes.c_void_p
            x.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x.XCloseDisplay.argtypes = [ctypes.c_void_p]
            x.XDefaultRootWindow.restype  = ctypes.c_ulong
            x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            x.XInternAtom.restype  = ctypes.c_ulong
            x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
            x.XGetWindowProperty.restype  = ctypes.c_int
            x.XGetWindowProperty.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long,
                ctypes.c_long, ctypes.c_int, ctypes.c_ulong,
                ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                ctypes.POINTER(ctypes.c_void_p)]
            x.XGetGeometry.restype  = ctypes.c_int
            x.XGetGeometry.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
                ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
            x.XTranslateCoordinates.restype  = ctypes.c_int
            x.XTranslateCoordinates.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int,
                ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong)]
            x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
            x.XFree.argtypes = [ctypes.c_void_p]
            # Windows can close between listing and querying them; Xlib's default
            # error handler would exit() the whole app on that BadWindow.
            self._err_proto = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
            self._err_handler = self._err_proto(lambda _d, _e: 0)
            x.XSetErrorHandler.restype  = ctypes.c_void_p
            x.XSetErrorHandler.argtypes = [ctypes.c_void_p]
            self._x = x
            self._dpy = x.XOpenDisplay(None)
            if not self._dpy:
                return
            self._root = x.XDefaultRootWindow(self._dpy)
            self._atoms = {n: x.XInternAtom(self._dpy, n.encode(), 0) for n in (
                '_NET_CLIENT_LIST_STACKING', '_NET_CURRENT_DESKTOP', '_NET_WM_DESKTOP',
                '_NET_WM_PID', '_NET_WM_STATE', '_NET_WM_STATE_HIDDEN',
                '_NET_WM_STATE_FULLSCREEN', '_NET_WM_STATE_MAXIMIZED_VERT',
                '_NET_WM_STATE_MAXIMIZED_HORZ', '_NET_WM_WINDOW_TYPE',
                '_NET_WM_WINDOW_TYPE_DESKTOP', '_NET_WM_WINDOW_TYPE_NORMAL',
                '_NET_WM_WINDOW_TYPE_DIALOG', '_NET_FRAME_EXTENTS', '_GTK_FRAME_EXTENTS')}
        except Exception as e:
            print(f"[platforms] X11 scanner unavailable: {e}")
            self._dpy = None

    @property
    def available(self):
        return bool(self._dpy)

    def close(self):
        if self._dpy:
            self._x.XCloseDisplay(self._dpy)
            self._dpy = None

    def _prop(self, win, name):
        """32-bit (CARDINAL/ATOM/WINDOW) property as a list of ints, [] if unset."""
        typ, fmt = ctypes.c_ulong(), ctypes.c_int()
        n, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        ok = self._x.XGetWindowProperty(
            self._dpy, win, self._atoms[name], 0, 1024, 0, 0,   # AnyPropertyType
            ctypes.byref(typ), ctypes.byref(fmt), ctypes.byref(n),
            ctypes.byref(after), ctypes.byref(data))
        if ok != 0 or not data.value:
            return []
        try:
            if fmt.value != 32:
                return []
            # Format-32 data is handed back as an array of C longs
            return list(ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[:n.value])
        finally:
            self._x.XFree(data)

    def _frame_rect(self, win):
        """Visible frame in root coordinates (WM decorations in, CSD shadows out)."""
        x = self._x
        root, gx, gy = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_int()
        w, h, bw, depth = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        if not x.XGetGeometry(self._dpy, win, ctypes.byref(root), ctypes.byref(gx),
                              ctypes.byref(gy), ctypes.byref(w), ctypes.byref(h),
                              ctypes.byref(bw), ctypes.byref(depth)):
            return None
        rx, ry, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
        if not x.XTranslateCoordinates(self._dpy, win, self._root, 0, 0,
                                       ctypes.byref(rx), ctypes.byref(ry), ctypes.byref(child)):
            return None
        l, t, r, b = rx.value, ry.value, rx.value + w.value, ry.value + h.value
        fe = self._prop(win, '_NET_FRAME_EXTENTS')        # left, right, top, bottom
        if len(fe) >= 4:
            l, r, t, b = l - fe[0], r + fe[1], t - fe[2], b + fe[3]
        gtk = self._prop(win, '_GTK_FRAME_EXTENTS')       # invisible shadow margins
        if len(gtk) >= 4:
            l, r, t, b = l + gtk[0], r - gtk[1], t + gtk[2], b - gtk[3]
        return l, t, r, b

    def scan(self):
        """Visible client windows as Platforms, topmost first. Never raises."""
        if not self._dpy:
            return []
        a = self._atoms
        prev = self._x.XSetErrorHandler(ctypes.cast(self._err_handler, ctypes.c_void_p))
        try:
            stack = self._prop(self._root, '_NET_CLIENT_LIST_STACKING')   # bottom → top
            cur = self._prop(self._root, '_NET_CURRENT_DESKTOP')
            cur = cur[0] if cur else None
            own_pid = os.getpid()
            wins = []
            for win in reversed(stack):
                if win == self._own_wid:
                    continue
                pid = self._prop(win, '_NET_WM_PID')
                if pid and pid[0] == own_pid:              # Hornet herself, her menus
                    continue
                desk = self._prop(win, '_NET_WM_DESKTOP')
                if cur is not None and desk and desk[0] not in (cur, 0xFFFFFFFF):
                    continue                               # other workspace
                state = set(self._prop(win, '_NET_WM_STATE'))
                if a['_NET_WM_STATE_HIDDEN'] in state:     # minimized
                    continue
                types = self._prop(win, '_NET_WM_WINDOW_TYPE')
                if a['_NET_WM_WINDOW_TYPE_DESKTOP'] in types:   # the desktop itself
                    continue
                rect = self._frame_rect(win)
                if rect is None:
                    continue
                l, t, r, b = rect
                if r - l < 4 or b - t < 4 or r <= 0 or b <= 0:   # tiny or parked off-screen
                    continue
                maximized = (a['_NET_WM_STATE_MAXIMIZED_VERT'] in state
                             and a['_NET_WM_STATE_MAXIMIZED_HORZ'] in state)
                normal = not types or types[0] in (a['_NET_WM_WINDOW_TYPE_NORMAL'],
                                                   a['_NET_WM_WINDOW_TYPE_DIALOG'])
                is_plat = (normal and not maximized
                           and a['_NET_WM_STATE_FULLSCREEN'] not in state
                           and r - l >= _PLATFORM_MIN_W)
                wins.append((int(win), l, t, r, b, is_plat))
        except Exception as e:
            print(f"[platforms] X11 scan failed: {e}")
            return []
        finally:
            self._x.XSync(self._dpy, 0)    # flush errors into our handler before restoring
            self._x.XSetErrorHandler(prev)
        return _platforms_from_stack(wins)


# ─────────────────────────────────────────────────────────────────────────────
# Sprite loading
# ─────────────────────────────────────────────────────────────────────────────

def _num_sorted(pattern):
    """Glob + sort by the trailing integer in filenames (natural order)."""
    paths = glob.glob(pattern)
    return sorted(paths, key=lambda p: int(re.search(r'(\d+)\.[^.]+$', p).group(1)))


def load_raw_assets():
    """Load sprite images without convert() -  safe to call before set_mode."""
    seqs = {
        'idle':       _num_sorted(_resource('assets/sprites/idle/hornet_idle_*.png')),
        'sit_down':   _num_sorted(_resource('assets/sprites/sit_down/sit_*.png')),
        'sit_intro':  _num_sorted(_resource('assets/sprites/sit_intro/sit_play_*.png')),
        'sit_loop':   _num_sorted(_resource('assets/sprites/sit_loop/hornet_sit_play_*.png')),
        'sit_outro':  _num_sorted(_resource('assets/sprites/sit_outro/sit_end_*.png')),
        'sit_up':     _num_sorted(_resource('assets/sprites/sit_up/sit_get_up_*.png')),
        'sleep_wake': _num_sorted(_resource('assets/sprites/sleep_wake/sleep_wake_*.png')),
        'land':       _num_sorted(_resource('assets/sprites/land/land_*.png')),
        'wall_cling': _num_sorted(_resource('assets/sprites/wall_cling/wall_cling_*.png')),
        'wall_slide': _num_sorted(_resource('assets/sprites/wall_slide/wall_slide_*.png')),
        'taunt':      _num_sorted(_resource('assets/sprites/taunt/taunt_[0-9]*.png')),
        'taunt_silk': _num_sorted(_resource('assets/sprites/taunt/taunt_silk_*.png')),
        'walk':       _num_sorted(_resource('assets/sprites/walk/walk_*.png')),
        'walk_stop':  _num_sorted(_resource('assets/sprites/walk_stop/walkstop_*.png')),
        'turn':       _num_sorted(_resource('assets/sprites/turn/turn_*.png')),
        'map_open':   _num_sorted(_resource('assets/sprites/map_open/map_open_*.png')),
        'map_idle':   _num_sorted(_resource('assets/sprites/map_idle/map_idle_*.png')),
        'map_walk':   _num_sorted(_resource('assets/sprites/map_walk/map_walk_*.png')),
        'map_turn':   _num_sorted(_resource('assets/sprites/map_turn/map_turn_*.png')),
        'run':        _num_sorted(_resource('assets/sprites/run/run_*.png')),
        'run_start':  _num_sorted(_resource('assets/sprites/run_start/run_start_*.png')),
        'run_stop':   _num_sorted(_resource('assets/sprites/run_stop/run_stop_*.png')),
        'jump':       _num_sorted(_resource('assets/sprites/jump/jump_*.png')),
        'hop':        _num_sorted(_resource('assets/sprites/hop/hop_*.png')),
        'hop_land':   _num_sorted(_resource('assets/sprites/hop_land/hop_land_*.png')),
        'somersault': _num_sorted(_resource('assets/sprites/somersault/somersault_*.png')),
        'fall':       _num_sorted(_resource('assets/sprites/fall/fall_*.png')),
        'weak_fall':  _num_sorted(_resource('assets/sprites/weak_fall/weak_fall_*.png')),
        'bonk_land':  _num_sorted(_resource('assets/sprites/bonk_land/bonk_land_*.png')),
        'wall_mantle': _num_sorted(_resource('assets/sprites/wall_mantle/wall_mantle_*.png')),
        'mantle_land': _num_sorted(_resource('assets/sprites/mantle_land/mantle_land_*.png')),
        'walljump':   _num_sorted(_resource('assets/sprites/walljump/walljump_*.png')),
        'climb':      _num_sorted(_resource('assets/sprites/climb/climb_*.png')),
        'climb_cling': _num_sorted(_resource('assets/sprites/climb_cling/climb_cling_*.png')),
        'walljump_antic': _num_sorted(_resource('assets/sprites/walljump_antic/walljump_antic_*.png')),
        'sit_rest':   _num_sorted(_resource('assets/sprites/sit_rest/sit_rest_*.png')),
        'umbrella_open':  _num_sorted(_resource('assets/sprites/umbrella_open/umbrella_open_*.png')),
        'umbrella_float': _num_sorted(_resource('assets/sprites/umbrella_float/umbrella_float_*.png')),
        'umbrella_close': _num_sorted(_resource('assets/sprites/umbrella_close/umbrella_close_*.png')),
    }
    singles = {
        'FAST_FALL':       _resource('assets/sprites/fast_fall/hornet_fast_fall.png'),
        'FAST_FALL_WRONG': _resource('assets/sprites/fast_fall/hornet_fast_fall_wrong.png'),
        'sleep':           _resource('assets/sprites/sleep_wake/sleep_wake_1.png'),
    }
    missing = [p for p in singles.values() if not os.path.exists(p)]
    missing += [f'assets/sprites/{k}/' for k, v in seqs.items() if not v]
    if missing:
        print('Missing sprites:', missing)
        sys.exit(1)
    raw_sprites = {k: pygame.image.load(v) for k, v in singles.items()}
    raw_seqs    = {k: [pygame.image.load(f) for f in v] for k, v in seqs.items()}
    return raw_sprites, raw_seqs


def _tint_cloak(surface, hex_color):
    """Return a copy of surface with colored pixels recolored towards hex_color:
    hue is replaced outright, saturation/value are blended towards the target's so
    that grayscale targets (black/white) actually darken/lighten the cloak instead
    of being ignored (hue is meaningless for zero-saturation colors).
    Pixels that are near-black (outlines/skin) or near-white (mask) are left untouched."""
    r_t = int(hex_color[1:3], 16) / 255.0
    g_t = int(hex_color[3:5], 16) / 255.0
    b_t = int(hex_color[5:7], 16) / 255.0
    target_h, target_s, target_v = colorsys.rgb_to_hsv(r_t, g_t, b_t)

    arr_rgb   = pygame.surfarray.array3d(surface).astype(np.float32) / 255.0
    arr_alpha = pygame.surfarray.array_alpha(surface)

    r, g, b = arr_rgb[:, :, 0], arr_rgb[:, :, 1], arr_rgb[:, :, 2]
    max_c = np.maximum(np.maximum(r, g), b)
    min_c = np.minimum(np.minimum(r, g), b)
    delta = max_c - min_c
    v = max_c
    s = np.where(max_c > 1e-6, delta / max_c, 0.0)

    # Only recolor pixels that have actual chroma and are neither too dark nor too light
    mask = (arr_alpha > 0) & (s > 0.10) & (v > 0.08) & (v < 0.97)

    # Saturation follows the target exactly (so black/white targets end up neutral
    # gray instead of tinted), value blends towards the target while keeping some
    # of the original shading contrast (highlights/shadows).
    ns = target_s
    nv = v * 0.45 + target_v * 0.55

    # Vectorized HSV→RGB using the fixed target hue and blended saturation/value
    h6 = target_h * 6.0
    hi = int(h6) % 6
    f  = h6 - int(h6)
    p  = nv * (1.0 - ns)
    q  = nv * (1.0 - f * ns)
    tv = nv * (1.0 - (1.0 - f) * ns)

    rgb_cases = [
        (nv, tv, p ),
        (q,  nv, p ),
        (p,  nv, tv),
        (p,  q,  nv),
        (tv, p,  nv),
        (nv, p,  q ),
    ]
    nr, ng, nb = rgb_cases[hi]

    out = (np.clip(np.stack([
        np.where(mask, nr, r),
        np.where(mask, ng, g),
        np.where(mask, nb, b),
    ], axis=-1), 0.0, 1.0) * 255.0).astype(np.uint8)

    new_surf = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
    pix = pygame.surfarray.pixels3d(new_surf)
    pix[:] = out
    del pix
    alp = pygame.surfarray.pixels_alpha(new_surf)
    alp[:] = arr_alpha
    del alp
    return new_surf


def convert_assets(raw_sprites, raw_seqs):
    """Convert raw surfaces to SRCALPHA with optional scaling and cloak tint."""
    def _conv(s):
        s = s.convert_alpha()
        if SPRITE_SCALE != 1.0:
            w = max(1, int(s.get_width()  * SPRITE_SCALE))
            h = max(1, int(s.get_height() * SPRITE_SCALE))
            s = pygame.transform.smoothscale(s, (w, h))
        # Always quantize alpha: snaps semi-transparent PNG edge pixels to 0 or 255
        # so they don't composite with the blue Win32 background and leave a visible fringe
        alpha = pygame.surfarray.pixels_alpha(s)
        alpha[:] = np.where(alpha < 128, 0, 255)
        del alpha
        if CLOAK_COLOR != 'default':
            s = _tint_cloak(s, CLOAK_COLOR)
        return s
    sprites = {k: _conv(v) for k, v in raw_sprites.items()}
    seqs    = {k: [_conv(s) for s in v] for k, v in raw_seqs.items()}
    return sprites, seqs


def load_app_icon(pre_display=False):
    for path in ICON_FILES:
        if os.path.exists(path):
            try:
                icon = pygame.image.load(path)
                if pre_display:
                    return icon  # no convert() -  display mode not set yet
                return icon.convert_alpha() if icon.get_alpha() else icon.convert()
            except Exception:
                pass
    return None


def set_windows_app_id():
    try:
        shell32 = ctypes.windll.shell32
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
        shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.HRESULT
        shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def set_windows_app_icon(hwnd):
    ico_path = next((os.path.abspath(p) for p in ICON_FILES
                     if p.lower().endswith('.ico') and os.path.exists(p)), None)
    if not ico_path:
        return
    try:
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        hicon = user32.LoadImageW(None, ico_path, ICON_IMAGE, 0, 0,
                                 LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not hicon:
            return
        user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
        user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Hornet entity
# ─────────────────────────────────────────────────────────────────────────────

DRAG_PIXELS   = 6       # pixels moved before a click becomes a drag
# Physics/offset constants below are set by load_config() at startup from config.json
FAST_FALL_VY  = 300.0
WRONG_MIX     = 0.65
ON_GROUND_TOL = 8
SIT_Y_OFFSET  = 0.235
IDLE_Y_OFFSET = -0.075
SLEEP_Y_OFFSET = 0.12
Z_OVERHEAD    = 70   # headroom (px at 100% scale) above sprite for sleeping Z particles


class ZParticle:
    __slots__ = ('x', 'y', 'vx', 'vy', 'age', 'lifetime', 'wobble_phase')
    def __init__(self, x, y, vx, vy, lifetime, wobble_phase):
        self.x = x; self.y = y
        self.vx = vx; self.vy = vy
        self.age = 0.0
        self.lifetime = lifetime
        self.wobble_phase = wobble_phase


class Hornet:
    GRAVITY       = 1800.0
    BOUNCE_DAMP   = 0.45
    FRICTION      = 0.88
    MIN_BOUNCE_VY = 80.0
    SIT_FPS       = 0.1
    IDLE_FPS      = 0.15
    SIT_PAUSE_DUR = 0.25   # pause between sit_down→sit_intro and sit_outro→sit_up
    SLEEP_FPS     = 0.08   # seconds per frame for sleep_wake animation
    SLEEP_TIMEOUT = 300.0  # seconds of ground inactivity before falling asleep
    LAND_FPS       = 0.04   # seconds per frame for land/wall-cling/wall-land animations
    WALL_SLIDE_FPS = 0.08   # seconds per frame for wall-slide animation
    TAUNT_FPS      = 0.05   # seconds per frame for taunt animation
    TAUNT_COOLDOWN = 120.0  # seconds before Hornet can be annoyed again
    TAUNT_HOVER_TIME = 2.5  # seconds cursor must hover near Hornet to trigger taunt
    RUN_IN_SKID    = 0.3    # run-in entrance: speed kept while skidding to a stop (x run speed)

    # Wandering: while idle on the ground she walks around and reads her map.
    # Walk speeds are derived from the stride so her feet don't slide.
    WANDER_IDLE_MIN    = 4.0    # seconds of standing still between activities (min)
    WANDER_IDLE_MAX    = 12.0   # ... and max
    WANDER_WALK_FPS    = 0.07   # seconds per frame for walk / walk start / walk stop
    WANDER_TURN_FPS    = 0.07   # seconds per frame for the turn-around
    WALK_STRIDE_PX     = 4.5    # px the planted foot travels per walk frame (unscaled art)
    MAP_OPEN_FPS       = 0.08   # seconds per frame for opening / putting away the map
    MAP_IDLE_FPS       = 0.12   # seconds per frame while standing and reading
    MAP_WALK_FPS       = 0.08   # seconds per frame while walking and reading
    MAP_WALK_STRIDE_PX = 3.7    # px the planted foot travels per map-walk frame (unscaled art)
    WANDER_WALK_DIST   = (150.0, 500.0)  # px range of a walk (unscaled)
    MAP_WALK_DIST      = (60.0, 250.0)   # px range of a walk while reading (unscaled)

    # Getting around windows: running, jumping, climbing (window_platforms)
    IDLE_FACE_X     = 92.8    # face column in the idle frame (unscaled art)
    WALL_FACE_D     = 50.0    # face distance from the wall while clinging (unscaled art)
    RUN_FPS         = 0.06    # seconds per frame for run / run start / run stop
    RUN_STRIDE_PX   = 21.0    # px the planted foot travels per run frame (unscaled art)
    CLIMB_FPS       = 0.06    # seconds per frame for the scramble leap / cling / wall-jump antic
    CLIMB_CROUCH    = 0.08    # seconds crouched against the wall before each leap
    CLIMB_SETTLE    = 0.16    # seconds settled in the cling after each leap
    CLIMB_HOP       = 0.9     # height gained per scramble leap (x her height)
    MANTLE_FPS      = 0.07    # seconds per frame for pulling up over a ledge
    HOP_LAND_FPS    = 0.05    # seconds per frame for the light landing after a jump
    BONK_LAND_FPS   = 0.07    # seconds per frame for landing after a tumble
    SIT_REST_FPS    = 0.1     # seconds per frame for the quiet sit (no music)
    EXTRA_SEQS = ('run', 'run_start', 'run_stop', 'jump', 'hop', 'hop_land', 'somersault',
                  'fall', 'weak_fall', 'bonk_land', 'wall_mantle', 'mantle_land', 'walljump',
                  'climb', 'climb_cling', 'walljump_antic', 'sit_rest')
    GROUND_PHASES = frozenset(('turn', 'walk_start', 'walk', 'walk_stop', 'run_start', 'run',
                               'run_stop', 'map_open', 'map_idle', 'map_turn', 'map_walk',
                               'map_close'))
    WALL_PHASES   = frozenset(('climb_crouch', 'climb_leap', 'climb_settle', 'cling',
                               'walljump_antic'))
    CENTRED_LANDS = frozenset(('hop_land', 'bonk_land'))

    # Umbrella glide (land_mode == 'glide'): high falls open the umbrella and drift down.
    GLIDE_FPS          = 0.07   # seconds per frame for the float loop
    GLIDE_OPEN_FPS     = 0.05   # seconds per frame for the open (inflate) animation
    GLIDE_CLOSE_FPS    = 0.06   # seconds per frame for the close (deflate) animation
    GLIDE_FALL_VY      = 140.0  # px/sec terminal fall speed while gliding
    GLIDE_MIN_HEIGHT   = 250.0  # px above the floor required to open the umbrella
    GLIDE_TRIGGER_VY   = 250.0  # px/sec of downward speed before the umbrella opens
    GLIDE_DRAG         = 4.0    # 1/sec: how fast vy eases toward GLIDE_FALL_VY
    GLIDE_AIR_DRAG     = 1.2    # 1/sec: decay of horizontal momentum while gliding
    GLIDE_SWAY_AMP     = 30.0   # px side-to-side sway amplitude
    GLIDE_SWAY_PERIOD  = 2.4    # seconds per full sway cycle

    # Grab-swing pendulum: sprite pivots around the grip point while dragged.
    DRAG_SWING_DAMPING   = 0.16    # damping ratio; <1 = oscillates before settling
    DRAG_SWING_IMPULSE_K = 0.0022  # rad/s of ang-vel per (px/s) of pivot-accel impulse
    DRAG_SWING_MAX_DEG   = 60.0    # cap angle so the sprite never flips upside-down
    DRAG_SWING_MAX_DVX   = 3500.0  # px/s cap on per-frame velocity delta (spike guard)

    _z_font      = None
    _z_font_size = 0

    def __init__(self, x, y, sprites, seqs, floor_y,
                 monitors=None, world_left=0.0, world_w=0.0, world_top=0.0):
        self.x = float(x);  self.y = float(y)
        self.vx = 0.0;      self.vy = 0.0

        # Multi-monitor physics bounds: `monitors` is a list of per-monitor
        # (mon_left, mon_top, mon_right, mon_bottom, work_left, work_top,
        #  work_right, work_bottom) tuples used to pick the correct floor
        # (taskbar height) for whichever monitor she's currently over.
        # world_left/world_w/world_top bound the combined desktop for
        # left/right/top wall bounces.
        self.monitors   = monitors or []
        self.world_left = float(world_left)
        self.world_w    = float(world_w)
        self.world_top  = float(world_top)

        self.sprites            = sprites
        self.idle_frames        = seqs['idle']
        self.sit_down_frames    = seqs['sit_down']
        self.sit_intro_frames   = seqs['sit_intro']
        self.sit_loop_frames    = seqs['sit_loop']
        self.sit_outro_frames   = seqs['sit_outro']
        self.sit_up_frames      = seqs['sit_up']
        self.sleep_wake_frames  = seqs['sleep_wake']
        self.sleep_frame        = sprites['sleep']
        self.land_frames        = seqs['land']
        self.wall_cling_frames  = seqs['wall_cling']
        self.wall_slide_frames  = seqs['wall_slide']
        self.taunt_frames       = seqs['taunt']
        self.taunt_silk_frames  = seqs['taunt_silk']
        self.walk_frames        = seqs['walk']
        self.walk_stop_frames   = seqs['walk_stop']
        self.umbrella_open_frames  = seqs['umbrella_open']
        self.umbrella_float_frames = seqs['umbrella_float']
        self.umbrella_close_frames = seqs['umbrella_close']
        self.turn_frames        = seqs['turn']
        self.map_open_frames    = seqs['map_open']
        self.map_idle_frames    = seqs['map_idle']
        self.map_walk_frames    = seqs['map_walk']
        self.map_turn_frames    = seqs['map_turn']
        self.bind_extra_frames(seqs)
        self.floor_y            = floor_y

        self.state        = 'IDLE'
        self.facing_right = True

        self.idle_idx   = 0
        self.idle_timer = 0.0

        # sit_phase: None|'sit_down'|'sit_pause_pre'|'sit_intro'|
        #            'sit_loop'|'sit_outro'|'sit_pause_post'|'sit_up'
        self.sit_phase = None
        self.sit_idx   = 0
        self.sit_timer = 0.0

        # sleep_phase: None|'falling_asleep'|'sleeping'|'waking'
        self.sleep_phase      = None
        self.sleep_idx        = 0
        self.sleep_timer      = 0.0
        self.inactivity_timer = 0.0
        self.z_particles      = []
        self.z_spawn_timer    = 0.0

        # land_phase: None|'land'|'wall_cling'|'wall_slide'|'wall_land'
        self.land_phase = None
        self.land_idx   = 0
        self.land_timer = 0.0
        self.wall_side  = None  # 'left' | 'right'  — which wall she clung to

        # glide_phase: None|'open'|'float'|'close'
        self.glide_phase = None
        self.glide_idx   = 0
        self.glide_timer = 0.0
        self.glide_t     = 0.0   # time since the umbrella opened (drives the sway)
        self.glide_drift = 0.0   # horizontal momentum carried into the glide

        # wander_phase: None|'turn'|'walk_start'|'walk'|'walk_stop'|
        #               'map_open'|'map_idle'|'map_turn'|'map_walk'|'map_close'
        self.wander_phase     = None
        self.wander_idx       = 0
        self.wander_timer     = 0.0
        self.wander_wait      = random.uniform(self.WANDER_IDLE_MIN, self.WANDER_IDLE_MAX)
        self.wander_dir       = 0       # -1 = walking left, +1 = walking right
        self.wander_target_x  = 0.0
        self.wander_then_map  = False   # open the map after this walk stops
        self.wander_map_time  = 0.0     # seconds left reading before deciding what's next
        self.wander_map_walks = 0       # map walks left before putting the map away
        self.wander_gait      = 'walk'  # 'walk' | 'run' for the current goto

        # Plans: queued steps ('goto', 'jump', 'climb', 'walljump', 'map', 'sit')
        self.plan         = []
        self.plan_running = False

        # Window platforms
        self.platforms  = []     # Platform list from the last window scan
        self.support    = None   # Platform she's standing on (None = monitor floor)
        self.floor_plat = None   # Platform the current floor_y belongs to

        # Airborne on her own (jumps, drops): None|'jump'|'hop'|'somersault'|
        #                                      'walljump'|'fall'|'weak_fall'
        self.air_anim   = None
        self.air_idx    = 0
        self.air_t      = 0.0
        self.air_apex_t = None
        self.air_catch  = None   # (t, x, y, climb step) to grab a wall mid-jump

        # Climbing: the wall she's on and where she stops
        self.climb_wall_x = 0.0
        self.climb_side   = 'left'   # which side of her the wall is on
        self.climb_stop_y = 0.0
        self.climb_ledge  = None     # hwnd of the window to mantle onto at the top
        self.leap_y0      = 0.0      # y where the current scramble leap started
        self.leap_h       = 0.0      # height of the current scramble leap
        self.leaps_left   = 0        # scramble leaps still needed to reach the top
        self.climb_rect   = None     # that window's rect when she started climbing it
        self.wj_target    = None     # (x, y, catch) for the queued wall jump

        # Quiet sit (no music) used when she rests on her own
        self.sit_quiet     = False
        self.sit_rest_time = 0.0

        # taunt state
        self.taunt_phase         = None   # None | 'taunting'
        self.taunt_idx           = 0
        self.taunt_timer         = 0.0
        self.taunt_cooldown_timer = 0.0   # counts down; taunt allowed when <= 0
        self.taunt_hover_timer   = 0.0    # how long cursor has been near Hornet

        # walk-in entrance state: None | 'walking' | 'stopping'
        self.walk_in_phase    = None
        self.walk_in_idx      = 0
        self.walk_in_timer    = 0.0
        self.walk_in_target_x = 0.0
        self.walk_in_dir      = 0        # -1 = walking left, +1 = walking right

        # Single-frame events consumed by main()
        self.ev_music_start = False
        self.ev_music_stop  = False

        self._pending  = False
        self._pend_mx  = 0;  self._pend_my  = 0
        self.dragging  = False
        self._off_x    = 0.0; self._off_y   = 0.0
        self._mvx      = 0.0; self._mvy     = 0.0
        self._last_mx  = 0;   self._last_my  = 0

        # Grab-swing pendulum state (set on _start_drag, animated in update()).
        # _grip_local_(x|y) is the cursor position expressed in the displayed
        # (post-flip) idle frame's pixel coords; rotation pivots around it.
        # _drag_rest_angle is the orientation at which the CoM hangs directly
        # below the grip under gravity, so grabs off-centre tilt Hornet immediately.
        self._drag_angle      = 0.0
        self._drag_ang_vel    = 0.0
        self._drag_rest_angle = 0.0
        self._drag_L          = 30.0
        self._grip_local_x    = 0.0
        self._grip_local_y    = 0.0

    # ── helpers ───────────────────────────────────────────────────────────────
    @property
    def sitting(self):
        return self.sit_phase is not None

    @property
    def sleeping(self):
        return self.sleep_phase is not None

    @property
    def taunting(self):
        return self.taunt_phase is not None

    @property
    def walking_in(self):
        return self.walk_in_phase is not None

    @property
    def gliding(self):
        return self.glide_phase is not None

    @property
    def wandering(self):
        return self.wander_phase is not None

    def bind_extra_frames(self, seqs):
        for k in self.EXTRA_SEQS:
            setattr(self, k + '_frames', seqs[k])

    @property
    def _idle_w(self): return self.idle_frames[0].get_width()
    @property
    def _idle_h(self): return self.idle_frames[0].get_height()

    def current_frame(self) -> pygame.Surface:
        if self.walk_in_phase == 'walking':
            return self.run_frames[self.walk_in_idx]
        if self.walk_in_phase == 'stopping':
            return self.run_stop_frames[self.walk_in_idx]
        if self.sleep_phase == 'falling_asleep':
            rev = len(self.sleep_wake_frames) - 1 - self.sleep_idx
            return self.sleep_wake_frames[rev]
        if self.sleep_phase == 'sleeping':
            return self.sleep_frame
        if self.sleep_phase == 'waking':
            return self.sleep_wake_frames[self.sleep_idx]
        if self.land_phase == 'land':
            return self.land_frames[self.land_idx]
        if self.land_phase == 'wall_cling':
            return self.wall_cling_frames[self.land_idx]
        if self.land_phase == 'wall_slide':
            return self.wall_slide_frames[self.land_idx]
        if self.land_phase == 'wall_land':
            # sleep_wake frames 11-14 are indices 10-13
            return self.sleep_wake_frames[10 + self.land_idx]
        if self.wander_phase is not None:
            return self._wander_frame()
        if self.air_anim is not None:
            return getattr(self, self.air_anim + '_frames')[self.air_idx]
        if self.land_phase == 'hop_land':
            return self.hop_land_frames[self.land_idx]
        if self.land_phase == 'bonk_land':
            return self.bonk_land_frames[self.land_idx]
        if self.glide_phase == 'open':
            return self.umbrella_open_frames[self.glide_idx]
        if self.glide_phase == 'float':
            return self.umbrella_float_frames[self.glide_idx]
        if self.glide_phase == 'close':
            return self.umbrella_close_frames[self.glide_idx]
        if self.taunt_phase == 'taunting':
            return self.taunt_frames[self.taunt_idx]
        if self.sit_phase == 'sit_down':
            return self.sit_down_frames[self.sit_idx]
        if self.sit_phase == 'sit_pause_pre':
            return self.sit_down_frames[-1]
        if self.sit_phase == 'sit_rest':
            return self.sit_rest_frames[self.sit_idx]
        if self.sit_phase == 'sit_intro':
            return self.sit_intro_frames[self.sit_idx]
        if self.sit_phase == 'sit_loop':
            return self.sit_loop_frames[self.sit_idx]
        if self.sit_phase == 'sit_outro':
            return self.sit_outro_frames[self.sit_idx]
        if self.sit_phase == 'sit_pause_post':
            return self.sit_outro_frames[-1]
        if self.sit_phase == 'sit_up':
            return self.sit_up_frames[self.sit_idx]
        if self.state == 'IDLE':
            return self.idle_frames[self.idle_idx]
        return self.sprites[self.state]

    def is_clicked(self, mx, my) -> bool:
        return (self.x <= mx <= self.x + self._idle_w and
                self.y <= my <= self.y + self._idle_h)

    def is_on_ground(self) -> bool:
        return self.y >= self.floor_y - ON_GROUND_TOL and abs(self.vy) < 60

    def _floor_for_x(self, cx) -> float:
        """Work-area bottom (minus sprite height) of whichever monitor sits under x=cx.
        Falls back to the nearest monitor by edge distance if cx is over a gap."""
        if not self.monitors:
            return self.floor_y
        idle_h = self._idle_h
        best_wb = None
        best_dist = None
        for (ml, mt, mr, mb, wl, wt, wr, wb) in self.monitors:
            dist = 0.0 if ml <= cx <= mr else min(abs(cx - ml), abs(cx - mr))
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_wb = wb
        return float(best_wb - idle_h)

    # ── events ────────────────────────────────────────────────────────────────
    def mouse_down(self, mx, my):
        if not self.is_clicked(mx, my):
            return
        if self.sleep_phase in ('falling_asleep', 'waking'):
            return  # ignore during sleep transitions
        self.inactivity_timer = 0.0
        self._pending = True
        self._pend_mx = mx;  self._pend_my = my
        self._last_mx = mx;  self._last_my = my
        self._mvx = 0.0;     self._mvy = 0.0

    def mouse_move(self, mx, my, dt):
        if self.sleeping:
            return  # no dragging while asleep or transitioning
        if self._pending:
            if math.hypot(mx - self._pend_mx, my - self._pend_my) >= DRAG_PIXELS:
                self._pending = False
                if self.sitting:
                    self._cancel_sit_drag()
                self._start_drag(self._pend_mx, self._pend_my)
                self._upd_drag(mx, my, dt)
        elif self.dragging:
            self._upd_drag(mx, my, dt)

    def mouse_up(self, mx, my):
        if self._pending:
            self._pending = False
            if self.sleep_phase == 'sleeping':
                # Wake up on click
                self.sleep_phase = 'waking'
                self.sleep_idx   = 0
                self.sleep_timer = 0.0
                return
            if self.sleeping:
                return  # ignore during sleep transitions
            if self.sit_phase == 'sit_rest':
                # Resting on her own: a click makes her play for you instead
                self._cancel_wander()
                self.sit_quiet = False
                self.sit_phase = 'sit_pause_pre'
                self.sit_timer = 0.0
            elif self.sit_phase == 'sit_loop':
                # Graceful exit: play the outro sequence
                self.sit_phase = 'sit_outro'
                self.sit_idx   = 0
                self.sit_timer = 0.0
                self.ev_music_stop = True
            elif self.sit_phase is not None:
                pass  # Ignore clicks during transition animations
            elif self.is_on_ground():
                # Clear any overlapping states whose frames and update priority would
                # shadow the sit animation: land frame renders on top of sit frames in
                # current_frame(), and taunt blocks _update_sit() via early return.
                self.land_phase  = None
                self.land_idx    = 0
                self.land_timer  = 0.0
                self.taunt_phase = None
                self.taunt_idx   = 0
                self.taunt_timer = 0.0
                self.glide_phase = None
                self._cancel_wander()
                self.sit_phase = 'sit_down'
                self.sit_idx   = 0
                self.sit_timer = 0.0
        elif self.dragging:
            self._end_drag()

    def _cancel_sit_drag(self):
        """Immediately cancel all sit phases when dragged away."""
        if self.sit_phase in ('sit_loop', 'sit_outro'):
            self.ev_music_stop = True
        self.sit_phase = None
        self.sit_idx   = 0
        self.sit_timer = 0.0
        self.sit_quiet = False

    def _start_drag(self, mx, my):
        self.land_phase  = None
        self.glide_phase = None
        self.air_anim    = None
        self.air_catch   = None
        self.climb_ledge = None
        self.climb_rect  = None
        self._cancel_wander()
        # Grabbing during the walk-in entrance cancels it so the user is in control.
        self.walk_in_phase = None
        self.walk_in_idx   = 0
        self.walk_in_timer = 0.0
        # Taunt survives across mouse_down (update() only cancels it once dragging
        # is True); cancel it here so the frame we base the grip on is an idle frame.
        self.taunt_phase = None
        self.taunt_idx   = 0
        self.dragging = True
        self._off_x = self.x - mx
        self._off_y = self.y - my
        self.vx = self.vy = 0.0
        # Grip is stored relative to the idle frame that will actually be drawn
        # during the drag (state is forced to IDLE by _upd_state while dragging).
        idle_frame = self.idle_frames[self.idle_idx]
        fw = idle_frame.get_width()
        fh = idle_frame.get_height()
        draw_x_idle = int(self.x)
        draw_y_idle = int(self.y) + self._idle_h - fh + self._idle_y_offset()
        self._grip_local_x = float(mx - draw_x_idle)
        self._grip_local_y = float(my - draw_y_idle)
        # Rest angle: rotation needed so the sprite's centre of mass (approximated
        # as the frame centre) hangs directly below the grip under gravity.
        # Using the pygame rotation matrix R(θ)·v = (v_x·cos + v_y·sin,
        # -v_x·sin + v_y·cos), we want R(θ)·v = (0, |v|); solving gives
        # sin θ = -v_x/|v|, cos θ = v_y/|v| → θ = atan2(-v_x, v_y).
        v_x = fw * 0.5 - self._grip_local_x
        v_y = fh * 0.5 - self._grip_local_y
        self._drag_L = max(30.0, math.hypot(v_x, v_y))
        self._drag_rest_angle = math.atan2(-v_x, v_y) if (v_x or v_y) else 0.0
        # Start upright and let gravity swing Hornet into the rest pose over time
        # — snapping to _drag_rest_angle looks unphysical.
        self._drag_angle   = 0.0
        self._drag_ang_vel = 0.0
        # Seed velocity tracker at the press point so the first _update_drag_swing
        # measures cursor delta from where the user actually clicked.
        self._last_mx = mx
        self._last_my = my
        self._mvx = 0.0
        self._mvy = 0.0

    def _upd_drag(self, mx, my, dt):
        # Position sync on MOUSEMOTION. Velocity tracking + pendulum physics run
        # in _update_drag_swing() so they fire every frame, including the frame
        # the mouse stops moving (no MOUSEMOTION event → deceleration would be
        # missed if we tracked velocity here).
        self.x = mx + self._off_x
        self.y = my + self._off_y

    def _end_drag(self):
        self.dragging = False
        self.vx = self._mvx * 0.8
        self.vy = self._mvy * 0.8
        self.inactivity_timer = 0.0

    def _rotated_bounds(self, angle_rad):
        """Bounding box of the (idle) sprite rotated by ``angle_rad`` around the
        grip, expressed as offsets from the pivot (min_x, min_y, max_x, max_y)."""
        idle_frame = self.idle_frames[self.idle_idx]
        fw = idle_frame.get_width()
        fh = idle_frame.get_height()
        gx = self._grip_local_x
        gy = self._grip_local_y
        cos_a = math.cos(angle_rad); sin_a = math.sin(angle_rad)
        # Four corners relative to the grip, rotated with pygame's convention
        # (x' = x·cos + y·sin ; y' = -x·sin + y·cos).
        xs = []; ys = []
        for (cx, cy) in ((0.0 - gx, 0.0 - gy),
                          (fw  - gx, 0.0 - gy),
                          (0.0 - gx, fh  - gy),
                          (fw  - gx, fh  - gy)):
            xs.append(cx * cos_a + cy * sin_a)
            ys.append(-cx * sin_a + cy * cos_a)
        return min(xs), min(ys), max(xs), max(ys)

    def _bounds_fit(self, angle_rad, pivot_x, pivot_y):
        """True if the sprite rotated by ``angle_rad`` around ``(pivot_x, pivot_y)``
        fits inside the world bounds (taskbar as bottom, monitor rect as sides/top)."""
        min_x, min_y, max_x, max_y = self._rotated_bounds(angle_rad)
        return (pivot_x + min_x >= self.world_left
                and pivot_x + max_x <= self.world_left + self.world_w
                and pivot_y + min_y >= self.world_top
                and pivot_y + max_y <= self.floor_y + self._idle_h)

    def _update_drag_swing(self, dt, mx, my):
        """Pendulum simulation while dragged.

        The spring pulls Hornet toward ``_drag_rest_angle`` (gravity-aligned pose
        where the frame centre hangs below the grip); starting from angle 0 makes
        the fall to that pose progressive. Rotation is constrained by the world
        edges + taskbar: instead of teleporting the pivot up, we bisect the swing
        angle so the rotated sprite rests against the barrier — that way grabbing
        Hornet's feet on the taskbar keeps her almost-but-not-fully upside-down
        until the user lifts, exactly matching the "she falls onto the taskbar"
        expectation.
        """
        if dt <= 0 or mx is None or my is None:
            return
        # Position sync in case no MOUSEMOTION event fired this frame.
        self.x = mx + self._off_x
        self.y = my + self._off_y
        # Angular impulse from change in pivot velocity (bounded to tame spikes).
        # Rightward pivot jerk (positive d_vx) makes the body lag left of the
        # pivot, which is a visually-CW tilt = pygame-negative angle → the
        # negation aligns physics with pygame's CCW-positive rotation convention.
        new_mvx = (mx - self._last_mx) / dt
        new_mvy = (my - self._last_my) / dt
        d_vx = new_mvx - self._mvx
        cap = self.DRAG_SWING_MAX_DVX
        if d_vx >  cap: d_vx =  cap
        if d_vx < -cap: d_vx = -cap
        self._drag_ang_vel += -d_vx * self.DRAG_SWING_IMPULSE_K
        self._mvx = new_mvx
        self._mvy = new_mvy
        self._last_mx = mx
        self._last_my = my
        if not tray_globals.get('drag_pendulum', True):
            # Pendulum disabled: keep _drag_angle at 0 so _blit_rotated / _rotated_bounds
            # collapse to axis-aligned no-ops; throw velocity is still tracked above.
            self._drag_angle = 0.0
            self._drag_ang_vel = 0.0
            return
        # Spring toward the rest angle + viscous damping (standard pendulum ODE).
        omega_n_sq  = self.GRAVITY / self._drag_L
        two_zeta_wn = 2.0 * self.DRAG_SWING_DAMPING * math.sqrt(omega_n_sq)
        delta = self._drag_angle - self._drag_rest_angle
        ang_accel = -omega_n_sq * math.sin(delta) - two_zeta_wn * self._drag_ang_vel
        self._drag_ang_vel += ang_accel * dt
        proposed_angle = self._drag_angle + self._drag_ang_vel * dt
        # ── barrier collision ────────────────────────────────────────────────
        # Prefer clamping the angle (sprite rotates only as far as it fits at the
        # current pivot). Only if the pivot itself is deep enough that no angle
        # works do we fall back to lifting the pivot away from the barrier.
        pivot_x = self.x - self._off_x
        pivot_y = self.y - self._off_y
        if not self._bounds_fit(proposed_angle, pivot_x, pivot_y):
            if self._bounds_fit(self._drag_angle, pivot_x, pivot_y):
                # Bisect between last-frame angle (fits) and proposal (doesn't).
                lo, hi = self._drag_angle, proposed_angle
                for _ in range(12):
                    mid = (lo + hi) * 0.5
                    if self._bounds_fit(mid, pivot_x, pivot_y):
                        lo = mid
                    else:
                        hi = mid
                proposed_angle = lo
                # Kill outward velocity so gravity doesn't keep piling into the wall.
                self._drag_ang_vel = 0.0
            else:
                # Even the previous angle doesn't fit — pivot moved deeper into a
                # wall (e.g. cursor dragged below the taskbar). Fall back to the
                # rest angle and clamp the pivot itself so nothing goes off-screen.
                proposed_angle = self._drag_rest_angle
                min_x, min_y, max_x, max_y = self._rotated_bounds(proposed_angle)
                world_right  = self.world_left + self.world_w
                floor_bottom = self.floor_y + self._idle_h
                if pivot_x + min_x < self.world_left:
                    pivot_x = self.world_left - min_x
                elif pivot_x + max_x > world_right:
                    pivot_x = world_right - max_x
                if pivot_y + min_y < self.world_top:
                    pivot_y = self.world_top - min_y
                if pivot_y + max_y > floor_bottom:
                    pivot_y = floor_bottom - max_y
                self.x = pivot_x + self._off_x
                self.y = pivot_y + self._off_y
                self._drag_ang_vel = 0.0
        self._drag_angle = proposed_angle

    # ── state ─────────────────────────────────────────────────────────────────
    def _upd_state(self):
        if (self.sitting or self.dragging or self.land_phase is not None
                or self.gliding or self.wandering or self.air_anim is not None):
            self.state = 'IDLE'; return
        spd = math.hypot(self.vx, self.vy)
        if spd < 80 or self.vy <= 0:
            self.state = 'IDLE'
        elif self.vy >= FAST_FALL_VY:
            self.state = ('FAST_FALL_WRONG'
                          if abs(self.vx)/(spd+1e-9) > WRONG_MIX else 'FAST_FALL')
        else:
            self.state = 'IDLE'

    # ── sit state machine ─────────────────────────────────────────────────────
    def _update_sit(self, dt):
        p = self.sit_phase

        if p == 'sit_down':
            self.sit_timer += dt
            if self.sit_timer >= self.SIT_FPS:
                self.sit_timer = 0.0
                self.sit_idx += 1
                if self.sit_idx >= len(self.sit_down_frames):
                    self.sit_phase = 'sit_rest' if self.sit_quiet else 'sit_pause_pre'
                    self.sit_idx   = 0
                    self.sit_timer = 0.0

        elif p == 'sit_rest':
            self.sit_rest_time -= dt
            self.sit_timer += dt
            if self.sit_timer >= self.SIT_REST_FPS:
                self.sit_timer = 0.0
                self.sit_idx = (self.sit_idx + 1) % len(self.sit_rest_frames)
            if self.sit_rest_time <= 0:
                self.sit_phase = 'sit_up'
                self.sit_idx   = 0
                self.sit_timer = 0.0

        elif p == 'sit_pause_pre':
            self.sit_timer += dt
            if self.sit_timer >= self.SIT_PAUSE_DUR:
                self.sit_phase = 'sit_intro'
                self.sit_idx   = 0
                self.sit_timer = 0.0

        elif p == 'sit_intro':
            self.sit_timer += dt
            if self.sit_timer >= self.SIT_FPS:
                self.sit_timer = 0.0
                self.sit_idx += 1
                if self.sit_idx >= len(self.sit_intro_frames):
                    self.sit_phase      = 'sit_loop'
                    self.sit_idx        = 0
                    self.sit_timer      = 0.0
                    self.ev_music_start = True

        elif p == 'sit_loop':
            self.sit_timer += dt
            if self.sit_timer >= self.SIT_FPS:
                self.sit_timer = 0.0
                self.sit_idx = (self.sit_idx + 1) % len(self.sit_loop_frames)

        elif p == 'sit_outro':
            self.sit_timer += dt
            if self.sit_timer >= self.SIT_FPS:
                self.sit_timer = 0.0
                self.sit_idx += 1
                if self.sit_idx >= len(self.sit_outro_frames):
                    self.sit_phase = 'sit_pause_post'
                    self.sit_timer = 0.0

        elif p == 'sit_pause_post':
            self.sit_timer += dt
            if self.sit_timer >= self.SIT_PAUSE_DUR:
                self.sit_phase = 'sit_up'
                self.sit_idx   = 0
                self.sit_timer = 0.0

        elif p == 'sit_up':
            self.sit_timer += dt
            if self.sit_timer >= self.SIT_FPS:
                self.sit_timer = 0.0
                self.sit_idx += 1
                if self.sit_idx >= len(self.sit_up_frames):
                    self.sit_phase = None  # done -  back to idle
                    if self.sit_quiet:
                        self.sit_quiet = False
                        if self.plan_running:
                            self._next_step()

    # ── sleep state machine ───────────────────────────────────────────────────
    def _update_sleep(self, dt):
        p = self.sleep_phase
        n = len(self.sleep_wake_frames)

        if p == 'falling_asleep':
            self.sleep_timer += dt
            if self.sleep_timer >= self.SLEEP_FPS:
                self.sleep_timer = 0.0
                self.sleep_idx += 1
                if self.sleep_idx >= n:
                    self.sleep_phase = 'sleeping'
                    self.sleep_idx   = 0

        elif p == 'waking':
            self.sleep_timer += dt
            if self.sleep_timer >= self.SLEEP_FPS:
                self.sleep_timer = 0.0
                self.sleep_idx += 1
                if self.sleep_idx >= n:
                    self.sleep_phase      = None
                    self.sleep_idx        = 0
                    self.inactivity_timer = 0.0

        self._update_z_particles(dt)

    # ── landing / wall-cling state machine ────────────────────────────────────
    def _update_land(self, dt):
        p = self.land_phase
        world_right = self.world_left + self.world_w

        if p == 'land':
            self.land_timer += dt
            if self.land_timer >= self.LAND_FPS:
                self.land_timer = 0.0
                self.land_idx += 1
                if self.land_idx >= len(self.land_frames):
                    self.land_phase = None  # back to idle
                    self._land_done()

        elif p in ('hop_land', 'bonk_land'):
            frames = self.hop_land_frames if p == 'hop_land' else self.bonk_land_frames
            fps    = self.HOP_LAND_FPS if p == 'hop_land' else self.BONK_LAND_FPS
            self.land_timer += dt
            if self.land_timer >= fps:
                self.land_timer = 0.0
                self.land_idx += 1
                if self.land_idx >= len(frames):
                    self.land_phase = None
                    self._land_done()

        elif p == 'wall_cling':
            if self.wall_side == 'right':
                self.x = float(world_right - self.wall_cling_frames[self.land_idx].get_width())
            self.land_timer += dt
            if self.land_timer >= self.LAND_FPS:
                self.land_timer = 0.0
                self.land_idx += 1
                if self.land_idx >= len(self.wall_cling_frames):
                    self.land_phase = 'wall_slide'
                    self.land_idx   = 0
                    self.land_timer = 0.0
                    self.vy         = 0.0

        elif p == 'wall_slide':
            # Apply gravity so she slides down naturally
            self.vy += self.GRAVITY * dt
            self.y  += self.vy * dt
            # Keep her pinned to the wall she clung to
            if self.wall_side == 'left':
                self.x = self.world_left
            else:
                self.x = float(world_right - self.wall_slide_frames[self.land_idx].get_width())
            # Advance animation (clamp at last frame)
            self.land_timer += dt
            if self.land_timer >= self.WALL_SLIDE_FPS:
                self.land_timer = 0.0
                if self.land_idx < len(self.wall_slide_frames) - 1:
                    self.land_idx += 1
            # Floor under the wall she's clinging to (recomputed each tick in
            # case monitors of different heights meet at this world edge, or a
            # window top sits under her)
            self.floor_y = self._floor_here()
            # Transition to wall_land when she reaches the floor
            if self.y >= self.floor_y:
                self.y = self.floor_y
                self.vy = 0.0
                # Snap x back to the idle-width boundary so wake frames align with idle
                if self.wall_side == 'right':
                    self.x = float(world_right - self._idle_w)
                else:
                    self.x = self.world_left
                self.facing_right = (self.wall_side == 'right')
                self.land_phase = 'wall_land'
                self.land_idx   = 0
                self.land_timer = 0.0

        elif p == 'wall_land':
            self.land_timer += dt
            if self.land_timer >= self.LAND_FPS:
                self.land_timer = 0.0
                self.land_idx += 1
                if self.land_idx >= 4:  # sleep_wake frames 11-14 = 4 frames
                    self.land_phase = None  # back to idle

    def _start_wall_cling(self, side):
        world_right = self.world_left + self.world_w
        self.vx           = 0.0
        self.vy           = 0.0
        self.wall_side    = side
        self.facing_right = (side == 'left')
        self.land_phase   = 'wall_cling'
        self.land_idx     = 0
        self.land_timer   = 0.0
        if side == 'left':
            self.x = self.world_left
        else:
            self.x = float(world_right - self.wall_cling_frames[0].get_width())

    # ── umbrella glide ────────────────────────────────────────────────────────
    def _start_glide(self):
        self.air_anim    = None
        self.air_catch   = None
        self.glide_phase = 'open'
        self.glide_idx   = 0
        self.glide_timer = 0.0
        self.glide_t     = 0.0
        self.glide_drift = self.vx

    def _glide_bottom(self) -> float:
        """Lowest visible pixel of the current glide frame, in the same space as
        the idle frame's bottom edge (self.y + idle_h). The needle tip hangs below
        her feet, so it touches the floor first."""
        return self.y + self.current_frame().get_bounding_rect().bottom

    def _update_glide(self, dt):
        p = self.glide_phase
        world_right = self.world_left + self.world_w
        self.inactivity_timer = 0.0

        # Animation
        self.glide_timer += dt
        if p == 'open':
            if self.glide_timer >= self.GLIDE_OPEN_FPS:
                self.glide_timer = 0.0
                self.glide_idx += 1
                if self.glide_idx >= len(self.umbrella_open_frames):
                    self.glide_phase = 'float'
                    self.glide_idx   = 0
        elif p == 'float':
            if self.glide_timer >= self.GLIDE_FPS:
                self.glide_timer = 0.0
                self.glide_idx = (self.glide_idx + 1) % len(self.umbrella_float_frames)
        elif p == 'close':
            if self.glide_timer >= self.GLIDE_CLOSE_FPS:
                self.glide_timer = 0.0
                if self.glide_idx < len(self.umbrella_close_frames) - 1:
                    self.glide_idx += 1

        # Physics
        self.glide_drift *= math.exp(-self.GLIDE_AIR_DRAG * dt)
        if self.glide_phase == 'close':
            # Umbrella folded: regular gravity for the last drop to the floor
            self.vy += self.GRAVITY * dt
            self.vx  = self.glide_drift
        else:
            self.glide_t += dt
            self.vy += (self.GLIDE_FALL_VY - self.vy) * (1.0 - math.exp(-self.GLIDE_DRAG * dt))
            # Sway eases in so she doesn't jerk sideways the moment it opens
            w    = 2.0 * math.pi / self.GLIDE_SWAY_PERIOD
            ramp = min(1.0, self.glide_t / 0.8)
            self.vx = self.glide_drift + self.GLIDE_SWAY_AMP * w * math.cos(w * self.glide_t) * ramp
        # Face the carried momentum only; the sway alone must not flip her back and forth.
        # Source art faces left, so unflipped (facing_right=True) means moving left.
        if abs(self.glide_drift) > 30:
            self.facing_right = self.glide_drift < 0
        self.x += self.vx * dt
        self.y += self.vy * dt

        if self.y < self.world_top:
            self.y  = self.world_top
            self.vy = max(self.vy, 0.0)

        # Walls: she still clings, however gently she drifts into them
        if self.x < self.world_left:
            self.glide_phase = None
            self._start_wall_cling('left')
            return
        if self.x > world_right - self._idle_w:
            self.glide_phase = None
            self._start_wall_cling('right')
            return

        # Floor: fold the umbrella when the needle touches, land when she does
        floor_line = self.floor_y + self._idle_h
        if self._glide_bottom() >= floor_line:
            if self.glide_phase != 'close':
                self.glide_phase = 'close'
                self.glide_idx   = 0
                self.glide_timer = 0.0
            else:
                self._end_glide_land()
        elif self.y > self.floor_y + self._idle_h:
            # Safety net: never sink through the floor (e.g. floor rose under her)
            self._end_glide_land()

    def _end_glide_land(self):
        self.glide_phase = None
        self.y          = self.floor_y
        self.vx         = 0.0
        self.vy         = 0.0
        self.land_phase = 'land'
        self.land_idx   = 0
        self.land_timer = 0.0

    def _spawn_z_particle(self):
        sw = self.sleep_frame.get_width()
        sh = self.sleep_frame.get_height()
        if self.facing_right:
            base_x = sw * 0.32
        else:
            base_x = sw * 0.68
        base_y = sh * 0.30
        x  = base_x + random.uniform(-6, 6) * SPRITE_SCALE
        y  = base_y + random.uniform(-4, 4) * SPRITE_SCALE
        vx = (1 if self.facing_right else -1) * random.uniform(3, 7) * SPRITE_SCALE
        vy = random.uniform(-16, -22) * SPRITE_SCALE
        lifetime     = random.uniform(2.0, 2.8)
        wobble_phase = random.uniform(0, math.pi * 2)
        self.z_particles.append(ZParticle(x, y, vx, vy, lifetime, wobble_phase))

    def _update_z_particles(self, dt):
        alive = []
        for p in self.z_particles:
            p.age += dt
            if p.age < p.lifetime:
                p.x += p.vx * dt
                p.y += p.vy * dt
                alive.append(p)
        self.z_particles = alive
        if tray_globals['sleep_z'] and self.sleep_phase == 'sleeping' and len(self.z_particles) < 3:
            self.z_spawn_timer += dt
            if self.z_spawn_timer >= 1.2:
                self.z_spawn_timer = 0.0
                self._spawn_z_particle()

    def draw_z_particles(self, surface, ox, oy):
        if not self.z_particles:
            return
        font_size = max(12, self._idle_h // 7)
        if Hornet._z_font is None or Hornet._z_font_size != font_size:
            Hornet._z_font      = pygame.font.SysFont(None, font_size, bold=True)
            Hornet._z_font_size = font_size
        font = Hornet._z_font
        for p in self.z_particles:
            t = p.age / p.lifetime
            if t >= 1.0:
                continue
            # Fade brightness white→gray and stop drawing near the end.
            # Using colour brightness (not set_alpha) so semi-transparent pixels
            # never get composited against the Win32 chroma-key blue background.
            v = int(255 * max(0.0, 1.0 - t * 1.15))
            if v < 20:
                continue
            wobble_x = math.sin(p.wobble_phase + p.age * 2.8) * 4 * SPRITE_SCALE
            px = int(ox + p.x + wobble_x)
            py = int(oy + p.y)
            glyph = font.render('Z', True, (v, v, v)).convert_alpha()
            # Quantize per-pixel alpha to 0/255 — same trick as sprite loading —
            # so no pixel partially blends with the chroma-key background.
            arr = pygame.surfarray.pixels_alpha(glyph)
            arr[:] = np.where(arr > 127, 255, 0)
            del arr
            surface.blit(glyph, (px, py))

    def _start_sleep(self):
        self.vx           = 0.0
        self.vy           = 0.0
        self.sleep_phase  = 'falling_asleep'
        self.sleep_idx    = 0
        self.sleep_timer  = 0.0
        self.inactivity_timer = 0.0
        self.z_particles  = []
        self.z_spawn_timer = 0.0

    # ── taunt state machine ───────────────────────────────────────────────────
    def _start_taunt(self):
        self._cancel_wander()
        self.taunt_phase         = 'taunting'
        self.taunt_idx           = 0
        self.taunt_timer         = 0.0
        self.taunt_cooldown_timer = self.TAUNT_COOLDOWN
        self.taunt_hover_timer   = 0.0

    def _update_taunt(self, dt):
        self.taunt_timer += dt
        if self.taunt_timer >= self.TAUNT_FPS:
            self.taunt_timer = 0.0
            self.taunt_idx += 1
            if self.taunt_idx >= len(self.taunt_frames):
                self.taunt_phase = None
                self.taunt_idx   = 0

    def draw_taunt_silk(self, surface, ox, oy):
        """Draw the silk layer behind Hornet during taunt frames 6–13 (1-indexed)."""
        if self.taunt_phase != 'taunting':
            return
        # Silk appears during taunt frames 6–13 (0-indexed: 5–12)
        if not (5 <= self.taunt_idx <= 12):
            return
        silk_raw = self.taunt_silk_frames[self.taunt_idx - 5]
        if not self.facing_right:
            silk_raw = pygame.transform.flip(silk_raw, True, False)
        # Center silk on the taunt frame's center (ox/oy is the taunt frame's top-left)
        taunt_frame = self.taunt_frames[self.taunt_idx]
        sx = ox + (taunt_frame.get_width()  - silk_raw.get_width())  // 2
        sy = oy + (taunt_frame.get_height() - silk_raw.get_height()) // 2
        surface.blit(silk_raw, (sx, sy))

    # ── walk-in entrance state machine ────────────────────────────────────────
    def _start_walk_in(self, from_side, target_x):
        """Begin the walk-in entrance from off-screen.
        from_side: 'right' (walk leftward) | 'left' (walk rightward).
        target_x: x-coord where the walk should stop (Hornet's top-left).
        Source sprites face left; facing_right=True renders unmirrored (left-facing),
        facing_right=False flips to right-facing — same convention used everywhere."""
        self.walk_in_phase    = 'walking'
        self.walk_in_idx      = 0
        self.walk_in_timer    = 0.0
        self.walk_in_target_x = float(target_x)
        if from_side == 'right':
            self.walk_in_dir  = -1
            self.facing_right = True   # walking leftward → source orientation
        else:
            self.walk_in_dir  = +1
            self.facing_right = False  # walking rightward → flipped
        self.vx = 0.0
        self.vy = 0.0
        self.y  = self.floor_y

    def _update_walk_in(self, dt):
        # Runs in at the run's own stride speed (no foot sliding), then skids to a stop
        speed = self.RUN_STRIDE_PX * SPRITE_SCALE / self.RUN_FPS
        skid  = speed * self.RUN_IN_SKID
        if self.walk_in_phase == 'walking':
            self.x += speed * self.walk_in_dir * dt
            # Start the stop early so the skid ends right on the target
            stop_at = (self.walk_in_target_x
                       - self.walk_in_dir * skid * len(self.run_stop_frames) * self.RUN_FPS)
            reached = (self.walk_in_dir < 0 and self.x <= stop_at) or \
                      (self.walk_in_dir > 0 and self.x >= stop_at)
            if reached:
                self.walk_in_phase = 'stopping'
                self.walk_in_idx   = 0
                self.walk_in_timer = 0.0
                return
            self.walk_in_timer += dt
            if self.walk_in_timer >= self.RUN_FPS:
                self.walk_in_timer = 0.0
                self.walk_in_idx = (self.walk_in_idx + 1) % len(self.run_frames)
        elif self.walk_in_phase == 'stopping':
            self.x += skid * self.walk_in_dir * dt
            self.walk_in_timer += dt
            if self.walk_in_timer >= self.RUN_FPS:
                self.walk_in_timer = 0.0
                self.walk_in_idx += 1
                if self.walk_in_idx >= len(self.run_stop_frames):
                    self.x = self.walk_in_target_x
                    self.walk_in_phase = None
                    self.walk_in_idx   = 0
                    # facing_right is preserved so the idle sprite keeps the same
                    # inward-facing orientation Hornet had while stopping.

    # ── wandering & getting around ────────────────────────────────────────────
    # Everything she does on her own is a *plan*: a queue of steps run one after
    # another. Ground and wall moves play as wander phases; jumps hand over to the
    # physics with an air_anim, and the plan resumes once she has landed.
    #   ('goto', x, gait)                         walk/run to x on her surface
    #   ('jump', x, y, style[, climb_step])       ballistic jump; optional wall catch
    #   ('climb', wall_x, side, stop_y, ledge)    climb a wall, mantle onto ledge
    #   ('walljump', x, y, climb_step|None)       from a cling, leap to x,y
    #   ('map',)  ('sit', seconds)
    def _wander_frame(self) -> pygame.Surface:
        p, i = self.wander_phase, self.wander_idx
        if p == 'turn':       return self.turn_frames[i]
        if p == 'walk_start': return self.walk_stop_frames[-1 - i]   # stop, played backwards
        if p == 'walk':       return self.walk_frames[i]
        if p == 'walk_stop':  return self.walk_stop_frames[i]
        if p == 'run_start':  return self.run_start_frames[i]
        if p == 'run':        return self.run_frames[i]
        if p == 'run_stop':   return self.run_stop_frames[i]
        if p == 'map_open':   return self.map_open_frames[i]
        if p == 'map_idle':   return self.map_idle_frames[i]
        if p == 'map_turn':   return self.map_turn_frames[i]
        if p == 'map_walk':   return self.map_walk_frames[i]
        if p == 'map_close':  return self.map_open_frames[-1 - i]
        if p == 'climb_crouch':   return self.climb_frames[0]
        if p == 'climb_leap':     return self.climb_frames[1 + i]
        if p == 'climb_settle':   return self.climb_frames[-1]
        if p == 'cling':          return self.climb_cling_frames[i]
        if p == 'walljump_antic': return self.walljump_antic_frames[i]
        if p == 'mantle':         return self.wall_mantle_frames[0]   # rolling over the corner
        return self.mantle_land_frames[i]                             # mantle_land

    def _wander_seq_info(self, p):
        """(frame count, seconds per frame, loops?) for a wander phase."""
        return {
            'turn':       (len(self.turn_frames),      self.WANDER_TURN_FPS, False),
            'walk_start': (len(self.walk_stop_frames), self.WANDER_WALK_FPS, False),
            'walk':       (len(self.walk_frames),      self.WANDER_WALK_FPS, True),
            'walk_stop':  (len(self.walk_stop_frames), self.WANDER_WALK_FPS, False),
            'run_start':  (len(self.run_start_frames), self.RUN_FPS,         False),
            'run':        (len(self.run_frames),       self.RUN_FPS,         True),
            'run_stop':   (len(self.run_stop_frames),  self.RUN_FPS,         False),
            'map_open':   (len(self.map_open_frames),  self.MAP_OPEN_FPS,    False),
            'map_idle':   (len(self.map_idle_frames),  self.MAP_IDLE_FPS,    True),
            'map_turn':   (len(self.map_turn_frames),  self.MAP_OPEN_FPS,    False),
            'map_walk':   (len(self.map_walk_frames),  self.MAP_WALK_FPS,    True),
            'map_close':  (len(self.map_open_frames),  self.MAP_OPEN_FPS,    False),
            'climb_crouch':   (1,                               self.CLIMB_CROUCH, False),
            'climb_leap':     (len(self.climb_frames) - 2,      self.CLIMB_FPS,    False),
            'climb_settle':   (1,                               self.CLIMB_SETTLE, False),
            'cling':          (len(self.climb_cling_frames),    self.CLIMB_FPS,   False),
            'walljump_antic': (len(self.walljump_antic_frames), self.CLIMB_FPS,   False),
            'mantle':         (1,                               self.MANTLE_FPS * 2, False),
            'mantle_land':    (len(self.mantle_land_frames),    self.MANTLE_FPS,  False),
        }[p]

    def _set_wander_phase(self, p):
        self.wander_phase = p
        self.wander_idx   = 0
        self.wander_timer = 0.0

    def _cancel_wander(self):
        """Interrupted (grabbed, clicked, taunted, dropped): forget the plan."""
        self.wander_phase = None
        self.wander_idx   = 0
        self.wander_timer = 0.0
        self.plan         = []
        self.plan_running = False
        self.wj_target    = None
        self.wander_wait  = max(self.wander_wait, self.WANDER_IDLE_MIN)

    def _end_wander(self):
        self.wander_phase = None
        self.wander_idx   = 0
        self.wander_timer = 0.0
        self.plan         = []
        self.plan_running = False
        self.idle_idx     = 0
        self.idle_timer   = 0.0
        self.wander_wait  = random.uniform(self.WANDER_IDLE_MIN, self.WANDER_IDLE_MAX)

    # ── plan runner ──
    def _start_plan(self, steps):
        self.plan         = list(steps)
        self.plan_running = True
        self._next_step()

    def _next_step(self):
        self.wander_phase = None
        self.wander_idx   = 0
        self.wander_timer = 0.0
        if not self.plan:
            self._end_wander()
            return
        step, args = self.plan[0][0], self.plan[0][1:]
        self.plan.pop(0)
        if step == 'goto':
            self._start_goto(*args)
        elif step == 'jump':
            self._start_jump(*args)
        elif step == 'climb':
            self._start_climb(*args)
        elif step == 'walljump':
            self.wj_target = args
            self._set_wander_phase('walljump_antic')
        elif step == 'map':
            self._start_map()
        elif step == 'sit':
            self._start_rest_sit(*args)
        else:
            self._next_step()

    def _land_done(self):
        """A landing animation finished: carry on with the plan, if any."""
        if self.plan_running:
            self._next_step()

    def _start_goto(self, x, gait='walk'):
        lo, hi = self._wander_bounds()
        x = min(max(x, lo - self._idle_w), hi + self._idle_w)
        if abs(x - self.x) < 3:
            self._next_step()
            return
        self.wander_dir      = 1 if x > self.x else -1
        self.wander_target_x = x
        self.wander_gait     = gait
        want_facing = self.wander_dir < 0   # source art faces left
        if want_facing != self.facing_right:
            self.facing_right = want_facing
            self._set_wander_phase('turn')
        else:
            self._set_wander_phase('run_start' if gait == 'run' else 'walk_start')

    def _start_map(self):
        self.wander_map_walks = random.choice((0, 1, 1, 2))
        self._set_wander_phase('map_open')

    def _start_rest_sit(self, seconds):
        self.wander_phase  = None
        self.sit_quiet     = True
        self.sit_rest_time = seconds
        self.sit_phase     = 'sit_down'
        self.sit_idx       = 0
        self.sit_timer     = 0.0

    # ── air ──
    def _start_air(self, kind, vx=0.0, vy=0.0):
        self.air_anim   = kind
        self.air_idx    = 0
        self.air_t      = 0.0
        self.air_apex_t = None
        self.air_catch  = None
        self.vx, self.vy = vx, vy
        self.support    = None

    def _start_jump(self, tx, ty, style, catch=None):
        """Ballistic jump so her top-left lands on (tx, ty); with `catch` she
        grabs a wall at (tx, ty) instead and starts that climb step."""
        g    = self.GRAVITY
        lift = {'hop': 0.25, 'jump': 0.45, 'somersault': 0.6, 'walljump': 0.3}[style] * self._idle_h
        apex = min(self.y, ty) - lift
        up   = math.sqrt(2.0 * g * max(1.0, self.y - apex))
        t    = up / g + math.sqrt(2.0 * max(0.0, ty - apex) / g)
        vx   = (tx - self.x) / t
        self.wander_phase = None
        self._start_air(style, vx, -up)
        if abs(vx) > 1:
            self.facing_right = vx < 0      # source art faces left
        if catch:
            self.air_catch = (t, tx, ty, catch)

    def _tick_air(self, dt):
        self.air_t += dt
        k, t = self.air_anim, self.air_t
        n = len(getattr(self, k + '_frames'))
        if self.vy >= 0 and self.air_apex_t is None:
            self.air_apex_t = t
        falling = self.air_apex_t is not None
        if k == 'jump':        # 0-4 rising, 5-14 turning over into the fall
            self.air_idx = (min(n - 1, 5 + int((t - self.air_apex_t) / 0.06)) if falling
                            else min(4, int(t / 0.06)))
        elif k == 'hop':       # 1-2 rising, 3-5 coming down
            self.air_idx = (min(n - 1, 3 + int((t - self.air_apex_t) / 0.08)) if falling
                            else min(2, 1 + int(t / 0.08)))
        elif k in ('somersault', 'walljump'):   # lead-in, then the spin loops
            loop_from = 5 if k == 'somersault' else 2
            i = int(t / 0.045)
            self.air_idx = i if i < loop_from else loop_from + (i - loop_from) % (n - loop_from)
        elif k == 'weak_fall':  # startled, then tumbling
            i = int(t / 0.07)
            self.air_idx = i if i < 2 else 2 + (i - 2) % (n - 2)
        else:                   # fall: play once, hold the last frame
            self.air_idx = min(n - 1, int(t / 0.07))

    def _air_land(self, impact_vy):
        kind = self.air_anim
        self.air_anim  = None
        self.air_catch = None
        self.vx = self.vy = 0.0
        if kind == 'weak_fall':
            self.land_phase = 'bonk_land'
        elif kind == 'fall' or impact_vy > 1100:
            self.land_phase = 'land'
        else:
            self.land_phase = 'hop_land'
        self.land_idx   = 0
        self.land_timer = 0.0

    # ── walls ──
    def _wall_pos_x(self, wall_x, side):
        """Top-left x that puts her hands on a wall at wall_x on her `side`."""
        fx = self.IDLE_FACE_X * SPRITE_SCALE
        d  = self.WALL_FACE_D * SPRITE_SCALE
        if side == 'left':
            return wall_x + d - fx
        return wall_x - d - (self._idle_w - fx)

    def _start_climb(self, wall_x, side, stop_y, ledge=None, caught=False):
        if ledge is not None:
            p = self._platform(ledge)
            if p is None:           # the window is gone: give up on this plan
                self._end_wander()
                return
            self.climb_rect = (p.l, p.t, p.r, p.b)
        else:
            self.climb_rect = None
        self.climb_wall_x = wall_x
        self.climb_side   = side
        self.climb_stop_y = stop_y
        self.climb_ledge  = ledge
        self.facing_right = (side == 'left')   # wall sprites have the wall on their left
        self.x = self._wall_pos_x(wall_x, side)
        self.vx = self.vy = 0.0
        self.support = None
        # Split the climb into even leaps so none is a full launch for a tiny step;
        # a short bit left (grabbed right under the ledge) is just a pull-up
        rise = self.y - stop_y
        hop  = self._idle_h * self.CLIMB_HOP
        self.leaps_left = 0 if rise < self._idle_h * 0.4 else max(1, round(rise / hop))
        # Caught mid-jump: she's already on the wall, so settle first
        if caught or self.leaps_left == 0:
            self._set_wander_phase('climb_settle')
        else:
            self._set_wander_phase('climb_crouch')

    def _climb_arrive(self):
        """Reached the top of the climb: pull up onto the ledge, or hold on."""
        self.y = self.climb_stop_y
        if self.climb_ledge is not None:
            self._start_mantle()
        else:
            self._set_wander_phase('cling')

    def _start_mantle(self):
        p = self._platform(self.climb_ledge)
        if p is None:
            self._drop('fall')
            return
        iw = self._idle_w
        self.y = p.t - self._idle_h
        # Pull up over the corner onto the window top (ledge on the wall's side)
        self.x = p.r - iw * 0.75 if self.climb_side == 'left' else p.l - iw * 0.25
        self.climb_ledge = None
        self.climb_rect  = None
        self._set_wander_phase('mantle')

    # ── surfaces ──
    def _platform(self, hwnd):
        for p in self.platforms:
            if p.hwnd == hwnd:
                return p
        return None

    def _monitor_work(self, cx=None):
        """(left, top, right, bottom) work area of the monitor under x=cx."""
        if cx is None:
            cx = self.x + self._idle_w / 2
        for (ml, mt, mr, mb, wl, wt, wr, wb) in self.monitors:
            if ml <= cx <= mr:
                return wl, wt, wr, wb
        return (self.world_left, self.world_top,
                self.world_left + self.world_w, self.floor_y + self._idle_h)

    def _wander_bounds(self):
        """Hard x range (top-left) for walking: the monitor she's on, so she never
        walks onto a monitor with another floor."""
        wl, wt, wr, wb = self._monitor_work()
        lo = max(self.world_left, wl)
        hi = min(self.world_left + self.world_w, wr) - self._idle_w
        return lo, hi

    def _surface_span(self):
        """(lo, hi) span her centre can use on the surface she's standing on."""
        cx = self.x + self._idle_w / 2
        wl, wt, wr, wb = self._monitor_work(cx)
        if self.support is not None:
            segs = self.support.segs or [(self.support.l, self.support.r)]
            for a, b in segs:
                if a <= cx <= b:
                    return max(a, wl), min(b, wr)
            return max(self.support.l, wl), min(self.support.r, wr)
        return wl, wr

    def _surface_bounds(self):
        """Comfortable x range (top-left) for strolling on her current surface."""
        lo, hi = self._surface_span()
        iw = self._idle_w
        if self.support is not None:
            a, b = lo - iw / 2 + iw * 0.15, hi - iw / 2 - iw * 0.15
        else:
            a, b = lo + iw * 0.3, hi - iw * 1.3
        return a, b

    def _pick_wander_target(self, dist_range):
        """Random (dir, target_x) with enough room, or None if she's boxed in."""
        dmin, dmax = (d * SPRITE_SCALE for d in dist_range)
        lo, hi = self._surface_bounds()
        dirs = [-1, 1]
        random.shuffle(dirs)
        for d in dirs:
            room = (self.x - lo) if d < 0 else (hi - self.x)
            if room >= dmin:
                return d, self.x + d * random.uniform(dmin, min(dmax, room))
        return None

    # ── choosing what to do ──
    def _stroll_plan(self):
        pick = self._pick_wander_target(self.WANDER_WALK_DIST)
        if not pick:
            return None
        steps = [('goto', pick[1], 'walk')]
        if random.random() < 0.3:
            steps.append(('map',))
        return steps

    def _choose_wander(self):
        r = random.random()
        explore_ok = tray_globals.get('window_platforms', True) and bool(self.platforms)
        plan = None
        if self.support is not None:
            # Up on a window: rest, look around, move on
            if r < 0.22:
                plan = [('sit', random.uniform(8.0, 20.0))]
            elif r < 0.72:
                if explore_ok and random.random() < 0.4:
                    plan = self._plan_explore()
                plan = plan or self._plan_leave()
            elif r < 0.82:
                plan = self._stroll_plan()
            elif r < 0.92:
                plan = [('map',)]
        else:
            if explore_ok and r < 0.35:
                plan = self._plan_explore()
            if plan is None:
                r2 = random.random()
                if r2 < 0.55:
                    plan = self._stroll_plan() or [('map',)]
                elif r2 < 0.85:
                    plan = [('map',)]
        if plan:
            self._start_plan(plan)
        else:
            # Just keep standing a while longer
            self.wander_wait = random.uniform(self.WANDER_IDLE_MIN, self.WANDER_IDLE_MAX)

    def _gait(self, x):
        return 'run' if abs(x - self.x) > self._idle_w * 2.5 else 'walk'

    def _after_arrival(self):
        r = random.random()
        if r < 0.35:
            return [('sit', random.uniform(8.0, 20.0))]
        if r < 0.6:
            return [('map',)]
        return []

    def _plan_explore(self):
        """Pick a window to get onto from where she stands and build the moves."""
        options = []
        for p in self.platforms:
            if self.support is not None and p.hwnd == self.support.hwnd:
                continue
            for a, b in p.segs:
                options += self._routes_to(p, a, b)
        if not options:
            return None
        iw = self._idle_w
        weights = [1.0 / (1.0 + dist / (iw * 4.0)) for _, dist in options]
        plan = random.choices([o for o, _ in options], weights)[0]
        return plan + self._after_arrival()

    def _routes_to(self, p, a, b):
        """Ways onto the visible top segment [a, b] of window p: list of (plan, distance)."""
        iw, ih = self._idle_w, self._idle_h
        wl, wt, wr, wb = self._monitor_work()
        a, b = max(a, wl), min(b, wr)
        if b - a < iw * 0.9 or p.t - ih < wt + 4:
            return []                       # too narrow, or no headroom under the screen top
        feet  = self.y + ih                 # her surface
        h     = feet - p.t                  # how far up the window top is
        s_lo, s_hi = self._surface_bounds()
        s_lo, s_hi = min(s_lo, self.x), max(s_hi, self.x)
        stop_y = p.t - ih * 0.55            # head over the ledge: time to mantle
        out = []

        def reachable(x):
            return s_lo - 2 <= x <= s_hi + 2

        # 1) Jump straight onto the top (low windows, or windows below her)
        if h <= ih * 1.25:
            for side in (-1, 1):
                land_cx = a + iw * 0.45 if side < 0 else b - iw * 0.45
                for dx in (iw * random.uniform(0.9, 2.0), iw * 0.6):
                    take_cx = land_cx + side * dx
                    tx = take_cx - iw / 2
                    if not reachable(tx):
                        continue
                    if h > 0 and a - iw * 0.2 < take_cx < b + iw * 0.2:
                        continue            # don't jump up through the window from below
                    style = ('somersault' if h < -ih * 1.5 or dx > iw * 2.6
                             else 'hop' if abs(h) < ih * 0.35 and dx < iw * 1.6 else 'jump')
                    out.append(([('goto', tx, self._gait(tx)),
                                 ('jump', land_cx - iw / 2, p.t - ih, style)],
                                abs(tx - self.x) + abs(h)))
                    break

        # 2) Climb one of the window's sides and pull up over the top corner
        #    (lower windows are simply jumped onto)
        if h > ih * 1.0:
            sides = []
            if a <= p.l + iw * 0.1 and b >= p.l + iw * 0.6:
                sides.append((p.l, 'right', p.l - iw * 0.85, -1))   # wall on her right
            if b >= p.r - iw * 0.1 and a <= p.r - iw * 0.6:
                sides.append((p.r, 'left', p.r - iw * 0.15, 1))     # wall on her left
            for wall_x, side, stand_x, away in sides:
                if not (wl <= wall_x <= wr):
                    continue
                gap = feet - p.b            # window bottom above her feet
                climb = ('climb', wall_x, side, stop_y, p.hwnd)
                if gap <= ih * 0.45 and reachable(stand_x):
                    out.append(([('goto', stand_x, self._gait(stand_x)), climb],
                                abs(stand_x - self.x) + h))
                elif gap <= ih * 1.4:
                    catch_y = p.b - ih * 0.75
                    if catch_y <= stop_y + ih * 0.3:
                        continue
                    take_x = stand_x + away * iw * 0.8
                    if reachable(take_x):
                        out.append(([('goto', take_x, self._gait(take_x)),
                                     ('jump', self._wall_pos_x(wall_x, side), catch_y, 'jump',
                                      climb)],
                                    abs(take_x - self.x) + h))

        # 3) Too high: climb the screen edge, then wall-jump across onto it
        if h > ih * 1.25 and self.support is None:
            world_r = self.world_left + self.world_w
            for edge, near, side, toward in ((self.world_left, p.l, 'left', 1),
                                             (world_r, p.r, 'right', -1)):
                if not (wl - 1 <= edge <= wr + 1):
                    continue            # that screen edge isn't on her monitor
                gap = (near - edge) * toward
                if not (iw * 0.6 <= gap <= iw * 4.5):
                    continue
                corner_ok = (a <= p.l + iw * 0.1) if side == 'left' else (b >= p.r - iw * 0.1)
                if not corner_ok:
                    continue
                stand_x = edge if side == 'left' else edge - iw
                tall = p.b - p.t > ih * 1.6
                over_ok = p.t - ih * 1.6 >= wt + ih * 0.35   # room to cling above the window top
                if not (tall or over_ok):
                    continue
                if tall and (not over_ok or random.random() < 0.5):
                    # Grab the window's side mid-height, then climb it
                    catch_y = p.t + random.uniform(0.25, 0.5) * (p.b - p.t) - ih * 0.5
                    cling_y = catch_y - ih * 0.35
                    wall_side = 'right' if side == 'left' else 'left'
                    target = (self._wall_pos_x(near, wall_side), catch_y,
                              ('climb', near, wall_side, stop_y, p.hwnd))
                else:
                    cling_y = p.t - ih * 1.6
                    land_cx = near + toward * iw * 0.45
                    target = (land_cx - iw / 2, p.t - ih, None)
                if cling_y < wt or cling_y >= self.y - ih * 0.5:
                    continue
                out.append(([('goto', stand_x, 'run'),
                             ('climb', edge, side, cling_y, None),
                             ('walljump',) + target],
                            abs(stand_x - self.x) + h + iw * 2))
        return out

    def _plan_leave(self):
        """From a window top: jump down to the floor beside it, or step off the edge."""
        if self.support is None:
            return None
        iw, ih = self._idle_w, self._idle_h
        lo, hi = self._surface_span()
        wl, wt, wr, wb = self._monitor_work()
        sides = [(-1, lo), (1, hi)]
        random.shuffle(sides)
        for d, edge in sides:
            land_cx = edge + d * iw * random.uniform(0.9, 2.5)
            if not (wl + iw * 0.6 <= land_cx <= wr - iw * 0.6):
                continue
            ground_y = self._floor_for_x(land_cx)
            drop = ground_y - self.y
            if random.random() < 0.35:
                # Just walk off the edge and drop
                return [('goto', edge + d * iw * 0.6 - iw / 2, 'walk')]
            take_x = edge - d * iw * 0.35 - iw / 2
            style = 'somersault' if drop > ih * 2.0 else 'jump'
            return [('goto', take_x, self._gait(take_x)),
                    ('jump', land_cx - iw / 2, ground_y, style)]
        return None

    # ── per-tick ──
    def _wander_walk_step(self, dt, stride_px, fps, factor=1.0):
        """Move toward the target; returns True once it's reached."""
        speed = stride_px * SPRITE_SCALE / fps * factor
        self.x += self.wander_dir * speed * dt
        lo, hi = self._wander_bounds()
        reached = ((self.wander_dir < 0 and self.x <= self.wander_target_x) or
                   (self.wander_dir > 0 and self.x >= self.wander_target_x) or
                   self.x <= lo or self.x >= hi)
        if reached:
            self.x = min(max(self.x, lo), hi)
        return reached

    def _ground_speed(self):
        if self.wander_phase in ('run_start', 'run', 'run_stop'):
            return self.RUN_STRIDE_PX * SPRITE_SCALE / self.RUN_FPS
        return self.WALK_STRIDE_PX * SPRITE_SCALE / self.WANDER_WALK_FPS

    def _update_wander(self, dt):
        self.vx = self.vy = 0.0
        p = self.wander_phase

        if p in self.GROUND_PHASES:
            if self.floor_y > self.y + 2:
                # Walked off the edge of a window: drop, and pick the plan up after landing
                vx = self.wander_dir * self._ground_speed() * 0.7 if p in ('walk', 'run') else 0.0
                self.wander_phase = None
                self._start_air('fall', vx, 0.0)
                return
            self.y = self.floor_y

        if p in ('walk', 'run'):
            stride, fps = ((self.RUN_STRIDE_PX, self.RUN_FPS) if p == 'run'
                           else (self.WALK_STRIDE_PX, self.WANDER_WALK_FPS))
            if self._wander_walk_step(dt, stride, fps):
                nxt = self.plan[0][0] if self.plan else None
                if p == 'run' and nxt in ('jump', 'climb'):
                    self._next_step()       # keep the momentum into the jump / wall
                else:
                    self._set_wander_phase('run_stop' if p == 'run' else 'walk_stop')
                return
        elif p == 'run_start':
            self._wander_walk_step(dt, self.RUN_STRIDE_PX, self.RUN_FPS, 0.5)
        elif p == 'run_stop':
            self.x += self.wander_dir * self._ground_speed() * 0.3 * dt
            lo, hi = self._wander_bounds()
            self.x = min(max(self.x, lo), hi)
        elif p == 'map_walk':
            if self._wander_walk_step(dt, self.MAP_WALK_STRIDE_PX, self.MAP_WALK_FPS):
                self._set_wander_phase('map_idle')
                self.wander_map_time = random.uniform(2.0, 5.0)
                return
        elif p == 'map_idle':
            self.wander_map_time -= dt
            if self.wander_map_time <= 0:
                pick = (self._pick_wander_target(self.MAP_WALK_DIST)
                        if self.wander_map_walks > 0 and tray_globals.get('wander', True) else None)
                if pick:
                    self.wander_map_walks -= 1
                    self.wander_dir, self.wander_target_x = pick
                    want_facing = self.wander_dir < 0
                    if want_facing != self.facing_right:
                        self.facing_right = want_facing
                        self._set_wander_phase('map_turn')
                    else:
                        self._set_wander_phase('map_walk')
                else:
                    self._set_wander_phase('map_close')
                return
        elif p in self.WALL_PHASES:
            self.x = self._wall_pos_x(self.climb_wall_x, self.climb_side)
            if p == 'climb_leap':
                # Rise only during the leap: fast off the wall, easing into the cling
                count, fps, _ = self._wander_seq_info(p)
                t = min(1.0, (self.wander_idx + self.wander_timer / fps) / count)
                self.y = self.leap_y0 - self.leap_h * (1.0 - (1.0 - t) ** 2)

        # Animation
        count, fps, loops = self._wander_seq_info(p)
        self.wander_timer += dt
        if self.wander_timer < fps:
            return
        self.wander_timer = 0.0
        self.wander_idx += 1
        if self.wander_idx < count:
            return
        if loops:
            self.wander_idx = 0
            return
        # One-shot finished: move on
        if p == 'turn':
            # TurnWalk already ends mid-stride, so it flows straight into the gait
            self._set_wander_phase('run' if self.wander_gait == 'run' else 'walk')
        elif p == 'walk_start':
            self._set_wander_phase('walk')
        elif p == 'run_start':
            self._set_wander_phase('run')
        elif p == 'map_open':
            self._set_wander_phase('map_idle')
            self.wander_map_time = random.uniform(3.0, 8.0)
        elif p == 'map_turn':
            self._set_wander_phase('map_walk')
        elif p == 'climb_crouch':
            self.leap_y0 = self.y
            self.leap_h  = max(0.0, self.y - self.climb_stop_y) / max(1, self.leaps_left)
            self.leaps_left -= 1
            self._set_wander_phase('climb_leap')
        elif p == 'climb_leap':
            self.y = self.leap_y0 - self.leap_h
            self._set_wander_phase('climb_settle')
        elif p == 'climb_settle':
            if self.leaps_left <= 0:
                self._climb_arrive()
            else:
                self._set_wander_phase('climb_crouch')
        elif p == 'cling':
            if self.plan and self.plan[0][0] == 'walljump':
                self._next_step()
            else:
                self._end_wander()
                self._start_air('fall')
        elif p == 'walljump_antic':
            tx, ty, catch = self.wj_target
            self.wj_target = None
            self._start_jump(tx, ty, 'walljump', catch)
        elif p == 'mantle':
            self._set_wander_phase('mantle_land')
        else:  # walk_stop, run_stop, map_close, mantle_land
            self._next_step()

    # ── window platforms ──
    def _floor_here(self) -> float:
        """Top-left y she'd stand at below her feet: the highest window top under
        her centre (one-way: only tops at or below her feet count), else the
        monitor floor. Sets floor_plat to the window it belongs to."""
        cx   = self.x + self._idle_w / 2
        feet = self.y + self._idle_h
        best = self._floor_for_x(cx) + self._idle_h
        best_p = None
        sup = self.support.hwnd if self.support is not None else None
        for p in self.platforms:
            if p.t < feet - 3 or p.t >= best:
                continue
            if p.hwnd == sup:
                ok = p.l <= cx <= p.r          # keep standing even if another window covers it
            else:
                ok = any(a <= cx <= b for a, b in p.segs)
            if ok:
                best, best_p = p.t, p
        self.floor_plat = best_p
        return best - self._idle_h

    def set_platforms(self, plats):
        """New window scan. If the window she's on (or climbing) moved, she falls
        off; if it closed or was minimized, she drops in a tumble."""
        self.platforms = plats
        by_hwnd = {p.hwnd: p for p in plats}
        if self.support is not None:
            old, new = self.support, by_hwnd.get(self.support.hwnd)
            cx = self.x + self._idle_w / 2
            if new is None:
                self._drop('weak_fall')
            elif (abs(new.t - old.t) > 2
                  or (abs(new.l - old.l) > 2 and abs(new.r - old.r) > 2)
                  or not (new.l <= cx <= new.r)):
                self._drop('fall')
            else:
                self.support = new
        if self.climb_rect is not None and self.climb_ledge is not None:
            new = by_hwnd.get(self.climb_ledge)
            if new is None:
                self._drop('weak_fall')
            elif any(abs(u - v) > 2 for u, v in zip((new.l, new.t, new.r, new.b), self.climb_rect)):
                self._drop('fall')

    def _drop(self, kind):
        """The ground (or wall) went away under her: stop everything and fall."""
        if self.dragging or self.walk_in_phase is not None:
            return
        if self.sit_phase in ('sit_loop', 'sit_outro'):
            self.ev_music_stop = True
        self.sit_phase   = None
        self.sit_quiet   = False
        self.sleep_phase = None
        self.taunt_phase = None
        self.land_phase  = None
        self.glide_phase = None
        self.climb_ledge = None
        self.climb_rect  = None
        self._cancel_wander()
        self._start_air(kind, 0.0, -160.0 if kind == 'weak_fall' else 0.0)

    # ── physics ───────────────────────────────────────────────────────────────
    def update(self, dt, mx=None, my=None):
        world_right = self.world_left + self.world_w

        # Tick cooldown regardless of state
        if self.taunt_cooldown_timer > 0:
            self.taunt_cooldown_timer -= dt

        # Refresh the floor under her: monitor floor or a window top below her feet
        if self.monitors:
            self.floor_y = self._floor_here()
            self.support = self.floor_plat if self.y >= self.floor_y - 1 else None

        # Give her a breather after any interaction before she wanders off
        if (self.walk_in_phase or self.sleep_phase or self.taunt_phase or self.sit_phase
                or self.land_phase or self.glide_phase or self.dragging or self._pending
                or self.air_anim):
            self.wander_wait = max(self.wander_wait, self.WANDER_IDLE_MIN)

        # Walk-in entrance overrides all other state until it completes.
        if self.walk_in_phase is not None:
            self._update_walk_in(dt)
            return

        if self.sleeping:
            self._update_sleep(dt)
            return
        # Keep ticking any Z particles still fading out after waking
        if self.z_particles:
            self._update_z_particles(dt)

        # Taunt: cancel if dragged, otherwise update animation
        if self.taunt_phase is not None:
            if self.dragging:
                self.taunt_phase = None
                self.taunt_idx   = 0
            else:
                self._update_taunt(dt)
                return

        if self.sitting:
            self._update_sit(dt)
            return
        if self.land_phase is not None:
            self._update_land(dt)
            return
        if self.glide_phase is not None:
            self._update_glide(dt)
            return
        self.idle_timer += dt
        if self.idle_timer >= self.IDLE_FPS:
            self.idle_timer = 0.0
            self.idle_idx = (self.idle_idx + 1) % len(self.idle_frames)
        if self.dragging:
            self._update_drag_swing(dt, mx, my)
            self._upd_state(); return

        # Hover-proximity taunt trigger (idle on ground only)
        if (mx is not None and my is not None
                and self.is_on_ground()
                and self.taunt_cooldown_timer <= 0
                and self.land_phase is None):
            cx = self.x + self._idle_w / 2
            cy = self.y + self._idle_h / 2
            if math.hypot(mx - cx, my - cy) <= self._idle_w:
                self.taunt_hover_timer += dt
                if self.taunt_hover_timer >= self.TAUNT_HOVER_TIME:
                    self._start_taunt()
                    return
            else:
                self.taunt_hover_timer = 0.0
        else:
            self.taunt_hover_timer = 0.0

        if self.wander_phase is not None:
            self._update_wander(dt)
            return

        # Inactivity sleep: only count when resting on the ground
        if self.is_on_ground():
            self.inactivity_timer += dt
            if self.inactivity_timer >= self.SLEEP_TIMEOUT:
                self._start_sleep()
                return
            # Wander brain: after standing still a while, pick something to do
            if (tray_globals.get('wander', True) and not self._pending
                    and not self.plan_running and self.air_anim is None
                    and abs(self.vx) < 5 and self.vy == 0.0):
                self.wander_wait -= dt
                if self.wander_wait <= 0:
                    self._choose_wander()
                    if self.wander_phase is not None:
                        return
        else:
            self.inactivity_timer = 0.0
            self.wander_wait = max(self.wander_wait, self.WANDER_IDLE_MIN)
        if self.air_anim is not None:
            self._tick_air(dt)
        elif abs(self.vx) > 30:
            self.facing_right = self.vx > 0
        self.vy += self.GRAVITY * dt
        self.x  += self.vx * dt
        self.y  += self.vy * dt
        soft = tray_globals.get('land_mode', 'bounce') in ('soft', 'glide')
        if self.y >= self.floor_y:
            self.y = self.floor_y
            if self.air_anim is not None:
                # Her own jumps and drops always land on their feet, never bounce
                self._air_land(self.vy)
            elif soft and abs(self.vy) * self.BOUNCE_DAMP >= self.MIN_BOUNCE_VY:
                self.vy         = 0.0
                self.vx         = 0.0
                self.land_phase = 'land'
                self.land_idx   = 0
                self.land_timer = 0.0
            else:
                self.vy = -self.vy * self.BOUNCE_DAMP
                self.vx *= self.FRICTION
                if abs(self.vy) < self.MIN_BOUNCE_VY:
                    self.vy = 0.0
        if self.air_anim is not None:
            # Jumping on her own: screen edges just stop her (she may start a little
            # past the edge after a wall jump, so only block moving further out)
            if self.x < self.world_left and self.vx < 0:
                self.x, self.vx = self.world_left, 0.0
            elif self.x > world_right - self._idle_w and self.vx > 0:
                self.x, self.vx = float(world_right - self._idle_w), 0.0
        elif self.x < self.world_left:
            self.x = self.world_left
            if soft and abs(self.vx) * self.BOUNCE_DAMP >= 50.0:
                self._start_wall_cling('left')
            else:
                self.vx = abs(self.vx) * self.BOUNCE_DAMP
        elif self.x > world_right - self._idle_w:
            if soft and abs(self.vx) * self.BOUNCE_DAMP >= 50.0:
                self._start_wall_cling('right')
            else:
                self.x  = float(world_right - self._idle_w)
                self.vx = -abs(self.vx) * self.BOUNCE_DAMP
        if self.y < self.world_top and self.air_anim is None:
            self.y  = self.world_top;  self.vy = abs(self.vy) * self.BOUNCE_DAMP
        # Grab a wall mid-jump once the jump reaches it
        if self.air_anim is not None and self.air_catch is not None:
            t, tx, ty, climb = self.air_catch
            if self.air_t >= t:
                self.air_anim  = None
                self.air_catch = None
                self.y = ty
                self._start_climb(*climb[1:], caught=True)
                return
        # Umbrella glide: once she's falling fast enough from high enough, open it
        if (tray_globals.get('land_mode', 'bounce') == 'glide'
                and self.land_phase is None
                and self.air_anim in (None, 'fall', 'weak_fall')
                and self.vy >= self.GLIDE_TRIGGER_VY
                and self.floor_y - self.y >= self.GLIDE_MIN_HEIGHT):
            self._start_glide()
        self._upd_state()

    # ── draw ──────────────────────────────────────────────────────────────────
    def _sleep_low_frame(self) -> bool:
        """True for sleep_wake frames 5–10 (indices 4–9) that need sleep_y_offset."""
        if self.sleep_phase == 'falling_asleep':
            rev = len(self.sleep_wake_frames) - 1 - self.sleep_idx
            return 4 <= rev <= 9
        if self.sleep_phase == 'waking':
            return 4 <= self.sleep_idx <= 9
        return False

    def _sit_offset(self):
        if self._sleep_low_frame():
            return int(self._idle_h * SLEEP_Y_OFFSET)
        if not self.sitting:
            return 0
        base = int(self._idle_h * SIT_Y_OFFSET)
        if self.sit_phase == 'sit_up' and len(self.sit_up_frames) > 1:
            t = self.sit_idx / (len(self.sit_up_frames) - 1)
            return int(base * (1.0 - t))
        return base

    def _idle_y_offset(self):
        if self.sitting or self._sleep_low_frame():
            return 0
        return -int(self._idle_h * IDLE_Y_OFFSET)

    def _sit_x_offset(self, frame: pygame.Surface) -> int:
        if self.wander_phase in self.WALL_PHASES:
            # Wall frames are cropped at the hands/feet: put that edge on the wall
            wx = self.climb_wall_x if self.climb_side == 'left' else self.climb_wall_x - frame.get_width()
            return int(wx) - int(self.x)
        centred = (self.sitting or self.gliding or self.wandering or self.walking_in
                   or self.air_anim is not None or self.land_phase in self.CENTRED_LANDS)
        return (self._idle_w - frame.get_width()) // 2 if centred else 0

    def _glide_y_offset(self, frame: pygame.Surface) -> int:
        # Umbrella frames are top-aligned to the idle head; the needle hangs below
        return frame.get_height() - self._idle_h if self.gliding else 0

    def display_frame(self) -> pygame.Surface:
        """Current frame as it will actually be rendered (h-flip applied)."""
        frame = self.current_frame()
        if not self.facing_right:
            frame = pygame.transform.flip(frame, True, False)
        return frame

    def draw(self, surface):
        if self.dragging:
            # Pivot in world coords = current mouse pos (self.x/y are kept synced
            # to mouse + grab-offset every frame; subtracting the offset recovers it).
            self._blit_rotated(surface, self.x - self._off_x, self.y - self._off_y)
            return
        frame = self.display_frame()
        draw_x = int(self.x) + self._sit_x_offset(frame)
        draw_y = (int(self.y) + self._idle_h - frame.get_height() + self._idle_y_offset()
                  + self._sit_offset() + self._glide_y_offset(frame))
        surface.blit(frame, (draw_x, draw_y))

    def _blit_rotated(self, surface, pivot_x, pivot_y):
        """Blit the current displayed frame rotated by self._drag_angle around
        the grip point, placing that grip point at (pivot_x, pivot_y) in `surface`.

        pygame.transform.rotate uses nearest-neighbour sampling, which preserves
        the alpha quantisation (0/255) that the Win32 chroma-key path relies on.
        """
        frame = self.display_frame()
        fw, fh = frame.get_width(), frame.get_height()
        angle_deg = math.degrees(self._drag_angle)
        rotated = pygame.transform.rotate(frame, angle_deg)
        rw, rh = rotated.get_width(), rotated.get_height()
        # Rotation matrix pygame uses (positive = visually CCW on y-down screen):
        #   x' =  x·cos + y·sin
        #   y' = -x·sin + y·cos
        rad = math.radians(angle_deg)
        cos_a = math.cos(rad); sin_a = math.sin(rad)
        off_x = self._grip_local_x - fw * 0.5
        off_y = self._grip_local_y - fh * 0.5
        rot_off_x =  off_x * cos_a + off_y * sin_a
        rot_off_y = -off_x * sin_a + off_y * cos_a
        grip_rx = rw * 0.5 + rot_off_x
        grip_ry = rh * 0.5 + rot_off_y
        surface.blit(rotated, (int(round(pivot_x - grip_rx)),
                                int(round(pivot_y - grip_ry))))

    def shape_key(self):
        """Hashable cache key for the current visible frame."""
        if self.walk_in_phase:
            return ('walk_in', self.walk_in_phase, self.walk_in_idx, self.facing_right)
        if self.sleep_phase:
            return ('sleep', self.sleep_phase, self.sleep_idx, self.facing_right)
        if self.land_phase:
            return ('land', self.land_phase, self.land_idx, self.facing_right)
        if self.wander_phase:
            return ('wander', self.wander_phase, self.wander_idx, self.facing_right)
        if self.air_anim:
            return ('air', self.air_anim, self.air_idx, self.facing_right)
        if self.glide_phase:
            return ('glide', self.glide_phase, self.glide_idx, self.facing_right)
        if self.taunt_phase:
            return ('taunt', self.taunt_idx, self.facing_right)
        if self.sit_phase:
            return ('sit', self.sit_phase, self.sit_idx, self.facing_right)
        if self.state == 'IDLE':
            return ('idle', self.idle_idx, self.facing_right)
        return (self.state, self.facing_right)

    def draw_pos(self):
        """Top-left (x, y) of the current frame as drawn."""
        frame = self.display_frame()
        draw_x = int(self.x) + self._sit_x_offset(frame)
        draw_y = (int(self.y) + self._idle_h - frame.get_height() + self._idle_y_offset()
                  + self._sit_offset() + self._glide_y_offset(frame))
        return draw_x, draw_y


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

_CONFIG_DEFAULTS = {
    'gravity':        1800.0,
    'bounce_damp':    0.45,
    'friction':       0.88,
    'min_bounce_vy':  80.0,
    'sit_fps':        0.1,
    'idle_fps':       0.15,
    'sit_pause_dur':  0.25,
    'fast_fall_vy':   300.0,
    'wrong_mix':      0.65,
    'on_ground_tol':  8,
    'sit_y_offset':   0.235,
    'idle_y_offset':  -0.075,
    'sleep_y_offset': 0.12,
    'volume':         1.0,
    'scale':          100,
    'sleep_timeout':  300.0,
    'land_fps':       0.04,
    'wall_slide_fps': 0.08,
    'sleep_z':        True,
    'land_mode':      'bounce',
    'drag_pendulum':  True,
    'taunt_fps':      0.05,
    'taunt_cooldown': 120.0,
    'taunt_hover_time': 2.5,
    'cloak_color': 'default',
    'spawn_mode':  'fall',
    'glide_fall_vy':    140.0,
    'glide_min_height': 250.0,
    'glide_sway_amp':   30.0,
    'glide_fps':        0.07,
    'wander':           True,
    'wander_idle_min':  4.0,
    'wander_idle_max':  12.0,
    'wander_walk_fps':  0.07,
    'window_platforms': True,
}

_SPAWN_MODES = ('fall', 'walk_from_right', 'walk_from_left')
_LAND_MODES  = ('bounce', 'soft', 'glide')
_LAND_MODE_LABELS = (('Bounce', 'bounce'),
                     ('Soft Landing', 'soft'),
                     ('Umbrella Glide', 'glide'))

SPRITE_SCALE     = 1.0    # set by load_config()
CLOAK_COLOR      = 'default'  # set by load_config(); 'default' or '#RRGGBB'
_raw_sprites     = None   # stored after load_raw_assets() so runtime rescale can re-convert
_raw_seqs        = None
_pending_rescale = False  # set True by load_config() when scale changes at runtime

_CLOAK_PRESETS = [
    ('Default', 'default'),
    ('Red',     '#CC2233'),
    ('Orange',  '#DD6622'),
    ('Yellow',  '#CCAA11'),
    ('Green',   '#22AA44'),
    ('Teal',    '#22AAAA'),
    ('Blue',    '#2255DD'),
    ('Purple',  '#8833CC'),
    ('Pink',    '#DD3399'),
    ('Black',   '#000000'),
    ('White',   '#FFFFFF'),
]

def _set_cloak_color(hex_color):
    """Set cloak color at runtime: update global, persist to config, trigger re-conversion."""
    global CLOAK_COLOR, _pending_rescale
    CLOAK_COLOR = hex_color
    tray_globals['cloak_color'] = hex_color
    _save_config_key('cloak_color', hex_color)
    _pending_rescale = True


def _set_spawn_mode(mode):
    """Set spawn mode at runtime and persist. Applies on next launch."""
    if mode not in _SPAWN_MODES:
        return
    tray_globals['spawn_mode'] = mode
    _save_config_key('spawn_mode', mode)


def _set_land_mode(mode):
    """Set landing mode at runtime and persist."""
    if mode not in _LAND_MODES:
        return
    tray_globals['land_mode'] = mode
    _save_config_key('land_mode', mode)


def _save_config_key(key, value):
    """Persist a single key back to config.json without touching other values."""
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
        except Exception:
            pass
    cfg[key] = value
    try:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        print(f"[config] failed to save {key}: {e}")

def load_config(apply_volume=False):
    global FAST_FALL_VY, WRONG_MIX, ON_GROUND_TOL, SIT_Y_OFFSET, IDLE_Y_OFFSET, SLEEP_Y_OFFSET, SPRITE_SCALE, CLOAK_COLOR, _pending_rescale
    cfg = dict(_CONFIG_DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                user = json.load(f)
            cfg.update({k: v for k, v in user.items() if k in _CONFIG_DEFAULTS})
            # Legacy: 'soft_land' bool predates 'land_mode'
            if 'land_mode' not in user and user.get('soft_land'):
                cfg['land_mode'] = 'soft'
        except Exception as e:
            print(f"[config] failed to load {CONFIG_PATH}: {e}")
    else:
        try:
            with open(CONFIG_PATH, 'w') as f:
                json.dump(_CONFIG_DEFAULTS, f, indent=4)
            print(f"[config] created default {CONFIG_PATH}")
        except Exception as e:
            print(f"[config] failed to write default {CONFIG_PATH}: {e}")

    Hornet.GRAVITY        = float(cfg['gravity'])
    Hornet.BOUNCE_DAMP    = float(cfg['bounce_damp'])
    Hornet.FRICTION       = float(cfg['friction'])
    Hornet.MIN_BOUNCE_VY  = float(cfg['min_bounce_vy'])
    Hornet.SIT_FPS        = float(cfg['sit_fps'])
    Hornet.IDLE_FPS       = float(cfg['idle_fps'])
    Hornet.SIT_PAUSE_DUR  = float(cfg['sit_pause_dur'])
    Hornet.SLEEP_TIMEOUT   = float(cfg['sleep_timeout'])
    Hornet.LAND_FPS        = float(cfg['land_fps'])
    Hornet.WALL_SLIDE_FPS  = float(cfg['wall_slide_fps'])
    Hornet.TAUNT_FPS       = float(cfg['taunt_fps'])
    Hornet.TAUNT_COOLDOWN  = float(cfg['taunt_cooldown'])
    Hornet.TAUNT_HOVER_TIME = float(cfg['taunt_hover_time'])
    Hornet.GLIDE_FALL_VY    = float(cfg['glide_fall_vy'])
    Hornet.GLIDE_MIN_HEIGHT = float(cfg['glide_min_height'])
    Hornet.GLIDE_SWAY_AMP   = float(cfg['glide_sway_amp'])
    Hornet.GLIDE_FPS        = float(cfg['glide_fps'])
    Hornet.WANDER_IDLE_MIN  = float(cfg['wander_idle_min'])
    Hornet.WANDER_IDLE_MAX  = max(Hornet.WANDER_IDLE_MIN, float(cfg['wander_idle_max']))
    Hornet.WANDER_WALK_FPS  = float(cfg['wander_walk_fps'])
    new_scale = max(0.1, float(cfg['scale']) / 100.0)
    new_cloak = str(cfg['cloak_color'])
    if apply_volume and (abs(new_scale - SPRITE_SCALE) > 1e-6 or new_cloak != CLOAK_COLOR):
        _pending_rescale = True
    SPRITE_SCALE = new_scale
    CLOAK_COLOR  = new_cloak
    FAST_FALL_VY  = float(cfg['fast_fall_vy'])
    WRONG_MIX     = float(cfg['wrong_mix'])
    ON_GROUND_TOL = int(cfg['on_ground_tol'])
    SIT_Y_OFFSET   = float(cfg['sit_y_offset'])
    IDLE_Y_OFFSET  = float(cfg['idle_y_offset'])
    SLEEP_Y_OFFSET = float(cfg['sleep_y_offset'])
    tray_globals['volume']      = float(cfg['volume'])
    tray_globals['sleep_z']     = bool(cfg['sleep_z'])
    lm = str(cfg['land_mode'])
    tray_globals['land_mode']   = lm if lm in _LAND_MODES else 'bounce'
    tray_globals['drag_pendulum'] = bool(cfg['drag_pendulum'])
    tray_globals['wander']      = bool(cfg['wander'])
    tray_globals['window_platforms'] = bool(cfg['window_platforms'])
    tray_globals['cloak_color'] = CLOAK_COLOR
    sm = str(cfg['spawn_mode'])
    if sm not in _SPAWN_MODES:
        sm = 'fall'
    tray_globals['spawn_mode'] = sm
    if apply_volume:
        try:
            pygame.mixer.music.set_volume(tray_globals['volume'])
        except Exception:
            pass
    print(f"[config] loaded -  gravity={Hornet.GRAVITY} bounce={Hornet.BOUNCE_DAMP} "
          f"sit_fps={Hornet.SIT_FPS} idle_fps={Hornet.IDLE_FPS} sit_pause={Hornet.SIT_PAUSE_DUR}s "
          f"volume={tray_globals['volume']} scale={SPRITE_SCALE*100:.0f}%"
          + (" (rescaling sprites)" if _pending_rescale else ""))


# ─────────────────────────────────────────────────────────────────────────────
# ARGB render helpers
# ─────────────────────────────────────────────────────────────────────────────

def render_argb(screen, offscreen, hornet):
    offscreen.fill((0, 0, 0, 0))
    hornet.draw_taunt_silk(offscreen, *hornet.draw_pos())
    hornet.draw(offscreen)
    hornet.draw_z_particles(offscreen, *hornet.draw_pos())
    pygame.surfarray.blit_array(screen, pygame.surfarray.array2d(offscreen))


# ─────────────────────────────────────────────────────────────────────────────
# Shared right-click context menu (tkinter popup)
# Used both by the Linux SNI tray and by right-clicking Hornet herself.
# ─────────────────────────────────────────────────────────────────────────────

def _show_context_menu(x, y, hornet_ref, on_quit=None):
    """Pop up a tkinter context menu at (x, y) mirroring the tray icon's menu.
    Runs in its own thread since tkinter needs its own mainloop.
    `on_quit`, if given, replaces the default quit handler (e.g. the Linux SNI
    tray also needs to stop its D-Bus loop)."""
    def _run():
        try:
            import tkinter as tk
            import tkinter.colorchooser as cc
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)

            def close_run(cb):
                def inner():
                    root.after(50, root.destroy)
                    try: cb()
                    except Exception: pass
                return inner

            def on_song(idx):
                def cb():
                    if idx == -1:
                        tray_globals['auto_random_song'] = True
                        tray_globals['current_song'] = -1
                    else:
                        tray_globals['current_song'] = idx
                        tray_globals['auto_random_song'] = False
                    if hornet_ref[0] and hornet_ref[0].sitting:
                        start_song_from_tray(idx if idx >= 0 else random.randrange(len(NEEDOLINE_SEGMENTS)))
                return cb

            def on_vol(vol):
                def cb():
                    tray_globals['volume'] = vol
                    pygame.mixer.music.set_volume(vol)
                    _save_config_key('volume', vol)
                return cb

            def on_cloak_preset(val):
                def cb(): _set_cloak_color(val)
                return cb

            def on_spawn_mode(val):
                def cb(): _set_spawn_mode(val)
                return cb

            def on_toggle_sleep_z():
                tray_globals['sleep_z'] = not tray_globals['sleep_z']
                if not tray_globals['sleep_z'] and hornet_ref[0]:
                    hornet_ref[0].z_particles  = []
                    hornet_ref[0].z_spawn_timer = 0.0
                _save_config_key('sleep_z', tray_globals['sleep_z'])

            def on_land_mode(val):
                def cb(): _set_land_mode(val)
                return cb

            def on_toggle_drag_pendulum():
                tray_globals['drag_pendulum'] = not tray_globals['drag_pendulum']
                _save_config_key('drag_pendulum', tray_globals['drag_pendulum'])

            def on_toggle_wander():
                tray_globals['wander'] = not tray_globals['wander']
                _save_config_key('wander', tray_globals['wander'])

            def on_toggle_window_platforms():
                tray_globals['window_platforms'] = not tray_globals['window_platforms']
                _save_config_key('window_platforms', tray_globals['window_platforms'])

            def on_reload_config():
                load_config(apply_volume=True)

            def on_reset_topmost():
                h = tray_globals.get('hwnd')
                if h:
                    _win_assert_topmost(h)

            def on_quit_default():
                tray_globals['running'] = False

            def open_color_picker():
                root.after(50, root.destroy)
                def _pick():
                    try:
                        r2 = tk.Tk()
                        r2.withdraw()
                        r2.attributes('-topmost', True)
                        init = tray_globals['cloak_color'] if tray_globals['cloak_color'] != 'default' else '#8833CC'
                        result = cc.askcolor(color=init, title='Cloak Color', parent=r2)
                        r2.destroy()
                        if result and result[1]:
                            _set_cloak_color(result[1].upper())
                    except Exception as e:
                        print(f"[menu] color picker error: {e}")
                threading.Thread(target=_pick, daemon=True).start()

            pop = tk.Menu(root, tearoff=0)

            sm = tk.Menu(pop, tearoff=0)
            sm.add_command(label='Random', command=close_run(on_song(-1)))
            for i, n in enumerate(SONG_NAMES):
                sm.add_command(label=n, command=close_run(on_song(i)))
            pop.add_cascade(label='Songs', menu=sm)

            vm = tk.Menu(pop, tearoff=0)
            for v in [0.0, 0.25, 0.5, 0.75, 1.0]:
                vm.add_command(label=f'{int(v*100)}%', command=close_run(on_vol(v)))
            pop.add_cascade(label='Volume', menu=vm)

            cm = tk.Menu(pop, tearoff=0)
            cur_cloak = tray_globals.get('cloak_color', 'default')
            cloak_var = tk.StringVar(value=cur_cloak)
            for label, val in _CLOAK_PRESETS:
                cm.add_radiobutton(label=label, value=val, variable=cloak_var,
                                   command=close_run(on_cloak_preset(val)))
            cm.add_separator()
            cm.add_command(label='Custom…', command=open_color_picker)
            pop.add_cascade(label='Cloak Color', menu=cm)

            spm = tk.Menu(pop, tearoff=0)
            cur_spawn = tray_globals.get('spawn_mode', 'fall')
            spawn_var = tk.StringVar(value=cur_spawn)
            for label, val in (('Fall (Default)', 'fall'),
                                ('Walk from Right', 'walk_from_right'),
                                ('Walk from Left', 'walk_from_left')):
                spm.add_radiobutton(label=label, value=val, variable=spawn_var,
                                    command=close_run(on_spawn_mode(val)))
            pop.add_cascade(label='Spawn Mode', menu=spm)

            lm = tk.Menu(pop, tearoff=0)
            land_var = tk.StringVar(value=tray_globals.get('land_mode', 'bounce'))
            for label, val in _LAND_MODE_LABELS:
                lm.add_radiobutton(label=label, value=val, variable=land_var,
                                   command=close_run(on_land_mode(val)))
            pop.add_cascade(label='Landing', menu=lm)

            pop.add_separator()
            sleep_z_var = tk.BooleanVar(value=tray_globals['sleep_z'])
            pop.add_checkbutton(label="Sleep Z's", variable=sleep_z_var,
                                command=close_run(on_toggle_sleep_z))
            drag_pendulum_var = tk.BooleanVar(value=tray_globals['drag_pendulum'])
            pop.add_checkbutton(label='Drag Pendulum', variable=drag_pendulum_var,
                                command=close_run(on_toggle_drag_pendulum))
            wander_var = tk.BooleanVar(value=tray_globals['wander'])
            pop.add_checkbutton(label='Wander', variable=wander_var,
                                command=close_run(on_toggle_wander))
            if tray_globals['window_platforms_ok']:
                platforms_var = tk.BooleanVar(value=tray_globals['window_platforms'])
                pop.add_checkbutton(label='Climb Windows', variable=platforms_var,
                                    command=close_run(on_toggle_window_platforms))
            pop.add_separator()
            pop.add_command(label='Reload Config', command=close_run(on_reload_config))
            if PLAT == 'Windows':
                pop.add_command(label='Reset Topmost', command=close_run(on_reset_topmost))
            pop.add_command(label='Quit', command=close_run(on_quit or on_quit_default))
            pop.bind('<Unmap>', lambda e: root.after(150, root.destroy))
            root.after(0, lambda: pop.tk_popup(x, y, 0))
            root.mainloop()
        except Exception as e:
            print(f"[menu] context menu error: {e}")
    threading.Thread(target=_run, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# Tray Icon (Windows only)
# ─────────────────────────────────────────────────────────────────────────────

def _create_tray_icon(hwnd, hornet_ref):
    """Create and run the system tray icon (blocking, run in separate thread)."""
    if PLAT == 'Linux':
        _create_tray_icon_sni(hornet_ref)
        return

    # ── Windows: pystray ──────────────────────────────────────────────────────
    try:
        from pystray import Icon, Menu, MenuItem
    except ImportError:
        print("pystray not installed, tray icon disabled")
        return

    _icon_ref = [None]

    def on_song(song_idx):
        def handler(icon=None, item=None):
            if song_idx == -1:
                tray_globals['auto_random_song'] = True
                tray_globals['current_song'] = -1
            else:
                tray_globals['current_song'] = song_idx
                tray_globals['auto_random_song'] = False
            if hornet_ref[0] and hornet_ref[0].sitting:
                start_song_from_tray(song_idx if song_idx >= 0 else random.randrange(len(NEEDOLINE_SEGMENTS)))
        return handler

    def on_volume(vol):
        def handler(icon=None, item=None):
            tray_globals['volume'] = vol
            pygame.mixer.music.set_volume(vol)
            _save_config_key('volume', vol)
        return handler

    def on_quit(icon=None, item=None):
        tray_globals['running'] = False
        ic = icon if icon is not None else _icon_ref[0]
        if ic:
            ic.stop()

    def on_reload_config(icon=None, item=None):
        load_config(apply_volume=True)

    def on_reset_topmost(icon=None, item=None):
        h = tray_globals.get('hwnd')
        if h:
            _win_assert_topmost(h)

    def on_toggle_sleep_z(icon=None, item=None):
        tray_globals['sleep_z'] = not tray_globals['sleep_z']
        if not tray_globals['sleep_z'] and hornet_ref[0]:
            hornet_ref[0].z_particles  = []
            hornet_ref[0].z_spawn_timer = 0.0
        _save_config_key('sleep_z', tray_globals['sleep_z'])

    def on_land_mode(mode):
        def handler(icon=None, item=None):
            _set_land_mode(mode)
        return handler

    def on_toggle_drag_pendulum(icon=None, item=None):
        tray_globals['drag_pendulum'] = not tray_globals['drag_pendulum']
        _save_config_key('drag_pendulum', tray_globals['drag_pendulum'])

    def on_toggle_wander(icon=None, item=None):
        tray_globals['wander'] = not tray_globals['wander']
        _save_config_key('wander', tray_globals['wander'])

    def on_toggle_window_platforms(icon=None, item=None):
        tray_globals['window_platforms'] = not tray_globals['window_platforms']
        _save_config_key('window_platforms', tray_globals['window_platforms'])

    def on_cloak_preset(color_val):
        def handler(icon=None, item=None):
            _set_cloak_color(color_val)
        return handler

    def on_spawn_mode(mode):
        def handler(icon=None, item=None):
            _set_spawn_mode(mode)
        return handler

    def on_cloak_custom(icon=None, item=None):
        def _pick():
            try:
                import tkinter as tk
                import tkinter.colorchooser as cc
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                init = tray_globals['cloak_color'] if tray_globals['cloak_color'] != 'default' else '#8833CC'
                result = cc.askcolor(color=init, title='Cloak Color', parent=root)
                root.destroy()
                if result and result[1]:
                    _set_cloak_color(result[1].upper())
            except Exception as e:
                print(f"[tray] color picker error: {e}")
        threading.Thread(target=_pick, daemon=True).start()

    volume_items = [MenuItem(f'{int(v*100)}%', on_volume(v)) for v in [0.0,0.25,0.5,0.75,1.0]]
    song_items   = [MenuItem('Random', on_song(-1))] + [MenuItem(SONG_NAMES[i], on_song(i)) for i in range(len(SONG_NAMES))]

    def _cloak_checked(val):
        return lambda item: tray_globals.get('cloak_color', 'default') == val

    cloak_items = [
        MenuItem(label, on_cloak_preset(val), checked=_cloak_checked(val), radio=True)
        for label, val in _CLOAK_PRESETS
    ] + [MenuItem('Custom…', on_cloak_custom)]

    def _spawn_checked(val):
        return lambda item: tray_globals.get('spawn_mode', 'fall') == val

    spawn_items = [
        MenuItem('Fall (Default)',    on_spawn_mode('fall'),
                 checked=_spawn_checked('fall'), radio=True),
        MenuItem('Walk from Right',   on_spawn_mode('walk_from_right'),
                 checked=_spawn_checked('walk_from_right'), radio=True),
        MenuItem('Walk from Left',    on_spawn_mode('walk_from_left'),
                 checked=_spawn_checked('walk_from_left'), radio=True),
    ]

    def _land_checked(val):
        return lambda item: tray_globals.get('land_mode', 'bounce') == val

    land_items = [
        MenuItem(label, on_land_mode(val), checked=_land_checked(val), radio=True)
        for label, val in _LAND_MODE_LABELS
    ]

    def build_menu():
        return Menu(
            MenuItem('Songs', Menu(*song_items)),
            MenuItem('Volume', Menu(*volume_items)),
            MenuItem('Cloak Color', Menu(*cloak_items)),
            MenuItem('Spawn Mode', Menu(*spawn_items)),
            MenuItem('Landing', Menu(*land_items)),
            MenuItem('Sleep Z\'s', on_toggle_sleep_z,
                     checked=lambda item: tray_globals['sleep_z']),
            MenuItem('Drag Pendulum', on_toggle_drag_pendulum,
                     checked=lambda item: tray_globals['drag_pendulum']),
            MenuItem('Wander', on_toggle_wander,
                     checked=lambda item: tray_globals['wander']),
            MenuItem('Climb Windows', on_toggle_window_platforms,
                     checked=lambda item: tray_globals['window_platforms']),
            MenuItem('Reload Config', on_reload_config),
            MenuItem('Reset Topmost', on_reset_topmost),
            MenuItem('Quit', on_quit),
        )

    try:
        icon_img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        for path in ICON_FILES:
            if os.path.exists(path) and not path.endswith('.ico'):
                try:
                    img = Image.open(path).convert('RGBA')
                    img.thumbnail((64, 64))
                    icon_img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
                    icon_img.paste(img, ((64-img.width)//2, (64-img.height)//2), img)
                    break
                except Exception:
                    pass
    except Exception:
        icon_img = Image.new('RGBA', (64, 64), (0, 0, 255, 255))

    try:
        icon = Icon('HornetCompanion', icon_img, menu=build_menu())
        _icon_ref[0] = icon
        icon.run()
    except Exception as e:
        print(f"Tray icon failed: {e}")


def _create_tray_icon_sni(hornet_ref):
    """
    Linux tray icon via StatusNotifierItem (SNI) over D-Bus.
    Works on Ubuntu GNOME with any AppIndicator-compatible extension.
    Requires system python3-gi (gi) and python3-dbus (dbus) -  both already
    available via /usr/lib/python3/dist-packages which is in sys.path.
    Menu is shown as a tkinter popup (no GTK/AppIndicator3 package needed).
    """
    import struct

    if 'DBUS_SESSION_BUS_ADDRESS' not in os.environ:
        print("[tray] DBUS_SESSION_BUS_ADDRESS not set -  tray disabled")
        return

    try:
        import dbus
        import dbus.service
        from dbus.mainloop.glib import DBusGMainLoop
        import gi
        gi.require_version('GLib', '2.0')
        from gi.repository import GLib
    except ImportError as e:
        print(f"[tray] SNI unavailable ({e}), tray disabled")
        return

    _sni_ref = [None]

    def on_quit_cb():
        tray_globals['running'] = False
        if _sni_ref[0]:
            _sni_ref[0].stop()

    # Menu content lives in the shared _show_context_menu (also used by
    # right-clicking Hornet herself); only quitting needs SNI-specific cleanup.
    def show_menu(x, y):
        _show_context_menu(x, y, hornet_ref, on_quit=on_quit_cb)

    # ── Icon pixmap (SNI ARGB32 big-endian format) ─────────────────────────────
    def build_pixmap(size=22):
        for path in ICON_FILES:
            if os.path.exists(path) and not path.endswith('.ico'):
                try:
                    img = Image.open(path).convert('RGBA').resize((size, size), Image.LANCZOS)
                    raw = bytearray()
                    for r, g, b, a in img.getdata():
                        raw += struct.pack('>I', (a << 24) | (r << 16) | (g << 8) | b)
                    return dbus.Array(
                        [dbus.Struct(
                            (dbus.Int32(size), dbus.Int32(size),
                             dbus.Array(list(raw), signature='y')),
                            signature='iiay')],
                        signature='(iiay)')
                except Exception as e:
                    print(f"[tray] pixmap error: {e}")
        return dbus.Array([], signature='(iiay)')

    # ── StatusNotifierItem D-Bus service ───────────────────────────────────────
    SNI = 'org.kde.StatusNotifierItem'

    class HornetSNI(dbus.service.Object):
        def __init__(self):
            DBusGMainLoop(set_as_default=True)

            # D-Bus session bus rejects root. If we're root but the session
            # belongs to another user, temporarily drop effective uid for the
            # connection handshake only (D-Bus authenticates at connect time).
            _saved_euid = os.geteuid()
            _drop_uid   = None
            if _saved_euid == 0:
                bus_addr = os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')
                sock = ''
                for part in bus_addr.split(','):
                    if part.startswith('unix:path='):
                        sock = part[len('unix:path='):]
                    elif part.startswith('path='):
                        sock = part[len('path='):]
                if sock:
                    try:
                        _drop_uid = os.stat(sock).st_uid
                        if _drop_uid and _drop_uid != 0:
                            os.seteuid(_drop_uid)
                    except Exception:
                        _drop_uid = None

            try:
                bus = dbus.SessionBus()
            finally:
                if _drop_uid:
                    os.seteuid(_saved_euid)   # restore root after handshake

            svc_name = f'org.kde.StatusNotifierItem-{os.getpid()}-1'
            bn       = dbus.service.BusName(svc_name, bus)
            super().__init__(bn, '/StatusNotifierItem')
            self._loop   = GLib.MainLoop()
            self._pixmap = build_pixmap()

            for watcher in ('org.kde.StatusNotifierWatcher',
                            'com.canonical.StatusNotifierWatcher'):
                try:
                    dbus.Interface(
                        bus.get_object(watcher, '/StatusNotifierWatcher'),
                        watcher
                    ).RegisterStatusNotifierItem(svc_name)
                    print(f"[tray] registered with {watcher}")
                    break
                except Exception:
                    pass

        def run(self):
            GLib.timeout_add(500, lambda: tray_globals['running'] or
                             (self._loop.quit() or False))
            self._loop.run()

        def stop(self):
            self._loop.quit()

        # SNI action methods
        @dbus.service.method(SNI, in_signature='ii')
        def Activate(self, x, y):           show_menu(x, y)

        @dbus.service.method(SNI, in_signature='ii')
        def SecondaryActivate(self, x, y):  show_menu(x, y)

        @dbus.service.method(SNI, in_signature='ii')
        def ContextMenu(self, x, y):        show_menu(x, y)

        @dbus.service.method(SNI, in_signature='is')
        def Scroll(self, delta, orientation): pass

        # Properties
        @dbus.service.method('org.freedesktop.DBus.Properties',
                             in_signature='ss', out_signature='v')
        def Get(self, iface, prop):
            return self.GetAll(iface).get(prop, dbus.String(''))

        @dbus.service.method('org.freedesktop.DBus.Properties',
                             in_signature='s', out_signature='a{sv}')
        def GetAll(self, iface):
            empty = dbus.Array([], signature='(iiay)')
            return {
                'Id':                  dbus.String('HornetCompanion'),
                'Category':            dbus.String('ApplicationStatus'),
                'Status':              dbus.String('Active'),
                'Title':               dbus.String('Hornet Desktop Companion'),
                'IconName':            dbus.String(''),
                'IconPixmap':          self._pixmap,
                'AttentionIconName':   dbus.String(''),
                'AttentionIconPixmap': empty,
                'OverlayIconName':     dbus.String(''),
                'OverlayIconPixmap':   empty,
                'ToolTip':             dbus.Struct(
                    ('', dbus.Array([], signature='(iiay)'), 'Hornet', ''),
                    signature='sa(iiay)ss'),
                'ItemIsMenu':          dbus.Boolean(False),
            }

        @dbus.service.method('org.freedesktop.DBus.Properties', in_signature='ssv')
        def Set(self, iface, prop, val): pass

        @dbus.service.signal('org.freedesktop.DBus.Properties', signature='sa{sv}as')
        def PropertiesChanged(self, iface, changed, invalidated): pass

        @dbus.service.signal(SNI)
        def NewIcon(self): pass

        @dbus.service.signal(SNI)
        def NewTitle(self): pass

        @dbus.service.signal(SNI, signature='s')
        def NewStatus(self, status): pass

        @dbus.service.signal(SNI)
        def NewAttentionIcon(self): pass

        @dbus.service.signal(SNI)
        def NewOverlayIcon(self): pass

        @dbus.service.signal(SNI)
        def NewToolTip(self): pass

    try:
        sni = HornetSNI()
        _sni_ref[0] = sni
        sni.run()
    except Exception as e:
        import traceback
        print(f"[tray] SNI error: {e}")
        traceback.print_exc()


def start_song_from_tray(song_idx):
    """Called from tray to start a specific song."""
    global music_active, music_seg, music_tick, music_dur_ms
    if not has_music:
        return
    music_seg = song_idx
    start_s, end_s = NEEDOLINE_SEGMENTS[music_seg]
    music_dur_ms = (end_s - start_s) * 1000
    try:
        pygame.mixer.music.set_volume(tray_globals['volume'])
        pygame.mixer.music.load(MUSIC_FILE)
        pygame.mixer.music.play(start=float(start_s))
        music_tick = pygame.time.get_ticks()
        music_active = True
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    load_config()
    pygame.mixer.pre_init(44100, -16, 2, 2048)
    pygame.init()
    # Allow only the event types this app uses. Pygame 2.x + Python 3.10 raises
    # SystemError(KeyError: 1) inside event.get() when processing SDL window
    # sub-events (e.g. WINDOWSHOWN=1) whose type isn't in pygame's internal map.
    # Blocking all unneeded events prevents pygame from ever hitting that path.
    pygame.event.set_allowed([
        pygame.QUIT, pygame.KEYDOWN,
        pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION,
    ])
    # SDL may fail to open the default audio device on PipeWire/PulseAudio systems.
    # Retry with explicit drivers until one works.
    if not pygame.mixer.get_init():
        for _drv in ['pipewire', 'pulse', 'alsa', 'jack']:
            os.environ['SDL_AUDIODRIVER'] = _drv
            pygame.mixer.quit()
            try:
                pygame.mixer.init(44100, -16, 2, 2048)
                if pygame.mixer.get_init():
                    print(f"[music] audio OK with SDL_AUDIODRIVER={_drv}")
                    break
            except Exception as _e:
                print(f"[music] {_drv} failed: {_e}")
        else:
            print("[music] all audio drivers failed -  music disabled")
    print(f"[music] mixer init: {pygame.mixer.get_init()}")
    if PLAT == 'Windows':
        set_windows_app_id()

    # Screen dimensions -------------------------------------------------------
    if SCREEN_W:
        screen_w, screen_h = SCREEN_W, SCREEN_H
    else:
        info = pygame.display.Info()
        screen_w, screen_h = info.current_w, info.current_h

    usable_h = USABLE_H if USABLE_H else screen_h

    # Multi-monitor bounds. On Windows these are already populated with real
    # per-monitor data; everywhere else, treat the whole (already-multi-monitor
    # on Linux, via xrandr) screen as a single synthetic monitor.
    global MONITORS, VIRT_LEFT, VIRT_TOP, VIRT_W, VIRT_H
    if not MONITORS:
        MONITORS = [(0, 0, screen_w, screen_h, 0, 0, screen_w, usable_h)]
    if not VIRT_W:
        VIRT_LEFT, VIRT_TOP, VIRT_W, VIRT_H = 0, 0, screen_w, screen_h

    # Load raw sprites before set_mode so we know sizes for the Windows window
    global _raw_sprites, _raw_seqs
    raw_sprites, raw_seqs = load_raw_assets()
    _raw_sprites, _raw_seqs = raw_sprites, raw_seqs
    all_raw = list(raw_sprites.values()) + [s for seq in raw_seqs.values() for s in seq]
    win_w   = max(1, int(max(s.get_width()  for s in all_raw) * SPRITE_SCALE))
    win_h   = max(1, int(max(s.get_height() for s in all_raw) * SPRITE_SCALE))

    # Set icon before set_mode so X11/SDL picks it up at window creation time
    icon_surf_raw = load_app_icon(pre_display=True)
    if icon_surf_raw is not None:
        pygame.display.set_icon(icon_surf_raw)

    # On Windows use a small sprite-sized window; tracking it is much faster
    # than compositing a full-screen layered window every frame via GDI.
    if PLAT == 'Windows':
        screen = pygame.display.set_mode((win_w, win_h + int(Z_OVERHEAD * SPRITE_SCALE)), pygame.NOFRAME)
    else:
        screen = pygame.display.set_mode((screen_w, screen_h), pygame.NOFRAME)
    pygame.display.set_caption('Hornet')
    # Re-set icon with a properly converted surface now that the display exists
    icon_surf = load_app_icon()
    if icon_surf is not None:
        pygame.display.set_icon(icon_surf)

    # Platform setup ----------------------------------------------------------
    hwnd        = None
    click_thru  = True
    shape_mgr   = None
    scan_platforms = _win_scan_platforms if PLAT == 'Windows' else None
    x11_scanner = None

    if PLAT == 'Windows':
        hwnd = pygame.display.get_wm_info()['window']
        tray_globals['hwnd'] = hwnd
        _win_setup(hwnd)
        _win_click_through(hwnd, True)
        set_windows_app_icon(hwnd)
        # Pre-fill with chroma key so the window is invisible before first draw
        screen.fill(CHROMA_KEY)
        pygame.display.flip()

    elif PLAT == 'Linux':
        wm  = pygame.display.get_wm_info()
        wid = wm.get('window', 0)
        if wid:
            # Proper EWMH ClientMessage -  the only reliable way to set ABOVE on a
            # mapped window (xprop -set writes the property directly and is ignored
            # by the WM for windows that are already visible).
            _linux_set_above(wid)
            # Also try wmctrl as an additional belt-and-suspenders approach
            try:
                subprocess.run(['wmctrl', '-i', '-r', hex(wid),
                               '-b', 'add,above,skip_taskbar'],
                              capture_output=True, check=False, timeout=2)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

            # Without a compositor the shape manager gives pixel-perfect
            # transparency; with ARGB it still sets the input shape so the
            # full-screen window doesn't swallow clicks meant for the desktop.
            shape_mgr = X11ShapeManager(input_only=ARGB_MODE)
            if not shape_mgr.connect(wid):
                shape_mgr = None

            # Under Wayland only XWayland apps are listed, so she'd stand on
            # ledges hidden behind native windows; keep platforms off there.
            if not os.environ.get('WAYLAND_DISPLAY'):
                x11_scanner = X11PlatformScanner(wid)
                if x11_scanner.available:
                    scan_platforms = x11_scanner.scan
            tray_globals['window_platforms_ok'] = scan_platforms is not None

    sprites, seqs = convert_assets(raw_sprites, raw_seqs)

    idle_h  = seqs['idle'][0].get_height()
    floor_y = float(usable_h - idle_h)

    idle_w = seqs['idle'][0].get_width()
    center_x = float(screen_w // 2 - idle_w // 2)
    spawn_mode = tray_globals.get('spawn_mode', 'fall')

    hornet = Hornet(
        x       = center_x,
        y       = 50.0,
        sprites = sprites,
        seqs    = seqs,
        floor_y = floor_y,
        monitors   = MONITORS,
        world_left = float(VIRT_LEFT),
        world_w    = float(VIRT_W),
        world_top  = float(VIRT_TOP),
    )
    # Virtual-screen extents span every monitor (VIRT_LEFT/VIRT_W are always
    # populated by this point -  see the MONITORS/VIRT_* fallback above).
    virt_left  = VIRT_LEFT
    virt_right = VIRT_LEFT + VIRT_W
    if spawn_mode == 'walk_from_right':
        hornet.x = float(virt_right)
        hornet._start_walk_in('right', center_x)
    elif spawn_mode == 'walk_from_left':
        hornet.x = float(virt_left - idle_w)
        hornet._start_walk_in('left', center_x)
    else:
        hornet.vy = 120.0

    offscreen = (pygame.Surface((screen_w, screen_h), pygame.SRCALPHA)
                 if ARGB_MODE else None)
    clock = pygame.time.Clock()

    # ── Music state ───────────────────────────────────────────────────────────
    global has_music, music_active, music_seg, music_tick, music_dur_ms
    has_music     = os.path.exists(MUSIC_FILE)

    def start_needoline():
        global music_active, music_seg, music_tick, music_dur_ms
        if not has_music:
            return
        # If auto_random_song is False and a song is selected, use it
        if not tray_globals['auto_random_song'] and tray_globals['current_song'] >= 0:
            music_seg = tray_globals['current_song']
        else:
            music_seg = random.randrange(len(NEEDOLINE_SEGMENTS))
        start_s, end_s = NEEDOLINE_SEGMENTS[music_seg]
        music_dur_ms = (end_s - start_s) * 1000
        try:
            pygame.mixer.music.set_volume(tray_globals['volume'])
            pygame.mixer.music.load(MUSIC_FILE)
            pygame.mixer.music.play(start=float(start_s))
            music_tick   = pygame.time.get_ticks()
            music_active = True
        except Exception as e:
            print(f"[music] playback error: {e}")

    def stop_needoline():
        global music_active
        if music_active:
            pygame.mixer.music.stop()
            music_active = False

    # ── Tray icon (Windows only) ──────────────────────────────────────────────
    hornet_ref = [hornet]  # Mutable reference for tray thread
    tray_thread = None
    if PLAT in ('Windows', 'Linux'):
        tray_thread = threading.Thread(
            target=_create_tray_icon,
            args=(hwnd, hornet_ref),
            daemon=True
        )
        tray_thread.start()

    running = True
    platform_timer = 0.0
    while running and tray_globals['running']:
        dt = min(clock.tick(60) / 1000.0, 0.05)

        # Window tops she can stand on (re-scanned a few times a second)
        if scan_platforms:
            platform_timer -= dt
            if platform_timer <= 0:
                platform_timer = 0.25
                hornet.set_platforms(scan_platforms()
                                     if tray_globals['window_platforms'] else [])

        # On Windows, GetCursorPos works even when WS_EX_TRANSPARENT is set
        # (pygame.mouse.get_pos() returns stale coords when the window is click-through)
        if hwnd:
            _pt = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(_pt))
            mx, my = _pt.x, _pt.y
        else:
            pos = shape_mgr.pointer() if shape_mgr else None
            mx, my = pos if pos else pygame.mouse.get_pos()

        # Windows click-through toggle
        if hwnd:
            should_ct = not hornet.is_clicked(mx,my) and not hornet.dragging and not hornet._pending
            if should_ct != click_thru:
                _win_click_through(hwnd, should_ct)
                click_thru = should_ct

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                hornet.mouse_down(mx, my)
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                hornet.mouse_up(mx, my)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
                if hornet.is_clicked(mx, my):
                    _show_context_menu(mx, my, hornet_ref)
            elif event.type == pygame.MOUSEMOTION:
                hornet.mouse_move(mx, my, dt)

        hornet.update(dt, mx, my)

        # Consume sit animation events
        if hornet.ev_music_start:
            hornet.ev_music_start = False
            start_needoline()
        if hornet.ev_music_stop:
            hornet.ev_music_stop = False
            stop_needoline()

        # Music: loop active segment
        global music_active, music_seg, music_tick, music_dur_ms
        if music_active:
            elapsed = pygame.time.get_ticks() - music_tick
            if elapsed >= music_dur_ms:
                if tray_globals['auto_random_song']:
                    candidates = [i for i in range(len(NEEDOLINE_SEGMENTS)) if i != music_seg]
                    music_seg = random.choice(candidates) if candidates else music_seg
                    music_dur_ms = (NEEDOLINE_SEGMENTS[music_seg][1] - NEEDOLINE_SEGMENTS[music_seg][0]) * 1000
                start_s, _ = NEEDOLINE_SEGMENTS[music_seg]
                pygame.mixer.music.play(start=float(start_s))
                music_tick = pygame.time.get_ticks()

        # Runtime rescale: re-convert all sprites with new SPRITE_SCALE
        global _pending_rescale
        if _pending_rescale:
            _pending_rescale = False
            new_sprites, new_seqs = convert_assets(_raw_sprites, _raw_seqs)
            hornet.sprites           = new_sprites
            hornet.idle_frames       = new_seqs['idle']
            hornet.sit_down_frames   = new_seqs['sit_down']
            hornet.sit_intro_frames  = new_seqs['sit_intro']
            hornet.sit_loop_frames   = new_seqs['sit_loop']
            hornet.sit_outro_frames  = new_seqs['sit_outro']
            hornet.sit_up_frames     = new_seqs['sit_up']
            hornet.sleep_wake_frames = new_seqs['sleep_wake']
            hornet.sleep_frame       = new_sprites['sleep']
            hornet.land_frames       = new_seqs['land']
            hornet.wall_cling_frames = new_seqs['wall_cling']
            hornet.wall_slide_frames = new_seqs['wall_slide']
            hornet.taunt_frames      = new_seqs['taunt']
            hornet.taunt_silk_frames = new_seqs['taunt_silk']
            hornet.walk_frames       = new_seqs['walk']
            hornet.walk_stop_frames  = new_seqs['walk_stop']
            hornet.umbrella_open_frames  = new_seqs['umbrella_open']
            hornet.umbrella_float_frames = new_seqs['umbrella_float']
            hornet.umbrella_close_frames = new_seqs['umbrella_close']
            hornet.turn_frames       = new_seqs['turn']
            hornet.map_open_frames   = new_seqs['map_open']
            hornet.map_idle_frames   = new_seqs['map_idle']
            hornet.map_walk_frames   = new_seqs['map_walk']
            hornet.map_turn_frames   = new_seqs['map_turn']
            hornet.bind_extra_frames(new_seqs)
            old_floor_y    = hornet.floor_y
            hornet.floor_y = hornet._floor_for_x(hornet.x + hornet._idle_w / 2)
            hornet.y      += hornet.floor_y - old_floor_y
            # Stale particle positions are meaningless after a rescale
            hornet.z_particles  = []
            hornet.z_spawn_timer = 0.0
            if PLAT == 'Windows':
                all_scaled = list(new_sprites.values()) + [s for sq in new_seqs.values() for s in sq]
                new_w = max(s.get_width()  for s in all_scaled)
                new_h = max(s.get_height() for s in all_scaled)
                screen = pygame.display.set_mode((new_w, new_h + int(Z_OVERHEAD * SPRITE_SCALE)), pygame.NOFRAME)
                hwnd = pygame.display.get_wm_info()['window']
                tray_globals['hwnd'] = hwnd
                _win_setup(hwnd)
                _win_click_through(hwnd, True)
                click_thru = True
                screen.fill(CHROMA_KEY)
                pygame.display.flip()

        # Render
        if PLAT == 'Windows':
            u32 = ctypes.windll.user32
            z_oh = int(Z_OVERHEAD * SPRITE_SCALE)
            frame = hornet.display_frame()
            frame_x = (screen.get_width() - frame.get_width()) // 2
            if hornet.dragging:
                # Follow the *rotated* bounding box, not the unrotated draw_pos:
                # a big rotation (e.g. grabbed by the feet) swings the sprite far
                # outside the idle-frame footprint, so the fixed sprite-sized
                # window has to slide to keep the rotated bitmap inside.
                min_x, min_y, max_x, max_y = hornet._rotated_bounds(hornet._drag_angle)
                piv_wx = hornet.x - hornet._off_x
                piv_wy = hornet.y - hornet._off_y
                sprite_cx = piv_wx + (min_x + max_x) * 0.5
                sprite_cy = piv_wy + (min_y + max_y) * 0.5
                win_left  = int(sprite_cx - screen.get_width()  * 0.5)
                win_top   = int(sprite_cy - screen.get_height() * 0.5)
                u32.SetWindowPos(hwnd, 0, win_left, win_top, 0, 0, 0x0015)
                draw_x, draw_y = win_left + frame_x, win_top + z_oh  # unused visually now
            else:
                draw_x, draw_y = hornet.draw_pos()
                # Move the small window to follow the sprite. Window is positioned
                # Z_OVERHEAD pixels above the sprite so Z particles have room to
                # float upward without being clipped.
                u32.SetWindowPos(hwnd, 0, draw_x - frame_x, draw_y - z_oh, 0, 0, 0x0015)
            if tray_globals['topmost'] and u32.GetWindow(hwnd, 3):
                # Something is above Hornet -  reassert unless a menu or capturing
                # popup is active (GetGUIThreadInfo catches Win32 menus incl.
                # custom-styled ones; GetCapture catches fully-custom popups).
                gti = _GUITHREADINFO()
                gti.cbSize = ctypes.sizeof(_GUITHREADINFO)
                u32.GetGUIThreadInfo(0, ctypes.byref(gti))
                if not (gti.flags & _GUI_INMENUMODE) and not u32.GetCapture():
                    _win_assert_topmost(hwnd)
            screen.fill(CHROMA_KEY)
            hornet.draw_taunt_silk(screen, frame_x, z_oh)
            if hornet.dragging:
                piv_wx = hornet.x - hornet._off_x
                piv_wy = hornet.y - hornet._off_y
                hornet._blit_rotated(screen, piv_wx - win_left, piv_wy - win_top)
            else:
                screen.blit(frame, (frame_x, z_oh))
            hornet.draw_z_particles(screen, frame_x, z_oh)
        elif ARGB_MODE and offscreen:
            render_argb(screen, offscreen, hornet)
        else:
            screen.fill((0, 0, 0))
            hornet.draw_taunt_silk(screen, *hornet.draw_pos())
            hornet.draw(screen)
            hornet.draw_z_particles(screen, *hornet.draw_pos())

        pygame.display.flip()

        # Update X11 shape (non-ARGB path)
        if shape_mgr:
            fx, fy = hornet.draw_pos()
            shape_mgr.update(fx, fy, hornet.display_frame(), hornet.shape_key())

    if shape_mgr:
        shape_mgr.disconnect()
    if x11_scanner:
        x11_scanner.close()
    stop_needoline()
    pygame.quit()
    sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
