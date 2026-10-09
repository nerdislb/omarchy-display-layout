#!/bin/bash
# Synchronize source files without deleting unrelated installed files.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
id="$(jq -r .id "$here/manifest.json")"
target="$HOME/.config/omarchy/plugins/$id"
monitor="$HOME/.config/omarchy/plugins/nerdibeard.monitor"
mode="${1:-}"
if [[ -n "$mode" && "$mode" != --integrate-monitor ]]; then
  echo 'Usage: dev-install.sh [--integrate-monitor]' >&2
  exit 2
fi
omarchy plugin validate "$here"
if [[ "$mode" == --integrate-monitor ]]; then
  if [[ ! -f "$monitor/manifest.json" ]] || [[ $(jq -r '.id' "$monitor/manifest.json") != nerdibeard.monitor ]]; then
    echo 'Clone omarchy.monitor first, then install the adapter as nerdibeard.monitor.' >&2
    exit 1
  fi
  if jq -e --arg id "$id" 'any(.bar.layout[]?[]?; .id == $id)' "$HOME/.config/omarchy/shell.json" >/dev/null; then
    echo 'Disable the standalone display-layout bar button before installing the embedded adapter.' >&2
    exit 1
  fi
  omarchy plugin validate "$here/integration/monitor"
  backup="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy-display-layout/monitor-backups/$(date +%Y%m%d-%H%M%S)-$$"
  mkdir -p "$backup"
  cp -a "$monitor/." "$backup/"
  echo "Display widget backup: $backup"
fi
mkdir -p "$target"
# Dependencies first, entry point last; each file is replaced atomically.
install_file() {
  local src="$1" dst="$2" temp
  temp=$(mktemp "${dst}.install.XXXXXX")
  cp -p "$src" "$temp"
  mv "$temp" "$dst"
}
for file in Registry.js Model.js display_layout.py Editor.qml manifest.json LICENSE README.md Panel.qml; do
  install_file "$here/$file" "$target/$file"
done
omarchy plugin validate "$target"
if [[ "$mode" == --integrate-monitor ]]; then
  for file in Model.js manifest.json LICENSE Panel.qml; do
    install_file "$here/integration/monitor/$file" "$monitor/$file"
  done
  echo 'Integrated into Display & brightness. Do not enable a second layout button.'
fi
omarchy-shell shell rescanPlugins
printf 'Synced to %s\n' "$target"
