# GNOME Shell Per-Monitor Background Patch

This project provides a Proof of Concept (PoC) and a lightweight automated installer to unlock a missing native feature in GNOME Shell: **setting independent, individual wallpapers for each connected monitor**.

Instead of relying on third-party GNOME Extensions that use unstable monkey-patching and frequently break during major release updates, this project patches the GNOME Shell core JavaScript layer directly (`ui/background.js` and `ui/backgroundMenu.js`). It splits the global wallpaper logic into per-connector settings using native GSettings.

---

## Features

* **Native Core Patches:** Modifies `background.js` to decouple display rendering from a single unified global URI.
* **Context Menu Integration:** Patches `backgroundMenu.js` to add a dedicated "Per-Monitor Backgrounds…" option to the right-click desktop menu.
* **GSettings Driven:** Custom schema with `a{ss}` dictionaries mapping monitor connectors (`DP-1`, `HDMI-1`, …) to image URIs. Supports Light and Dark modes.
* **Runtime Optimizations:** Cached per-monitor maps, selective monitor refresh, wallpaper file validation, robust animation cache keys, safer monitor connector resolution, and proper GSettings signal cleanup.
* **CLI/GUI Helper Tool:** Installs `gnome-per-monitor-background` for managing wallpapers (previews, light/dark, per-monitor or all-monitors, remove override).
* **Safe Installer:** Preflight checks, `--dry-run`, GResource size verification before patching, versioned patch profiles, backup/restore.

---

## Repository Structure

```text
├── patch_installer.py                       # Automated installer (install|uninstall|check)
├── org.gnome.shell.per-monitor.gschema.xml  # GSettings schema
├── requirements.txt                         # Optional Python deps for the helper
├── patches/
│   ├── gnome-50/                            # Versioned JS patches for GNOME 50 (preferred)
│   │   ├── background.js
│   │   └── backgroundMenu.js
│   └── gnome-51/                            # Versioned JS patches for GNOME 51
│       ├── background.js
│       └── backgroundMenu.js
├── local_patches/                           # gnome-50 legacy fallback copies
│   ├── background.js
│   └── backgroundMenu.js
└── tools/
    └── gnome-per-monitor-background         # GTK4/libadwaita helper
```

## What the Script Does

When you execute the installer, it:

- Verifies root privileges and required tools (`gresource`, `glib-compile-resources`, `glib-compile-schemas`, `gnome-shell`, `objdump`).
- Detects the GNOME Shell version and selects the matching patch profile under `patches/` (GNOME 50 → `patches/gnome-50/`, GNOME 51 → `patches/gnome-51/`; `local_patches/` is used only as a gnome-50 fallback).
- Runs preflight checks (including compiling the schema in a temp dir and estimating the patched GResource size).
- Backs up `libshell-*.so` to `.bak` (once).
- Extracts all JS resources from the library's `.gresource.shell_js_resources` section.
- Injects the patched `background.js` / `backgroundMenu.js`.
- Recompiles the GResource and **aborts without writing** if the new bundle exceeds the original ELF section size.
- Patches the section in-place (byte replacement with zero padding; no `objcopy`).
- Installs the GSettings schema and the helper into `$HOME/.local/bin`.

## Prerequisites

### Build Tools (required by the installer)

- `gresource`
- `glib-compile-resources`
- `glib-compile-schemas`
- `objdump`

### Runtime Dependencies (helper tool)

**Arch Linux:**
```bash
sudo pacman -S python-gobject gtk4 libadwaita gdk-pixbuf2 glib2
```

**Fedora:**
```bash
sudo dnf install python3-gobject gtk4 libadwaita gdk-pixbuf2 glib2
```

**Debian / Ubuntu:**
```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1-0 gir1.2-gdkpixbuf-2.0 gir1.2-glib-2.0
```

Optional:
```bash
pip install -r requirements.txt
```

## How to Run

### Preflight / dry-run (no system changes)

```bash
./patch_installer.py check
# or
sudo ./patch_installer.py install --dry-run
```

Dry-run may be run without root for most checks; a full install still requires `sudo`.

### Install

```bash
sudo ./patch_installer.py install
```

Then **log out and back in** on Wayland to load the patched library.

### Uninstall / rollback

```bash
sudo ./patch_installer.py uninstall
```

Restores `libshell-*.so` from `.bak`, removes the schema and helper. Log out/in to finish.

## Manual configuration via gsettings

```bash
# Set
gsettings set org.gnome.shell.per-monitor per-monitor-background \
  "{'DP-1': 'file:///home/user/Wallpapers/left.jpg', 'HDMI-1': 'file:///home/user/Wallpapers/right.jpg'}"

# Read
gsettings get org.gnome.shell.per-monitor per-monitor-background

# Dark mode map
gsettings get org.gnome.shell.per-monitor per-monitor-background-dark
```

## Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| Wallpaper does not change | Shell still running old JS | Log out / log in (Wayland) |
| Installer aborts on version | Unsupported GNOME major series | Check `gnome-shell --version`; add a profile under `patches/` |
| Installer aborts on size | Patched GResource too large | Reduce patch size or free space in the section; backup is left intact |
| Tool does not open | Helper missing / not executable | Re-run installer; ensure `~/.local/bin` is in `PATH` |
| Notification about missing tool | Helper not installed | `sudo ./patch_installer.py install` |
| Wrong wallpaper after hotplug | Connector identity changed | Re-assign in the helper or via `gsettings` |
| Broken desktop after update | Distro replaced `libshell-*.so` | Re-run install, or `uninstall` then restore from `.bak` if needed |

### Recovery

1. Backup path: `/usr/lib/gnome-shell/libshell-*.so.bak`
2. Quick restore: `sudo ./patch_installer.py uninstall`
3. Manual restore: `sudo cp /usr/lib/gnome-shell/libshell-XX.so.bak /usr/lib/gnome-shell/libshell-XX.so`
4. After any GNOME Shell package update, re-run dry-run then install if the series is still supported.

## Manual test checklist

- [ ] No per-monitor mapping → global wallpaper on all monitors
- [ ] Mapping on one monitor → only that monitor changes
- [ ] Mapping on all monitors → each shows its image
- [ ] Dark mode uses `per-monitor-background-dark` with fallback to light map
- [ ] Missing file URI → fallback to global wallpaper (shell log warning)
- [ ] Changing one monitor does not flicker unrelated monitors
- [ ] Unplug / replug monitor does not crash Shell
- [ ] Desktop menu opens helper; missing helper shows a notification
- [ ] Helper can set, preview, and remove overrides
- [ ] Uninstall restores stock behaviour after logout/login

## Disclaimer

This project is an unofficial community-maintained Proof of Concept. Always save important work before applying system-level modifications. Binary patching of `libshell-*.so` may need to be repeated after distribution updates.
