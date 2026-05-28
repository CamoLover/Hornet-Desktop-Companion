#!/usr/bin/env python3
"""
companion.py-  Hornet desktop companion.
ESC to quit.  Left-click drag to throw.  Click on ground to sit / stand.
"""
import os, sys, math, glob, platform, ctypes, ctypes.util, subprocess, re, random

PLAT = platform.system()

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


def load_app_icon():
    for path in ICON_FILES:
        if os.path.exists(path):
            try:
                icon = pygame.image.load(path)
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

FAST_FALL_VY  = 550.0
WRONG_MIX     = 0.65
DRAG_PIXELS   = 6       # pixels moved before a click becomes a drag
ON_GROUND_TOL = 8
SIT_Y_OFFSET  = 0.20   # fraction of idle_h to shift sit sprite down (crouching effect)


class Hornet:
    GRAVITY       = 1800.0
    BOUNCE_DAMP   = 0.45
    FRICTION      = 0.88
    MIN_BOUNCE_VY = 80.0
    SIT_FPS       = 0.15

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
        draw_y = int(self.y) + self._idle_h - frame.get_height() + self._sit_offset()
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
        draw_y = int(self.y) + self._idle_h - frame.get_height() + self._sit_offset()
        return draw_x, draw_y


# ─────────────────────────────────────────────────────────────────────────────
# ARGB render helpers
# ─────────────────────────────────────────────────────────────────────────────

def render_argb(screen, offscreen, hornet):
    offscreen.fill((0, 0, 0, 0))
    hornet.draw(offscreen)
    pygame.surfarray.blit_array(screen, pygame.surfarray.array2d(offscreen))


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    pygame.mixer.pre_init(44100, -16, 2, 2048)
    pygame.init()
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

    # On Windows use a small sprite-sized window; tracking it is much faster
    # than compositing a full-screen layered window every frame via GDI.
    if PLAT == 'Windows':
        screen = pygame.display.set_mode((win_w, win_h), pygame.NOFRAME)
    else:
        screen = pygame.display.set_mode((screen_w, screen_h), pygame.NOFRAME)
    pygame.display.set_caption('Hornet')
    icon_surf = load_app_icon()
    if icon_surf is not None:
        pygame.display.set_icon(icon_surf)
    if hasattr(pygame.display, "set_window_always_on_top"):
        pygame.display.set_window_always_on_top(True)

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
            # Always-on-top (once, via subprocess-  tools may not be installed)
            try:
                subprocess.run(['xprop', '-id', str(wid), '-f', '_NET_WM_STATE', '32a',
                               '-set', '_NET_WM_STATE', '_NET_WM_STATE_ABOVE'],
                              capture_output=True, check=False)
            except FileNotFoundError:
                pass
            try:
                subprocess.run(['wmctrl', '-i', '-r', hex(wid),
                               '-b', 'add,above,skip_taskbar'],
                              capture_output=True, check=False)
            except FileNotFoundError:
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
    has_music     = os.path.exists(MUSIC_FILE)
    music_active  = False
    music_seg     = 0      # index into NEEDOLINE_SEGMENTS
    music_tick    = 0      # ticks() when current segment started playing
    music_dur_ms  = 0      # duration of current segment in ms

    def start_needoline():
        nonlocal music_active, music_seg, music_tick, music_dur_ms
        if not has_music:
            return
        music_seg    = random.randrange(len(NEEDOLINE_SEGMENTS))
        start_s, end_s = NEEDOLINE_SEGMENTS[music_seg]
        music_dur_ms = (end_s - start_s) * 1000
        try:
            pygame.mixer.music.load(MUSIC_FILE)
            pygame.mixer.music.play(start=float(start_s))
            music_tick   = pygame.time.get_ticks()
            music_active = True
        except Exception:
            pass

    def stop_needoline():
        nonlocal music_active
        if music_active:
            pygame.mixer.music.stop()
            music_active = False

    running = True
    while running:
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
        if music_active and not hornet.sitting:
            stop_needoline()
        elif music_active:
            elapsed = pygame.time.get_ticks() - music_tick
            if elapsed >= music_dur_ms:
                start_s, _ = NEEDOLINE_SEGMENTS[music_seg]
                pygame.mixer.music.play(start=float(start_s))
                music_tick = pygame.time.get_ticks()

        # Render
        if PLAT == 'Windows':
            draw_x, draw_y = hornet.draw_pos()
            # Move the small window to follow the sprite (SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE)
            ctypes.windll.user32.SetWindowPos(hwnd, 0, draw_x, draw_y, 0, 0, 0x0015)
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
    main()
