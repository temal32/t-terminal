#!/usr/bin/env bash

set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
build_dir="${project_dir}/build/package"
package_root="${build_dir}/t-terminal"
output_deb="${project_dir}/t-terminal.deb"

rm -rf "${build_dir}" "${output_deb}"
mkdir -p \
  "${package_root}/DEBIAN" \
  "${package_root}/usr/bin" \
  "${package_root}/usr/share/applications" \
  "${package_root}/usr/share/doc/t-terminal"
chmod 755 \
  "${package_root}" \
  "${package_root}/DEBIAN" \
  "${package_root}/usr" \
  "${package_root}/usr/bin" \
  "${package_root}/usr/share" \
  "${package_root}/usr/share/applications" \
  "${package_root}/usr/share/doc" \
  "${package_root}/usr/share/doc/t-terminal"

install -m 755 "${project_dir}/t_terminal.py" "${package_root}/usr/bin/t-terminal"
install -m 644 "${project_dir}/t-terminal.desktop" "${package_root}/usr/share/applications/t-terminal.desktop"
install -m 644 "${project_dir}/README.md" "${package_root}/usr/share/doc/t-terminal/README.md"

cat > "${package_root}/DEBIAN/control" <<EOF
Package: t-terminal
Version: 1.2.1
Section: utils
Priority: optional
Architecture: all
Maintainer: Temal <temal@localhost>
Depends: python3, python3-gi, gir1.2-gtk-3.0, gir1.2-vte-2.91
Provides: x-terminal-emulator
Description: GTK/VTE terminal emulator with standard desktop features
 t-terminal is a GTK/VTE terminal emulator for Debian systems.
 It includes tabs, search, zoom controls, context menus, desktop integration,
 and a selection-aware Ctrl+C shortcut that copies when text is selected and
 otherwise behaves like the normal terminal interrupt.
EOF

cat > "${package_root}/DEBIAN/postinst" <<EOF
#!/bin/sh
set -e

update-alternatives --install /usr/bin/x-terminal-emulator x-terminal-emulator /usr/bin/t-terminal 60
exit 0
EOF

cat > "${package_root}/DEBIAN/postrm" <<EOF
#!/bin/sh
set -e

case "\$1" in
  remove|purge|disappear)
    update-alternatives --remove x-terminal-emulator /usr/bin/t-terminal || true
    ;;
esac

exit 0
EOF

chmod 755 "${package_root}/DEBIAN/postinst" "${package_root}/DEBIAN/postrm"

dpkg-deb --build --root-owner-group "${package_root}" "${output_deb}"

printf 'Built package: %s\n' "${output_deb}"
printf 'Install with: sudo apt install ./%s\n' "$(basename "${output_deb}")"
