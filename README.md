# DebCtrl Terminal

`DebCtrl Terminal` is a small GTK/VTE terminal emulator for Debian that changes one key behavior:

- If text is selected, `Ctrl+C` copies the selection.
- If nothing is selected, `Ctrl+C` is passed through to the shell as the normal interrupt signal.

To make interrupting feel natural after a copy, the selection is cleared right after `Ctrl+C` copies it. That means a second `Ctrl+C` immediately behaves like a normal terminal break.

## Requirements

Install the GTK and VTE bindings that the app uses:

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91
```

## Run It

```bash
chmod +x debctrlterm.py
./debctrlterm.py
```

You can also start a specific command instead of your default shell:

```bash
./debctrlterm.py htop
./debctrlterm.py python3
./debctrlterm.py /bin/bash
```

## Install As A Desktop App

Run the installer:

```bash
chmod +x install.sh
./install.sh
```

That copies the launcher into `~/.local/bin/debctrlterm` and creates a desktop entry in `~/.local/share/applications/debctrlterm.desktop`.

## Built-In Shortcuts

- `Ctrl+C`: copy only when text is selected, otherwise send interrupt to the running process
- `Ctrl+Shift+C`: always copy without clearing the selection
- `Ctrl+Shift+V`: paste from the clipboard

## Notes

This uses the real `Vte.Terminal` widget, so programs like `bash`, `vim`, `htop`, `ssh`, and `top` work like they do in other Linux terminal emulators.
