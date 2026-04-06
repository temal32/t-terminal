#!/usr/bin/env python3

import os
import shutil
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")

from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte


APP_ID = "com.temal.DebCtrlTerminal"
APP_TITLE = "DebCtrl Terminal"
APP_SUBTITLE = "Ctrl+C copies a selection, otherwise it interrupts the running program"


class DebCtrlTerminalWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, command: list[str]) -> None:
        super().__init__(application=app)
        self.command = command

        self.set_default_size(1080, 720)
        self.set_title(APP_TITLE)
        self.set_icon_name("utilities-terminal")

        self.header_bar = Gtk.HeaderBar()
        self.header_bar.set_show_close_button(True)
        self.header_bar.set_title(APP_TITLE)
        self.header_bar.set_subtitle(APP_SUBTITLE)
        self.set_titlebar(self.header_bar)

        self.copy_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.BUTTON)
        self.copy_button.set_tooltip_text("Copy selected text")
        self.copy_button.set_sensitive(False)
        self.copy_button.connect("clicked", self.on_copy_button_clicked)
        self.header_bar.pack_start(self.copy_button)

        self.paste_button = Gtk.Button.new_from_icon_name("edit-paste-symbolic", Gtk.IconSize.BUTTON)
        self.paste_button.set_tooltip_text("Paste from clipboard")
        self.paste_button.connect("clicked", self.on_paste_button_clicked)
        self.header_bar.pack_start(self.paste_button)

        self.select_all_button = Gtk.Button.new_from_icon_name("edit-select-all-symbolic", Gtk.IconSize.BUTTON)
        self.select_all_button.set_tooltip_text("Select everything in scrollback")
        self.select_all_button.connect("clicked", self.on_select_all_button_clicked)
        self.header_bar.pack_end(self.select_all_button)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        self.terminal = Vte.Terminal()
        self.terminal.set_font(Pango.FontDescription("Monospace 12"))
        self.terminal.set_scrollback_lines(100_000)
        self.terminal.set_rewrap_on_resize(True)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_scroll_on_output(False)
        self.terminal.set_mouse_autohide(True)
        self.terminal.set_audible_bell(False)
        self.terminal.set_allow_hyperlink(True)
        self.terminal.connect("key-press-event", self.on_terminal_key_press)
        self.terminal.connect("selection-changed", self.on_selection_changed)
        self.terminal.connect("window-title-changed", self.on_window_title_changed)
        self.terminal.connect("child-exited", self.on_child_exited)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self.terminal)
        root.pack_start(scroller, True, True, 0)

        hint_bar = Gtk.ActionBar()
        hint_label = Gtk.Label(
            label="Tip: select text and press Ctrl+C to copy. Without a selection, Ctrl+C behaves like a normal terminal interrupt."
        )
        hint_label.set_xalign(0.0)
        hint_bar.pack_start(hint_label)
        root.pack_end(hint_bar, False, False, 0)

        self.spawn_terminal_child()
        self.show_all()
        self.terminal.grab_focus()

    def spawn_terminal_child(self) -> None:
        argv = self.command if self.command else [os.environ.get("SHELL", "/bin/bash")]
        resolved_executable = self.resolve_executable(argv[0])
        if resolved_executable is None:
            self.show_error(f"Could not find an executable for: {argv[0]}")
            GLib.idle_add(self.close)
            return

        argv = [resolved_executable, *argv[1:]]
        environment = [f"{key}={value}" for key, value in os.environ.items()]
        working_directory = self.get_safe_working_directory()

        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            working_directory,
            argv,
            environment,
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            None,
            -1,
            None,
            None,
            None,
        )

    def get_safe_working_directory(self) -> str:
        current_directory = os.getcwd()
        if os.path.isdir(current_directory) and os.access(current_directory, os.X_OK):
            return current_directory

        return os.path.expanduser("~")

    def resolve_executable(self, command: str) -> str | None:
        if os.path.isabs(command) or "/" in command:
            return command if os.access(command, os.X_OK) else None

        return shutil.which(command)

    def show_error(self, message: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text="DebCtrl Terminal could not start the requested command.",
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def copy_selection(self, clear_selection: bool) -> None:
        if not self.terminal.get_has_selection():
            return

        self.terminal.copy_clipboard_format(Vte.Format.TEXT)
        if clear_selection:
            self.terminal.unselect_all()

    def on_copy_button_clicked(self, _button: Gtk.Button) -> None:
        self.copy_selection(clear_selection=False)
        self.terminal.grab_focus()

    def on_paste_button_clicked(self, _button: Gtk.Button) -> None:
        self.terminal.paste_clipboard()
        self.terminal.grab_focus()

    def on_select_all_button_clicked(self, _button: Gtk.Button) -> None:
        self.terminal.select_all()
        self.terminal.grab_focus()

    def on_selection_changed(self, terminal: Vte.Terminal) -> None:
        self.copy_button.set_sensitive(terminal.get_has_selection())

    def on_window_title_changed(self, terminal: Vte.Terminal) -> None:
        title = terminal.get_window_title() or APP_TITLE
        self.set_title(title)
        self.header_bar.set_title(title)

    def on_child_exited(self, _terminal: Vte.Terminal, _status: int) -> None:
        self.close()

    def on_terminal_key_press(self, _terminal: Vte.Terminal, event: Gdk.EventKey) -> bool:
        masked_state = event.state & Gtk.accelerator_get_default_mod_mask()

        if self.is_plain_ctrl(event, masked_state, Gdk.KEY_c):
            if self.terminal.get_has_selection():
                # Copy first and clear the selection so the next Ctrl+C is an interrupt.
                self.copy_selection(clear_selection=True)
                return True
            return False

        if self.is_ctrl_shift(event, masked_state, Gdk.KEY_c):
            self.copy_selection(clear_selection=False)
            return True

        if self.is_ctrl_shift(event, masked_state, Gdk.KEY_v):
            self.terminal.paste_clipboard()
            return True

        return False

    @staticmethod
    def is_plain_ctrl(event: Gdk.EventKey, state: Gdk.ModifierType, key: int) -> bool:
        return state == Gdk.ModifierType.CONTROL_MASK and event.keyval in (key, Gdk.keyval_to_upper(key))

    @staticmethod
    def is_ctrl_shift(event: Gdk.EventKey, state: Gdk.ModifierType, key: int) -> bool:
        required = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        return state == required and event.keyval in (key, Gdk.keyval_to_upper(key))


class DebCtrlTerminalApp(Gtk.Application):
    def __init__(self, command: list[str]) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.command = command

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = DebCtrlTerminalWindow(self, self.command)
        window.present()


def main() -> int:
    command = sys.argv[1:]
    app = DebCtrlTerminalApp(command)
    return app.run(sys.argv[:1])


if __name__ == "__main__":
    raise SystemExit(main())
