#!/bin/sh
set -eu

repo_url="${TOOLBURN_REPO_URL:-https://github.com/DarkExec/ToolBurn.git}"
ref="${TOOLBURN_REF:-main}"

if [ "$(id -u)" = "0" ]; then
  install_dir="${TOOLBURN_INSTALL_DIR:-/opt/toolburn}"
else
  install_dir="${TOOLBURN_INSTALL_DIR:-$HOME/.local/share/toolburn}"
fi

if [ -n "${TOOLBURN_BIN_DIR:-}" ]; then
  bin_dir="$TOOLBURN_BIN_DIR"
elif [ -w /usr/local/bin ] || { [ ! -e /usr/local/bin ] && mkdir -p /usr/local/bin 2>/dev/null; }; then
  bin_dir="/usr/local/bin"
else
  bin_dir="$HOME/.local/bin"
fi

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "toolburn installer needs '$1' on PATH" >&2
    exit 1
  }
}

need git
need python3

mkdir -p "$install_dir" "$bin_dir"

if [ -d "$install_dir/.git" ]; then
  git -C "$install_dir" fetch --quiet origin "$ref"
  git -C "$install_dir" reset --quiet --hard "origin/$ref"
else
  rm -rf "$install_dir"
  git clone --quiet --branch "$ref" "$repo_url" "$install_dir"
fi

cat >"$bin_dir/toolburn" <<EOF
#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$install_dir/src\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m toolburn.cli "\$@"
EOF
chmod 755 "$bin_dir/toolburn"

echo "installed toolburn at $bin_dir/toolburn"
"$bin_dir/toolburn" --help >/dev/null
echo "try: toolburn recent --hours 24"
