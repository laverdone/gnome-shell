# Per-Monitor Background Support for GNOME Shell (AI Supported)

## Overview

This patch adds native per-monitor background support to GNOME Shell. It allows
setting different wallpapers for each connected monitor via the
`org.gnome.shell per-monitor-background` GSettings key.

## What was changed

### `js/ui/background.js`

1. **Fixed crash** (`_getMonitorConnector`): The original code called
   `monitorManager.get_logical_monitor(index)` which doesn't exist in the JS
   API. Replaced with `get_logical_monitors().find(lm => lm.get_number() ===
   monitorIndex)` — searches the logical monitor list by monitor number
   (matching `layoutManager.monitors` indices).

2. **GSettings watcher** (`_onPerMonitorBackgroundChanged`): Added a listener
   on `changed::per-monitor-background` on the `org.gnome.shell` schema. When
   the key changes, all existing `Background` objects emit `bg-changed`,
   triggering a full reload with the new per-monitor URIs.

### `js/ui/backgroundMenu.js`

Added a "Per-Monitor Backgrounds…" entry in the desktop right‑click menu
(between "Change Background…" and the separator). It launches the external
helper tool.

### `~/.local/bin/gnome-per-monitor-background`

A standalone GTK4/Adw1 Python tool that provides a graphical interface for
managing per-monitor wallpapers.

## Installation

### Prerequisites

- Arch Linux with `gnome-shell` built from source
- Python 3 with PyGObject, GTK4, and libadwaita:
  ```
  sudo pacman -S python-gobject gtk4 libadwaita
  ```

### Build and install GNOME Shell

```bash
cd /home/gianluca/Documenti/Progetti/gnome-shell

# Remove old build directory if needed
rm -rf builddir

# Configure with system Python (important: avoids pyenv/g-ir-scanner mismatch)
PATH="/usr/bin:$PATH" meson setup builddir \
    --prefix=/usr \
    -Dtests=false \
    -Dman=false

# Compile
PATH="/usr/bin:$PATH" meson compile -C builddir

# Install
sudo PATH="/usr/bin:$PATH" meson install -C builddir
```

### Install the helper tool

The script is already at `~/.local/bin/gnome-per-monitor-background`. Make sure
it's executable:

```bash
chmod +x ~/.local/bin/gnome-per-monitor-background
```

## Testing

### Quick test from terminal

```bash
# Debug mode: prints monitor connectors and current dconf values
~/.local/bin/gnome-per-monitor-background --debug
```

### Setting per-monitor backgrounds via dconf

```bash
# Replace 'eDP-1' and 'DP-1' with your actual connector names
gsettings set org.gnome.shell per-monitor-background \
  "{'eDP-1': 'file:///path/to/image1.jpg', 'DP-1': 'file:///path/to/image2.jpg'}"
```

### Using the GUI tool

1. Right‑click on the desktop
2. Select "Per-Monitor Backgrounds…"
3. For each monitor, click "Choose Image" and select a wallpaper
4. Click "Clear" to revert a monitor to the global background

Changes are saved immediately — the desktop updates on the fly.

## Reverting

To remove all per-monitor overrides:

```bash
gsettings reset org.gnome.shell per-monitor-background
```

## Known issues / limitations

- The helper tool reads monitor connectors via Mutter's D‑Bus API
  (`org.gnome.Mutter.DisplayConfig.GetCurrentState`); it requires a running
  GNOME session.
- Wallpapers are stored as file:// URIs with a `a{ss}` GSettings variant
  (string → string dictionary keyed by connector name).
- The implementation is a proof‑of‑concept patch; it has not been upstreamed.
