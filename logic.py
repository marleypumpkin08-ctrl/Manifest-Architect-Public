"""Shared logic helpers for SteamToolPro.

This file was added to support maintainable feature logic separate from UI.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from datetime import datetime, timezone


SUBNAUTICA2_APPID = "1962700"
SUBNAUTICA2_EXE_NAME = "Subnautica2.exe"
SUBNAUTICA2_STATEFLAGS = "1026"


def find_steam_library() -> str | None:
    """Best-effort Steam library root.

    The main UI already contains a richer implementation in steam_injector.py.
    This fallback keeps logic.py usable for the Purge/Permissions/Misc features
    if steam_injector isn't imported.
    """
    # Prefer existing implementation if available.
    try:
        from steam_injector import find_steam_library as _impl  # type: ignore

        return _impl()
    except Exception:
        return None


def apply_subnautica_2_fix() -> None:
    """Ghost-file bypass for Subnautica 2.

    Required behavior (per project audit):
      - AppID 1962700
      - Create Subnautica2.exe
      - Set StateFlags to 1026 in appmanifest_1962700.acf
    """

    appid = SUBNAUTICA2_APPID
    exe_name = SUBNAUTICA2_EXE_NAME

    steam_root = find_steam_library()
    if not steam_root:
        raise RuntimeError("Steam library not found")

    target_dir = os.path.join(steam_root, "steamapps", "common", "Subnautica 2")
    os.makedirs(target_dir, exist_ok=True)

    exe_path = os.path.join(target_dir, exe_name)

    # Create 0-byte exe
    Path(exe_path).touch(exist_ok=True)

    # Ensure executable
    try:
        os.chmod(exe_path, 0o755)
    except Exception:
        pass

    steamapps = os.path.join(steam_root, "steamapps")
    os.makedirs(steamapps, exist_ok=True)
    acf_path = os.path.join(steamapps, f"appmanifest_{appid}.acf")

    now_ts = int(datetime.now().timestamp())
    acf_text = (
        '"AppState"\n'
        "{\n"
        f"\t\"appid\"\t\t\"{appid}\"\n"
        '\t"Universe"\t\t"1"\n'
        f'\t"StateFlags"\t\t"{SUBNAUTICA2_STATEFLAGS}"\n'
        '\t"installdir"\t\t"Subnautica 2"\n'
        f'\t"LastUpdated"\t\t"{now_ts}"\n'
        "}\n"
    )

    with open(acf_path, "w", encoding="utf-8") as f:
        f.write(acf_text)


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def fix_permissions_for_game_binaries(steam_install_dir: str, mode: int = 0o755) -> dict[str, int]:
    """chmod +x for game .exe/.x86_64 in the provided install dir.

    Returns a small stats dict: {"changed": N}.
    """
    changed = 0
    p = Path(steam_install_dir)
    if not p.exists():
        return {"changed": changed}

    for ext in (".exe", ".x86_64", ".bin"):
        for f in p.rglob(f"*{ext}"):
            try:
                os.chmod(str(f), mode)
                changed += 1
            except Exception:
                pass

    return {"changed": changed}


def purge_shader_cache(shadercache_dir: str) -> dict[str, int]:
    """Delete cached shader entries.

    This is conservative: it deletes files under the shadercache directory
    but keeps the directory itself.
    """
    p = Path(shadercache_dir)
    if not p.exists() or not p.is_dir():
        return {"deleted": 0}

    deleted = 0
    for child in p.rglob("*"):
        try:
            if child.is_file() or child.is_symlink():
                child.unlink(missing_ok=True)
                deleted += 1
            elif child.is_dir():
                # Skip directories; we don't want to remove nested trees blindly.
                pass
        except Exception:
            pass

    # Also try deleting known shader blobs if they exist
    for fname in ("shadercache", "shaderCache", "ShaderCache"):
        sp = p / fname
        if sp.exists() and sp.is_dir():
            try:
                for f in sp.rglob("*"):
                    if f.is_file() or f.is_symlink():
                        f.unlink(missing_ok=True)
                        deleted += 1
            except Exception:
                pass

    return {"deleted": deleted}


def get_proton_tools_root() -> str | None:
    """Return Steam compatibilitytools.d root (common locations)."""
    candidates = [
        os.path.expanduser("~/.local/share/Steam/compatibilitytools.d"),
        os.path.expanduser("~/.var/app/com.valvesoftware.Steam/data/compatibilitytools.d"),
        os.path.expanduser("~/.steam/steam/compatibilitytools.d"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def list_proton_versions() -> list[str]:
    """List Proton/GE-Proton-like folders under compatibilitytools.d."""
    root = get_proton_tools_root()
    if not root:
        return []

    try:
        items = [
            name for name in os.listdir(root)
            if os.path.isdir(os.path.join(root, name))
        ]
    except Exception:
        return []

    # Keep it forgiving.
    tools = [name for name in items if ("Proton" in name or "GE" in name)]
    tools.sort(key=lambda s: s.lower())
    return tools


def set_proton_log_env(enabled: bool) -> None:
    """If enabled, append PROTON_LOG=1 to environment for subprocesses.

    UI can call this before launching Steam.
    """
    if enabled:
        os.environ["PROTON_LOG"] = "1"
    else:
        os.environ.pop("PROTON_LOG", None)

