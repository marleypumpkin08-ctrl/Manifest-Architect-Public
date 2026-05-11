#!/usr/bin/env python3

import json
import re
import urllib.request


GAME_DATABASE = {
    "1962700": {
        "name": "Subnautica 2",
        "installdir": "Subnautica 2",
        "size": 50_000_000_000,
        "release_date": "May 14, 2026",
    },
    "302510": {
        "name": "Ryse: Son of Rome",
        "installdir": "Ryse Son of Rome",
        "size": 26_000_000_000,
    },
    "2215430": {
        "name": "Ghost of Tsushima DIRECTOR'S CUT",
        "installdir": "Ghost of Tsushima DIRECTOR'S CUT",
        "size": 75_000_000_000,
    },
    "201270": {
        "name": "Total War: SHOGUN 2",
        "installdir": "total war shogun 2",
        "size": 20_000_000_000,
    },
    "1196590": {
        "name": "Resident Evil Village",
        "installdir": "Resident Evil Village",
        "size": 33_000_000_000,
    },
    "814380": {
        "name": "Sekiro: Shadows Die Twice - GOTY Edition",
        "installdir": "Sekiro Shadows Die Twice",
        "size": 25_000_000_000,
    },
}


def lookup_game(appid):
    return GAME_DATABASE.get(appid)


def scrape_steam_store(appid):
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) "
                       "AppleWebKit/537.36"),
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        entry = data.get(appid, {})
        if entry.get("success"):
            info = entry["data"]
            name = info.get("name", "")
            installdir = re.sub(r'[^a-zA-Z0-9 ]', '', name).strip()
            installdir = re.sub(r'\s+', ' ', installdir)
            size = 0
            for req_info in info.get("pc_requirements", {}).values():
                if isinstance(req_info, dict):
                    m = re.search(
                        r'(\d+)\s*GB',
                        req_info.get("minimum", ""),
                        re.IGNORECASE,
                    )
                    if m:
                        size = int(m.group(1)) * 1_000_000_000
            return {"name": name, "installdir": installdir, "size": size}
    except Exception:
        pass
    return None


def scrape_steamdb(appid):
    url = f"https://steamdb.info/app/{appid}/"
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) "
                       "AppleWebKit/537.36"),
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode()
        m = re.search(r'<title>(.*?) on SteamDB</title>', html)
        if m:
            name = m.group(1).strip()
            installdir = re.sub(r'[^a-zA-Z0-9 ]', '', name).strip()
            installdir = re.sub(r'\s+', ' ', installdir)
            return {"name": name, "installdir": installdir, "size": 0}
    except Exception:
        pass
    return None


def resolve_game(appid, with_scrape=True):
    info = lookup_game(appid)
    if info:
        return info, "local"
    if not with_scrape:
        return None, None
    info = scrape_steam_store(appid)
    if info:
        return info, "steam"
    info = scrape_steamdb(appid)
    if info:
        return info, "steamdb"
    return None, None


def generate_json_template(appid, name, installdir, size=0):
    return json.dumps({
        "appid": appid,
        "name": name,
        "installdir": installdir,
        "size": size,
    }, indent=2)


def generate_lua_template(appid, name, installdir, size=0):
    esc_name = name.replace('"', '\\"')
    esc_dir = installdir.replace('"', '\\"')
    return (
        f'-- Steam manifest for {appid}\n'
        f'-- {name}\n'
        'local manifest = {\n'
        f'    appid = {appid},\n'
        f'    name = "{esc_name}",\n'
        f'    installdir = "{esc_dir}",\n'
        f'    size = {size},\n'
        '}\n'
        'return manifest\n'
    )
