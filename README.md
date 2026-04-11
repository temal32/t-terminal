# t-terminal

`t-terminal` is a GTK/VTE terminal emulator for Debian with the custom copy behavior you asked for plus the standard features people expect from a desktop terminal app.

- If text is selected, `Ctrl+C` copies the selection.
- If nothing is selected, `Ctrl+C` is passed through to the shell as the normal interrupt signal.

After copying with `Ctrl+C`, the selection is cleared so the next `Ctrl+C` immediately works as a normal interrupt again.

## Included Features

- Real PTY-based terminal behavior via `Vte.Terminal`
- Tabs with close buttons and tab reordering
- New window and new tab actions
- Terminal search bar with next/previous navigation
- Adjustable background opacity with a saved appearance setting
- Saved SSH profiles with direct connect from the header bar
- Zoom in, zoom out, and reset zoom
- Right-click context menu
- Link detection with `Ctrl+Click` to open links
- Select all, copy, paste, terminal reset, and reset + clear scrollback
- Desktop launcher integration

## Requirements

Install the GTK and VTE bindings that the app uses:

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91 openssh-client sshpass
```

## Run It

```bash
chmod +x t_terminal.py
./t_terminal.py
```

You can also start a specific command instead of your default shell:

```bash
./t_terminal.py htop
./t_terminal.py python3
./t_terminal.py -- /bin/bash -lc "echo hello from t-terminal"
```

You can also start in a specific directory:

```bash
./t_terminal.py --working-directory ~/Downloads
```

## Build A Debian Package

Create the `.deb` file:

```bash
chmod +x build-deb.sh
./build-deb.sh
```

That produces `./t-terminal.deb`.

Install it with:

```bash
sudo apt install ./t-terminal.deb
```

For a local `.deb` file, Debian `apt` needs a path such as `./t-terminal.deb` or an absolute path. `sudo apt install t-terminal.deb` without `./` is usually treated as a package name, not a local file.

After installation, `t-terminal` also registers itself as an `x-terminal-emulator` alternative on Debian. Because the package installs with priority `60`, it can become the default terminal automatically if your alternatives system is in auto mode.

You can check or switch the default terminal with:

```bash
update-alternatives --display x-terminal-emulator
sudo update-alternatives --config x-terminal-emulator
```

## Built-In Shortcuts

- `Ctrl+C`: copy only when text is selected, otherwise send interrupt to the running process
- `Ctrl+Shift+C`: always copy without clearing the selection
- `Ctrl+V`: paste from the clipboard
- `Ctrl+Shift+T`: new tab
- `Ctrl+Shift+N`: new window
- `Ctrl+Shift+W`: close tab
- `Ctrl+F`: open search
- `F3` / `Shift+F3`: next or previous search result
- `Ctrl++`, `Ctrl+-`, `Ctrl+0`: zoom in, zoom out, reset zoom
- `Ctrl` + mouse wheel: zoom in or out while keeping normal wheel scrolling
- `Ctrl+PageDown` / `Ctrl+PageUp`: next or previous tab
- `F11`: fullscreen

## Appearance

Open the top-right menu and choose `Appearance` to change the background opacity. `0` makes the terminal background fully transparent while keeping the text visible, and `100` makes it fully opaque. The chosen value is saved in `~/.config/t-terminal/settings.json` and used again the next time you start the terminal.

## SSH Connections

Use the server button in the header bar or the app menu entry `SSH Connections` to manage saved SSH servers. You can add, edit, delete, and connect to profiles with a saved name, host, port, username, and password.

SSH profiles are saved in `~/.config/t-terminal/settings.json` so you can reconnect without typing the credentials again. Passwords are stored there locally in plain text because `t-terminal` uses them for direct `sshpass`-based logins.

## Notes

This uses the real `Vte.Terminal` widget, so programs like `bash`, `vim`, `htop`, `ssh`, `top`, and `tmux` work like they do in other Linux terminal emulators.
