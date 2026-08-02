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
    'soft_land': False,        # Play landing/wall-cling animations instead of bouncing
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

    def __init__(self):
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
        if key not in self._bitmaps:
            bm = self._make_bitmap(surface)
            if bm:
                self._bitmaps[key] = bm
            else:
                return

        bm = self._bitmaps[key]
        w, h = surface.get_size()

        # Visual: pixel-perfect alpha mask
        self._xext.XShapeCombineMask.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_ulong, ctypes.c_int]
        self._xext.XShapeCombineMask(
            self._dpy, self._win, self.ShapeBounding,
            x, y, bm, self.ShapeSet)

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

        self._xlib.XFlush(self._dpy)


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

def _win_click_through(hwnd, enable: bool):
    u = ctypes.windll.user32
    s = u.GetWindowLongW(hwnd, -20)
    u.SetWindowLongW(hwnd, -20, (s | 0x20) if enable else (s & ~0x20))


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
    """Return a copy of surface with colored pixels' hue replaced by hex_color's hue.
    Pixels that are near-black (outlines/skin) or near-white (mask) are left untouched."""
    r_t = int(hex_color[1:3], 16) / 255.0
    g_t = int(hex_color[3:5], 16) / 255.0
    b_t = int(hex_color[5:7], 16) / 255.0
    target_h, _, _ = colorsys.rgb_to_hsv(r_t, g_t, b_t)

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

    # Vectorized HSV→RGB using the fixed target hue
    h6 = target_h * 6.0
    hi = int(h6) % 6
    f  = h6 - int(h6)
    p  = v * (1.0 - s)
    q  = v * (1.0 - f * s)
    tv = v * (1.0 - (1.0 - f) * s)

    rgb_cases = [
        (v,  tv, p ),
        (q,  v,  p ),
        (p,  v,  tv),
        (p,  q,  v ),
        (tv, p,  v ),
        (v,  p,  q ),
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
    WALK_FPS       = 0.06   # seconds per frame for the walk-in entrance
    WALK_STOP_FPS  = 0.08   # seconds per frame for the walk-in stop animation
    WALK_SPEED     = 240.0  # px/sec while walking on-screen at the entrance

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
    def _idle_w(self): return self.idle_frames[0].get_width()
    @property
    def _idle_h(self): return self.idle_frames[0].get_height()

    def current_frame(self) -> pygame.Surface:
        if self.walk_in_phase == 'walking':
            return self.walk_frames[self.walk_in_idx]
        if self.walk_in_phase == 'stopping':
            return self.walk_stop_frames[self.walk_in_idx]
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
        if self.taunt_phase == 'taunting':
            return self.taunt_frames[self.taunt_idx]
        if self.sit_phase == 'sit_down':
            return self.sit_down_frames[self.sit_idx]
        if self.sit_phase == 'sit_pause_pre':
            return self.sit_down_frames[-1]
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
            if self.sit_phase == 'sit_loop':
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

    def _start_drag(self, mx, my):
        self.land_phase = None
        # Grabbing during the walk-in entrance cancels it so the user is in control.
        self.walk_in_phase = None
        self.walk_in_idx   = 0
        self.walk_in_timer = 0.0
        self.dragging = True
        self._off_x = self.x - mx
        self._off_y = self.y - my
        self.vx = self.vy = 0.0

    def _upd_drag(self, mx, my, dt):
        if dt > 0:
            self._mvx = (mx - self._last_mx) / dt
            self._mvy = (my - self._last_my) / dt
        self._last_mx = mx;  self._last_my = my
        self.x = mx + self._off_x
        self.y = my + self._off_y

    def _end_drag(self):
        self.dragging = False
        self.vx = self._mvx * 0.8
        self.vy = self._mvy * 0.8
        self.inactivity_timer = 0.0

    # ── state ─────────────────────────────────────────────────────────────────
    def _upd_state(self):
        if self.sitting or self.dragging or self.land_phase is not None:
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
                    self.sit_phase = 'sit_pause_pre'
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
            # case monitors of different heights meet at this world edge)
            self.floor_y = self._floor_for_x(self.x + self._idle_w / 2)
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
        if self.walk_in_phase == 'walking':
            self.x += self.WALK_SPEED * self.walk_in_dir * dt
            reached = (self.walk_in_dir < 0 and self.x <= self.walk_in_target_x) or \
                      (self.walk_in_dir > 0 and self.x >= self.walk_in_target_x)
            if reached:
                self.x = self.walk_in_target_x
                self.walk_in_phase = 'stopping'
                self.walk_in_idx   = 0
                self.walk_in_timer = 0.0
                return
            self.walk_in_timer += dt
            if self.walk_in_timer >= self.WALK_FPS:
                self.walk_in_timer = 0.0
                self.walk_in_idx = (self.walk_in_idx + 1) % len(self.walk_frames)
        elif self.walk_in_phase == 'stopping':
            self.walk_in_timer += dt
            if self.walk_in_timer >= self.WALK_STOP_FPS:
                self.walk_in_timer = 0.0
                self.walk_in_idx += 1
                if self.walk_in_idx >= len(self.walk_stop_frames):
                    self.walk_in_phase = None
                    self.walk_in_idx   = 0
                    # facing_right is preserved so the idle sprite keeps the same
                    # inward-facing orientation Hornet had while stopping.

    # ── physics ───────────────────────────────────────────────────────────────
    def update(self, dt, mx=None, my=None):
        world_right = self.world_left + self.world_w

        # Tick cooldown regardless of state
        if self.taunt_cooldown_timer > 0:
            self.taunt_cooldown_timer -= dt

        # Refresh floor for whichever monitor sits under her right now
        if self.monitors:
            self.floor_y = self._floor_for_x(self.x + self._idle_w / 2)

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
        self.idle_timer += dt
        if self.idle_timer >= self.IDLE_FPS:
            self.idle_timer = 0.0
            self.idle_idx = (self.idle_idx + 1) % len(self.idle_frames)
        if self.dragging:
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

        # Inactivity sleep: only count when resting on the ground
        if self.is_on_ground():
            self.inactivity_timer += dt
            if self.inactivity_timer >= self.SLEEP_TIMEOUT:
                self._start_sleep()
                return
        else:
            self.inactivity_timer = 0.0
        if abs(self.vx) > 30:
            self.facing_right = self.vx > 0
        self.vy += self.GRAVITY * dt
        self.x  += self.vx * dt
        self.y  += self.vy * dt
        soft = tray_globals.get('soft_land', False)
        if self.y >= self.floor_y:
            self.y = self.floor_y
            if soft and abs(self.vy) * self.BOUNCE_DAMP >= self.MIN_BOUNCE_VY:
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
        if self.x < self.world_left:
            self.x = self.world_left
            if soft and abs(self.vx) * self.BOUNCE_DAMP >= 50.0:
                self.vx         = 0.0
                self.vy         = 0.0
                self.wall_side  = 'left'
                self.facing_right = True
                self.land_phase = 'wall_cling'
                self.land_idx   = 0
                self.land_timer = 0.0
            else:
                self.vx = abs(self.vx) * self.BOUNCE_DAMP
        elif self.x > world_right - self._idle_w:
            if soft and abs(self.vx) * self.BOUNCE_DAMP >= 50.0:
                self.vx         = 0.0
                self.vy         = 0.0
                self.wall_side  = 'right'
                self.facing_right = False
                self.land_phase = 'wall_cling'
                self.land_idx   = 0
                self.land_timer = 0.0
                self.x = float(world_right - self.wall_cling_frames[0].get_width())
            else:
                self.x  = float(world_right - self._idle_w)
                self.vx = -abs(self.vx) * self.BOUNCE_DAMP
        if self.y < self.world_top:
            self.y  = self.world_top;  self.vy = abs(self.vy) * self.BOUNCE_DAMP
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
        return (self._idle_w - frame.get_width()) // 2 if self.sitting else 0

    def display_frame(self) -> pygame.Surface:
        """Current frame as it will actually be rendered (h-flip applied)."""
        frame = self.current_frame()
        if not self.facing_right:
            frame = pygame.transform.flip(frame, True, False)
        return frame

    def draw(self, surface):
        frame = self.display_frame()
        draw_x = int(self.x) + self._sit_x_offset(frame)
        draw_y = int(self.y) + self._idle_h - frame.get_height() + self._idle_y_offset() + self._sit_offset()
        surface.blit(frame, (draw_x, draw_y))

    def shape_key(self):
        """Hashable cache key for the current visible frame."""
        if self.walk_in_phase:
            return ('walk_in', self.walk_in_phase, self.walk_in_idx, self.facing_right)
        if self.sleep_phase:
            return ('sleep', self.sleep_phase, self.sleep_idx, self.facing_right)
        if self.land_phase:
            return ('land', self.land_phase, self.land_idx, self.facing_right)
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
        draw_y = int(self.y) + self._idle_h - frame.get_height() + self._idle_y_offset() + self._sit_offset()
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
    'soft_land':      False,
    'taunt_fps':      0.05,
    'taunt_cooldown': 120.0,
    'taunt_hover_time': 2.5,
    'cloak_color': 'default',
    'spawn_mode':  'fall',
}

_SPAWN_MODES = ('fall', 'walk_from_right', 'walk_from_left')

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
    tray_globals['soft_land']   = bool(cfg['soft_land'])
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

    def on_toggle_soft_land(icon=None, item=None):
        tray_globals['soft_land'] = not tray_globals['soft_land']
        _save_config_key('soft_land', tray_globals['soft_land'])

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

    def build_menu():
        return Menu(
            MenuItem('Songs', Menu(*song_items)),
            MenuItem('Volume', Menu(*volume_items)),
            MenuItem('Cloak Color', Menu(*cloak_items)),
            MenuItem('Spawn Mode', Menu(*spawn_items)),
            MenuItem('Sleep Z\'s', on_toggle_sleep_z,
                     checked=lambda item: tray_globals['sleep_z']),
            MenuItem('Soft Landing', on_toggle_soft_land,
                     checked=lambda item: tray_globals['soft_land']),
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

    # ── Callbacks ──────────────────────────────────────────────────────────────
    def on_song_cb(idx):
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

    def on_vol_cb(vol):
        def cb():
            tray_globals['volume'] = vol
            pygame.mixer.music.set_volume(vol)
            _save_config_key('volume', vol)
        return cb

    def on_quit_cb():
        tray_globals['running'] = False
        if _sni_ref[0]:
            _sni_ref[0].stop()

    def on_reload_config_cb():
        load_config(apply_volume=True)

    def on_toggle_sleep_z_cb():
        tray_globals['sleep_z'] = not tray_globals['sleep_z']
        if not tray_globals['sleep_z'] and hornet_ref[0]:
            hornet_ref[0].z_particles  = []
            hornet_ref[0].z_spawn_timer = 0.0
        _save_config_key('sleep_z', tray_globals['sleep_z'])

    def on_toggle_soft_land_cb():
        tray_globals['soft_land'] = not tray_globals['soft_land']
        _save_config_key('soft_land', tray_globals['soft_land'])

    def on_cloak_preset_cb(val):
        def cb(): _set_cloak_color(val)
        return cb

    def on_spawn_mode_cb(val):
        def cb(): _set_spawn_mode(val)
        return cb

    # ── Tkinter popup menu ─────────────────────────────────────────────────────
    def show_menu(x, y):
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
                            print(f"[tray] color picker error: {e}")
                    threading.Thread(target=_pick, daemon=True).start()

                pop = tk.Menu(root, tearoff=0)

                sm = tk.Menu(pop, tearoff=0)
                sm.add_command(label='Random', command=close_run(on_song_cb(-1)))
                for i, n in enumerate(SONG_NAMES):
                    sm.add_command(label=n, command=close_run(on_song_cb(i)))
                pop.add_cascade(label='Songs', menu=sm)

                vm = tk.Menu(pop, tearoff=0)
                for v in [0.0, 0.25, 0.5, 0.75, 1.0]:
                    vm.add_command(label=f'{int(v*100)}%', command=close_run(on_vol_cb(v)))
                pop.add_cascade(label='Volume', menu=vm)

                cm = tk.Menu(pop, tearoff=0)
                cur_cloak = tray_globals.get('cloak_color', 'default')
                cloak_var = tk.StringVar(value=cur_cloak)
                for label, val in _CLOAK_PRESETS:
                    cm.add_radiobutton(label=label, value=val, variable=cloak_var,
                                       command=close_run(on_cloak_preset_cb(val)))
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
                                        command=close_run(on_spawn_mode_cb(val)))
                pop.add_cascade(label='Spawn Mode', menu=spm)

                pop.add_separator()
                sleep_z_var = tk.BooleanVar(value=tray_globals['sleep_z'])
                pop.add_checkbutton(label="Sleep Z's", variable=sleep_z_var,
                                    command=close_run(on_toggle_sleep_z_cb))
                soft_land_var = tk.BooleanVar(value=tray_globals['soft_land'])
                pop.add_checkbutton(label='Soft Landing', variable=soft_land_var,
                                    command=close_run(on_toggle_soft_land_cb))
                pop.add_separator()
                pop.add_command(label='Reload Config', command=close_run(on_reload_config_cb))
                pop.add_command(label='Quit', command=close_run(on_quit_cb))
                pop.bind('<Unmap>', lambda e: root.after(150, root.destroy))
                root.after(0, lambda: pop.tk_popup(x, y, 0))
                root.mainloop()
            except Exception as e:
                print(f"[tray] menu error: {e}")
        threading.Thread(target=_run, daemon=True).start()

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

            if not ARGB_MODE:
                # Shape manager gives pixel-perfect transparency without compositor
                shape_mgr = X11ShapeManager()
                if not shape_mgr.connect(wid):
                    shape_mgr = None

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
    while running and tray_globals['running']:
        dt = min(clock.tick(60) / 1000.0, 0.05)

        # On Windows, GetCursorPos works even when WS_EX_TRANSPARENT is set
        # (pygame.mouse.get_pos() returns stale coords when the window is click-through)
        if hwnd:
            _pt = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(_pt))
            mx, my = _pt.x, _pt.y
        else:
            mx, my = pygame.mouse.get_pos()

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
            draw_x, draw_y = hornet.draw_pos()
            u32 = ctypes.windll.user32
            # Move the small window to follow the sprite (SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE)
            # Window is positioned Z_OVERHEAD pixels above the sprite so Z particles have
            # room to float upward without being clipped.
            z_oh = int(Z_OVERHEAD * SPRITE_SCALE)
            # Center the current frame horizontally within the window so that
            # wider layers (taunt silk) don't overflow and get clipped on the sides.
            frame = hornet.display_frame()
            frame_x = (screen.get_width() - frame.get_width()) // 2
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
    stop_needoline()
    pygame.quit()
    sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
