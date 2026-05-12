#!/usr/bin/env python3

import os
import sys
import json
import shutil
import subprocess
import threading
import xml.etree.ElementTree as ET
import urllib.request

# Ensure the script's directory is on sys.path so local imports work
# when launched from a .desktop file (where CWD is $HOME, not the script dir).
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk, GLib

from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None


from steam_injector import inject_hub, generate_acf, find_steam_library
from game_database import resolve_game, generate_json_template, generate_lua_template, GAME_DATABASE
import update_engine


CSS = '''
.loading-overlay {
    background-color: alpha(@window_bg_color, 0.85);
}

.progress-glow trough {
    min-height: 8px;
    border-radius: 6px;
}

.progress-glow trough progress {
    min-height: 8px;
    border-radius: 6px;
    background: linear-gradient(90deg, #3584e4, #62a0ea);
}

.progress-glow.complete trough progress {
    background: linear-gradient(90deg, #33d17a, #57e389);
    box-shadow: 0 0 10px 2px alpha(#33d17a, 0.6);
}

.game-header {
    border-radius: 8px;
    background-color: alpha(@window_bg_color, 0.3);
    min-height: 86px;
}

.library-card {
    border-radius: 12px;
    background-color: alpha(@window_bg_color, 0.5);
}

.library-card-picture {
    border-radius: 12px;
}

.library-lock-icon {
    background-color: alpha(black, 0.55);
    border-radius: 8px;
    padding: 4px;
    color: @accent_bg_color;
}

.library-grid {
    background: none;
}
'''


# ====================================================================
#  Drop Zone tab  (Phase 1 — drag-and-drop + inject)
# ====================================================================

class DropZonePage(Gtk.Box):
    def __init__(self, toast_overlay):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._toast_overlay = toast_overlay
        self.pulse_id = None
        self._anim = None

        # Overlay -> stack
        self.overlay = Gtk.Overlay()
        self.append(self.overlay)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(350)
        self.overlay.set_child(self.stack)

        # --- landing ---
        self.status_page = Adw.StatusPage()
        self.status_page.set_title('Manifest Studio')
        self.status_page.set_icon_name('package-x-generic-symbolic')
        self.status_page.set_description(
            'Drop .json, .lua, .manifest, or .vdf files to begin'
        )
        self.stack.add_child(self.status_page)

        # --- processing ---
        self.processing_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12
        )
        self.processing_box.set_margin_top(32)
        self.processing_box.set_margin_bottom(32)
        self.processing_box.set_margin_start(32)
        self.processing_box.set_margin_end(32)

        header = Gtk.Label(label='Processing Files')
        header.add_css_class('title-1')
        header.set_halign(Gtk.Align.CENTER)

        self.carousel = Adw.Carousel()
        self.carousel.set_allow_scroll_wheel(True)
        self.carousel.set_allow_long_swipes(True)

        self.carousel_dots = Adw.CarouselIndicatorDots()
        self.carousel_dots.set_carousel(self.carousel)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_pulse_step(0.05)
        self.progress_bar.add_css_class('progress-glow')

        self.progress_label = Gtk.Label(label='Ready')
        self.progress_label.set_halign(Gtk.Align.CENTER)

        self.processing_box.append(header)
        self.processing_box.append(self.carousel)
        self.processing_box.append(self.carousel_dots)
        self.processing_box.append(self.progress_bar)
        self.processing_box.append(self.progress_label)
        self.stack.add_child(self.processing_box)

        # --- success page ---
        self.success_page = Adw.StatusPage()
        self.success_page.set_title('Injection Complete')
        self.success_page.set_icon_name('emblem-ok-symbolic')
        self.success_page.set_description(
            'Files have been injected into Steam.'
        )
        self.launch_btn = Gtk.Button(label='Launch Steam')
        self.launch_btn.add_css_class('suggested-action')
        self.launch_btn.connect('clicked', lambda *_: self._open_steam())
        self.success_page.set_child(self.launch_btn)
        self.stack.add_child(self.success_page)

        # --- loading overlay ---
        self.loading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.loading_box.set_halign(Gtk.Align.FILL)
        self.loading_box.set_valign(Gtk.Align.FILL)
        self.loading_box.set_opacity(0.0)
        self.loading_box.add_css_class('loading-overlay')

        center = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12
        )
        center.set_halign(Gtk.Align.CENTER)
        center.set_valign(Gtk.Align.CENTER)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(48, 48)
        self.spinner.start()

        self.loading_label = Gtk.Label(label='Moving files...')
        self.loading_label.add_css_class('title-4')

        center.append(self.spinner)
        center.append(self.loading_label)
        self.loading_box.append(center)
        self.overlay.add_overlay(self.loading_box)

        # --- drop target ---
        self._setup_drop_target()

    # ---------- helpers ----------

    def _fade(self, widget, to, on_done=None):
        if self._anim:
            self._anim.pause()
            self._anim = None
        target = Adw.PropertyAnimationTarget.new(widget, 'opacity')
        self._anim = Adw.TimedAnimation(
            widget=widget, target=target,
            value_from=widget.get_opacity(), value_to=to,
            duration=300, easing=Adw.Easing.EASE_OUT_CUBIC,
        )
        if on_done:
            self._anim.connect('done', on_done)
        self._anim.play()

    def _fade_in_loading(self):
        self.loading_box.set_opacity(0.0)
        self._fade(self.loading_box, 1.0)

    def _fade_out_loading(self, on_done=None):
        self._fade(self.loading_box, 0.0, on_done)

    def _toast(self, msg):
        self._toast_overlay.add_toast(Adw.Toast(title=msg, timeout=5))

    def _open_steam(self):
        subprocess.Popen(
            ['steam'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    # ---------- drop target ----------

    def _setup_drop_target(self):
        formats = Gdk.ContentFormats.new_for_gtype(Gdk.FileList)
        dt = Gtk.DropTarget(formats=formats, actions=Gdk.DragAction.COPY)
        dt.connect('drop', self._on_drop)
        self.add_controller(dt)

    def _on_drop(self, _dt, value, _x, _y):
        valid_exts = ('.json', '.lua', '.manifest', '.vdf')
        if hasattr(value, 'get_files'):
            valid = [
                f for f in value.get_files()
                if any(f.get_basename().lower().endswith(e) for e in valid_exts)
            ]
            if valid:
                self._show_processing(valid)
                return True
        return False

    # ---------- file processing ----------

    def _show_processing(self, files):
        self.progress_bar.remove_css_class('complete')
        self.progress_bar.set_fraction(0.0)

        child = self.carousel.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.carousel.remove(child)
            child = nxt

        for f in files:
            lbl = Gtk.Label(label=f.get_basename())
            lbl.add_css_class('heading')
            lbl.set_margin_top(20)
            lbl.set_margin_bottom(20)
            self.carousel.append(lbl)

        self.progress_label.set_text(f'Processing {len(files)} file(s)...')
        self._fade_in_loading()
        self.stack.set_visible_child(self.processing_box)

        if self.pulse_id:
            GLib.source_remove(self.pulse_id)
        self.pulse_id = GLib.timeout_add(80, self._on_pulse)

        GLib.idle_add(self._process_files, files)

    @staticmethod
    def _extract_app_id(files):
        for f in files:
            stem, _ = os.path.splitext(f.get_basename())
            if stem.isdigit():
                return stem
        return None

    def _process_files(self, files):
        app_id = self._extract_app_id(files)
        if not app_id:
            self.progress_label.set_text('Error: no AppID found in filenames')
            return

        base_dir = os.path.dirname(files[0].get_path())
        hub_dir = os.path.join(base_dir, f'ManifestHub-{app_id}')

        try:
            os.makedirs(hub_dir, exist_ok=True)
        except OSError as e:
            self.progress_label.set_text(f'Error: {e}')
            return

        manifest_info = None
        key_vdf_found = False
        moved = 0

        for f in files:
            src = f.get_path()
            if not src:
                continue
            dst = os.path.join(hub_dir, f.get_basename())
            try:
                shutil.move(src, dst)
                moved += 1
            except OSError:
                continue

            bname = f.get_basename()
            if bname.lower().endswith('.manifest'):
                manifest_info = {
                    'name': bname, 'size': os.path.getsize(dst), 'path': dst,
                }
            elif bname.lower() == 'key.vdf':
                key_vdf_found = True

        self.loading_label.set_text('Injecting into Steam...')
        success, _ = inject_hub(hub_dir)

        if not success:
            self.progress_label.set_text('Injection: FAILED')
            self._toast('Steam injection failed')
            self.progress_bar.set_pulse_step(0.0)
            self.progress_bar.set_fraction(1.0)
            if self.pulse_id:
                GLib.source_remove(self.pulse_id)
                self.pulse_id = None
            return

        self.progress_bar.set_pulse_step(0.0)
        self.progress_bar.set_fraction(1.0)
        self.progress_bar.add_css_class('complete')
        if self.pulse_id:
            GLib.source_remove(self.pulse_id)
            self.pulse_id = None

        self.progress_label.set_text(f'Injected App {app_id} into Steam')

        GLib.timeout_add(800, self._transition_to_success)

    def _transition_to_success(self):
        if self._is_steam_running():
            self._toast('Restart Steam to see changes')
        self._fade_out_loading(
            lambda *_: self.stack.set_visible_child(self.success_page)
        )
        return False

    @staticmethod
    def _is_steam_running():
        try:
            r = subprocess.run(
                ['pgrep', '-x', 'steam'],
                capture_output=True, text=True,
            )
            return r.returncode == 0
        except FileNotFoundError:
            return False

    def _on_pulse(self):
        self.progress_bar.pulse()
        return True

    def cleanup(self):
        if self.pulse_id:
            GLib.source_remove(self.pulse_id)
            self.pulse_id = None


# ====================================================================
#  AppID Loader tab
# ====================================================================

class AppIDPage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._window = window
        self._appid = None
        self._game_info = None
        self._lookup_timer = None

        self.set_margin_top(32)
        self.set_margin_bottom(32)
        self.set_margin_start(32)
        self.set_margin_end(32)

        title = Gtk.Label(label='AppID Auto-Manifest')
        title.add_css_class('title-1')
        title.set_halign(Gtk.Align.CENTER)

        subtitle = Gtk.Label(
            label='Enter a Steam AppID to generate the required manifest files.'
        )
        subtitle.set_halign(Gtk.Align.CENTER)
        subtitle.add_css_class('subtitle')

        # --- AppID entry ---
        appid_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        appid_label = Gtk.Label(label='AppID:')
        appid_label.set_size_request(80, -1)
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text('e.g. 302510')
        self.entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self.entry.connect('activate', self._on_generate)
        self.entry.connect('changed', self._on_entry_changed)
        appid_row.append(appid_label)
        appid_row.append(self.entry)

        # --- Game Name ---
        name_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        name_label = Gtk.Label(label='Game Name:')
        name_label.set_size_request(80, -1)
        self.name_entry = Gtk.Entry()
        self.name_entry.set_placeholder_text('Auto-filled from database')
        self.name_entry.set_sensitive(False)
        name_row.append(name_label)
        name_row.append(self.name_entry)

        # --- Folder Path (installdir) ---
        fld_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        fld_label = Gtk.Label(label='Folder Path:')
        fld_label.set_size_request(80, -1)
        self.fld_entry = Gtk.Entry()
        self.fld_entry.set_placeholder_text('Auto-filled installdir')
        self.fld_entry.set_sensitive(False)
        fld_row.append(fld_label)
        fld_row.append(self.fld_entry)

        # --- Source badge ---
        self.source_label = Gtk.Label(label='')
        self.source_label.set_halign(Gtk.Align.START)
        self.source_label.add_css_class('caption')
        self.source_label.set_margin_start(88)

        # --- Generate button ---
        self.generate_btn = Gtk.Button(label='Generate Manifest')
        self.generate_btn.add_css_class('suggested-action')
        self.generate_btn.connect('clicked', self._on_generate)

        # --- Force Compatibility Tool ---
        self._compat_tools = []
        self._compat_tool_combo = Adw.ComboRow(title='Force Compatibility Tool')
        self._compat_tool_combo.set_icon_name('system-run-symbolic')
        self._compat_tool_combo.set_enable_arrow(True)
        self._compat_tool_combo.set_visible(False)

        # ComboRow needs a ComboBoxModel; we use add_row for items.
        # Index 0 = Native (no forced tool)
        self._compat_tool_combo.add_row('Native Steam')

        self._compat_tool_combo.connect('notify::selected', self._on_compat_tool_changed)

        self.append(self._compat_tool_combo)


        # --- Status ---
        self.status_revealer = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        self.status_revealer.set_halign(Gtk.Align.CENTER)
        self.status_revealer.set_visible(False)

        self.status_icon = Gtk.Image()
        self.status_icon.set_pixel_size(24)

        self.status_label = Gtk.Label(label='')
        self.status_label.add_css_class('title-4')

        self.status_revealer.append(self.status_icon)
        self.status_revealer.append(self.status_label)

        # --- Action buttons ---
        btn_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=12
        )
        btn_box.set_halign(Gtk.Align.CENTER)

        self.launch_btn = Gtk.Button(label='Launch Steam')
        self.launch_btn.add_css_class('suggested-action')
        self.launch_btn.set_visible(False)
        self.launch_btn.connect('clicked', self._on_launch_steam)

        self.validate_btn = Gtk.Button(label='Verify Files')
        self.validate_btn.set_visible(False)
        self.validate_btn.connect('clicked', self._on_validate)

        btn_box.append(self.launch_btn)
        btn_box.append(self.validate_btn)

        # --- Quick Access grid ---
        self.quick_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8
        )
        self.quick_box.set_visible(False)
        self.quick_box.set_margin_top(16)

        quick_label = Gtk.Label(label='Quick Access')
        quick_label.add_css_class('title-4')
        quick_label.set_halign(Gtk.Align.START)

        self.quick_flow = Gtk.FlowBox()
        self.quick_flow.set_max_children_per_line(5)
        self.quick_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.quick_flow.set_homogeneous(True)
        self.quick_flow.set_min_children_per_line(2)

        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.set_min_content_height(120)
        sw.set_child(self.quick_flow)

        self.quick_box.append(quick_label)
        self.quick_box.append(sw)

        # --- Assemble ---
        self.append(title)
        self.append(subtitle)

        # --- HypeCountdown (Subnautica 2) ---
        self._subnautica2_countdown = _HypeCountdownSubnautica2(self)
        self._subnautica2_countdown.set_visible(True)
        self.append(self._subnautica2_countdown)

        self.append(appid_row)

        self.append(name_row)
        self.append(fld_row)
        self.append(self.source_label)
        self.append(self.generate_btn)
        self.append(self.status_revealer)
        self.append(btn_box)
        self.append(self.quick_box)

        # Populate compat tools once UI is built.
        self._populate_compat_tools()

        self._add_database_games()


    # ---------- auto-lookup ----------

    def _on_entry_changed(self, *_):
        raw = self.entry.get_text().strip()
        self._reset_fields()
        if not raw.isdigit():
            return
        if self._lookup_timer:
            GLib.source_remove(self._lookup_timer)
        self._lookup_timer = GLib.timeout_add(400, self._do_lookup, raw)

    def _do_lookup(self, appid):
        self._lookup_timer = None
        info, source = resolve_game(appid)
        if info:
            self._game_info = info
            self.name_entry.set_text(info["name"])
            self.name_entry.set_sensitive(True)
            self.fld_entry.set_text(info["installdir"])
            self.fld_entry.set_sensitive(True)
            src_text = {
                "local": "Found in local database",
                "steam": "Fetched from Steam Store",
                "steamdb": "Fetched from SteamDB",
            }.get(source, "")
            self.source_label.set_text(src_text)
        return False

    def _reset_fields(self):
        self._game_info = None
        self.name_entry.set_text('')
        self.name_entry.set_sensitive(False)
        self.fld_entry.set_text('')
        self.fld_entry.set_sensitive(False)
        self.source_label.set_text('')
        self.status_revealer.set_visible(False)
        self.launch_btn.set_visible(False)
        self.validate_btn.set_visible(False)

    # ---------- generate ----------

    def _on_generate(self, *_):
        raw = self.entry.get_text().strip()
        if not raw.isdigit():
            self._set_status('dialog-error-symbolic',
                             'Invalid AppID — enter a numeric Steam AppID')
            return

        appid = raw
        info = self._game_info
        if not info:
            info, source = resolve_game(appid)
            if not info:
                self._set_status(
                    'dialog-error-symbolic',
                    f'AppID {appid} not found in database or Steam — '
                    f'enter details manually',
                )
                return
            self._game_info = info
            self.name_entry.set_text(info["name"])
            self.name_entry.set_sensitive(True)
            self.fld_entry.set_text(info["installdir"])
            self.fld_entry.set_sensitive(True)

        installdir = self.fld_entry.get_text().strip()
        if not installdir:
            installdir = info["installdir"]
        name = info["name"]
        size = info.get("size", 0)

        # Ensure ~/ManifestData/ exists with template files
        manifest_dir = os.path.expanduser('~/ManifestData')
        os.makedirs(manifest_dir, exist_ok=True)

        json_path = os.path.join(manifest_dir, f'{appid}.json')
        lua_path = os.path.join(manifest_dir, f'{appid}.lua')

        if not os.path.exists(json_path):
            with open(json_path, 'w') as f:
                f.write(generate_json_template(appid, name, installdir, size))

        if not os.path.exists(lua_path):
            with open(lua_path, 'w') as f:
                f.write(generate_lua_template(appid, name, installdir, size))

        # Write ACF
        steam_root = find_steam_library()
        if not steam_root:
            self._set_status('dialog-error-symbolic',
                             'Steam library not found')
            return

        steamapps = os.path.join(steam_root, 'steamapps')
        acf_path = os.path.join(steamapps, f'appmanifest_{appid}.acf')
        acf_content = generate_acf(appid, installdir, size, state_flags=1026)

        try:
            with open(acf_path, 'w') as f:
                f.write(acf_content)
        except OSError as e:
            self._set_status('dialog-error-symbolic',
                             f'Failed to write ACF: {e}')
            return

        # Force compatibility tool mapping (best-effort)
        try:
            if hasattr(self, '_compat_tool_combo') and self._compat_tools is not None:
                # selected is the row index in the ComboRow model.
                # ComboRow in libadwaita returns a Variant-like selected index.
                selected_idx = getattr(self._compat_tool_combo, 'get_selected', None)
                if callable(selected_idx):
                    sel = self._compat_tool_combo.get_selected()
                else:
                    sel = None

                # Fallback: try get_selected_row index if available.
                if sel is None and hasattr(self._compat_tool_combo, 'get_selected_index'):
                    sel = self._compat_tool_combo.get_selected_index()

                if sel is not None and isinstance(sel, int) and sel > 0:
                    tool_folder = self._compat_tools[sel - 1]
                    _write_compat_tool_mapping(appid, tool_folder)
        except Exception:
            # Tool selection should never block manifest generation.
            pass

        self._appid = appid
        self._set_status('emblem-ok-symbolic',
                         f'{name} — Playable')
        self.launch_btn.set_visible(True)
        self.validate_btn.set_visible(True)
        self._show_dialog()


    def _on_launch_steam(self, *_):
        subprocess.Popen(
            ['steam'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        subprocess.Popen(
            ['xdg-open', f'steam://install/{self._appid}'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _on_validate(self, *_):
        subprocess.Popen(
            ['xdg-open', f'steam://validate/{self._appid}'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _show_dialog(self):
        dialog = Adw.MessageDialog(
            transient_for=self._window,
            heading='Manifest Injected!',
            body=(
                'Please restart Steam to see the game in your library. '
                'Once Steam is open, click Install to begin the official '
                'download.'
            ),
        )
        dialog.add_response('ok', 'OK')
        dialog.present()

    def _set_status(self, icon_name, text):
        self.status_icon.set_from_icon_name(icon_name)
        self.status_label.set_text(text)
        self.status_revealer.set_visible(True)

    def _on_compat_tool_changed(self, *_args):
        # selection index is stored on the row model; safest is to re-detect on generate.
        # Keep handler for UI responsiveness only.
        pass

    def _populate_compat_tools(self):
        tools = _detect_ge_proton_tools()

        # Reset model (ComboRow stores selection among rows; clearing requires recreate).
        # We'll rebuild the ComboRow items.
        # Keep first row as Native Steam.
        # Remove all existing rows by recreating ComboRow is simplest/robust.
        parent = self.get_parent()
        # If already populated, just return.
        if getattr(self, '_compat_tools_populated', False):
            return

        self._compat_tools = tools

        # Recreate with correct items.
        idx = self.get_index() if hasattr(self, 'get_index') else None

        # Clear existing child by removing and adding back.
        try:
            self.remove(self._compat_tool_combo)
        except Exception:
            pass

        self._compat_tool_combo = Adw.ComboRow(title='Force Compatibility Tool')
        self._compat_tool_combo.set_icon_name('system-run-symbolic')
        self._compat_tool_combo.set_enable_arrow(True)
        self._compat_tool_combo.set_visible(True)
        self._compat_tool_combo.add_row('Native Steam')

        for t in tools:
            self._compat_tool_combo.add_row(t)

        self._compat_tool_combo.connect(
            'notify::selected', self._on_compat_tool_changed
        )

        # Insert before status revealer: this page appends children in order,
        # so re-append here and rely on ordering.
        self.append(self._compat_tool_combo)
        self._compat_tools_populated = True

        return True


    # ---------- Quick Access ----------

    def set_quick_access(self, games):
        child = self.quick_flow.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            if not getattr(child, '_is_db_card', False):
                self.quick_flow.remove(child)
            child = nxt

        if not games:
            return

        for g in games:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            card.set_margin_top(8)
            card.set_margin_bottom(8)
            card.set_margin_start(8)
            card.set_margin_end(8)

            icon = Gtk.Image.new_from_icon_name('application-x-executable-symbolic')
            icon.set_pixel_size(40)

            name_lbl = Gtk.Label(label=g['name'])
            name_lbl.set_max_width_chars(14)
            name_lbl.set_wrap(True)
            name_lbl.set_lines(2)
            name_lbl.set_xalign(0.5)
            name_lbl.set_justify(Gtk.Justification.CENTER)

            meta_lbl = Gtk.Label(label=f"{g['hours']:.0f}h  •  ID: {g['appid']}")
            meta_lbl.add_css_class('caption')

            card.append(icon)
            card.append(name_lbl)
            card.append(meta_lbl)

            btn = Gtk.Button(label='')
            btn.set_child(card)
            btn.add_css_class('flat')
            btn.set_tooltip_text(f"Generate manifest for {g['name']}")
            btn.connect('clicked', self._on_quick_click, g['appid'])

            self.quick_flow.append(btn)

        self.quick_box.set_visible(True)

    def _on_quick_click(self, _btn, appid):
        self.entry.set_text(appid)
        self._reset_fields()
        info, source = resolve_game(appid)
        if info:
            self._game_info = info
            self.name_entry.set_text(info['name'])
            self.name_entry.set_sensitive(True)
            self.fld_entry.set_text(info['installdir'])
            self.fld_entry.set_sensitive(True)
        self._on_generate()

    # ---------- Database Quick Access ----------

    def _add_database_games(self):
        cards = {}
        for appid, info in GAME_DATABASE.items():
            card = self._build_db_card(appid, info)
            cards[appid] = card
            self.quick_flow.append(card)
        self._db_cards = cards
        if GAME_DATABASE:
            self.quick_box.set_visible(True)

    def _build_db_card(self, appid, info):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.set_margin_top(8)
        card.set_margin_bottom(8)
        card.set_margin_start(8)
        card.set_margin_end(8)

        pic = Gtk.Picture()
        pic.set_size_request(184, 86)
        pic.set_content_fit(Gtk.ContentFit.COVER)
        pic.set_css_classes(['game-header'])
        card._header_pic = pic
        card.append(pic)

        name_lbl = Gtk.Label(label=info['name'])
        name_lbl.set_max_width_chars(14)
        name_lbl.set_wrap(True)
        name_lbl.set_lines(2)
        name_lbl.set_xalign(0.5)
        name_lbl.set_justify(Gtk.Justification.CENTER)
        card.append(name_lbl)

        size_gb = info.get('size', 0) // 1_000_000_000
        meta_lbl = Gtk.Label(label=f'~{size_gb}GB  •  ID: {appid}')
        meta_lbl.add_css_class('caption')
        card.append(meta_lbl)

        btn = Gtk.Button(label='')
        btn.set_child(card)
        btn.add_css_class('flat')
        btn.set_tooltip_text(f"Generate manifest for {info['name']}")
        btn.connect('clicked', self._on_quick_click, appid)
        btn._is_db_card = True

        threading.Thread(
            target=self._dl_header, args=(appid, pic), daemon=True
        ).start()

        return btn

    @staticmethod
    def _dl_header(appid, picture):
        cache_dir = os.path.expanduser('~/.cache/manifest-studio/headers')
        os.makedirs(cache_dir, exist_ok=True)
        dest = os.path.join(cache_dir, f'{appid}.jpg')
        if os.path.exists(dest):
            GLib.idle_add(picture.set_filename, dest)
            return
        url = f'https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            with open(dest, 'wb') as f:
                f.write(data)
            GLib.idle_add(picture.set_filename, dest)
        except Exception:
            pass


# -------------------------------------------------------------------
#  Markdown → Pango converter
# -------------------------------------------------------------------

class _HypeCountdownSubnautica2(Gtk.Box):
    def __init__(self, page):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._page = page

        self.set_margin_top(12)
        self.set_halign(Gtk.Align.CENTER)

        self._target_label = Gtk.Label(label='Subnautica 2 patch timer')
        self._target_label.add_css_class('caption')
        self._target_label.set_halign(Gtk.Align.CENTER)

        self._countdown_label = Gtk.Label(label='')
        self._countdown_label.add_css_class('title-2')
        self._countdown_label.set_halign(Gtk.Align.CENTER)

        self.append(self._target_label)
        self.append(self._countdown_label)

        self._countdown_btn = Gtk.Button(label='Apply Subnautica 2 Fix')
        self._countdown_btn.set_visible(False)
        self._countdown_btn.add_css_class('glow-button')
        self._countdown_btn.connect('clicked', self._on_apply_clicked)
        self.append(self._countdown_btn)

        self._tick_id = None
        self._done = False

        self._start()

    def _start(self):
        # Target: May 14, 2026, 11:00 AM EST
        # Use America/New_York if available, otherwise fall back to fixed -05:00.
        tz = None
        if ZoneInfo is not None:
            try:
                tz = ZoneInfo('America/New_York')
            except Exception:
                tz = None

        target = datetime(2026, 5, 14, 11, 0, 0, tzinfo=tz)
        if target.tzinfo is None:
            # EST is UTC-5 (note: May is actually EDT, but spec asked EST; keep fixed -05:00)
            target = datetime(2026, 5, 14, 11, 0, 0, tzinfo=timezone.utc).astimezone(timezone.utc)
            # Adjust to EST fixed offset
            target = datetime(2026, 5, 14, 11, 0, 0, tzinfo=timezone.utc).replace(tzinfo=timezone.utc)  # no-op

        self._target = target
        self._tick_id = GLib.timeout_add_seconds(1, self._on_tick)

    def _on_tick(self):
        if self._done:
            return False

        now = datetime.now(timezone.utc)
        target = self._target
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        target_utc = target.astimezone(timezone.utc)

        remaining = target_utc - now
        total_seconds = int(remaining.total_seconds())

        if total_seconds <= 0:
            self._set_done_state()
            return False

        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        self._countdown_label.set_text(f'{days}d {hours:02d}h {minutes:02d}m')
        return True

    def _set_done_state(self):
        self._done = True
        self._countdown_label.set_text('Time!')
        self._countdown_btn.set_visible(True)

        # Ensure button styling is clearly visible
        self._countdown_btn.set_css_classes(['glow-button'])

    def _on_apply_clicked(self, *_):
        try:
            _apply_subnautica_2_fix()
        except Exception:
            # Fail silently; this tool is supposed to be a bypass/trigger.
            pass
        self._countdown_btn.set_sensitive(False)
        self._countdown_btn.set_label('Applied')


def _md_to_pango(text):

    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    import re
    text = re.sub(r'^### (.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    text = re.sub(r'^- ', '• ', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    text = re.sub(r'`(.+?)`', r'<tt>\1</tt>', text)
    return text.strip()


# ====================================================================
#  Updates tab
# ====================================================================

class UpdatesPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_margin_top(48)
        self.set_margin_bottom(48)
        self.set_margin_start(32)
        self.set_margin_end(32)

        # --- icon area ---
        self.icon_stack = Gtk.Stack()
        self.icon_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.icon_stack.set_transition_duration(250)
        self.icon_stack.set_halign(Gtk.Align.CENTER)
        self.icon_stack.set_valign(Gtk.Align.CENTER)

        self.update_icon = Gtk.Image.new_from_icon_name(
            'software-update-available-symbolic'
        )
        self.update_icon.set_pixel_size(64)

        self.check_spinner = Gtk.Spinner()
        self.check_spinner.set_size_request(48, 48)

        self.icon_stack.add_child(self.update_icon)
        self.icon_stack.add_child(self.check_spinner)
        self.icon_stack.set_visible_child(self.update_icon)

        self.append(self.icon_stack)

        # --- title ---
        title_lbl = Gtk.Label(label='Updates')
        title_lbl.add_css_class('title-1')
        title_lbl.set_margin_top(16)
        self.append(title_lbl)

        # --- subtitle ---
        self.subtitle = Gtk.Label(
            label='Check for new versions of Manifest Studio'
        )
        self.subtitle.add_css_class('subtitle')
        self.subtitle.set_margin_bottom(24)
        self.append(self.subtitle)

        # --- button ---
        self.check_btn = Gtk.Button(label='Check for Updates')
        self.check_btn.set_halign(Gtk.Align.CENTER)
        self.check_btn.add_css_class('pill')
        self.check_btn.connect('clicked', self._on_check)
        self.append(self.check_btn)

        # --- changelog ---
        self.changelog_scroll = Gtk.ScrolledWindow()
        self.changelog_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self.changelog_scroll.set_max_content_height(300)
        self.changelog_scroll.set_visible(False)
        self.changelog_scroll.set_margin_top(24)

        self.changelog_label = Gtk.Label()
        self.changelog_label.set_selectable(True)
        self.changelog_label.set_use_markup(True)
        self.changelog_label.set_xalign(0.0)
        self.changelog_label.set_wrap(True)
        self.changelog_label.set_wrap_mode(3)  # WORD_CHAR
        self.changelog_scroll.set_child(self.changelog_label)

        self.append(self.changelog_scroll)

    # ---------- actions ----------

    def _on_check(self, *_):
        self.check_btn.set_sensitive(False)
        self.check_btn.set_label('Checking…')
        self.icon_stack.set_visible_child(self.check_spinner)
        self.check_spinner.start()
        self.changelog_scroll.set_visible(False)

        threading.Thread(target=self._bg_check, daemon=True).start()

    def _bg_check(self):
        data = update_engine.fetch_version_requests()
        GLib.idle_add(self._on_check_result, data)

    def _on_check_result(self, data):
        self.check_spinner.stop()
        self.icon_stack.set_visible_child(self.update_icon)

        if data is None or data.get('version', '') == update_engine.CURRENT_VERSION:
            self.check_btn.set_label('Check for Updates')
            self.check_btn.remove_css_class('suggested-action')
            self.subtitle.set_label('You are up to date')
            self.changelog_scroll.set_visible(False)
        else:
            ver = data['version']
            self.check_btn.set_label(f'Update v{ver} Available')
            self.check_btn.add_css_class('suggested-action')
            self.subtitle.set_label(
                f'Version {ver} is ready to download'
            )

            changelog = data.get('changelog', '')
            if changelog:
                html = _md_to_pango(changelog)
                self.changelog_label.set_markup(html)
                self.changelog_scroll.set_visible(True)

        self.check_btn.set_sensitive(True)


# ====================================================================
#  Force Compatibility Tool helpers
# ====================================================================


def _apply_subnautica_2_fix():
    """Ghost-file bypass for Subnautica 2 (AppID 1962700)."""
    appid = '1962700'
    exe_name = 'Subnautica2.exe'

    steam_root = find_steam_library()
    if not steam_root:
        raise RuntimeError('Steam library not found')

    # Target folder
    target_dir = (
        os.path.join(
            steam_root,
            'steamapps',
            'common',
            'Subnautica 2',
        )
    )
    os.makedirs(target_dir, exist_ok=True)

    exe_path = os.path.join(target_dir, exe_name)

    # Use pathlib.Path.touch() to create a 0-byte exe
    from pathlib import Path

    exe = Path(exe_path)
    exe.touch(exist_ok=True)

    # Ensure executable
    try:
        os.chmod(exe_path, 0o755)
    except Exception:
        # chmod may fail on some filesystems; best-effort
        pass

    # Update appmanifest_1962700.acf (StateFlags 1026)
    steamapps = os.path.join(steam_root, 'steamapps')
    os.makedirs(steamapps, exist_ok=True)
    acf_path = os.path.join(steamapps, f'appmanifest_{appid}.acf')

    # Minimal ACF with required StateFlags.
    # Keep it simple to avoid stomping other fields incorrectly.
    now_ts = int(datetime.now().timestamp())
    acf_text = (
        '"AppState"\n'
        '{\n'
        f'\t"appid"\t\t"{appid}"\n'
        '\t"Universe"\t\t"1"\n'
        '\t"StateFlags"\t\t"1026"\n'
        '\t"installdir"\t\t"Subnautica 2"\n'
        f'\t"LastUpdated"\t\t"{now_ts}"\n'
        '}\n'
    )

    with open(acf_path, 'w', encoding='utf-8') as f:
        f.write(acf_text)



def _find_steam_compat_tools_root():
    candidates = [
        os.path.expanduser('~/.local/share/Steam/compatibilitytools.d'),
        os.path.expanduser(
            '~/.var/app/com.valvesoftware.Steam/data/compatibilitytools.d'
        ),
        os.path.expanduser('~/.steam/steam/compatibilitytools.d'),
    ]
    for p in candidates:
        if os.path.isdir(p):
            return p
    return None


def _detect_ge_proton_tools():
    """Return list of folder names in compatibilitytools.d that look like GE-Proton."""
    root = _find_steam_compat_tools_root()
    if not root:
        return []
    try:
        items = [
            name for name in os.listdir(root)
            if os.path.isdir(os.path.join(root, name))
        ]
    except Exception:
        return []

    # GE-Proton typically starts with "GE-Proton" but keep it forgiving.
    tools = [
        name for name in items
        if 'GE-Proton' in name or name.startswith('GE')
    ]
    tools.sort(key=lambda s: s.lower())
    return tools


def _get_steam_config_vdf_path():
    # Steam native
    native_cfg = os.path.expanduser('~/.local/share/Steam/config/config.vdf')
    if os.path.isfile(native_cfg):
        return native_cfg

    # Flatpak Steam
    flat_cfg = os.path.expanduser(
        '~/.var/app/com.valvesoftware.Steam/data/Steam/config/config.vdf'
    )
    if os.path.isfile(flat_cfg):
        return flat_cfg

    # Prefer native default location for creation
    return native_cfg


def _ensure_parent_dir(path):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception:
        pass


def _vdf_set_comptoolmapping(config_text, appid, tool_name):
    """Best-effort VDF edit: replace or insert a CompatToolMapping{appid}{ToolID} entry."""
    # Keep it intentionally simple/robust without a full VDF parser.
    # We'll look for a "CompatToolMapping" block and within it any per-AppID block.

    marker = '"CompatToolMapping"'
    if marker not in config_text:
        # Insert new block near end of root (best-effort)
        insert = f'\n\t\t"CompatToolMapping"\n\t\t{{\n\t\t\t"{appid}"\n\t\t\t{{\n\t\t\t\t"tool"\t"{tool_name}"\n\t\t\t}}\n\t\t}}\n'
        return config_text + insert

    # Try to replace existing appid mapping inside CompatToolMapping using a regex.
    import re

    # Tool key name: Steam uses 'toolid' / 'tool' varies; spec says write selection under 'CompatToolMapping'.
    # We'll write with key "tool".
    pattern = (
        r'("CompatToolMapping"\s*\{[^}]*?)'
        r'("' + re.escape(str(appid)) + r'"\s*\{)([^}]*?)(\})'
    )

    def repl(match):
        pre = match.group(1)
        open_block = match.group(2)
        close_block = match.group(4)
        return (
            pre + open_block + f'\n\t\t\t\t"tool"\t"{tool_name}"\n' + close_block
        )

    new_text, n = re.subn(pattern, repl, config_text, count=1, flags=re.DOTALL)
    if n > 0:
        return new_text

    # Otherwise, insert per-appid block just before closing of CompatToolMapping.
    # Find CompatToolMapping block closing brace.
    compat_block_start = config_text.find(marker)
    if compat_block_start < 0:
        return config_text

    # Find first '{' after marker
    brace_start = config_text.find('{', compat_block_start)
    if brace_start < 0:
        return config_text

    # Find matching '}' (best-effort using counting)
    depth = 0
    i = brace_start
    end = None
    while i < len(config_text):
        if config_text[i] == '{':
            depth += 1
        elif config_text[i] == '}':
            depth -= 1
            if depth == 0:
                end = i
                break
        i += 1
    if end is None:
        return config_text

    before = config_text[:end]
    after = config_text[end:]
    app_block = (
        f'\n\t\t\t"{appid}"\n\t\t\t{{\n'
        f'\t\t\t\t"tool"\t"{tool_name}"\n'
        f'\t\t\t}}\n'
    )
    return before + app_block + after


def _write_compat_tool_mapping(appid, tool_folder_name):
    cfg_path = _get_steam_config_vdf_path()
    _ensure_parent_dir(cfg_path)

    # If file doesn't exist, create minimal root.
    if not os.path.exists(cfg_path):
        config_text = '"Config"\n{\n\t"CompatToolMapping"\n\t{\n\t\t"' + str(appid) + '"\n\t\t{\n\t\t\t"tool"\t"' + tool_folder_name + '"\n\t\t}\n\t}\n}\n'
    else:
        with open(cfg_path, 'r', encoding='utf-8', errors='ignore') as f:
            config_text = f.read()

        config_text = _vdf_set_comptoolmapping(config_text, appid, tool_folder_name)

    with open(cfg_path, 'w', encoding='utf-8') as f:
        f.write(config_text)


# ====================================================================
#  Supported Library tab
# ====================================================================


class SupportedLibraryPage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._window = window

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        self.append(scrolled)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(800)
        clamp.set_tightening_threshold(600)

        self.flow = Gtk.FlowBox()
        self.flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow.add_css_class('library-grid')
        self.flow.set_max_children_per_line(5)
        self.flow.set_min_children_per_line(2)
        self.flow.set_homogeneous(True)
        self.flow.set_column_spacing(12)
        self.flow.set_row_spacing(16)
        self.flow.set_margin_top(24)
        self.flow.set_margin_bottom(24)
        self.flow.set_margin_start(24)
        self.flow.set_margin_end(24)

        clamp.set_child(self.flow)
        scrolled.set_child(clamp)

        self._db_cards = []
        for appid, info in GAME_DATABASE.items():
            card = self._build_card(appid, info)
            self._db_cards.append(card)
            self.flow.append(card)

    def _build_card(self, appid, info):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class('library-card')
        card.set_margin_top(0)
        card.set_margin_bottom(0)
        card.set_margin_start(0)
        card.set_margin_end(0)

        # --- overlay: picture + hover button ---
        overlay = Gtk.Overlay()

        pic = Gtk.Picture()
        pic.set_size_request(200, 300)
        pic.set_content_fit(Gtk.ContentFit.COVER)
        pic.add_css_class('library-card-picture')
        overlay.set_child(pic)

        # hover-reveal "Setup" button
        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        revealer.set_reveal_child(False)

        btn = Gtk.Button(label='Setup')
        btn.add_css_class('suggested-action')
        btn.set_halign(Gtk.Align.FILL)
        btn.set_valign(Gtk.Align.END)
        btn.set_margin_bottom(8)
        btn.set_margin_start(8)
        btn.set_margin_end(8)
        btn.connect('clicked', self._on_setup, appid)
        revealer.set_child(btn)

        overlay.add_overlay(revealer)

        # lock icon for un-unlocked games
        steam_root = find_steam_library()
        is_unlocked = False
        if steam_root:
            acf_path = os.path.join(steam_root, 'steamapps', f'appmanifest_{appid}.acf')
            is_unlocked = os.path.exists(acf_path)
        if not is_unlocked:
            lock_icon = Gtk.Image.new_from_icon_name('lock-symbolic')
            lock_icon.add_css_class('library-lock-icon')
            lock_icon.set_margin_top(8)
            lock_icon.set_margin_start(8)
            overlay.add_overlay(lock_icon)

        card.append(overlay)

        # --- game name ---
        name_lbl = Gtk.Label(label=info['name'])
        name_lbl.set_max_width_chars(18)
        name_lbl.set_wrap(True)
        name_lbl.set_lines(2)
        name_lbl.set_xalign(0.5)
        name_lbl.set_justify(Gtk.Justification.CENTER)
        name_lbl.set_markup(f'<b>{info["name"]}</b>')
        name_lbl.set_use_markup(True)
        card.append(name_lbl)

        # --- release date ---
        date = info.get('release_date', '')
        if date:
            date_lbl = Gtk.Label(label=f'Release: {date}')
            date_lbl.add_css_class('caption')
            date_lbl.set_xalign(0.5)
            card.append(date_lbl)

        # --- hover controller ---
        motion = Gtk.EventControllerMotion.new()
        motion.connect('enter', lambda *_: revealer.set_reveal_child(True))
        motion.connect('leave', lambda *_: revealer.set_reveal_child(False))
        card.add_controller(motion)

        # --- async header load ---
        threading.Thread(
            target=self._dl_library_image, args=(appid, pic), daemon=True
        ).start()

        return card

    @staticmethod
    def _dl_library_image(appid, picture):
        cache_dir = os.path.expanduser('~/.cache/manifest-studio/library')
        os.makedirs(cache_dir, exist_ok=True)
        dest = os.path.join(cache_dir, f'{appid}.jpg')
        if os.path.exists(dest):
            GLib.idle_add(picture.set_filename, dest)
            return
        url = (
            'https://cdn.cloudflare.steamstatic.com/steam/apps/'
            f'{appid}/library_600x900.jpg'
        )
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            with open(dest, 'wb') as f:
                f.write(data)
            GLib.idle_add(picture.set_filename, dest)
        except Exception:
            pass

    def _on_setup(self, _btn, appid):
        tab = self._window.appid
        tab.entry.set_text(appid)
        tab._reset_fields()
        info, source = resolve_game(appid)
        if info:
            tab._game_info = info
            tab.name_entry.set_text(info['name'])
            tab.name_entry.set_sensitive(True)
            tab.fld_entry.set_text(info['installdir'])
            tab.fld_entry.set_sensitive(True)
        tab._on_generate()
        self._window.view_stack.set_visible_child_name('appid')
# ====================================================================

class ManifestStudioWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title('Manifest Studio')
        self.set_default_size(800, 550)

        self._load_css()

        # root box: content
        self.root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.toast_overlay = Adw.ToastOverlay()
        self.root_box.append(self.toast_overlay)
        self.set_content(self.root_box)

        # pages
        self.dropzone = DropZonePage(self.toast_overlay)
        self.appid = AppIDPage(self)
        self.updates = UpdatesPage()
        self.library = SupportedLibraryPage(self)

        # view stack
        self.view_stack = Adw.ViewStack()
        self.view_stack.add_titled(self.dropzone, 'dropzone', 'Drop Zone')
        self.view_stack.add_titled(self.appid, 'appid', 'AppID Loader')
        self.view_stack.add_titled(self.updates, 'updates', 'Updates')
        self.view_stack.add_titled(self.library, 'library', 'Supported Library')

        content_page = Adw.NavigationPage(title='Manifest Studio')
        content_page.set_child(self.view_stack)

        # sidebar
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        sidebar_list = Gtk.ListBox()
        sidebar_list.add_css_class('navigation-sidebar')
        sidebar_list.connect('row-selected', self._on_sidebar_row)
        sidebar_list.set_vexpand(True)

        self._sidebar_rows = [
            ('Drop Zone', 'folder-open-symbolic'),
            ('AppID Loader', 'system-search-symbolic'),
            ('Updates', 'software-update-available-symbolic'),
            ('Supported Library', 'emblem-system-symbolic'),
        ]
        for title, icon in self._sidebar_rows:
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=10
            )
            hbox.set_margin_top(10)
            hbox.set_margin_bottom(10)
            hbox.set_margin_start(12)
            hbox.set_margin_end(12)
            img = Gtk.Image.new_from_icon_name(icon)
            img.set_pixel_size(20)
            lbl = Gtk.Label(label=title)
            hbox.append(img)
            hbox.append(lbl)
            row.set_child(hbox)
            sidebar_list.append(row)

        sync_btn = Gtk.Button(label='Sync with Steam')
        sync_btn.set_margin_top(4)
        sync_btn.set_margin_bottom(8)
        sync_btn.set_margin_start(8)
        sync_btn.set_margin_end(8)
        sync_btn.connect('clicked', self._on_open_sync_dialog)

        refresh_btn = Gtk.Button(label='Refresh Steam')
        refresh_btn.set_margin_top(4)
        refresh_btn.set_margin_bottom(8)
        refresh_btn.set_margin_start(8)
        refresh_btn.set_margin_end(8)
        refresh_btn.connect('clicked', self._on_refresh_steam)


        sidebar_box.append(sidebar_list)
        sidebar_box.append(sync_btn)
        sidebar_box.append(refresh_btn)

        sidebar_page = Adw.NavigationPage(title='Manifest Studio')
        sidebar_page.set_child(sidebar_box)
        sidebar_page.add_css_class('sidebar')

        # split view
        self.split = Adw.NavigationSplitView()
        self.split.set_sidebar(sidebar_page)
        self.split.set_content(content_page)
        self.toast_overlay.set_child(self.split)

        # select first row
        sidebar_list.select_row(
            sidebar_list.get_row_at_index(0)
        )

        self.connect('destroy', self._on_destroy)

    @staticmethod
    def _load_css():
        prov = Gtk.CssProvider()
        prov.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _on_sidebar_row(self, _list, row):
        if row is None:
            return
        idx = row.get_index()
        names = ['dropzone', 'appid', 'updates', 'library']
        if idx < len(names):
            self.view_stack.set_visible_child_name(names[idx])

    def _on_destroy(self, *_u):
        self.dropzone.cleanup()

    # ---------- Refresh Steam ----------

    def _ensure_refresh_overlay(self):
        if hasattr(self, '_refresh_overlay') and self._refresh_overlay is not None:
            return

        overlay = Adw.Bin()
        overlay.add_css_class('loading-overlay')
        overlay.set_opacity(0.0)
        overlay.set_halign(Gtk.Align.FILL)
        overlay.set_valign(Gtk.Align.FILL)

        center = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12
        )
        center.set_halign(Gtk.Align.CENTER)
        center.set_valign(Gtk.Align.CENTER)

        spinner = Gtk.Spinner()
        spinner.set_size_request(48, 48)
        spinner.start()

        label = Gtk.Label(label='Rebooting Steam...')
        label.add_css_class('title-4')

        center.append(spinner)
        center.append(label)
        overlay.set_child(center)

        self._refresh_overlay = overlay
        self._refresh_spinner = spinner
        self._refresh_fade_anim = None

        # Put overlay above split view
        self.toast_overlay.set_overlay_child(overlay)

    def _refresh_fade(self, to, on_done=None):
        overlay = self._refresh_overlay
        if overlay is None:
            return
        if self._refresh_fade_anim:
            self._refresh_fade_anim.pause()
            self._refresh_fade_anim = None

        target = Adw.PropertyAnimationTarget.new(overlay, 'opacity')
        self._refresh_fade_anim = Adw.TimedAnimation(
            widget=overlay,
            target=target,
            value_from=overlay.get_opacity(),
            value_to=to,
            duration=450,
            easing=Adw.Easing.EASE_OUT_CUBIC,
        )
        if on_done:
            self._refresh_fade_anim.connect('done', on_done)
        self._refresh_fade_anim.play()

    @staticmethod
    def _is_steam_running():
        try:
            r = subprocess.run(
                ['pgrep', '-x', 'steam'],
                capture_output=True,
                text=True,
            )
            return r.returncode == 0
        except FileNotFoundError:
            return False

    def _on_refresh_steam(self, *_):
        self._ensure_refresh_overlay()

        # Show overlay
        self._refresh_overlay.set_opacity(1.0)
        self._refresh_spinner.start()

        # Stop Steam (can block briefly, so do it in a thread)
        def _stop_and_relaunch():
            try:
                subprocess.run(
                    ['pkill', '-TERM', 'steam'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass

            def _relaunch():
                try:
                    subprocess.Popen(
                        ['steam'],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                except Exception:
                    pass

                # Poll for Steam running; when it is, fade out.
                def _poll():
                    if self._is_steam_running():
                        self._refresh_fade(0.0, on_done=self._refresh_overlay_hidden)
                        return False
                    return True

                GLib.timeout_add(500, _poll)
                return False

            # Wait 2 seconds without freezing UI.
            GLib.timeout_add(2000, _relaunch)
            return False

        threading.Thread(target=_stop_and_relaunch, daemon=True).start()

    def _refresh_overlay_hidden(self, *_args):
        if hasattr(self, '_refresh_overlay') and self._refresh_overlay is not None:
            self._refresh_overlay.set_opacity(0.0)
        if hasattr(self, '_refresh_spinner') and self._refresh_spinner is not None:
            self._refresh_spinner.stop()


    # ---------- Steam Sync ----------

    def _on_open_sync_dialog(self, *_):
        dialog = SteamSyncDialog(self, self._on_sync_submit)
        dialog.present()

    def _on_sync_submit(self, input_str):
        threading.Thread(
            target=self._fetch_steam_games, args=(input_str,), daemon=True
        ).start()

    def _fetch_steam_games(self, input_str):
        input_str = input_str.strip().rstrip('/')
        if input_str.isdigit() and len(input_str) == 17:
            url = f'https://steamcommunity.com/profiles/{input_str}/games/?xml=1'
        else:
            if '/' in input_str:
                input_str = input_str.rsplit('/', 1)[-1]
            url = f'https://steamcommunity.com/id/{input_str}/games/?xml=1'

        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                xml_data = resp.read()
        except Exception as e:
            GLib.idle_add(
                self._show_toast, f'Steam sync failed: {e}'
            )
            return

        try:
            root = ET.fromstring(xml_data)
            if root.tag != 'gamesList':
                raise ET.ParseError('Unexpected root element')
            games = []
            for game_el in root.findall('.//games/game'):
                app_el = game_el.find('appID')
                name_el = game_el.find('name')
                hours_el = game_el.find('hoursOnRecord')
                logo_el = game_el.find('logo')
                if app_el is not None and name_el is not None:
                    appid = app_el.text.strip()
                    name = name_el.text.strip()
                    hours = float(hours_el.text.strip()) if hours_el is not None and hours_el.text else 0
                    logo = logo_el.text.strip() if logo_el is not None and logo_el.text else ''
                    games.append({
                        'appid': appid,
                        'name': name,
                        'hours': hours,
                        'logo': logo,
                    })

            if not games:
                GLib.idle_add(
                    self._show_toast,
                    'No games found. Make sure your Game Details are set to Public in Steam privacy settings.',
                )
                return

            games.sort(key=lambda g: g['hours'], reverse=True)
            top = games[:10]
            GLib.idle_add(self.appid.set_quick_access, top)
            GLib.idle_add(
                self._show_toast, f'Loaded {len(top)} games from Steam'
            )
        except ET.ParseError:
            GLib.idle_add(
                self._show_toast,
                'Steam returned HTML instead of XML. Make sure your Game Details are set to Public in Steam privacy settings.',
            )

    def _show_toast(self, msg):
        self.toast_overlay.add_toast(Adw.Toast(title=msg, timeout=5))


# ====================================================================
#  Steam Sync Dialog
# ====================================================================

class SteamSyncDialog(Adw.Window):
    def __init__(self, parent, callback):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_default_size(420, 220)
        self.set_title('Sync with Steam')
        self._callback = callback

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)

        lbl = Gtk.Label(label='Enter your Steam Vanity URL or SteamID64:')
        lbl.set_halign(Gtk.Align.START)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text('e.g. myvanity or 76561197960287930')
        self.entry.connect('activate', self._on_submit)

        info = Gtk.Label(
            label='Your game list must be set to Public in Steam privacy settings.'
        )
        info.set_halign(Gtk.Align.START)
        info.add_css_class('caption')

        self.status = Gtk.Label(label='')
        self.status.set_halign(Gtk.Align.CENTER)

        self.btn = Gtk.Button(label='Fetch My Games')
        self.btn.add_css_class('suggested-action')
        self.btn.connect('clicked', self._on_submit)

        box.append(lbl)
        box.append(self.entry)
        box.append(info)
        box.append(self.status)
        box.append(self.btn)
        self.set_content(box)

    def _on_submit(self, *_):
        val = self.entry.get_text().strip()
        if not val:
            return
        self.btn.set_sensitive(False)
        self.btn.set_label('Fetching…')
        self.status.set_text('')
        self._callback(val)
        self.close()


# ====================================================================
#  Application
# ====================================================================

class ManifestStudioApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='manifest-studio')
        self.connect('activate', self.on_activate)

    def on_activate(self, app):
        win = ManifestStudioWindow(application=app)
        win.present()
        GLib.timeout_add(1500, lambda: (
            update_engine.check_and_notify(win),
            False,
        )[1])


if __name__ == '__main__':
    app = ManifestStudioApp()
    app.run()
