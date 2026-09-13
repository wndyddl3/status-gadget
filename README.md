# Status Gadget

A small always-on-top Windows widget that shows your **CPU, GPU and RAM** usage, with a little green pet that reacts to how hard your PC is working. It also shows each open **Houdini** session's CPU and memory.

- Busy PC (85%+): the pet casts magic while it wanders.
- Idle for a minute: it dozes off ("Zzz").
- Houdini open: it wears a wizard hat.
- Low memory: it sweats, then holds up a warning sign.
- The yard's sky follows the time of day (morning, day, evening, night).

It's light on your PC: usually well under 1% of one CPU core, a few percent while the pet is casting.

## What you need

- **Windows 10 or 11**
- **Python 3** from [python.org](https://www.python.org/downloads/) (free). Nothing else to install.
- GPU usage needs an **NVIDIA** graphics card; other cards show "—".

## Start it

1. Download this folder (green **Code** button → **Download ZIP**) and unzip it anywhere.
2. Double-click **`status_gadget.pyw`**.

Only one copy runs at a time. To start it with Windows, put a shortcut to `status_gadget.pyw` in your Startup folder (press `Win + R`, type `shell:startup`).

## How to use it

| Do this | What happens |
|---|---|
| Drag anywhere | Move the gadget |
| Drag the bottom-right corner | Resize (in the one-line view: drag the right end, sideways) |
| Right-click | Menu: views, options, Settings…, reset, quit |
| View button (top-right) | Switch view: Full → Compact → One line |
| Click a number | Show RAM in GB or % |
| Click the yard | The pet walks there |
| Click the pet | Hearts and a hop |

**Settings…** (in the right-click menu): status size (75–150%), show or hide the pet, transparent background. With a transparent background, the panel appears when you point at the gadget.

**Tray icon:** while it runs, a green pet icon sits in the taskbar's hidden-icons area (the **^** arrow). Right-click it → **Quit Status Gadget**.

Your window position and options are saved in `settings.json` next to the script.

## Coming soon

- **More DCC apps:** CPU and memory for other open DCC apps, shown like the Houdini rows today: Maya, Nuke, Blender, Unreal Engine, 3ds Max and Cinema 4D. The pet will wear a different hat for each app.

## Files

- `status_gadget.pyw`: the gadget (window, pet, drawing)
- `metrics.py`: reads CPU, GPU, RAM and Houdini usage
- `tray.py`: the taskbar tray icon
- `tools/perf_harness.py`: test tool that measures the gadget's own CPU use (for development)
- `docs/`: design notes and the interactive design prototype (for development)
