# Status Gadget — Session Handoff (2026-09-13)

## Where we are
Staged flow (see "Working rules" in `CLAUDE.md`):

1. Text plan — **done**
2. Static image Artifact — **done**
3. Interactive Artifact prototype — **approved** (latest publish: Version 15 = design v12).
4. Real tool — v10 features built; the v11 + v12 changes below are **built (2026-09-13)** and verified with the harness.

**Design v13 (prototype Version 21) is built into the real tool (2026-09-13).** The user skipped the static image stage for this round and approved the build. Harness (16 scenarios, share of one core): one-line view 0.3–0.8%, transparent hovered 1.0%, others unchanged (walking 1–2%, busy 4%, busy + 96% RAM at night 9.4%). Code notes: `view` StringVar + `NEXT_VIEW`; `gw()` = current width; `line_items()` trims; `_glide()` + `_kick_frame()` animate the bar width; `_poll_hover()` / `_cursor_inside()` (the harness fakes it) drive the hover panel; the yard bake is cached by `yard_key`, so hover redraws don't re-bake.

**Tray icon — built (2026-09-13)**, option (b) chosen by the user: `tray.py` (pure ctypes). While the gadget runs, a small green pet-face icon sits in the notification area ("^" overflow by default) with the tooltip "Status Gadget". Right-click → "Quit Status Gadget". Left-click does nothing. Its hidden window lives on the Tk thread and Tk's event loop dispatches its messages (no thread, no polling). It re-adds itself when Explorer restarts (`TaskbarCreated`), and is removed in `Gadget.quit()`. If the process is killed instead of quit, Windows keeps a stale icon until the mouse passes over it. Failure to create it is ignored.

**Next step:** wait for the user's feedback after real use.

What v13 contains:
- **New third view, "One line"** (compact view stays as in v12): a thin bar, height 28 × status size, **no pet or yard**. CPU, GPU, RAM, then each Houdini session item (`● Houdini 1  CPU 61%  RAM 8.2G`; "+N more" when more than 3 sessions), packed with fixed gaps (12 between items, 14 left padding). Width: fits the content (`ceil((14 + items + 8) × size) + 40`, where 40 = view button + resize handle) until the user drags the right end. Resizing is **sideways only**, with an ↔ cursor and a small ‖ handle shown on hover. The drag sets a *target* width (settings key `line_w`, `null` = show everything), clamped between the CPU/GPU/RAM-only width and the full-content width. Trimming from the end: (1) session names shorten from the last item leftward, "Houdini 2" → "Hou 2" ("Houdini" → "Hou"); (2) then values drop **one at a time from the end** (user request, v13): the last session item loses its RAM part, then goes entirely. CPU/GPU/RAM always stay. **Width motion** (user asked for a natural slide; an instant snap felt jumpy): while dragging, the drawn width follows the pointer exactly (the gap before the button is at most one value). On release, and whenever the content changes, the width glides to the content width (`disp += (goal − disp) × min(1, dt × 14)`, about 0.15 s), and `line_w` is set to the snapped width so the next drag starts at the bar's end. Real tool: resize the window per frame only during the glide, then stop. The view button is drawn after the rows so it covers text while a growing bar glides open. The ">3 sessions → 2 + '+N more'" rule still applies before trimming. "Reset size" goes back to fit-content. Clicking anywhere except the button and handle toggles GB/%.
- **View switching:** the view button cycles Full → Compact → One line. It sits top-right in the yard, or at the right end of the bar in one-line view, with a three-dots icon. The menu replaces "Compact view" with three checkable items: Full view / Compact view / One line. Settings key: `view` (`full` | `compact` | `line`) replaces `compact`. The real tool should skip the pet frame loop in one-line view, like when the pet is hidden.
- **Transparent background reveals on hover:** the panel and edge are hidden until the cursor is inside the window rect. Then the normal panel, edge and resize grip show, so empty space can be dragged. No solid grip corner otherwise. Real tool: poll `GetCursorPos` against the window rect about 10×/s (cheap), and redraw only when the hover state changes. Color-key transparency can't do semi-transparent, so the panel is either shown or hidden.

**Built: prototype Versions 11 + 12** (the original approved spec, kept for reference)
- One Houdini row per session ("Houdini" if only one, else "Houdini 1", "Houdini 2", ...). If there are more than 3 sessions, show 2 rows plus "+N more" with their combined values.
- Helper processes (husk, mantra, hbatch, hython) count toward the session that started them, found via the parent PID. When run standalone, they get their own row.
- Houdini RAM should use the **private working set** (the same number as Task Manager), not the working set, which counted shared DLL and driver memory (11.3 GB shown vs 8.2 GB real). `PROCESS_MEMORY_COUNTERS_EX2.PrivateWorkingSetSize` was tested and works on this PC (Win10 19045).
- GB/% switch: click a number, or the menu item "Show RAM as %". It affects RAM and Houdini RAM only (CPU and GPU are always %) and is remembered in settings.
- **Version 12** adds:
  - **Show pet** (menu + Settings). Off hides the whole yard and the view-toggle button; the status sits at the top.
  - **Transparent background** (menu + Settings). No panel. (The dark text outline was built, then removed at the user's request.) Empty pixels are click-through, so a small solid grip corner stays for resizing.
  - **Settings…** window with a **Status size** slider (75–150%). It scales text, bars and rows only; the pet and yard don't scale; the min width grows with the size.
  - "Reset size" also resets the status size to 100%.
  - The Settings window is movable: drag its title row. Real tool: a frameless dark `Toplevel`, dragged by its title row, and it remembers its position.
- Real-tool notes:
  - Transparent mode uses Tk `-transparentcolor`. Anti-aliased text edges blend with the key color, so in transparent mode switch the key from magenta to a dark color close to the text outline (e.g. `#0F0D16`). The fringe then reads as part of the dark outline instead of pink.
  - Tk text has no stroke: fake the outline by drawing the text 4–8 times offset by 1 px in the dark color, then the colored text on top. That applies to status text only, and only in transparent mode, so it isn't an animation cost.
  - The Settings window is a small frameless dark `Toplevel` with a `ttk.Scale` (or `tk.Scale`) and checkboxes, dragged by its title row, and its position is saved.
  - Per-session Houdini: group processes by the main app (houdini/hindie/happrentice). Assign helper processes to a session via the parent PID chain from `PROCESSENTRY32W.th32ParentProcessID`. Read memory with `PrivateWorkingSetSize` (EX2 struct).
  - New settings keys: `ram_pct`, `show_pet`, `transparent`, `status_size`, `settings_x`, `settings_y`.

## Artifact
- URL: https://claude.ai/code/artifact/9aa605fb-bad3-4875-8c28-082df7fb149d
- Source: `docs/design/status-gadget-design.html`
- In a new session, updating it needs `url` passed to the Artifact tool, and the artifact must be **read first** (`action: "read"`), then republish from the same source file.
- Page text must stay English.

## Approved design (as in prototype v10)
**Window**
- Frameless, dark, always on top (toggle in menu). Drag anywhere to move; position remembered.
- Resizable from the bottom-right corner: width 220–400 (default 280), yard height 56–130 (default 80). Text size fixed; extra width widens the yard and bars, extra height only raises the yard. "Reset size" in the menu.
- Right-click menu: Always on top ✓, Compact view ✓, Reset position, Reset size, Quit.
- Launched by double-clicking; no auto-start with Windows.

**Layout**
- Top: the yard, a "sky window" whose gradient follows the real clock:
  morning 6–10 (teal to pink, rising sun), day 10–17 (calm blue, sun + clouds), evening 17–20 (purple to orange sunset), night 20–6 (near-black navy, stars, moon, fireflies).
- Small toggle button at the yard's top-right switches between full bars and a compact single line.
- Full view: CPU, GPU, RAM rows (label, continuous rounded bar, value). **No VRAM** (user explicitly dropped it).
- Compact view: one line of colored dot + label + value for each metric.
- Houdini row (only while Houdini runs): Houdini CPU % and RAM (combined across houdini*, hindie, happrentice, hython, husk, hbatch, mantra).
- Bar colors: <60 green, 60–85 amber, ≥85 red.

**Palette (gadget)**
panel `#1E1B26`, edge `#34303F`, track `#2E2A3A`, label `#8F88A8`, text `#ECE9F3`, ok `#6FCF9C`, warn `#F2C166`, crit `#F08B7E`, pet `#6CC486` (feet `#55AA6E`), hat `#9585F0`, magic `#B9A8FF`, star `#FFD86B`. Sky colors are in the prototype's `SKY` table.

**Pet** (round green blob; the frog idea was rejected)
- Walks and pauses, turns at the walls.
- Busy (**CPU, GPU or RAM ≥ 85%**): **keeps wandering** (walks, pauses, turns) while casting magic; the rotating magic circle moves with it and sparkles rise behind it. Faster and brighter at higher load. Gentle, slow wand sway only; **no shaking anywhere** (user request, v10).
- Idle (CPU and GPU < 15% for 1 minute; 5 s in the prototype): dozes, "Zzz".
- Houdini running: wizard hat. Houdini running + busy: hat + wand (full wizard).
- RAM warning levels: 50% glances at the bars, 70% sweat drop and walks slower, 85% yellow "!" sign and RAM bar glow, 95% red pulsing "!!" sign, RAM bar and border pulse red (the pet no longer shakes).
- Click the yard: the pet walks there (wakes if asleep). Click the pet: hearts and a hop.
- Click vs drag: a press without moving counts as a click; moving more than 3 px moves the window.

## Decisions & findings
- **Render detection:** the user renders in the Solaris viewport with **Arnold** (`hdArnold` is loaded in houdini.exe). That runs in-process with no `husk`, so it can't be detected from the outside. Busy is therefore based on overall system status. A future Houdini-side helper script could report "rendering" accurately; keep the busy trigger easy to swap.
- **No external installs:** CPU, RAM and per-process stats come from Windows kernel32 via ctypes (no psutil). GPU comes from `nvidia-smi` (RTX 2070 SUPER, 8 GB). System RAM is 64 GB.
- **Efficiency priority:** the monitor itself must stay light. Redraw the panel once per second and only the pet about 30 fps, run nvidia-smi on a background thread every 2 s, and lower the frame rate while dozing.

## Code
- `metrics.py`: `SystemCpu`, `read_ram()`, `HoudiniMonitor.sample()` → list of `{cpu, ram_gb}` per session, oldest first (empty list when none run), `GpuMonitor.sample()`. Sessions: each main app (`MAIN_PREFIXES`) is a root; helpers (`HELPER_PREFIXES`) walk the parent-PID chain (max 8 steps) to the nearest main app, else the highest helper ancestor. RAM = `PrivateWorkingSetSize` (EX2 struct), falling back to `WorkingSetSize` if the EX2 call fails. GPU reads NVML (`nvml.dll`) directly, about 0.5 ms per call. If NVML is missing, it falls back to ONE long-running `nvidia-smi -lms` process. VRAM and the "rendering" flag were removed.
- `status_gadget.pyw`: tkinter Canvas port of prototype v12. It allows a single instance (named mutex) and saves `settings.json` next to the script (x, y, w, yard_h, compact, topmost, ram_pct, show_pet, transparent, status_size, settings_x, settings_y).
  - `_refresh()` applies all view options (key color, row font size, min width, pet cleanup) and redraws; menu, Settings window and "Reset size" all go through it.
  - `draw_rows()` draws the status area in local units scaled by `self.sz` (own font `f_row`).
  - Transparent mode: key color `#0F0D16`, **no text outline**. The v12 fake outline was removed on 2026-09-13 at the user's request (it looked like a heavy dark edge on their grey desktop; they accept lower readability on bright wallpaper). Only a faint 1 px dark fringe from font anti-aliasing remains, which color-key transparency can't avoid.
  - Pet hidden: the frame loop skips the pet and wakes every 500 ms (50 ms only during the 95% RAM pulse).
  - Settings window: `open_settings()` builds a frameless `Toplevel` (Scale + 2 Checkbuttons sharing the menu's variables); default spot is left of the gadget, else below.
- Performance design (measured on 24 threads, share of one core): walking ~2.6%, busy/casting ~7%, dozing ~3%, standing still ~0.5%. That is about 0.1–0.3% of the whole PC.
  - Profiling showed the cost is Tk building and repainting pet items, not metrics or transparency.
  - The yard (gradient, sun/moon/clouds, floor dots, rounded corners) is baked into one PPM `PhotoImage` (~2 ms, only on layout or time-of-day change).
  - The pet redraws at 20 fps while moving, 8 fps while dozing, and not at all while standing still (look-key check). Sparks are capped at 16. Night stars update at 5 fps.
  - Alpha is faked with cached color mixing against the sky.
- Testing: `python tools/perf_harness.py` imports the .pyw, fakes metric values, runs 12 scenarios (6 s each), measures `process_time`, and saves GDI screenshots to `tools/sheet.png` (plus the Settings window in the 150% scenario). Results on 2026-09-13 (S = 1.0, 24 threads, share of one core; timer steps are ~0.26%): walking 2.3%, busy 5.2–5.5%, busy + 96% RAM at night 10.1%, dozing 2.3%, 5 sessions 2.3%, pet hidden 0.8%, pet hidden + RAM pulse 2.9%, transparent walking 3.6%, transparent + pet hidden 2.1%, 150% busy 5.2%, 75% 2.3%. Yard bake 1.9 ms.
- A live check found the user's 2 open Houdini sessions (4.2 GB and 4.0 GB private memory).

## Later ideas (not in scope now)
- Suggested on 2026-09-13; the user ended the session without picking one. Recommended order: 1 → 3 → 2.
  1. **Houdini closed/crash detection:** a session that held lots of RAM disappears → the pet looks startled, plus a note like "Houdini 2 closed at 14:32 (18 GB)". Detectable from outside.
  2. **Away reaction:** no input for a while (`GetLastInputInfo`) → the pet sleeps; on return it greets you and can show "while you were away: CPU peak 97%, RAM peak 58 GB".
  3. **Animated tray icon (RunCat-style):** the tray pet runs faster with CPU load.
  - Others: render-finished alert via a Houdini-side helper script (with render time), cache-drive free-space warning, daily summary, pet follows/looks at the cursor (Neko-style), pet leveling with accessories, Shimeji-style walking on other windows (not recommended: CPU cost).
- Stage 2 project: invite friends. Recommended approach is a shared cloud folder (Google Drive/OneDrive) where each gadget writes a small pet/status JSON. Open question: show only the friend's pet, or also their load?
- Render-finished alert, cache-drive free space, render elapsed time, opacity setting, pet leveling, pet colors, reactions to other apps (Nuke, Blender, ...), render log.
