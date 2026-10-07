#!/usr/bin/env python3
"""Installer for the GNOME Shell per-monitor wallpaper patch.

Supports install / uninstall / dry-run. Patches the embedded JS GResource
inside libshell-*.so and deploys the GSettings schema plus helper tool.
"""
from __future__ import annotations

import argparse
import os
import sys
import shutil
import subprocess
import re
import tempfile
from dataclasses import dataclass, field
from typing import Optional

# GNOME Shell major series supported by the shipped patches.
# Exact match is preferred; otherwise the nearest supported series is used.
SUPPORTED_SERIES = {
    "50": "gnome-50",
    "51": "gnome-51",
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = "/usr/lib/gnome-shell/"
SCHEMA_DIR = "/usr/share/glib-2.0/schemas/"
WORK_DIR = "/tmp/gnome_shell_patch_work"

HELPER_SOURCE = os.path.join(BASE_DIR, "tools", "gnome-per-monitor-background")
REAL_USER = os.environ.get("SUDO_USER")
if REAL_USER:
    USER_HOME = os.path.expanduser(f"~{REAL_USER}")
else:
    USER_HOME = os.environ.get("HOME", "/root")

HELPER_DEST_DIR = os.path.join(USER_HOME, ".local", "bin")
HELPER_DEST_PATH = os.path.join(HELPER_DEST_DIR, "gnome-per-monitor-background")

PATCH_FILES = ("background.js", "backgroundMenu.js")


@dataclass
class CheckResult:
    ok: bool
    name: str
    detail: str
    fatal: bool = True


@dataclass
class DryRunReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, ok: bool, name: str, detail: str, fatal: bool = True) -> None:
        self.results.append(CheckResult(ok, name, detail, fatal))

    @property
    def can_install(self) -> bool:
        return all(r.ok for r in self.results if r.fatal)

    def print(self) -> None:
        print("\n=== Dry-run report ===")
        for r in self.results:
            mark = "OK" if r.ok else ("FAIL" if r.fatal else "WARN")
            print(f"  [{mark}] {r.name}: {r.detail}")
        print()
        if self.can_install:
            print("[+] Dry-run passed: installation looks possible.")
        else:
            print("[!] Dry-run failed: fix the issues above before installing.")


def check_root(require: bool = True) -> bool:
    is_root = os.geteuid() == 0
    if require and not is_root:
        print("[!] Error: This script must be run with root privileges (sudo).")
        sys.exit(1)
    return is_root


def check_dependencies() -> list[str]:
    tools = [
        "gresource",
        "glib-compile-resources",
        "glib-compile-schemas",
        "gnome-shell",
        "objdump",
    ]
    missing = [t for t in tools if shutil.which(t) is None]
    return missing


def find_shell_library() -> Optional[str]:
    """Return the libshell library that gnome-shell actually loads.

    Older runs leave backups named libshell-*.so.bak in the same
    directory: they are valid GResource bundles too, so an unanchored
    name match would pick them first (sorted order) and silently patch
    a library that is never loaded. The linked library is therefore
    preferred and backups are excluded from the fallback scan.
    """
    if not os.path.isdir(LIB_DIR):
        return None

    def has_gresource(path: str) -> bool:
        try:
            subprocess.run(
                ["gresource", "list", path],
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, OSError):
            return False
        return True

    # 1. The library gnome-shell links against: the one that runs.
    try:
        ldd = subprocess.run(
            ["ldd", "gnome-shell"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in ldd.stdout.splitlines():
            match = re.search(r"(\S*libshell-\d+\.so)\s", line)
            if not match:
                continue
            path = match.group(1)
            if os.path.isfile(path) and has_gresource(path):
                return path
    except (subprocess.CalledProcessError, OSError):
        pass

    # 2. Fallback: first real libshell library, backups excluded.
    for name in sorted(os.listdir(LIB_DIR)):
        if re.fullmatch(r"libshell-\d+\.so", name) or name == "libgnome-shell.so":
            path = os.path.join(LIB_DIR, name)
            if has_gresource(path):
                return path

    return None


def get_section_info(lib_path: str) -> tuple[Optional[int], Optional[int]]:
    """Return (offset, size) of the .gresource section in the ELF."""
    result = subprocess.run(
        ["objdump", "-h", lib_path],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stdout.splitlines():
        if ".gresource" not in line:
            continue
        parts = line.split()
        # Format: idx name size vma lma offset align
        if len(parts) >= 6:
            try:
                size = int(parts[2], 16)
                offset = int(parts[4], 16)
                return offset, size
            except (ValueError, IndexError):
                continue
    return None, None


def parse_gnome_version() -> str:
    result = subprocess.run(
        ["gnome-shell", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    version_output = result.stdout.strip()
    match = re.search(r"([\d.]+)", version_output)
    if not match:
        raise RuntimeError(f"Could not parse GNOME Shell version: '{version_output}'")
    return match.group(1)


def resolve_patch_profile(version: str) -> tuple[str, str]:
    """Map a GNOME version to (series, patch_dir).

    Looks for patches/<profile>/ first. local_patches/ is a gnome-50-only
    legacy fallback and is never used for other series, so a mismatched
    patch is never injected into a newer shell.
    """
    major = version.split(".")[0]
    profile = SUPPORTED_SERIES.get(major)
    if not profile:
        raise RuntimeError(
            f"GNOME Shell {version} is not supported. "
            f"Supported major series: {', '.join(sorted(SUPPORTED_SERIES))}."
        )

    versioned = os.path.join(BASE_DIR, "patches", profile)
    legacy = os.path.join(BASE_DIR, "local_patches")

    if os.path.isdir(versioned) and all(
        os.path.isfile(os.path.join(versioned, f)) for f in PATCH_FILES
    ):
        return profile, versioned

    if profile == "gnome-50" and os.path.isdir(legacy) and all(
        os.path.isfile(os.path.join(legacy, f)) for f in PATCH_FILES
    ):
        return profile, legacy

    raise RuntimeError(
        f"No patch files found for profile '{profile}'. "
        f"Expected {versioned} with {', '.join(PATCH_FILES)}."
    )


def build_patches_map(patch_dir: str) -> dict[str, str]:
    return {
        os.path.join(patch_dir, "background.js"): "ui/background.js",
        os.path.join(patch_dir, "backgroundMenu.js"): "ui/backgroundMenu.js",
    }


def compile_schema_dry_run(schema_src: str) -> None:
    """Compile schema in a temp dir to verify it is valid without installing."""
    with tempfile.TemporaryDirectory(prefix="per-monitor-schema-") as tmp:
        shutil.copy2(schema_src, os.path.join(tmp, os.path.basename(schema_src)))
        subprocess.run(
            ["glib-compile-schemas", "--strict", tmp],
            check=True,
            capture_output=True,
            text=True,
        )


def _manifest_file_entry(local_path: str, patches_map: dict[str, str]) -> str:
    """Compress patched JS files so the rebuilt bundle fits the ELF section."""
    if local_path in patches_map.values():
        return f'    <file compressed="true">{local_path}</file>\n'
    return f"    <file>{local_path}</file>\n"


def estimate_patched_size(lib_path: str, patches_map: dict[str, str]) -> tuple[int, int]:
    """Extract resources, apply patches, compile, return (new_size, section_size)."""
    section_offset, section_size = get_section_info(lib_path)
    if section_offset is None or section_size is None:
        raise RuntimeError("Could not find GResource section in the library.")

    with tempfile.TemporaryDirectory(prefix="per-monitor-size-") as tmp:
        result = subprocess.run(
            ["gresource", "list", lib_path],
            capture_output=True,
            text=True,
            check=True,
        )
        resources = result.stdout.splitlines()
        manifest = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<gresources>\n"
            '  <gresource prefix="/org/gnome/shell">\n'
        )
        for res in resources:
            local = res.replace("/org/gnome/shell/", "")
            dst = os.path.join(tmp, local)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            data = subprocess.run(
                ["gresource", "extract", lib_path, res],
                capture_output=True,
                check=True,
            ).stdout
            with open(dst, "wb") as f:
                f.write(data)
            manifest += _manifest_file_entry(local, patches_map)
        manifest += "  </gresource>\n</gresources>\n"

        for src, internal in patches_map.items():
            shutil.copy2(src, os.path.join(tmp, internal))

        xml_path = os.path.join(tmp, "shell-resources.gresource.xml")
        with open(xml_path, "w", encoding="utf-8") as f:
            f.write(manifest)

        compiled = os.path.join(tmp, "shell-resources.gresource")
        subprocess.run(
            [
                "glib-compile-resources",
                "--target",
                compiled,
                "--sourcedir",
                tmp,
                xml_path,
            ],
            check=True,
            capture_output=True,
        )
        new_size = os.path.getsize(compiled)
        return new_size, section_size


def run_preflight(dry_run: bool = False) -> DryRunReport:
    report = DryRunReport()

    missing = check_dependencies()
    if missing:
        report.add(False, "Dependencies", f"Missing: {', '.join(missing)}")
    else:
        report.add(True, "Dependencies", "All required tools present")

    try:
        version = parse_gnome_version()
        report.add(True, "GNOME Shell version", version)
        try:
            profile, patch_dir = resolve_patch_profile(version)
            report.add(
                True,
                "Patch profile",
                f"{profile} → {patch_dir}",
            )
            patches_map = build_patches_map(patch_dir)
            for src, internal in patches_map.items():
                if os.path.isfile(src):
                    report.add(True, f"Patch file {internal}", src)
                else:
                    report.add(False, f"Patch file {internal}", f"Missing: {src}")
        except RuntimeError as e:
            report.add(False, "Patch profile", str(e))
            patches_map = {}
    except Exception as e:
        report.add(False, "GNOME Shell version", str(e))
        patches_map = {}

    lib_path = find_shell_library()
    if not lib_path:
        report.add(False, "Shell library", f"No libshell-*.so with GResource under {LIB_DIR}")
    else:
        report.add(True, "Shell library", lib_path)
        backup = lib_path + ".bak"
        if os.path.exists(backup):
            report.add(True, "Existing backup", backup, fatal=False)
        else:
            report.add(True, "Existing backup", "None (will be created on install)", fatal=False)

        try:
            offset, size = get_section_info(lib_path)
            if offset is None:
                report.add(False, "GResource section", "Not found in ELF")
            else:
                report.add(
                    True,
                    "GResource section",
                    f"offset=0x{offset:x}, size={size} bytes",
                )
                if patches_map and all(os.path.isfile(s) for s in patches_map):
                    try:
                        new_size, section_size = estimate_patched_size(lib_path, patches_map)
                        headroom = section_size - new_size
                        if new_size > section_size:
                            report.add(
                                False,
                                "Resource size",
                                f"Patched bundle {new_size} bytes exceeds "
                                f"section {section_size} bytes "
                                f"(overflow {new_size - section_size})",
                            )
                        else:
                            report.add(
                                True,
                                "Resource size",
                                f"Patched bundle {new_size} bytes fits in "
                                f"{section_size} bytes "
                                f"({headroom} bytes free)",
                            )
                    except Exception as e:
                        report.add(False, "Resource size", f"Could not estimate: {e}")
        except Exception as e:
            report.add(False, "GResource section", str(e))

    schema_src = os.path.join(BASE_DIR, "org.gnome.shell.per-monitor.gschema.xml")
    if not os.path.isfile(schema_src):
        report.add(False, "GSettings schema", f"Missing: {schema_src}")
    else:
        try:
            compile_schema_dry_run(schema_src)
            report.add(True, "GSettings schema", "Compiles successfully")
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or e.stdout or str(e)).strip()
            report.add(False, "GSettings schema", f"Compile failed: {detail}")

    if os.path.isfile(HELPER_SOURCE):
        report.add(True, "Helper tool source", HELPER_SOURCE)
        if os.access(HELPER_SOURCE, os.X_OK):
            report.add(True, "Helper executable bit", "Set", fatal=False)
        else:
            report.add(
                True,
                "Helper executable bit",
                "Not set (installer will chmod +x)",
                fatal=False,
            )
    else:
        report.add(False, "Helper tool source", f"Missing: {HELPER_SOURCE}", fatal=False)

    schema_writable = os.access(SCHEMA_DIR, os.W_OK) if os.path.isdir(SCHEMA_DIR) else False
    lib_writable = os.access(lib_path, os.W_OK) if lib_path and os.path.isfile(lib_path) else False
    if dry_run:
        if os.geteuid() != 0:
            report.add(
                True,
                "Permissions",
                "Dry-run as non-root (install still requires sudo)",
                fatal=False,
            )
        elif lib_writable and schema_writable:
            report.add(True, "Permissions", "Library and schema dir writable")
        else:
            report.add(
                True,
                "Permissions",
                f"Root dry-run note: lib writable={lib_writable}, "
                f"schema dir writable={schema_writable}",
                fatal=False,
            )
    else:
        if lib_writable and schema_writable:
            report.add(True, "Permissions", "Library and schema dir writable")
        else:
            report.add(
                False,
                "Permissions",
                f"lib writable={lib_writable}, schema dir writable={schema_writable}",
            )

    return report


def install_patch(dry_run: bool = False) -> None:
    print("[*] Starting patch installation..." + (" (dry-run)" if dry_run else ""))

    report = run_preflight(dry_run=dry_run)
    if dry_run:
        report.print()
        sys.exit(0 if report.can_install else 1)

    if not report.can_install:
        report.print()
        print("[!] Aborting install due to failed preflight checks.")
        sys.exit(1)

    version = parse_gnome_version()
    print(f"[*] Detected GNOME Shell version: {version}")
    profile, patch_dir = resolve_patch_profile(version)
    print(f"[*] Using patch profile '{profile}' from {patch_dir}")
    patches_map = build_patches_map(patch_dir)

    lib_path = find_shell_library()
    assert lib_path is not None
    print(f"[*] Found shell library: {lib_path}")

    section_offset, section_size = get_section_info(lib_path)
    assert section_offset is not None and section_size is not None
    print(
        f"[*] GResource section at offset 0x{section_offset:x}, "
        f"size 0x{section_size:x} ({section_size} bytes)"
    )

    lib_backup = lib_path + ".bak"

    # 1. Backup
    if not os.path.exists(lib_backup):
        print(f"[*] Creating backup at {lib_backup}")
        shutil.copy2(lib_path, lib_backup)
    else:
        print("[*] Backup already exists. Proceeding...")

    # 2. Workspace
    if os.path.exists(WORK_DIR):
        shutil.rmtree(WORK_DIR)
    os.makedirs(WORK_DIR)

    # 3. Extract ALL resources from the library
    print("[*] Extracting all resources from the library...")
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<gresources>\n"
        '  <gresource prefix="/org/gnome/shell">\n'
    )
    try:
        result = subprocess.run(
            ["gresource", "list", lib_path],
            capture_output=True,
            text=True,
            check=True,
        )
        resources = result.stdout.splitlines()
    except subprocess.CalledProcessError as e:
        print(f"[!] Error listing resources: {e}")
        sys.exit(1)

    for res in resources:
        local = res.replace("/org/gnome/shell/", "")
        dst = os.path.join(WORK_DIR, local)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        data = subprocess.run(
            ["gresource", "extract", lib_path, res],
            capture_output=True,
            check=True,
        ).stdout
        with open(dst, "wb") as f:
            f.write(data)
        manifest += _manifest_file_entry(local, patches_map)
    manifest += "  </gresource>\n</gresources>\n"

    xml_path = os.path.join(WORK_DIR, "shell-resources.gresource.xml")
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(manifest)
    print(f"[*] Extracted {len(resources)} resources.")

    # 4. Overwrite patched JS files
    for src, internal in patches_map.items():
        dst = os.path.join(WORK_DIR, internal)
        if os.path.exists(src):
            print(f"[*] Replacing {internal} with patched version...")
            shutil.copy2(src, dst)
        else:
            print(f"[!] Error: Patched file not found at {src}")
            sys.exit(1)

    # 5. Compile new gresource
    compiled = os.path.join(WORK_DIR, "shell-resources.gresource")
    print("[*] Compiling new resource bundle...")
    try:
        subprocess.run(
            [
                "glib-compile-resources",
                "--target",
                compiled,
                "--sourcedir",
                WORK_DIR,
                xml_path,
            ],
            check=True,
        )
        if not os.path.exists(compiled):
            print("[!] Compilation produced no output.")
            sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"[!] Error during compilation: {e}")
        sys.exit(1)

    # 6. Replace section data in the .so (direct byte manipulation, no objcopy)
    with open(compiled, "rb") as f:
        new_data = f.read()
    if len(new_data) > section_size:
        print(f"[!] New resource data ({len(new_data)} bytes) is larger than the")
        print(f"    original section ({section_size} bytes). Cannot patch in-place.")
        print("[!] Aborting without modifying libshell. Backup left untouched.")
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        sys.exit(1)

    new_data_padded = new_data + b"\x00" * (section_size - len(new_data))
    print(
        f"[*] Writing {len(new_data)} bytes "
        f"(padded to {section_size}) at offset 0x{section_offset:x}..."
    )
    with open(lib_path, "r+b") as f:
        f.seek(section_offset)
        f.write(new_data_padded)

    # Verify the patched library is still readable
    print("[*] Verifying patched library...")
    try:
        subprocess.run(
            ["gresource", "list", lib_path],
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        print("[!] Verification FAILED: library may be corrupted!")
        print("    Restoring backup...")
        shutil.copy2(lib_backup, lib_path)
        sys.exit(1)

    print(f"[*] Successfully patched {lib_path}")

    # 7. Install GSettings Schema
    schema_src = os.path.join(BASE_DIR, "org.gnome.shell.per-monitor.gschema.xml")
    schema_dst = os.path.join(SCHEMA_DIR, "org.gnome.shell.per-monitor.gschema.xml")
    if os.path.exists(schema_src):
        print("[*] Installing GSettings schema...")
        shutil.copy2(schema_src, schema_dst)
        subprocess.run(["glib-compile-schemas", SCHEMA_DIR], check=True)

    # 8. Install Helper Tool
    if os.path.exists(HELPER_SOURCE):
        print(f"[*] Installing helper tool to {HELPER_DEST_PATH}...")
        os.makedirs(HELPER_DEST_DIR, exist_ok=True)
        shutil.copy2(HELPER_SOURCE, HELPER_DEST_PATH)
        os.chmod(HELPER_DEST_PATH, 0o755)
        if REAL_USER:
            import pwd

            info = pwd.getpwnam(REAL_USER)
            os.chown(HELPER_DEST_PATH, info.pw_uid, info.pw_gid)

    shutil.rmtree(WORK_DIR)
    print("\n[+] Installation completed successfully!")
    print("[+] Restart GNOME Shell (log out/in on Wayland) to apply.")
    print(f"[+] Backup kept at: {lib_backup}")


def uninstall_patch() -> None:
    print("[*] Starting uninstallation...")

    lib_path = find_shell_library()
    if lib_path:
        lib_backup = lib_path + ".bak"
        if os.path.exists(lib_backup):
            print(f"[*] Restoring library from {lib_backup}...")
            shutil.copy2(lib_backup, lib_path)
            os.remove(lib_backup)
        else:
            print("[!] Warning: No library backup found.")
    else:
        print("[!] Warning: GNOME Shell library not found.")

    schema_path = os.path.join(SCHEMA_DIR, "org.gnome.shell.per-monitor.gschema.xml")
    if os.path.exists(schema_path):
        print("[*] Removing GSettings schema...")
        os.remove(schema_path)
        subprocess.run(["glib-compile-schemas", SCHEMA_DIR], check=True)

    if os.path.exists(HELPER_DEST_PATH):
        print("[*] Removing helper tool...")
        os.remove(HELPER_DEST_PATH)

    print("\n[+] Uninstallation completed!")
    print("[+] Log out/in to finish rollback.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install or remove the GNOME Shell per-monitor wallpaper patch.",
    )
    parser.add_argument(
        "action",
        choices=["install", "uninstall", "check"],
        help="install / uninstall the patch, or run preflight checks only",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="with install: verify everything without modifying the system",
    )
    args = parser.parse_args()

    if args.action == "check" or (args.action == "install" and args.dry_run):
        # Dry-run / check may be run without root to inspect the system.
        report = run_preflight(dry_run=True)
        report.print()
        sys.exit(0 if report.can_install else 1)

    check_root(require=True)
    missing = check_dependencies()
    if missing:
        print(f"[!] Error: Required system tools missing: {', '.join(missing)}")
        sys.exit(1)

    if args.action == "install":
        install_patch(dry_run=False)
    else:
        uninstall_patch()


if __name__ == "__main__":
    main()
