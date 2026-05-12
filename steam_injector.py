#!/usr/bin/env python3

import os
import shutil
import time
from pathlib import Path


STEAM_CANDIDATES = [
    os.path.expanduser('~/.local/share/Steam'),
    os.path.expanduser('~/.steam/steam'),
    os.path.expanduser('~/.var/app/com.valvesoftware.Steam/data/Steam'),
]


def find_steam_library():
    for p in STEAM_CANDIDATES:
        if os.path.isdir(os.path.join(p, 'steamapps')):
            return p
    return None


def generate_acf(appid, install_dir, size_on_disk=0, timestamp=None,
                 state_flags=4):
    if timestamp is None:
        timestamp = int(time.time())
    return (
        '"AppState"\n'
        '{\n'
        f'\t"appid"\t\t"{appid}"\n'
        '\t"Universe"\t\t"1"\n'
        f'\t"name"\t\t"SteamTool Pro"\n'
        f'\t"StateFlags"\t\t"{state_flags}"\n'
        f'\t"installdir"\t\t"{install_dir}"\n'
        f'\t"LastUpdated"\t\t"{timestamp}"\n'
        '\t"UpdateResult"\t\t"0"\n'
        f'\t"SizeOnDisk"\t\t"{size_on_disk}"\n'
        '\t"buildid"\t\t"0"\n'
        '\t"LastOwner"\t\t"0"\n'
        '\t"BytesToDownload"\t\t"0"\n'
        '\t"BytesDownloaded"\t\t"0"\n'
        '\t"AutoUpdateBehavior"\t\t"0"\n'
        '\t"AllowOtherDownloadsWhileRunning"\t\t"0"\n'
        '\t"ScheduledAutoUpdate"\t\t"0"\n'
        '\t"InstalledDepots"\n'
        '\t{\n'
        '\t}\n'
        '\t"UserConfig"\n'
        '\t{\n'
        '\t}\n'
        '\t"MountedConfig"\n'
        '\t{\n'
        '\t}\n'
        '}\n'
    )


def inject_hub(hub_path):
    hub = Path(hub_path)
    folder_name = hub.name

    if not folder_name.startswith('ManifestHub-'):
        return False, f"Not a ManifestHub folder: {folder_name}"

    appid = folder_name[len('ManifestHub-'):]
    if not appid.isdigit():
        return False, f"Invalid AppID extracted: {appid}"

    steam_root = find_steam_library()
    if not steam_root:
        return False, "Steam library not found. Checked: " + ', '.join(STEAM_CANDIDATES)

    steamapps = os.path.join(steam_root, 'steamapps')
    common = os.path.join(steamapps, 'common')

    all_items = list(hub.iterdir())
    if not all_items:
        return False, f"No files found in {folder_name}"

    total_size = 0
    manifest_file = None
    for item in all_items:
        if item.suffix.lower() == '.manifest':
            manifest_file = item
        if item.is_file():
            total_size += item.stat().st_size

    install_dir = appid
    game_dir = os.path.join(common, install_dir)
    os.makedirs(game_dir, exist_ok=True)

    for item in all_items:
        dest = os.path.join(game_dir, item.name)
        try:
            shutil.move(str(item), dest)
        except OSError:
            try:
                os.symlink(str(item), dest)
            except OSError:
                pass

    acf_content = generate_acf(appid, install_dir, total_size)
    acf_path = os.path.join(steamapps, f'appmanifest_{appid}.acf')

    try:
        with open(acf_path, 'w') as f:
            f.write(acf_content)
    except OSError as e:
        return False, f"Failed to write {acf_path}: {e}"

    msg = f"Injected App {appid} — {acf_path}"
    if manifest_file:
        msg += f" (manifest: {manifest_file.name})"
    return True, msg


def main():
    import sys
    if len(sys.argv) < 2:
        cwd = Path.cwd()
        hubs = sorted(cwd.glob('ManifestHub-*'))
        if not hubs:
            print("Usage: python3 steam_injector.py /path/to/ManifestHub-<AppID>")
            sys.exit(1)
        hub_path = str(hubs[-1])
        print(f"Auto-selected: {hub_path}")
    else:
        hub_path = sys.argv[1]

    success, msg = inject_hub(hub_path)
    print(msg)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
