"""Drives the real gadget through fake scenarios, measures its own CPU, and saves one contact-sheet PNG."""
import ctypes, importlib.machinery, importlib.util, os, struct, sys, time, zlib

OUT = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(OUT)                     # the project folder is this tool's parent
sys.path.insert(0, PROJECT)
ctypes.windll.shcore.SetProcessDpiAwareness(1)
loader = importlib.machinery.SourceFileLoader("sg", os.path.join(PROJECT, "status_gadget.pyw"))
spec = importlib.util.spec_from_loader("sg", loader)
sg = importlib.util.module_from_spec(spec)
loader.exec_module(sg)

sg.SETTINGS_PATH = os.path.join(OUT, "settings_test.json")
sg.SLEEP_AFTER = 2
vals = {"cpu": 34, "gpu": 28, "ram": 36, "hou": [], "phase": "day"}
sg.read_ram = lambda: (vals["ram"] / 100 * 64, 64.0)
sg.time_phase = lambda: vals["phase"]

g = sg.Gadget()
g.cpu_reader.sample = lambda: vals["cpu"]
g.gpu_reader.sample = lambda: vals["gpu"]
g.hou_reader.sample = lambda: vals["hou"]

# time the per-frame pet redraw
_draw = g.draw_pet
cost = []
def timed():
    t0 = time.perf_counter(); _draw(); cost.append(time.perf_counter() - t0)
g.draw_pet = timed

def sessions(n):
    return [{"cpu": c, "ram_gb": r} for c, r in ((61.0, 8.2), (14.0, 3.1), (4.0, 1.2), (2.0, 0.9), (1.0, 0.5))[:n]]


# name, cpu, gpu, ram, Houdini sessions, phase, options (view, size, pet, clear, hover, sz, pct, line_w, settings)
SCEN = [
    ("normal / day", 34, 28, 36, 0, "day", {}),
    ("busy + Houdini / evening", 92, 97, 71, 1, "evening", {}),
    ("low memory 96% / night", 30, 25, 96, 1, "night", {}),
    ("idle -> dozing / night / compact", 5, 3, 22, 0, "night", {"view": "compact"}),
    ("resized 360x120 / morning", 50, 40, 55, 1, "morning", {"size": (360, 120)}),
    ("5 Houdini sessions / RAM %", 60, 40, 55, 5, "day", {"pct": True}),
    ("pet hidden / 3 sessions", 50, 40, 55, 3, "day", {"pet": False}),
    ("pet hidden / RAM 96% pulse", 30, 25, 96, 1, "day", {"pet": False}),
    ("transparent / walking / 2 sessions", 34, 28, 36, 2, "evening", {"clear": True}),
    ("transparent + pet hidden / compact", 34, 28, 36, 2, "day", {"clear": True, "pet": False, "view": "compact"}),
    ("transparent hovered / walking", 34, 28, 36, 1, "evening", {"clear": True, "hover": True}),
    ("status 150% / busy / 2 sessions", 92, 60, 55, 2, "day", {"sz": 150, "settings": True}),
    ("status 75% / 4 sessions", 34, 28, 36, 4, "night", {"sz": 75}),
    ("one line / 2 sessions", 34, 28, 36, 2, "day", {"view": "line"}),
    ("one line narrowed to 330 / 4 sessions", 60, 40, 55, 4, "day", {"view": "line", "line_w": 330}),
    ("one line 150% / transparent hovered", 34, 28, 36, 2, "night", {"view": "line", "clear": True, "hover": True, "sz": 150}),
]
shots, report = [], []


def grab(win=None):
    win = win or g.root
    win.update_idletasks()
    x, y, w, h = win.winfo_rootx() - 6, win.winfo_rooty() - 6, win.winfo_width() + 12, win.winfo_height() + 12
    u, gd = ctypes.windll.user32, ctypes.windll.gdi32
    for f in (u.GetDC, gd.CreateCompatibleDC, gd.CreateCompatibleBitmap, gd.SelectObject):
        f.restype = ctypes.c_void_p
    gd.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gd.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gd.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gd.BitBlt.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint32]
    gd.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    gd.DeleteObject.argtypes = gd.DeleteDC.argtypes = [ctypes.c_void_p]
    u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    hdc = u.GetDC(None); mdc = gd.CreateCompatibleDC(hdc); bmp = gd.CreateCompatibleBitmap(hdc, w, h)
    gd.SelectObject(mdc, bmp); gd.BitBlt(mdc, 0, 0, w, h, hdc, x, y, 0x00CC0020)
    hdr = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bih = ctypes.create_string_buffer(hdr, 44)
    buf = ctypes.create_string_buffer(w * h * 4)
    gd.GetDIBits(mdc, bmp, 0, h, buf, bih, 0)
    gd.DeleteObject(bmp); gd.DeleteDC(mdc); u.ReleaseDC(None, hdc)
    raw = buf.raw
    rows = []
    for r in range(h):
        line = raw[r * w * 4:(r + 1) * w * 4]
        rgb = bytearray()
        for i in range(0, len(line), 4):
            rgb += bytes((line[i + 2], line[i + 1], line[i]))
        rows.append(bytes(rgb))
    return w, rows


def save_sheet(path, images, scale=2):
    W = max(w for w, _ in images) * scale
    rows = []
    for w, img in images:
        for r in img:
            px = b"".join(r[i:i + 3] * scale for i in range(0, len(r), 3))
            px += b"\x30\x30\x30" * (W - w * scale)
            rows.extend([px] * scale)
        rows.extend([b"\xff\xff\xff" * W] * 6)
    H = len(rows)
    data = b"".join(b"\x00" + r for r in rows)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(data, 6)) + chunk(b"IEND", b""))


def run(i=0):
    if i == len(SCEN):
        save_sheet(os.path.join(OUT, "sheet.png"), shots)
        print("\n".join(report)); print("S =", g.S, " logical CPUs =", os.cpu_count())
        t0 = time.perf_counter(); g._render_yard(); print(f"yard bake {(time.perf_counter() - t0) * 1000:.1f} ms")
        g.quit(); return
    name, c, gp, r, n_hou, ph, o = SCEN[i]
    vals.update(cpu=c, gpu=gp, ram=r, hou=sessions(n_hou), phase=ph)
    g.close_settings()
    g.view.set(o.get("view", "full"))
    g.line_w, g.line_disp = o.get("line_w"), None
    g._cursor_inside = (lambda h: lambda: h)(o.get("hover", False))   # fake the cursor position
    g.ram_as_pct.set(o.get("pct", False))
    g.show_pet.set(o.get("pet", True))
    g.transparent.set(o.get("clear", False))
    g.status_size.set(o.get("sz", 100))
    g.W, g.YH = o.get("size", (sg.W_DEF, sg.YH_DEF))
    g.hou = vals["hou"]
    g.hou_rows = g._hou_rows(g.hou)
    g._refresh()
    if o.get("settings"):
        g.open_settings()
    cost.clear()
    x0 = g.pet.x
    t0, p0 = time.perf_counter(), time.process_time()
    def done():
        wall, cpu = time.perf_counter() - t0, time.process_time() - p0
        avg = sum(cost) / len(cost) * 1000 if cost else 0
        report.append(f"{name:38s} mood={g.mood:5s} frames/s={len(cost) / wall:5.1f} "
                      f"pet draw={avg:.2f} ms  gadget CPU={cpu / wall * 100:5.2f}% of one core  pet moved {abs(g.pet.x - x0):.0f}px")
        shots.append(grab())
        if g.settings_win:
            shots.append(grab(g.settings_win))
        run(i + 1)
    g.root.after(6000, done)


g.root.after(1500, run)
g.run()
