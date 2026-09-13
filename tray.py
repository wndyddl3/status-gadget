"""Notification-area ("tray") icon: shows the gadget is running; right-click it to quit.

Pure ctypes, no installs. The icon's hidden window lives on the Tk thread, and Tk's own
event loop dispatches its messages, so there is no extra thread and no polling.
"""
import ctypes
import ctypes.wintypes as wt
import math

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = wt.LPARAM
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

NIM_ADD, NIM_DELETE = 0, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
WM_NULL, WM_CONTEXTMENU, WM_RBUTTONUP, WM_APP = 0x0000, 0x007B, 0x0205, 0x8000
TPM_RIGHTBUTTON, TPM_BOTTOMALIGN, TPM_NONOTIFY, TPM_RETURNCMD = 0x0002, 0x0020, 0x0080, 0x0100
ERROR_CLASS_ALREADY_EXISTS = 1410
CMD_QUIT = 1
CLASS_NAME = "StatusGadgetTray"


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wt.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wt.ATOM
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.DestroyWindow.argtypes = [wt.HWND]
user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
user32.RegisterWindowMessageW.restype = wt.UINT
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.LPVOID]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.CreateIconIndirect.restype = wt.HICON
user32.DestroyIcon.argtypes = [wt.HICON]
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFOHEADER), wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wt.UINT, wt.UINT, wt.LPVOID]
gdi32.CreateBitmap.restype = wt.HBITMAP
gdi32.DeleteObject.argtypes = [wt.HANDLE]
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE


def _pet_pixels(s):
    """The gadget's green pet (body + two eyes) as s*s top-down BGRA pixels, soft edges."""
    body, eye = (0x6C, 0xC4, 0x86), (0x1E, 0x1B, 0x26)

    def cover(px, py, cx, cy, rx, ry):
        return max(0.0, min(1.0, 0.5 - (math.hypot((px - cx) / rx, (py - cy) / ry) - 1) * min(rx, ry)))

    buf = bytearray(s * s * 4)
    for y in range(s):
        for x in range(s):
            px, py = x + 0.5, y + 0.5
            a = cover(px, py, s / 2, s * 0.56, s * 0.47, s * 0.4)
            if a <= 0:
                continue
            e = max(cover(px, py, s * 0.33, s * 0.5, s * 0.08, s * 0.08),
                    cover(px, py, s * 0.67, s * 0.5, s * 0.08, s * 0.08))
            r, g, b = (round(c0 + (c1 - c0) * e) for c0, c1 in zip(body, eye))
            i = (y * s + x) * 4
            buf[i:i + 4] = bytes((b, g, r, round(a * 255)))
    return bytes(buf)


def _make_icon():
    s = user32.GetSystemMetrics(49)            # SM_CXSMICON: tray icon size at the current DPI
    bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), s, -s, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    color = gdi32.CreateDIBSection(None, ctypes.byref(bih), 0, ctypes.byref(bits), None, 0)
    if not color:
        return None
    pixels = _pet_pixels(s)
    ctypes.memmove(bits, pixels, len(pixels))
    mask = gdi32.CreateBitmap(s, s, 1, 1, None)  # unused: the color bitmap's alpha decides
    icon = user32.CreateIconIndirect(ctypes.byref(ICONINFO(True, 0, 0, mask, color)))
    gdi32.DeleteObject(mask)
    gdi32.DeleteObject(color)
    return icon


class TrayIcon:
    """Adds the icon on creation; call remove() before the program exits."""

    def __init__(self, tip, on_quit):
        self.on_quit = on_quit
        # set before the window exists: Windows calls _proc during CreateWindowExW
        self.callback_msg = WM_APP + 1
        self.taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")   # Explorer restarted
        self._proc_ref = WNDPROC(self._proc)   # keep the callback alive as long as the window
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW(lpfnWndProc=self._proc_ref, hInstance=hinst, lpszClassName=CLASS_NAME)
        if not user32.RegisterClassW(ctypes.byref(wc)) and ctypes.get_last_error() != ERROR_CLASS_ALREADY_EXISTS:
            raise OSError("could not register the tray window class")
        self.hwnd = user32.CreateWindowExW(0, CLASS_NAME, tip, 0, 0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            raise OSError("could not create the tray window")
        self.icon = _make_icon()
        self.nid = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=self.hwnd, uID=1,
                                   uFlags=NIF_MESSAGE | NIF_ICON | NIF_TIP,
                                   uCallbackMessage=self.callback_msg, hIcon=self.icon, szTip=tip)
        self.shown = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid)))

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == self.callback_msg:
            if lparam & 0xFFFF in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._menu()
            return 0
        if msg == self.taskbar_created:
            self.shown = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid)))
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _menu(self):
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, 0, CMD_QUIT, "Quit Status Gadget")
        user32.SetForegroundWindow(self.hwnd)  # lets the menu close when clicking elsewhere
        cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_BOTTOMALIGN | TPM_NONOTIFY | TPM_RETURNCMD,
                                    pt.x, pt.y, 0, self.hwnd, None)
        user32.DestroyMenu(menu)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        if cmd == CMD_QUIT:
            self.on_quit()

    def remove(self):
        if self.hwnd:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))
            if self.icon:
                user32.DestroyIcon(self.icon)
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
