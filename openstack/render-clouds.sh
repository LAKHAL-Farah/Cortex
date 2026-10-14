#!/usr/bin/env bash
# Generate openstack/clouds.yaml (gitignored) from openstack/clouds.yaml.tpl, taking the
# passwords from infra/.env.prod (also gitignored). Run it again after changing either.
#
#   ./openstack/render-clouds.sh
#   ENV_FILE=/path/to/.env.prod OUT=/path/to/clouds.yaml ./openstack/render-clouds.sh
#
# Placeholders in the template look like "@@NAME@@" and are replaced by the value of NAME
# in the env file, written as a safe YAML string (quotes, backslashes, $, # are fine).
# CLOUDS_FILE_MODE (default 644): the API container runs as uid 10001, not as you, so the
# mounted file must be world-readable unless you chown it to 10001 (then use 600).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"
export TPL="${TPL:-$here/clouds.yaml.tpl}"
export ENV_FILE="${ENV_FILE:-$repo/infra/.env.prod}"
export OUT="${OUT:-$here/clouds.yaml}"
export MODE="${CLOUDS_FILE_MODE:-644}"

command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
[ -f "$TPL" ] || { echo "template not found: $TPL"; exit 1; }
[ -f "$ENV_FILE" ] || { echo "env file not found: $ENV_FILE (copy infra/.env.example to it first)"; exit 1; }

# Refuse to write secrets into a place git would track.
if git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1 && [ "${ALLOW_TRACKED:-0}" != "1" ]; then
  for f in "$OUT" "$ENV_FILE"; do
    case "$f" in "$repo"/*) ;; *) continue ;; esac
    git -C "$repo" check-ignore -q "$f" \
      || { echo "refusing: $f is not gitignored (it would hold secrets). Fix .gitignore or set ALLOW_TRACKED=1."; exit 1; }
    if git -C "$repo" ls-files --error-unmatch "$f" >/dev/null 2>&1; then
      echo "refusing: $f is tracked by git. Run: git rm --cached '$f' (and rotate the passwords if it was ever pushed)."; exit 1
    fi
  done
fi

umask 077
tmp="$(mktemp "${OUT}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
export TMP="$tmp"

python3 - <<'PY'
import json, os, re, sys

def load_env(path):
    env = {}
    for raw in open(path, encoding="utf-8"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, val = line.partition("=")
        if not sep:
            continue
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        env[key] = val
    return env

env = load_env(os.environ["ENV_FILE"])
tpl = open(os.environ["TPL"], encoding="utf-8").read()
pat = re.compile(r'"@@([A-Z0-9_]+)@@"')
names = sorted(set(pat.findall(tpl)))
if not names:
    sys.exit("no \"@@NAME@@\" placeholders in the template")

bad = []
for n in names:
    v = env.get(n, "")
    if not v or v.startswith(("insert-", "replace-", "changeme")):
        bad.append(n)
    elif "\n" in v or "\r" in v:
        bad.append(n + " (contains a newline)")
if bad:
    sys.exit("set these in %s: %s" % (os.environ["ENV_FILE"], ", ".join(bad)))

# json.dumps output is a valid YAML double-quoted scalar.
out = pat.sub(lambda m: json.dumps(env[m.group(1)], ensure_ascii=False), tpl)
open(os.environ["TMP"], "w", encoding="utf-8").write(out)
print("rendered %s with: %s" % (os.environ["OUT"], ", ".join(names)))
PY

chmod "$MODE" "$tmp"
mv -f "$tmp" "$OUT"
trap - EXIT
echo "clouds: $(grep -E '^  [a-z0-9-]+:$' "$OUT" | tr -d ' :' | paste -sd, -)   mode: $MODE"
