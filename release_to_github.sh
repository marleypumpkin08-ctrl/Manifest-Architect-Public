#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh (GitHub CLI) not found in PATH" >&2
  exit 1
fi
if ! command -v nuitka >/dev/null 2>&1; then
  echo "ERROR: nuitka not found in PATH" >&2
  exit 1
fi

if [ ! -f "metadata/version.json" ]; then
  echo "ERROR: metadata/version.json not found" >&2
  exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: not a git repository" >&2
  exit 1
fi

VERSION_JSON="metadata/version.json"
CURRENT_VERSION="$(python3 -c "import json;print(json.load(open('$VERSION_JSON'))['version'])")"

# Increment by 0.1 (supports simple floating versions like 1.1.0)
# If parsing fails, fall back to the first float-like token.
NEW_VERSION="$(python3 - <<'PY'
import json, re
p='${VERSION_JSON}'
with open(p,'r',encoding='utf-8') as f:
    v=json.load(f).get('version','')

m=re.match(r'^(\d+(?:\.\d+)*)', v)
base=v
if m:
    base=m.group(1)

# Use float increment on the first float-compatible prefix.
try:
    new=float(base)+0.1
    # Keep one decimal if base looks like x.y; otherwise keep original string precision heuristically.
    # For typical semantic-ish versions here (e.g. 1.1.0) we treat first two parts as x.y.
    parts=base.split('.')
    if len(parts)>=2 and parts[1].isdigit():
        # if original had at least one decimal part, keep 1 decimal
        s=f"{new:.1f}"
        # Reattach trailing .0 if original ended with .0
        if v.endswith('.0') and not s.endswith('.0'):
            # ensure ends with .0
            pass
        print(s)
    else:
        print(f"{new:.1f}")
except Exception:
    print(v)
PY
)"

# Normalize NEW_VERSION: if it becomes something like "1.2" but file expects "1.2.0", preserve patch if present.
NORMALIZED_VERSION="$(python3 - <<'PY'
import json
v_json='${VERSION_JSON}'
with open(v_json,'r',encoding='utf-8') as f:
    data=json.load(f)
old=data.get('version','')
new='${NEW_VERSION}'

# If old version has 3 dot-separated numeric parts, enforce 3 parts.
parts=old.split('.')
if len(parts)==3 and all(p.isdigit() for p in parts):
    major, minor, patch = parts
    # If new has 2 parts (major.minor), expand patch to 0.
    n_parts=new.split('.')
    if len(n_parts)==2 and all(p.isdigit() for p in n_parts):
        new=f"{n_parts[0]}.{n_parts[1]}.{patch}"
    elif len(n_parts)==1 and n_parts[0].isdigit():
        new=f"{n_parts[0]}.0.{patch}"

print(new)
PY
)"

# Update metadata/version.json
python3 - <<'PY'
import json
p='${VERSION_JSON}'
with open(p,'r',encoding='utf-8') as f:
    data=json.load(f)
old=data.get('version')
data['version']='${NORMALIZED_VERSION}'
with open(p,'w',encoding='utf-8') as f:
    json.dump(data,f,indent=2)
print(old,'->',data['version'])
PY

# Build
mkdir -p builds

# Security lock: build standalone onefile steamtoolpro binary
nuitka --standalone --onefile --remove-output \
  --plugin-enable=pyside6 \
  main.py \
  -o "builds/steamtoolpro"

# Git
git add .
if git diff --cached --quiet; then
  echo "ERROR: no changes staged after version bump" >&2
  exit 1
fi

git commit -m "Release v${NORMALIZED_VERSION} - Subnautica 2 Ready" || true

git push origin main

# Release
TAG="v${NORMALIZED_VERSION}"

# Create tag if missing (gh will create release; we ensure tag exists)
if ! git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
  git tag -a "${TAG}" -m "${TAG}"
  git push origin "${TAG}" || true
fi

# Create release (idempotent)
if gh release view "${TAG}" >/dev/null 2>&1; then
  gh release upload "${TAG}" "builds/steamtoolpro" --clobber
else
  gh release create "${TAG}" --title "${TAG}" --notes "Automated release: ${TAG}" "builds/steamtoolpro"
fi

