#!/usr/bin/env python3

import json
import os
import sys
import threading
import urllib.request
import subprocess
import shutil
from pathlib import Path

import requests

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk, GLib


CURRENT_VERSION = '1.2.0'
OWNER = 'marleypumpkin08-ctrl'
REPO = 'Manifest-Architect-Public'

_SKIP_CHECK = OWNER == 'YOUR_GITHUB_USERNAME'

VERSION_URL = (
    f'https://raw.githubusercontent.com/{OWNER}/{REPO}/main/'
    f'metadata/version.json'
)

UPDATE_CSS = '''
.update-window {
    background-color: @window_bg_color;
}

.update-banner {
    background: linear-gradient(135deg, #3584e4, #62a0ea);
    color: white;
    padding: 24px;
}

.update-banner label {
    color: white;
    font-weight: bold;
}

.update-progress levelbar trough {
    min-height: 8px;
    border-radius: 6px;
}

.update-progress levelbar trough block {
    border-radius: 6px;
    background: linear-gradient(90deg, #3584e4, #62a0ea);
}

.update-progress levelbar trough block.filled {
    background: linear-gradient(90deg, #33d17a, #57e389);
    box-shadow: 0 0 8px 1px alpha(#33d17a, 0.5);
}

.glow-button {
    background: linear-gradient(135deg, #3584e4, #62a0ea);
    color: white;
    border: none;
    border-radius: 8px;
    padding: 12px 24px;
    font-weight: bold;
    box-shadow: 0 0 12px 2px alpha(#3584e4, 0.4);
}

.glow-button:hover {
    box-shadow: 0 0 18px 4px alpha(#3584e4, 0.6);
}
'''


def load_css():
    prov = Gtk.CssProvider()
    prov.load_from_string(UPDATE_CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        prov,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


# ---------- network helpers ----------

def fetch_latest_version():
    try:
        req = urllib.request.Request(VERSION_URL, headers={
            'User-Agent': 'ManifestArchitect/1.0',
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def to_version_tuple(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in v.split('.'))
    except Exception:
        return (0,)


def check_for_update():
    data = fetch_latest_version()
    if data is None:
        return None
    latest_raw = data.get('version', '')
    latest_norm = str(latest_raw).strip()
    current_norm = str(CURRENT_VERSION).strip()

    # Only notify when there's a strictly newer version.
    # Use a proper comparison (latest > current), not 'not equal'.
    if to_version_tuple(latest_norm) <= to_version_tuple(current_norm):
        return None

    return {
        'current': current_norm,
        'latest': latest_norm,
        'url': data.get('url', ''),
        'changelog': data.get('changelog', ''),
    }



def download_progress(block_num, block_size, total_size):
    if total_size > 0:
        return min(1.0, (block_num * block_size) / total_size)
    return 0.0


# ---------- update window ----------

class UpdateWindow(Adw.Window):
    def __init__(self, update_info):

        super().__init__()
        self._update_info = update_info
        self._install_dir = str(Path(__file__).parent)
        self._temp_dir = None
        self._launch_script = None

        self.set_title('Update Available')
        self.set_default_size(520, 380)
        self.set_modal(True)
        self.add_css_class('update-window')

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(box)

        # banner
        banner = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8
        )
        banner.add_css_class('update-banner')
        banner.set_margin_bottom(24)

        sync_icon = Gtk.Image.new_from_icon_name('emblem-synchronizing-symbolic')
        sync_icon.set_pixel_size(48)
        sync_icon.set_opacity(0.9)

        banner_title = Gtk.Label(label='Update Available')
        banner_title.add_css_class('title-2')

        banner.append(sync_icon)
        banner.append(banner_title)
        box.append(banner)

        # body
        body = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=16
        )
        body.set_margin_start(24)
        body.set_margin_end(24)
        body.set_margin_bottom(24)

        msg = Gtk.Label(
            label=(
                f'A new version of Manifest Studio is available.\n'
                f'Your version: {update_info["current"]}  →  '
                f'New version: {update_info["latest"]}'
            )
        )
        msg.set_wrap(True)
        msg.set_justify(Gtk.Justification.CENTER)

        changelog_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        changelog_box.set_visible(bool(update_info.get('changelog')))

        cl_label = Gtk.Label(label="What's new:")
        cl_label.set_halign(Gtk.Align.START)
        cl_label.add_css_class('caption')

        cl_text = Gtk.Label(label=update_info.get('changelog', ''))
        cl_text.set_wrap(True)
        cl_text.set_xalign(0)

        changelog_box.append(cl_label)
        changelog_box.append(cl_text)

        # progress
        self.progress_bar = Gtk.LevelBar()
        self.progress_bar.set_min_value(0.0)
        self.progress_bar.set_max_value(1.0)
        self.progress_bar.set_value(0.0)
        self.progress_bar.add_css_class('update-progress')
        self.progress_bar.set_size_request(-1, 8)

        self.status_label = Gtk.Label(label='Checking for updates…')
        self.status_label.set_halign(Gtk.Align.CENTER)
        self.status_label.add_css_class('caption')

        # spinner
        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(24, 24)
        self.spinner.start()

        spinner_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        spinner_box.set_halign(Gtk.Align.CENTER)
        spinner_box.append(self.spinner)
        spinner_box.append(self.status_label)

        # download & restart buttons
        self.download_btn = Gtk.Button(label='Install and Relaunch')
        self.download_btn.add_css_class('glow-button')
        self.download_btn.set_halign(Gtk.Align.CENTER)
        self.download_btn.connect('clicked', self._on_download)

        self.restart_btn = Gtk.Button(label='Update Ready — Press to Restart')
        self.restart_btn.add_css_class('glow-button')
        self.restart_btn.set_halign(Gtk.Align.CENTER)
        self.restart_btn.set_visible(False)
        self.restart_btn.connect('clicked', self._on_restart)

        body.append(msg)
        body.append(changelog_box)
        body.append(spinner_box)
        body.append(self.progress_bar)
        body.append(self.download_btn)
        body.append(self.restart_btn)

        box.append(body)

    def _on_download(self, *_):
        self.download_btn.set_sensitive(False)
        self.download_btn.set_label('Downloading…')
        self.status_label.set_text('Downloading update…')
        threading.Thread(target=self._download_thread, daemon=True).start()

    def _download_thread(self):
        try:
            temp_dir = Path('/tmp/manifest_update')
            if temp_dir.exists():
                shutil.rmtree(str(temp_dir))
            temp_dir.mkdir(parents=True)

            base = f'https://raw.githubusercontent.com/{OWNER}/{REPO}/main'
            files = [
                'manifest_studio.py',
                'steam_injector.py',
                'update_engine.py',
                'game_database.py',
            ]

            for i, fn in enumerate(files):
                url = f'{base}/{fn}'
                r = requests.get(url, timeout=30)
                r.raise_for_status()
                (temp_dir / fn).write_bytes(r.content)
                frac = (i + 1) / len(files)
                GLib.idle_add(self._update_progress, frac, f'Downloading {fn}…')

            # copy updater.sh
            updater_src = Path(self._install_dir) / 'updater.sh'
            if updater_src.exists():
                shutil.copy2(str(updater_src), str(temp_dir / 'updater.sh'))
            else:
                (temp_dir / 'updater.sh').write_text(
                    '#!/usr/bin/env bash\n'
                    'set -euo pipefail\n'
                    f'cp "$2"/*.py "$1/" 2>/dev/null || true\n'
                    f'chmod +x "$1"/*.py 2>/dev/null || true\n'
                    f'rm -rf "$2"\n'
                    f'exec "$3"\n'
                )
            (temp_dir / 'updater.sh').chmod(0o755)

            # create launch.sh
            launch = temp_dir / 'launch.sh'
            launch.write_text(
                '#!/usr/bin/env bash\n'
                f'cd "{self._install_dir}" || exit 1\n'
                'LOG="log/launch.log"\n'
                f'python3 manifest_studio.py >> "$LOG" 2>&1\n'
            )
            launch.chmod(0o755)

            self._temp_dir = str(temp_dir)
            self._launch_script = str(launch)

            GLib.idle_add(self._download_complete)
        except Exception as e:
            GLib.idle_add(self._download_failed, str(e))

    def _update_progress(self, frac, message):
        self.progress_bar.set_value(frac)
        self.status_label.set_text(message)
        return False

    def _download_complete(self):
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.progress_bar.set_value(1.0)
        self.status_label.set_text('Download complete!')
        self.download_btn.set_visible(False)
        self.restart_btn.set_visible(True)

    def _download_failed(self, error):
        self.status_label.set_text(f'Download failed: {error}')
        self.download_btn.set_sensitive(True)
        self.download_btn.set_label('Retry Download')

    def _on_restart(self, *_):
        self.spinner.stop()
        self.spinner.set_visible(False)
        run_updater(self._install_dir, self._temp_dir, self._launch_script)


# ---------- update / restart logic ----------

def show_update(update_info, parent=None):
    """Create and present the update window."""
    load_css()
    win = UpdateWindow(update_info)
    if parent and hasattr(parent, 'get_root'):
        win.set_transient_for(parent.get_root())
    win.present()
    return win


def check_and_notify(parent=None):
    if _SKIP_CHECK:
        return None
    threading.Thread(
        target=_bg_check_and_notify, args=(parent,), daemon=True
    ).start()
    return None


def _bg_check_and_notify(parent):
    info = check_for_update()
    if info:
        # No popup when local version matches remote latest
        if info.get('latest', '') == CURRENT_VERSION:
            return
        GLib.idle_add(show_update, info, parent)


# -------------------------------------------------------------------
#  Lightweight version request helper
# -------------------------------------------------------------------

def fetch_version_requests():

    if _SKIP_CHECK:
        return None
    try:
        r = requests.get(VERSION_URL, timeout=10)
        r.raise_for_status()
        data = r.json()

        latest_norm = str(data.get('version', '')).strip()
        current_norm = str(CURRENT_VERSION).strip()

        # Only return update payload when latest > current.
        if to_version_tuple(latest_norm) <= to_version_tuple(current_norm):
            return None

        # Strictly newer only: latest > current.
        data['version'] = latest_norm
        return data

    except Exception:
        return None



def download_files(temp_dir):
    base = (
        f'https://raw.githubusercontent.com/{OWNER}/{REPO}/main'
    )
    files = [
        'manifest_studio.py',
        'steam_injector.py',
        'update_engine.py',
        'game_database.py',
    ]
    for fn in files:
        url = f'{base}/{fn}'
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        (temp_dir / fn).write_bytes(r.content)


def run_updater(install_dir, temp_dir, launch_script):
    updater_src = Path(__file__).parent / 'updater.sh'
    updater_dst = temp_dir / 'updater.sh'
    if updater_src.exists():
        shutil.copy2(str(updater_src), str(updater_dst))
    else:
        updater_dst.write_text(
            '#!/usr/bin/env bash\n'
            'set -euo pipefail\n'
            f'cp "$2"/*.py "$1/" 2>/dev/null || true\n'
            f'chmod +x "$1"/*.py 2>/dev/null || true\n'
            f'rm -rf "$2"\n'
            f'exec "$3"\n'
        )
    updater_dst.chmod(0o755)

    subprocess.Popen(
        ['sh', str(updater_dst), install_dir, str(temp_dir), launch_script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sys.exit(0)


if __name__ == '__main__':
    # Standalone updater entrypoint.
    # Only performs the check once; if versions match, no window is shown.
    app = Adw.Application(application_id='com.manifeststudio.updater')
    app.connect(
        'activate',
        lambda a: check_and_notify(),
    )
    app.run()

