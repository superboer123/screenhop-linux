#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
VERSION=${VERSION:-0.8.3}
ARCH=${ARCH:-all}
OUT="$ROOT/dist"
PKG="$OUT/screenhop-linux_${VERSION}_${ARCH}"
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/usr/lib/screenhop/screenhop" "$PKG/usr/bin" \
  "$PKG/usr/share/applications" "$PKG/usr/share/icons/hicolor/scalable/apps" \
  "$PKG/usr/share/gnome-shell/extensions/screenhop@open-source" "$PKG/etc/xdg/autostart"
cp -a "$ROOT/src/screenhop/." "$PKG/usr/lib/screenhop/screenhop/"
cp "$ROOT/extension/"* "$PKG/usr/share/gnome-shell/extensions/screenhop@open-source/"
cp "$ROOT/data/io.github.screenhop.Linux.desktop" "$PKG/usr/share/applications/"
cp "$ROOT/data/screenhop-autostart.desktop" "$PKG/etc/xdg/autostart/screenhop.desktop"
cp "$ROOT/data/screenhop.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/"
cp "$ROOT/data/style.css" "$PKG/usr/lib/screenhop/"
cat > "$PKG/usr/bin/screenhop-linux" <<'EOF'
#!/bin/sh
export PYTHONPATH=/usr/lib/screenhop
# Remote/debug launches do not always inherit the desktop XWayland variables.
if [ -z "${DISPLAY:-}" ] && [ -S /tmp/.X11-unix/X0 ]; then
    export DISPLAY=:0
fi
if [ -z "${XAUTHORITY:-}" ] && [ -n "${XDG_RUNTIME_DIR:-}" ]; then
    for auth in "$XDG_RUNTIME_DIR"/.mutter-Xwaylandauth.*; do
        [ -f "$auth" ] && { export XAUTHORITY="$auth"; break; }
    done
fi
if [ "${1:-}" = "--background" ]; then
    # Keep the desktop integration available even if a native display/Bluetooth
    # library terminates the Python process. A normal clean exit is respected.
    while :; do
        /usr/bin/python3 -m screenhop.app "$@"
        status=$?
        [ "$status" -eq 0 ] && exit 0
        echo "screenhop-linux: worker exited with status $status; restarting" >&2
        sleep 2
    done
fi
exec /usr/bin/python3 -m screenhop.app "$@"
EOF
cat > "$PKG/usr/bin/screenhopctl" <<'EOF'
#!/bin/sh
export PYTHONPATH=/usr/lib/screenhop
exec /usr/bin/python3 -m screenhop.cli "$@"
EOF
chmod 755 "$PKG/usr/bin/screenhop-linux" "$PKG/usr/bin/screenhopctl"
cat > "$PKG/DEBIAN/control" <<EOF
Package: screenhop-linux
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Depends: python3, python3-gi, python3-dbus, gir1.2-gtk-4.0, gir1.2-adw-1, bluez, gnome-shell, libx11-6
Maintainer: ScreenHop Linux Project
Description: Huawei Mouse WYN keyboard and mouse crossing client for Linux
 Hardware-assisted multi-host pointer edge crossing with a GTK/libadwaita UI.
EOF
cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
exit 0
EOF
chmod 755 "$PKG/DEBIAN/postinst"
mkdir -p "$OUT"
dpkg-deb --build --root-owner-group "$PKG" "$OUT/screenhop-linux_${VERSION}_${ARCH}.deb"
echo "$OUT/screenhop-linux_${VERSION}_${ARCH}.deb"

