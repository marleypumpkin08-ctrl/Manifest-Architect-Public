# -*- mode: python ; coding: utf-8 -*-
#
# Build command:
#   pyinstaller manifest_studio.spec
#
# Output: dist/ManifestStudio  (single-file executable)

import os
import subprocess
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

# ---------- locate GI typelibs ----------
gi_typelib_dir = os.path.join(
    os.path.dirname(gi._overridesdir), '..',
    'lib', 'girepository-1.0',
)
gi_typelib_dir = os.path.normpath(gi_typelib_dir)

# ---------- locate shared libraries ----------
def pkg_libs(name):
    try:
        out = subprocess.check_output(
            ['pkg-config', '--libs-only-l', name],
            text=True,
        ).strip()
        return [f'lib{l}.so*' for l in out.replace('-l', '').split()]
    except subprocess.CalledProcessError:
        return []

def find_so(pattern):
    r = subprocess.run(
        ['find', '/usr/lib', '/usr/lib64', '-name', pattern],
        capture_output=True, text=True,
    )
    return r.stdout.strip().split('\n') if r.stdout.strip() else []

gtk_libs = [
    'gtk4', 'libadwaita-1', 'gdk-pixbuf-2.0', 'pangocairo-1.0',
    'pango-1.0', 'harfbuzz', 'gobject-2.0', 'glib-2.0',
    'gio-2.0', 'gmodule-2.0', 'cairo', 'cairo-gobject',
    'fribidi', 'pixman-1', 'atk-bridge-2.0',
]

extra_binaries = []
for lib in gtk_libs:
    for pat in pkg_libs(lib):
        found = find_so(pat)
        for f in found:
            dest = os.path.basename(os.path.dirname(f))
            extra_binaries.append((f, dest))

# collect GI typelib files
typelib_files = []
for root, dirs, files in os.walk(gi_typelib_dir):
    for fn in files:
        if fn.endswith('.typelib'):
            full = os.path.join(root, fn)
            typelib_files.append((full, os.path.relpath(os.path.dirname(full), os.path.dirname(gi_typelib_dir))))

# collect GI overrides
override_files = []
override_dir = gi._overridesdir
if os.path.isdir(override_dir):
    for fn in os.listdir(override_dir):
        if fn.endswith('.py'):
            override_files.append((os.path.join(override_dir, fn), 'gi/overrides'))

# collect GTK4 icons & schemas
icon_dir = '/usr/share/icons/Adwaita'
schema_dir = '/usr/share/glib-2.0/schemas'

# ---------- Analysis ----------
a = Analysis(
    ['manifest_studio.py'],
    pathex=[],
    binaries=[],
    datas=(
        typelib_files + override_files +
        [(icon_dir, 'share/icons/Adwaita')] +
        [(schema_dir, 'share/glib-2.0/schemas')]
    ),
    hiddenimports=[
        'gi',
        'gi._gi',
        'gi._gi_cairo',
        'gi._option',
        'gi.repository.Gtk',
        'gi.repository.Adw',
        'gi.repository.Gdk',
        'gi.repository.GdkPixbuf',
        'gi.repository.GLib',
        'gi.repository.Gio',
        'gi.repository.GObject',
        'gi.repository.Pango',
        'gi.repository.PangoCairo',
        'gi.repository.HarfBuzz',
        'gi.repository.cairo',
        'gi.repository.HarfBuzz',
        'cairo',
        'steam_injector',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'numpy', 'PIL',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'scipy', 'pandas', 'sympy', 'notebook',
        'jupyter', 'ipython', 'tornado', 'zmq',
    ],
    noarchive=False,
)

# ---------- collect extra binaries ----------
a.binaries += [b for b in extra_binaries if b not in a.binaries]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ManifestStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=True,
    upx_exclude=[],
    name='ManifestStudio',
)
