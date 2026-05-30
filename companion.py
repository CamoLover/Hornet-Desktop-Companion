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

import os, sys, math, glob, platform, ctypes, ctypes.util, subprocess, re, random, threading, json
from PIL import Image
from io import BytesIO

PLAT = platform.system()

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


SCREEN_W = SCREEN_H = 0
USABLE_H = 0           # bottom of work area (where taskbar starts)
ARGB_MODE = False

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
    # Physical screen size
    SCREEN_W = ctypes.windll.user32.GetSystemMetrics(0)   # SM_CXSCREEN
    SCREEN_H = ctypes.windll.user32.GetSystemMetrics(1)   # SM_CYSCREEN
    # Work area bottom = where taskbar starts (SPI_GETWORKAREA = 48)
    _rc = _wt.RECT()
    ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(_rc), 0)
    USABLE_H = _rc.bottom

os.environ.setdefault('SDL_VIDEO_WINDOW_POS', '0,0')

import pygame
import numpy as np

CHROMA_KEY = (0, 0, 255)   # must match extract_sprite.py

MUSIC_FILE = "assets/audio/needoline.mp3"
ICON_FILES = [
    'assets/logo/logo-hdc.png',
    'assets/logo/logo-hdc.ico',
]
CONFIG_PATH = 'config.json'
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

_win32_topmost_proc = None  # module-level ref keeps ctypes callback alive

def _win_setup(hwnd):
    u = ctypes.windll.user32
    # hWndInsertAfter must be pointer-sized; without argtypes ctypes defaults to 32-bit
    # which truncates HWND_TOPMOST (-1) to 0xFFFFFFFF — an invalid handle on 64-bit Windows
    u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_ssize_t,
                                ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    s = u.GetWindowLongW(hwnd, -20)
    u.SetWindowLongW(hwnd, -20, s | 0x00080000)
    # COLORREF format is 0x00BBGGRR; pure blue (0,0,255) = 0x00FF0000
    u.SetLayeredWindowAttributes(hwnd, 0x00FF0000, 0, 0x1)
    u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)  # -1 = HWND_TOPMOST (64-bit)

def _win_install_topmost_hook(hwnd):
    """Subclass the WndProc to intercept WM_WINDOWPOSCHANGING and force SWP_NOZORDER,
    preventing SDL2 from removing our always-on-top z-order on focus loss."""
    global _win32_topmost_proc
    u = ctypes.windll.user32

    class _WP(ctypes.Structure):
        _fields_ = [('hwnd', ctypes.c_void_p), ('after', ctypes.c_void_p),
                    ('x', ctypes.c_int), ('y', ctypes.c_int),
                    ('cx', ctypes.c_int), ('cy', ctypes.c_int),
                    ('flags', ctypes.c_uint)]

    PROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_void_p,
                               ctypes.c_uint, ctypes.c_longlong, ctypes.c_longlong)
    u.GetWindowLongPtrW.restype  = ctypes.c_longlong
    u.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.SetWindowLongPtrW.restype  = ctypes.c_longlong
    u.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, PROC]
    u.CallWindowProcW.restype    = ctypes.c_longlong
    u.CallWindowProcW.argtypes   = [ctypes.c_longlong, ctypes.c_void_p,
                                     ctypes.c_uint, ctypes.c_longlong, ctypes.c_longlong]
    old = u.GetWindowLongPtrW(hwnd, -4)  # GWLP_WNDPROC

    def _proc(h, msg, wp, lp):
        if msg == 0x0046:  # WM_WINDOWPOSCHANGING
            pos = ctypes.cast(ctypes.c_void_p(lp), ctypes.POINTER(_WP)).contents
            pos.flags |= 0x0004  # SWP_NOZORDER: lock z-order, keeps HWND_TOPMOST
        return u.CallWindowProcW(old, h, msg, wp, lp)

    _win32_topmost_proc = PROC(_proc)
    u.SetWindowLongPtrW(hwnd, -4, _win32_topmost_proc)

def _win_click_through(hwnd, enable: bool):
    u = ctypes.windll.user32
    s = u.GetWindowLongW(hwnd, -20)
    u.SetWindowLongW(hwnd, -20, (s | 0x20) if enable else (s & ~0x20))


# ─────────────────────────────────────────────────────────────────────────────
# Sprite loading
# ─────────────────────────────────────────────────────────────────────────────

def load_raw_assets():
    """Load sprite images without convert()-  safe to call before set_mode."""
    required = {
        'IDLE':            'assets/sprites/idle/hornet_idle.png',
        'FAST_FALL':       'assets/sprites/fast_fall/hornet_fast_fall.png',
        'FAST_FALL_WRONG': 'assets/sprites/fast_fall/hornet_fast_fall_wrong.png',
    }
    missing = [f for f in required.values() if not os.path.exists(f)]
    if not os.path.exists('assets/sprites/sit/hornet_sit_0.png'):
        missing.append('assets/sprites/sit/hornet_sit_0.png')
    if missing:
        print('Missing sprites:', missing)
        print('Run:  python extract_sprite.py')
        sys.exit(1)
    raw_sprites    = {k: pygame.image.load(v) for k, v in required.items()}
    raw_sit_frames = [pygame.image.load(f) for f in sorted(glob.glob('assets/sprites/sit/hornet_sit_*.png'))]
    return raw_sprites, raw_sit_frames


def convert_assets(raw_sprites, raw_sit_frames):
    """Convert raw surfaces to display format and apply chroma key."""
    def _conv(s):
        s = s.convert()
        s.set_colorkey(CHROMA_KEY)
        return s
    sprites    = {k: _conv(v) for k, v in raw_sprites.items()}
    sit_frames = [_conv(s) for s in raw_sit_frames]
    return sprites, sit_frames


def load_app_icon(pre_display=False):
    for path in ICON_FILES:
        if os.path.exists(path):
            try:
                icon = pygame.image.load(path)
                if pre_display:
                    return icon  # no convert() — display mode not set yet
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


class Hornet:
    GRAVITY       = 1800.0
    BOUNCE_DAMP   = 0.45
    FRICTION      = 0.88
    MIN_BOUNCE_VY = 80.0
    SIT_FPS       = 0.1

    def __init__(self, x, y, sprites, sit_frames, floor_y):
        self.x = float(x);  self.y = float(y)
        self.vx = 0.0;      self.vy = 0.0

        self.sprites    = sprites
        self.sit_frames = sit_frames
        self.floor_y    = floor_y

        self.state        = 'IDLE'
        self.facing_right = True

        self.sitting   = False
        self.sit_idx   = 0
        self.sit_timer = 0.0

        self._pending  = False
        self._pend_mx  = 0;  self._pend_my  = 0
        self.dragging  = False
        self._off_x    = 0.0; self._off_y   = 0.0
        self._mvx      = 0.0; self._mvy     = 0.0
        self._last_mx  = 0;   self._last_my  = 0

    # ── helpers ───────────────────────────────────────────────────────────────
    @property
    def _idle_w(self): return self.sprites['IDLE'].get_width()
    @property
    def _idle_h(self): return self.sprites['IDLE'].get_height()

    def current_frame(self) -> pygame.Surface:
        if self.sitting and self.sit_frames:
            return self.sit_frames[self.sit_idx]
        return self.sprites[self.state]

    def is_clicked(self, mx, my) -> bool:
        return (self.x <= mx <= self.x + self._idle_w and
                self.y <= my <= self.y + self._idle_h)

    def is_on_ground(self) -> bool:
        return self.y >= self.floor_y - ON_GROUND_TOL and abs(self.vy) < 60

    # ── events ────────────────────────────────────────────────────────────────
    def mouse_down(self, mx, my):
        if not self.is_clicked(mx, my):
            return
        self._pending = True
        self._pend_mx = mx;  self._pend_my = my
        self._last_mx = mx;  self._last_my = my
        self._mvx = 0.0;     self._mvy = 0.0

    def mouse_move(self, mx, my, dt):
        if self._pending:
            if math.hypot(mx - self._pend_mx, my - self._pend_my) >= DRAG_PIXELS:
                self._pending = False
                if self.sitting:
                    self.sitting = False
                self._start_drag(self._pend_mx, self._pend_my)
                self._upd_drag(mx, my, dt)
        elif self.dragging:
            self._upd_drag(mx, my, dt)

    def mouse_up(self, mx, my):
        if self._pending:
            self._pending = False
            if self.sitting:
                self.sitting = False
            elif self.is_on_ground():
                self.sitting   = True
                self.sit_idx   = 0
                self.sit_timer = 0.0
        elif self.dragging:
            self._end_drag()

    def _start_drag(self, mx, my):
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

    # ── state ─────────────────────────────────────────────────────────────────
    def _upd_state(self):
        if self.sitting or self.dragging:
            self.state = 'IDLE'; return
        spd = math.hypot(self.vx, self.vy)
        if spd < 80 or self.vy <= 0:
            self.state = 'IDLE'
        elif self.vy >= FAST_FALL_VY:
            self.state = ('FAST_FALL_WRONG'
                          if abs(self.vx)/(spd+1e-9) > WRONG_MIX else 'FAST_FALL')
        else:
            self.state = 'IDLE'

    # ── physics ───────────────────────────────────────────────────────────────
    def update(self, dt, screen_w):
        if self.sitting:
            self.sit_timer += dt
            if self.sit_timer >= self.SIT_FPS:
                self.sit_timer = 0.0
                self.sit_idx = (self.sit_idx + 1) % max(1, len(self.sit_frames))
            return
        if self.dragging:
            self._upd_state(); return
        if abs(self.vx) > 30:
            self.facing_right = self.vx > 0
        self.vy += self.GRAVITY * dt
        self.x  += self.vx * dt
        self.y  += self.vy * dt
        if self.y >= self.floor_y:
            self.y  = self.floor_y
            self.vy = -self.vy * self.BOUNCE_DAMP
            self.vx *= self.FRICTION
            if abs(self.vy) < self.MIN_BOUNCE_VY:
                self.vy = 0.0
        if self.x < 0:
            self.x  = 0.0;  self.vx =  abs(self.vx) * self.BOUNCE_DAMP
        elif self.x > screen_w - self._idle_w:
            self.x  = float(screen_w - self._idle_w)
            self.vx = -abs(self.vx) * self.BOUNCE_DAMP
        if self.y < 0:
            self.y  = 0.0;  self.vy = abs(self.vy) * self.BOUNCE_DAMP
        self._upd_state()

    # ── draw ──────────────────────────────────────────────────────────────────
    def _sit_offset(self):
        return int(self._idle_h * SIT_Y_OFFSET) if self.sitting else 0

    def _idle_y_offset(self):
        return -int(self._idle_h * IDLE_Y_OFFSET) if not self.sitting else 0

    def _sit_x_offset(self, frame: pygame.Surface) -> int:
        return (self._idle_w - frame.get_width()) // 2 if self.sitting else 0

    def display_frame(self) -> pygame.Surface:
        """Current frame as it will actually be rendered (h-flip applied)."""
        frame = self.current_frame()
        # Sitting sprites are naturally mirrored vs movement sprites in the atlas
        should_flip = self.facing_right if self.sitting else not self.facing_right
        if should_flip:
            frame = pygame.transform.flip(frame, True, False)
        return frame

    def draw(self, surface):
        frame = self.display_frame()
        draw_x = int(self.x) + self._sit_x_offset(frame)
        draw_y = int(self.y) + self._idle_h - frame.get_height() + self._idle_y_offset() + self._sit_offset()
        surface.blit(frame, (draw_x, draw_y))

    def shape_key(self):
        """Hashable cache key for the current visible frame."""
        if self.sitting:
            return ('sit', self.sit_idx, self.facing_right)
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
    'gravity':       1800.0,
    'bounce_damp':   0.45,
    'friction':      0.88,
    'min_bounce_vy': 80.0,
    'sit_fps':       0.1,
    'fast_fall_vy':  300.0,
    'wrong_mix':     0.65,
    'on_ground_tol': 8,
    'sit_y_offset':  0.235,
    'idle_y_offset': -0.075,
    'volume':        1.0,
}

def load_config(apply_volume=False):
    global FAST_FALL_VY, WRONG_MIX, ON_GROUND_TOL, SIT_Y_OFFSET, IDLE_Y_OFFSET
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

    Hornet.GRAVITY       = float(cfg['gravity'])
    Hornet.BOUNCE_DAMP   = float(cfg['bounce_damp'])
    Hornet.FRICTION      = float(cfg['friction'])
    Hornet.MIN_BOUNCE_VY = float(cfg['min_bounce_vy'])
    Hornet.SIT_FPS       = float(cfg['sit_fps'])
    FAST_FALL_VY  = float(cfg['fast_fall_vy'])
    WRONG_MIX     = float(cfg['wrong_mix'])
    ON_GROUND_TOL = int(cfg['on_ground_tol'])
    SIT_Y_OFFSET  = float(cfg['sit_y_offset'])
    IDLE_Y_OFFSET = float(cfg['idle_y_offset'])
    tray_globals['volume'] = float(cfg['volume'])
    if apply_volume:
        try:
            pygame.mixer.music.set_volume(tray_globals['volume'])
        except Exception:
            pass
    print(f"[config] loaded — gravity={Hornet.GRAVITY}, bounce={Hornet.BOUNCE_DAMP}, volume={tray_globals['volume']}")


# ─────────────────────────────────────────────────────────────────────────────
# ARGB render helpers
# ─────────────────────────────────────────────────────────────────────────────

def render_argb(screen, offscreen, hornet):
    offscreen.fill((0, 0, 0, 0))
    hornet.draw(offscreen)
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
        return handler

    def on_quit(icon=None, item=None):
        tray_globals['running'] = False
        ic = icon if icon is not None else _icon_ref[0]
        if ic:
            ic.stop()

    def on_reload_config(icon=None, item=None):
        load_config(apply_volume=True)

    volume_items = [MenuItem(f'{int(v*100)}%', on_volume(v)) for v in [0.0,0.25,0.5,0.75,1.0]]
    song_items   = [MenuItem('Random', on_song(-1))] + [MenuItem(SONG_NAMES[i], on_song(i)) for i in range(len(SONG_NAMES))]

    def build_menu():
        return Menu(MenuItem('Songs', Menu(*song_items)), MenuItem('Volume', Menu(*volume_items)), MenuItem('Reload Config', on_reload_config), MenuItem('Quit', on_quit))

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
    Requires system python3-gi (gi) and python3-dbus (dbus) — both already
    available via /usr/lib/python3/dist-packages which is in sys.path.
    Menu is shown as a tkinter popup (no GTK/AppIndicator3 package needed).
    """
    import struct

    if 'DBUS_SESSION_BUS_ADDRESS' not in os.environ:
        print("[tray] DBUS_SESSION_BUS_ADDRESS not set — tray disabled")
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
        return cb

    def on_quit_cb():
        tray_globals['running'] = False
        if _sni_ref[0]:
            _sni_ref[0].stop()

    def on_reload_config_cb():
        load_config(apply_volume=True)

    # ── Tkinter popup menu ─────────────────────────────────────────────────────
    def show_menu(x, y):
        def _run():
            try:
                import tkinter as tk
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)

                def close_run(cb):
                    def inner():
                        root.after(50, root.destroy)
                        try: cb()
                        except Exception: pass
                    return inner

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
            print("[music] all audio drivers failed — music disabled")
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

    # Load raw sprites before set_mode so we know sizes for the Windows window
    raw_sprites, raw_sit_frames = load_raw_assets()
    all_raw = list(raw_sprites.values()) + raw_sit_frames
    win_w   = max(s.get_width()  for s in all_raw)
    win_h   = max(s.get_height() for s in all_raw)

    # Set icon before set_mode so X11/SDL picks it up at window creation time
    icon_surf_raw = load_app_icon(pre_display=True)
    if icon_surf_raw is not None:
        pygame.display.set_icon(icon_surf_raw)

    # On Windows use a small sprite-sized window; tracking it is much faster
    # than compositing a full-screen layered window every frame via GDI.
    if PLAT == 'Windows':
        screen = pygame.display.set_mode((win_w, win_h), pygame.NOFRAME)
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
        _win_setup(hwnd)
        _win_install_topmost_hook(hwnd)
        _win_click_through(hwnd, True)
        set_windows_app_icon(hwnd)
        # Pre-fill with chroma key so the window is invisible before first draw
        screen.fill(CHROMA_KEY)
        pygame.display.flip()

    elif PLAT == 'Linux':
        wm  = pygame.display.get_wm_info()
        wid = wm.get('window', 0)
        if wid:
            # Proper EWMH ClientMessage — the only reliable way to set ABOVE on a
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

    sprites, sit_frames = convert_assets(raw_sprites, raw_sit_frames)

    idle_h  = sprites['IDLE'].get_height()
    floor_y = float(usable_h - idle_h)

    hornet = Hornet(
        x          = float(screen_w//2 - sprites['IDLE'].get_width()//2),
        y          = 50.0,
        sprites    = sprites,
        sit_frames = sit_frames,
        floor_y    = floor_y,
    )
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
                was_sitting = hornet.sitting
                hornet.mouse_up(mx, my)
                if hornet.sitting and not was_sitting:
                    start_needoline()
                elif not hornet.sitting and was_sitting:
                    stop_needoline()
            elif event.type == pygame.MOUSEMOTION:
                hornet.mouse_move(mx, my, dt)

        hornet.update(dt, screen_w)

        # Music: stop if sitting ended (e.g. dragged away); loop active segment
        global music_active, music_seg, music_tick, music_dur_ms
        if music_active and not hornet.sitting:
            stop_needoline()
        elif music_active:
            elapsed = pygame.time.get_ticks() - music_tick
            if elapsed >= music_dur_ms:
                if tray_globals['auto_random_song']:
                    candidates = [i for i in range(len(NEEDOLINE_SEGMENTS)) if i != music_seg]
                    music_seg = random.choice(candidates) if candidates else music_seg
                    music_dur_ms = (NEEDOLINE_SEGMENTS[music_seg][1] - NEEDOLINE_SEGMENTS[music_seg][0]) * 1000
                start_s, _ = NEEDOLINE_SEGMENTS[music_seg]
                pygame.mixer.music.play(start=float(start_s))
                music_tick = pygame.time.get_ticks()

        # Render
        if PLAT == 'Windows':
            draw_x, draw_y = hornet.draw_pos()
            # Move the small window to follow the sprite (SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE)
            ctypes.windll.user32.SetWindowPos(hwnd, 0, draw_x, draw_y, 0, 0, 0x0015)
            # Maintain topmost state every frame
            if tray_globals['topmost']:
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)
            screen.fill(CHROMA_KEY)
            screen.blit(hornet.display_frame(), (0, 0))
        elif ARGB_MODE and offscreen:
            render_argb(screen, offscreen, hornet)
        else:
            screen.fill((0, 0, 0))
            hornet.draw(screen)

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
