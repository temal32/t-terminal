#!/usr/bin/env bash

set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin_dir="${HOME}/.local/bin"
applications_dir="${HOME}/.local/share/applications"
installed_script="${bin_dir}/debctrlterm"
desktop_file="${applications_dir}/debctrlterm.desktop"

mkdir -p "${bin_dir}" "${applications_dir}"
install -m 755 "${project_dir}/debctrlterm.py" "${installed_script}"

cat > "${desktop_file}" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=DebCtrl Terminal
Comment=Terminal emulator with Ctrl+C copy when text is selected
Exec=${installed_script}
Icon=utilities-terminal
Terminal=false
Categories=System;TerminalEmulator;
StartupNotify=true
EOF

printf 'Installed script: %s\n' "${installed_script}"
printf 'Installed launcher: %s\n' "${desktop_file}"
