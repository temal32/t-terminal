#!/usr/bin/env python3

import argparse
import json
import os
import pathlib
import pwd
import re
import shutil
import sys
import time
import urllib.parse
import uuid

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")

from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte


APP_ID = "com.temal.tterminal"
APP_NAME = "t-terminal"
APP_VERSION = "1.3.2"
DEFAULT_FONT = "Monospace 12"
URL_PATTERN = r"(?:https?|ftp)://[^\s\"'<>]+|mailto:[^\s\"'<>]+|file://[^\s\"'<>]+"
CONFIG_DIR = pathlib.Path.home() / ".config" / "t-terminal"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
DEFAULT_BACKGROUND_OPACITY = 1.0
DEFAULT_SSH_PORT = 22
COMPACT_CSS = b"""
window.t-terminal-window headerbar {
  min-height: 34px;
  padding-top: 0;
  padding-bottom: 0;
}

window.t-terminal-window button.compact-button {
  padding: 2px;
  min-width: 24px;
  min-height: 24px;
}

window.t-terminal-window notebook header.top tabs tab {
  padding: 2px 6px;
  min-height: 26px;
  margin: 0;
}

window.t-terminal-window notebook header.top tabs tab button {
  padding: 0;
  min-width: 16px;
  min-height: 16px;
}

window.t-terminal-window box.compact-search-box {
  padding: 4px;
}

window.t-terminal-window box.terminal-root,
window.t-terminal-window notebook.terminal-notebook,
window.t-terminal-window notebook.terminal-notebook stack,
window.t-terminal-window scrolledwindow.terminal-scroller,
window.t-terminal-window scrolledwindow.terminal-scroller viewport {
  background-color: transparent;
  background-image: none;
}

window.t-terminal-window eventbox.terminal-surface {
  background-image: none;
}
"""
INTERACTIVE_SHELL_FLAGS = {
    "bash": ["-i"],
    "sh": ["-i"],
    "dash": ["-i"],
    "zsh": ["-i"],
    "ksh": ["-i"],
    "fish": ["-i"],
}


def parse_cli_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="GTK/VTE terminal emulator with selection-aware Ctrl+C copy behavior.",
    )
    parser.add_argument(
        "-d",
        "--working-directory",
        default=None,
        help="Directory to use for the first tab instead of the current directory.",
    )
    parser.add_argument(
        "-e",
        "--execute",
        dest="execute",
        nargs=argparse.REMAINDER,
        help="Run the remaining command inside the first terminal tab.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Optional command to run in the first tab. Use -- before the command if needed.",
    )
    arguments = parser.parse_args(argv)
    if arguments.execute and arguments.execute[0] == "--":
        arguments.execute = arguments.execute[1:]
    if arguments.command and arguments.command[0] == "--":
        arguments.command = arguments.command[1:]
    if arguments.execute:
        arguments.command = arguments.execute
    return arguments


def resolve_executable(command: str) -> str | None:
    if os.path.isabs(command) or "/" in command:
        return command if os.access(command, os.X_OK) else None
    return shutil.which(command)


def get_home_directory() -> str:
    return os.path.expanduser("~")


def get_safe_current_directory() -> str | None:
    try:
        return os.getcwd()
    except FileNotFoundError:
        return None


def resolve_default_shell() -> str:
    shell_candidates = [
        os.environ.get("SHELL"),
        pwd.getpwuid(os.getuid()).pw_shell,
        "/bin/bash",
        "/bin/sh",
    ]

    for candidate in shell_candidates:
        if not candidate:
            continue
        executable = resolve_executable(candidate)
        if executable:
            return executable

    return "/bin/sh"


def build_default_shell_command() -> list[str]:
    shell_path = resolve_default_shell()
    shell_name = os.path.basename(shell_path)
    interactive_flags = INTERACTIVE_SHELL_FLAGS.get(shell_name, [])
    return [shell_path, *interactive_flags]


def build_shell_fallback_commands() -> list[list[str]]:
    fallbacks: list[list[str]] = []
    default_shell = resolve_default_shell()
    if os.path.basename(default_shell) == "bash":
        fallbacks.append([default_shell, "--noprofile", "--norc", "-i"])

    sh_path = resolve_executable("/bin/sh")
    if sh_path:
        fallbacks.append([sh_path, "-i"])

    unique_fallbacks: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in fallbacks:
        key = tuple(candidate)
        if key not in seen:
            seen.add(key)
            unique_fallbacks.append(candidate)
    return unique_fallbacks


def log_debug(message: str) -> None:
    log_dir = pathlib.Path.home() / ".local" / "state"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "t-terminal.log"
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def shorten_title(title: str, length: int = 28) -> str:
    if len(title) <= length:
        return title
    return title[: length - 3] + "..."


def wait_status_to_exit_code(status: int) -> int | None:
    try:
        return os.waitstatus_to_exitcode(status)
    except ValueError:
        return None


def normalize_background_opacity(value: object) -> float:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        numeric_value = DEFAULT_BACKGROUND_OPACITY
    return max(0.0, min(1.0, numeric_value))


def normalize_ssh_port(value: object) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        port = DEFAULT_SSH_PORT
    return max(1, min(65535, port))


def normalize_ssh_profile(raw_profile: object) -> dict[str, object] | None:
    if not isinstance(raw_profile, dict):
        return None

    host = str(raw_profile.get("host", "")).strip()
    if not host:
        return None

    name = str(raw_profile.get("name", "")).strip() or host
    username = str(raw_profile.get("username", "")).strip()
    password = str(raw_profile.get("password", "") or "")

    return {
        "id": str(raw_profile.get("id", "")).strip() or uuid.uuid4().hex,
        "name": name,
        "host": host,
        "port": normalize_ssh_port(raw_profile.get("port", DEFAULT_SSH_PORT)),
        "username": username,
        "password": password,
    }


def load_settings() -> dict[str, object]:
    settings: dict[str, object] = {
        "background_opacity": DEFAULT_BACKGROUND_OPACITY,
        "ssh_profiles": [],
    }
    try:
        if SETTINGS_PATH.exists():
            raw_settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw_settings, dict):
                settings["background_opacity"] = normalize_background_opacity(
                    raw_settings.get("background_opacity", DEFAULT_BACKGROUND_OPACITY)
                )
                raw_profiles = raw_settings.get("ssh_profiles", [])
                if isinstance(raw_profiles, list):
                    settings["ssh_profiles"] = [
                        profile
                        for profile in (normalize_ssh_profile(item) for item in raw_profiles)
                        if profile is not None
                    ]
    except (OSError, ValueError, TypeError) as error:
        log_debug(f"could not load settings: {error}")
    return settings


def save_settings(settings: dict[str, object]) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError as error:
        log_debug(f"could not save settings: {error}")


def install_application_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(COMPACT_CSS)
    screen = Gdk.Screen.get_default()
    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(
            screen,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


class TerminalTab(Gtk.Box):
    def __init__(
        self,
        window: "TTerminalWindow",
        command: list[str] | None = None,
        working_directory: str | None = None,
        session_type: str = "shell",
        session_name: str | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.command = list(command) if command else None
        self.working_directory = working_directory
        self.session_type = session_type
        self.session_name = session_name or ""
        self.title = session_name or "Shell"
        self.default_shell_launch = command is None
        self.fallback_commands = build_shell_fallback_commands() if self.default_shell_launch else []
        self.current_command_description = ""
        self.last_spawn_monotonic = 0.0
        self.base_background_color: Gdk.RGBA | None = None

        self.terminal = Vte.Terminal()
        self.terminal.set_font(Pango.FontDescription(DEFAULT_FONT))
        self.terminal.set_font_scale(self.window.font_scale)
        self.terminal.set_scrollback_lines(200_000)
        self.terminal.set_rewrap_on_resize(True)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_scroll_on_output(False)
        self.terminal.set_mouse_autohide(True)
        self.terminal.set_audible_bell(False)
        self.terminal.set_allow_hyperlink(True)
        self.terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.SYSTEM)
        self.terminal.set_clear_background(False)
        self.terminal.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.SCROLL_MASK)
        self.terminal.connect("key-press-event", self.on_terminal_key_press)
        self.terminal.connect("selection-changed", self.on_selection_changed)
        self.terminal.connect("window-title-changed", self.on_window_title_changed)
        self.terminal.connect("button-press-event", self.on_terminal_button_press)
        self.terminal.connect("scroll-event", self.on_terminal_scroll)
        self.terminal.connect("child-exited", self.on_child_exited)

        self.url_match_tag = self.register_url_match()

        self.background_surface = Gtk.EventBox()
        self.background_surface.set_visible_window(True)
        self.background_surface.get_style_context().add_class("terminal-surface")

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_shadow_type(Gtk.ShadowType.NONE)
        self.scroller.get_style_context().add_class("terminal-scroller")
        self.scroller.add(self.terminal)
        self.background_surface.add(self.scroller)
        self.pack_start(self.background_surface, True, True, 0)

        self.capture_base_background_color()
        self.apply_background_opacity(self.window.background_opacity)

        self.show_all()
        self.spawn()

    def spawn(self) -> None:
        argv = self.command or build_default_shell_command()
        self.current_command_description = " ".join(argv)
        executable = resolve_executable(argv[0])
        if executable is None:
            self.window.show_error_dialog(f"Could not find an executable for: {argv[0]}")
            GLib.idle_add(self.window.close_tab, self)
            return

        argv = [executable, *argv[1:]]
        working_directory = self.get_safe_working_directory(self.working_directory)
        environment = dict(os.environ)
        environment.setdefault("TERM", "xterm-256color")
        environment_list = [f"{key}={value}" for key, value in environment.items()]
        self.last_spawn_monotonic = time.monotonic()
        log_debug(f"spawn shell argv={argv!r} cwd={working_directory!r}")

        try:
            success, _child_pid = self.terminal.spawn_sync(
                Vte.PtyFlags.DEFAULT,
                working_directory,
                argv,
                environment_list,
                GLib.SpawnFlags.DEFAULT,
                None,
                None,
                None,
            )
        except GLib.Error as error:
            self.window.show_error_dialog(f"Could not start the shell process.\n\n{error.message}")
            GLib.idle_add(self.window.close_tab, self)
            return

        if not success:
            self.window.show_error_dialog("Could not start the shell process.")
            GLib.idle_add(self.window.close_tab, self)
            return

        self.update_title(os.path.basename(argv[0]) or "Shell")

    def get_safe_working_directory(self, preferred_directory: str | None) -> str:
        candidates = [preferred_directory, get_safe_current_directory(), get_home_directory()]
        for candidate in candidates:
            if candidate and os.path.isdir(candidate) and os.access(candidate, os.X_OK):
                return os.path.abspath(candidate)
        return get_home_directory()

    def register_url_match(self) -> int:
        regex = Vte.Regex.new_for_match(URL_PATTERN, -1, 0)
        tag = self.terminal.match_add_regex(regex, 0)
        self.terminal.match_set_cursor_name(tag, "pointer")
        return tag

    def capture_base_background_color(self) -> None:
        background = self.terminal.get_color_background_for_draw()
        if background is not None:
            self.base_background_color = background.copy()
            return

        fallback = Gdk.RGBA()
        fallback.parse("rgb(0, 0, 0)")
        self.base_background_color = fallback

    def apply_background_opacity(self, opacity: float) -> None:
        if self.base_background_color is None:
            self.capture_base_background_color()
        if self.base_background_color is None:
            return

        transparent = Gdk.RGBA()
        transparent.parse("rgba(0, 0, 0, 0)")
        self.apply_widget_background(self.scroller, transparent)
        self.apply_widget_background(self.terminal, transparent)

        background = self.base_background_color.copy()
        background.alpha = normalize_background_opacity(opacity)
        self.apply_widget_background(self.background_surface, background)

    def apply_widget_background(self, widget: Gtk.Widget, color: Gdk.RGBA | None) -> None:
        for state in (
            Gtk.StateFlags.NORMAL,
            Gtk.StateFlags.ACTIVE,
            Gtk.StateFlags.PRELIGHT,
            Gtk.StateFlags.SELECTED,
            Gtk.StateFlags.INSENSITIVE,
            Gtk.StateFlags.BACKDROP,
        ):
            widget.override_background_color(state, color)

    def update_title(self, title: str | None) -> None:
        cleaned_title = title.strip() if title else "Shell"
        self.title = cleaned_title or "Shell"
        self.window.update_tab_label(self)
        if self.window.get_active_tab() is self:
            self.window.refresh_window_title()

    def get_display_title(self) -> str:
        return self.title or "Shell"

    def get_terminal_text(self) -> str:
        text, _attributes = self.terminal.get_text(None, None)
        if text:
            return text.replace("\x00", "")

        row_count = self.terminal.get_row_count()
        column_count = self.terminal.get_column_count()
        if row_count <= 0 or column_count <= 0:
            return ""

        text, _attributes = self.terminal.get_text_range(
            0,
            0,
            row_count - 1,
            max(column_count - 1, 0),
            None,
            None,
        )
        return text.replace("\x00", "") if text else ""

    def get_recent_output(self, max_lines: int = 8) -> str:
        text = self.get_terminal_text()
        if not text:
            return ""

        lines = []
        for line in text.splitlines():
            cleaned = line.strip()
            if cleaned:
                lines.append(cleaned)

        if not lines:
            return ""

        return "\n".join(lines[-max_lines:])

    def get_recent_ssh_error_output(self, max_lines: int = 8) -> str:
        lines = [line.strip() for line in self.get_terminal_text().splitlines() if line.strip()]
        if not lines:
            return ""

        ssh_keywords = (
            "ssh:",
            "sshpass:",
            "permission denied",
            "connection refused",
            "connection timed out",
            "could not resolve hostname",
            "host key verification failed",
            "no route to host",
            "connection closed",
            "kex_exchange_identification",
            "operation timed out",
            "connection reset",
            "broken pipe",
        )
        matching_lines = [line for line in lines if any(keyword in line.lower() for keyword in ssh_keywords)]
        relevant_lines = matching_lines if matching_lines else lines
        return "\n".join(relevant_lines[-max_lines:])

    def build_ssh_failure_message(self, exit_code: int | None) -> str:
        display_name = self.session_name or self.get_display_title() or "the SSH server"
        details = self.get_recent_ssh_error_output()
        message = f"Could not connect to {display_name}."
        if details:
            message += f"\n\n{details}"
            if exit_code is not None:
                message += f"\n\nSSH exited with code {exit_code}."
        elif exit_code is not None:
            message += f"\n\nSSH exited with code {exit_code}."
        else:
            message += "\n\nThe SSH process exited unexpectedly."
        return message

    def get_current_working_directory(self) -> str:
        uri = self.terminal.get_current_directory_uri()
        if uri:
            parsed = urllib.parse.urlparse(uri)
            if parsed.scheme == "file":
                return urllib.parse.unquote(parsed.path)
        return self.get_safe_working_directory(self.working_directory)

    def set_font_scale(self, scale: float) -> None:
        self.terminal.set_font_scale(scale)

    def grab_focus_in_terminal(self) -> None:
        self.terminal.grab_focus()

    def copy_selection(self, clear_selection: bool = False) -> bool:
        if not self.terminal.get_has_selection():
            return False
        self.terminal.copy_clipboard_format(Vte.Format.TEXT)
        if clear_selection:
            self.terminal.unselect_all()
        return True

    def paste_clipboard(self) -> None:
        self.terminal.paste_clipboard()

    def select_all(self) -> None:
        self.terminal.select_all()

    def reset_terminal(self, clear_history: bool) -> None:
        self.terminal.reset(True, clear_history)

    def set_search(self, query: str, case_sensitive: bool) -> bool:
        if not query:
            self.terminal.search_set_regex(None, 0)
            return False

        pattern = re.escape(query)
        if not case_sensitive:
            pattern = "(?i)" + pattern

        try:
            regex = Vte.Regex.new_for_search(pattern, -1, 0)
        except GLib.Error:
            return False

        self.terminal.search_set_wrap_around(True)
        self.terminal.search_set_regex(regex, 0)
        return self.terminal.search_find_next()

    def find_next(self) -> bool:
        return self.terminal.search_find_next()

    def find_previous(self) -> bool:
        return self.terminal.search_find_previous()

    def get_selected_text(self) -> str:
        return self.terminal.get_text_selected(Vte.Format.TEXT) or ""

    def get_url_for_event(self, event: Gdk.EventButton) -> str | None:
        hyperlink = self.terminal.hyperlink_check_event(event)
        if hyperlink:
            return hyperlink

        match_result = self.terminal.match_check_event(event)
        if isinstance(match_result, tuple):
            url = match_result[0]
        else:
            url = match_result
        return url or None

    def open_url_for_event(self, event: Gdk.EventButton) -> bool:
        url = self.get_url_for_event(event)
        if not url:
            return False
        Gtk.show_uri_on_window(self.window, url, event.time)
        return True

    def show_context_menu(self, event: Gdk.EventButton) -> None:
        menu = Gtk.Menu()
        url = self.get_url_for_event(event)

        if url:
            open_link = Gtk.MenuItem(label="Open Link")
            open_link.connect("activate", lambda *_: Gtk.show_uri_on_window(self.window, url, event.time))
            menu.append(open_link)

            copy_link = Gtk.MenuItem(label="Copy Link")
            copy_link.connect("activate", lambda *_: self.window.copy_text_to_clipboard(url))
            menu.append(copy_link)

            menu.append(Gtk.SeparatorMenuItem())

        copy_item = Gtk.MenuItem(label="Copy")
        copy_item.set_sensitive(self.terminal.get_has_selection())
        copy_item.connect("activate", lambda *_: self.copy_selection(clear_selection=False))
        menu.append(copy_item)

        paste_item = Gtk.MenuItem(label="Paste")
        paste_item.connect("activate", lambda *_: self.paste_clipboard())
        menu.append(paste_item)

        select_all_item = Gtk.MenuItem(label="Select All")
        select_all_item.connect("activate", lambda *_: self.select_all())
        menu.append(select_all_item)

        menu.append(Gtk.SeparatorMenuItem())

        new_tab_item = Gtk.MenuItem(label="New Tab")
        new_tab_item.connect("activate", lambda *_: self.window.create_tab())
        menu.append(new_tab_item)

        close_tab_item = Gtk.MenuItem(label="Close Tab")
        close_tab_item.connect("activate", lambda *_: self.window.close_tab(self))
        menu.append(close_tab_item)

        menu.show_all()
        menu.popup_at_pointer(event)

    def on_selection_changed(self, _terminal: Vte.Terminal) -> None:
        if self.window.get_active_tab() is self:
            self.window.update_action_sensitivity()

    def on_window_title_changed(self, terminal: Vte.Terminal) -> None:
        self.update_title(terminal.get_window_title() or self.get_display_title())

    def on_child_exited(self, _terminal: Vte.Terminal, _status: int) -> None:
        runtime_seconds = time.monotonic() - self.last_spawn_monotonic if self.last_spawn_monotonic else 0.0
        exit_code = wait_status_to_exit_code(_status)
        log_debug(
            f"child exited status={_status} exit_code={exit_code!r} runtime_seconds={runtime_seconds:.3f} "
            f"command={self.current_command_description!r}"
        )

        if self.default_shell_launch and runtime_seconds < 1.5 and self.fallback_commands:
            self.command = self.fallback_commands.pop(0)
            log_debug(f"retrying with fallback shell command={self.command!r}")
            self.spawn()
            return

        if self.default_shell_launch and runtime_seconds < 1.5:
            self.window.show_error_dialog(
                "The terminal shell exited immediately after startup.\n\n"
                "A debug log was written to ~/.local/state/t-terminal.log."
            )

        if self.session_type == "ssh" and exit_code not in (None, 0):
            self.window.show_error_dialog(self.build_ssh_failure_message(exit_code))

        self.window.close_tab(self)

    def on_terminal_button_press(self, _terminal: Vte.Terminal, event: Gdk.EventButton) -> bool:
        if event.button == Gdk.BUTTON_PRIMARY and event.state & Gdk.ModifierType.CONTROL_MASK:
            return self.open_url_for_event(event)

        if event.button == Gdk.BUTTON_SECONDARY:
            self.show_context_menu(event)
            return True

        return False

    def on_terminal_scroll(self, _terminal: Vte.Terminal, event: Gdk.EventScroll) -> bool:
        masked_state = event.state & Gtk.accelerator_get_default_mod_mask()
        if masked_state != Gdk.ModifierType.CONTROL_MASK:
            return False

        if event.direction == Gdk.ScrollDirection.UP:
            self.window.set_font_scale(self.window.font_scale + 0.1)
            return True

        if event.direction == Gdk.ScrollDirection.DOWN:
            self.window.set_font_scale(self.window.font_scale - 0.1)
            return True

        if event.direction == Gdk.ScrollDirection.SMOOTH:
            if event.delta_y < 0:
                self.window.set_font_scale(self.window.font_scale + 0.1)
                return True
            if event.delta_y > 0:
                self.window.set_font_scale(self.window.font_scale - 0.1)
                return True

        return False

    def on_terminal_key_press(self, _terminal: Vte.Terminal, event: Gdk.EventKey) -> bool:
        masked_state = event.state & Gtk.accelerator_get_default_mod_mask()

        if masked_state == Gdk.ModifierType.CONTROL_MASK and event.keyval in (Gdk.KEY_v, Gdk.KEY_V):
            self.window.action_paste(None, None)
            return True

        if masked_state == Gdk.ModifierType.CONTROL_MASK and event.keyval in (Gdk.KEY_f, Gdk.KEY_F):
            self.window.action_find(None, None)
            return True

        if masked_state == Gdk.ModifierType.CONTROL_MASK and event.keyval in (Gdk.KEY_c, Gdk.KEY_C):
            if self.copy_selection(clear_selection=True):
                return True
        return False


class TTerminalWindow(Gtk.ApplicationWindow):
    def __init__(
        self,
        app: "TTerminalApp",
        startup_command: list[str] | None = None,
        startup_directory: str | None = None,
    ) -> None:
        super().__init__(application=app)
        self.startup_directory = startup_directory
        self.settings = load_settings()
        self.background_opacity = normalize_background_opacity(self.settings["background_opacity"])
        self.font_scale = 1.0
        self.is_window_fullscreen = False
        self.tab_labels: dict[TerminalTab, Gtk.Label] = {}
        self.appearance_dialog: Gtk.Dialog | None = None
        self.opacity_scale: Gtk.Scale | None = None
        self.ssh_manager_dialog: Gtk.Dialog | None = None
        self.ssh_profile_list: Gtk.ListBox | None = None
        self.ssh_empty_label: Gtk.Label | None = None
        self.ssh_connect_button: Gtk.Button | None = None
        self.ssh_edit_button: Gtk.Button | None = None
        self.ssh_delete_button: Gtk.Button | None = None

        self.set_default_size(1180, 780)
        self.set_title(APP_NAME)
        self.set_icon_name("utilities-terminal")
        self.enable_rgba_visual_if_available()
        self.connect("window-state-event", self.on_window_state_event)
        self.get_style_context().add_class("t-terminal-window")

        self.create_actions()
        self.build_ui()
        self.create_tab(command=startup_command, working_directory=startup_directory)
        self.show_all()
        self.search_bar.set_search_mode(False)
        self.refresh_window_title()
        self.update_action_sensitivity()

    def build_ui(self) -> None:
        self.header_bar = Gtk.HeaderBar()
        self.header_bar.set_show_close_button(True)
        self.header_bar.set_has_subtitle(False)
        self.header_title_label = Gtk.Label(label=APP_NAME)
        self.header_title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.header_bar.set_custom_title(self.header_title_label)
        self.set_titlebar(self.header_bar)

        self.new_tab_button = Gtk.Button.new_from_icon_name("list-add-symbolic", Gtk.IconSize.MENU)
        self.new_tab_button.set_tooltip_text("Open a new tab")
        self.new_tab_button.set_relief(Gtk.ReliefStyle.NONE)
        self.new_tab_button.get_style_context().add_class("compact-button")
        self.new_tab_button.connect("clicked", lambda *_: self.create_tab())
        self.header_bar.pack_start(self.new_tab_button)

        self.ssh_button = Gtk.Button.new_from_icon_name("network-server-symbolic", Gtk.IconSize.MENU)
        self.ssh_button.set_tooltip_text("Manage saved SSH connections")
        self.ssh_button.set_relief(Gtk.ReliefStyle.NONE)
        self.ssh_button.get_style_context().add_class("compact-button")
        self.ssh_button.connect("clicked", lambda *_: self.show_ssh_manager_dialog())
        self.header_bar.pack_start(self.ssh_button)

        self.search_toggle = Gtk.ToggleButton()
        self.search_toggle.add(Gtk.Image.new_from_icon_name("edit-find-symbolic", Gtk.IconSize.MENU))
        self.search_toggle.set_tooltip_text("Show the search bar")
        self.search_toggle.set_relief(Gtk.ReliefStyle.NONE)
        self.search_toggle.get_style_context().add_class("compact-button")
        self.search_toggle.connect("toggled", self.on_search_toggle_toggled)
        self.header_bar.pack_end(self.search_toggle)

        self.menu_button = Gtk.MenuButton()
        self.menu_button.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.MENU))
        self.menu_button.set_relief(Gtk.ReliefStyle.NONE)
        self.menu_button.get_style_context().add_class("compact-button")
        self.menu_button.set_menu_model(self.build_app_menu())
        self.header_bar.pack_end(self.menu_button)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.get_style_context().add_class("terminal-root")
        self.add(root)

        self.search_bar = Gtk.SearchBar()
        self.search_bar.connect("notify::search-mode-enabled", self.on_search_mode_changed)
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        search_box.set_border_width(4)
        search_box.get_style_context().add_class("compact-search-box")

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search in terminal output")
        self.search_entry.connect("search-changed", self.on_search_changed)
        self.search_entry.connect("activate", lambda *_: self.find_next())
        self.search_bar.connect_entry(self.search_entry)
        search_box.pack_start(self.search_entry, True, True, 0)

        self.case_sensitive_toggle = Gtk.CheckButton(label="Case sensitive")
        self.case_sensitive_toggle.connect("toggled", self.on_search_changed)
        search_box.pack_start(self.case_sensitive_toggle, False, False, 0)

        previous_button = Gtk.Button.new_from_icon_name("go-up-symbolic", Gtk.IconSize.BUTTON)
        previous_button.set_tooltip_text("Find previous match")
        previous_button.set_relief(Gtk.ReliefStyle.NONE)
        previous_button.get_style_context().add_class("compact-button")
        previous_button.connect("clicked", lambda *_: self.find_previous())
        search_box.pack_start(previous_button, False, False, 0)

        next_button = Gtk.Button.new_from_icon_name("go-down-symbolic", Gtk.IconSize.BUTTON)
        next_button.set_tooltip_text("Find next match")
        next_button.set_relief(Gtk.ReliefStyle.NONE)
        next_button.get_style_context().add_class("compact-button")
        next_button.connect("clicked", lambda *_: self.find_next())
        search_box.pack_start(next_button, False, False, 0)

        close_search_button = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON)
        close_search_button.set_tooltip_text("Hide the search bar")
        close_search_button.set_relief(Gtk.ReliefStyle.NONE)
        close_search_button.get_style_context().add_class("compact-button")
        close_search_button.connect("clicked", lambda *_: self.search_bar.set_search_mode(False))
        search_box.pack_start(close_search_button, False, False, 0)

        self.search_bar.add(search_box)
        root.pack_start(self.search_bar, False, False, 0)

        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.set_group_name(APP_ID)
        self.notebook.popup_enable()
        self.notebook.get_style_context().add_class("terminal-notebook")
        self.notebook.connect("switch-page", self.on_switch_page)
        root.pack_start(self.notebook, True, True, 0)

    def create_actions(self) -> None:
        actions = {
            "new-tab": self.action_new_tab,
            "new-window": self.action_new_window,
            "close-tab": self.action_close_tab,
            "copy": self.action_copy,
            "paste": self.action_paste,
            "select-all": self.action_select_all,
            "find": self.action_find,
            "find-next": self.action_find_next,
            "find-previous": self.action_find_previous,
            "show-appearance": self.action_show_appearance,
            "show-ssh-manager": self.action_show_ssh_manager,
            "zoom-in": self.action_zoom_in,
            "zoom-out": self.action_zoom_out,
            "zoom-reset": self.action_zoom_reset,
            "next-tab": self.action_next_tab,
            "previous-tab": self.action_previous_tab,
            "reset-terminal": self.action_reset_terminal,
            "reset-and-clear": self.action_reset_and_clear,
            "toggle-fullscreen": self.action_toggle_fullscreen,
        }

        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def build_app_menu(self) -> Gio.Menu:
        menu = Gio.Menu()

        file_section = Gio.Menu()
        file_section.append("New Tab", "win.new-tab")
        file_section.append("New Window", "win.new-window")
        file_section.append("SSH Connections", "win.show-ssh-manager")
        file_section.append("Close Tab", "win.close-tab")
        menu.append_section(None, file_section)

        edit_section = Gio.Menu()
        edit_section.append("Copy", "win.copy")
        edit_section.append("Paste", "win.paste")
        edit_section.append("Select All", "win.select-all")
        edit_section.append("Find", "win.find")
        edit_section.append("Find Next", "win.find-next")
        edit_section.append("Find Previous", "win.find-previous")
        menu.append_section(None, edit_section)

        view_section = Gio.Menu()
        view_section.append("Appearance", "win.show-appearance")
        view_section.append("Zoom In", "win.zoom-in")
        view_section.append("Zoom Out", "win.zoom-out")
        view_section.append("Reset Zoom", "win.zoom-reset")
        view_section.append("Fullscreen", "win.toggle-fullscreen")
        menu.append_section(None, view_section)

        terminal_section = Gio.Menu()
        terminal_section.append("Reset Terminal", "win.reset-terminal")
        terminal_section.append("Reset And Clear Scrollback", "win.reset-and-clear")
        menu.append_section(None, terminal_section)

        return menu

    def get_default_working_directory(self) -> str:
        return self.startup_directory or get_home_directory()

    def get_ssh_profiles(self) -> list[dict[str, object]]:
        profiles = self.settings.get("ssh_profiles")
        if isinstance(profiles, list):
            return profiles
        self.settings["ssh_profiles"] = []
        return self.settings["ssh_profiles"]

    def enable_rgba_visual_if_available(self) -> None:
        screen = Gdk.Screen.get_default()
        if screen is None or not screen.is_composited():
            return
        rgba_visual = screen.get_rgba_visual()
        if rgba_visual is not None:
            self.set_app_paintable(True)
            self.set_visual(rgba_visual)

    def create_tab(
        self,
        command: list[str] | None = None,
        working_directory: str | None = None,
        session_type: str = "shell",
        session_name: str | None = None,
    ) -> TerminalTab:
        directory = working_directory or self.get_active_working_directory() or self.get_default_working_directory()
        tab = TerminalTab(
            self,
            command=command,
            working_directory=directory,
            session_type=session_type,
            session_name=session_name,
        )

        tab_label = self.build_tab_label(tab)
        page_index = self.notebook.append_page(tab, tab_label)
        self.notebook.set_tab_reorderable(tab, True)
        self.notebook.set_tab_detachable(tab, True)
        self.notebook.set_current_page(page_index)
        self.notebook.set_show_tabs(self.notebook.get_n_pages() > 1)
        self.notebook.show_all()
        tab.grab_focus_in_terminal()
        return tab

    def build_tab_label(self, tab: TerminalTab) -> Gtk.Box:
        label = Gtk.Label(label=shorten_title(tab.get_display_title()))
        label.set_tooltip_text(tab.get_display_title())
        close_button = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        close_button.set_relief(Gtk.ReliefStyle.NONE)
        close_button.set_focus_on_click(False)
        close_button.set_tooltip_text("Close tab")
        close_button.get_style_context().add_class("compact-button")
        close_button.connect("clicked", lambda *_: self.close_tab(tab))

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.pack_start(label, True, True, 0)
        box.pack_start(close_button, False, False, 0)
        box.show_all()

        self.tab_labels[tab] = label
        return box

    def update_tab_label(self, tab: TerminalTab) -> None:
        label = self.tab_labels.get(tab)
        if label is None:
            return
        title = tab.get_display_title()
        label.set_text(shorten_title(title))
        label.set_tooltip_text(title)

    def close_tab(self, tab: TerminalTab) -> None:
        page = self.notebook.page_num(tab)
        if page == -1:
            return

        self.tab_labels.pop(tab, None)
        self.notebook.remove_page(page)
        self.notebook.set_show_tabs(self.notebook.get_n_pages() > 1)

        if self.notebook.get_n_pages() == 0:
            self.close()
            return

        active_tab = self.get_active_tab()
        if active_tab:
            active_tab.grab_focus_in_terminal()
        self.refresh_window_title()
        self.update_action_sensitivity()

    def get_active_tab(self) -> TerminalTab | None:
        page = self.notebook.get_current_page()
        if page < 0:
            return None
        widget = self.notebook.get_nth_page(page)
        return widget if isinstance(widget, TerminalTab) else None

    def get_active_working_directory(self) -> str | None:
        active_tab = self.get_active_tab()
        if active_tab:
            return active_tab.get_current_working_directory()
        return self.startup_directory

    def refresh_window_title(self) -> None:
        active_tab = self.get_active_tab()
        title = active_tab.get_display_title() if active_tab else APP_NAME
        self.set_title(f"{title} - {APP_NAME}")
        self.header_title_label.set_text(shorten_title(title, 42))

    def update_action_sensitivity(self) -> None:
        active_tab = self.get_active_tab()
        has_selection = bool(active_tab and active_tab.terminal.get_has_selection())
        for name in ("copy", "paste", "select-all", "find", "find-next", "find-previous", "close-tab", "show-appearance"):
            self.lookup_action(name).set_enabled(active_tab is not None)
        self.lookup_action("copy").set_enabled(has_selection)

    def copy_text_to_clipboard(self, text: str) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        clipboard.store()

    def show_error_dialog(self, message: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=f"{APP_NAME} could not complete the requested action.",
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def apply_search(self) -> None:
        active_tab = self.get_active_tab()
        if not active_tab:
            return

        query = self.search_entry.get_text()
        case_sensitive = self.case_sensitive_toggle.get_active()
        active_tab.set_search(query, case_sensitive)

    def find_next(self) -> None:
        active_tab = self.get_active_tab()
        if not active_tab:
            return
        if self.search_entry.get_text():
            active_tab.set_search(self.search_entry.get_text(), self.case_sensitive_toggle.get_active())
        active_tab.find_next()

    def find_previous(self) -> None:
        active_tab = self.get_active_tab()
        if not active_tab:
            return
        if self.search_entry.get_text():
            active_tab.set_search(self.search_entry.get_text(), self.case_sensitive_toggle.get_active())
        active_tab.find_previous()

    def set_font_scale(self, scale: float) -> None:
        self.font_scale = max(0.5, min(3.0, scale))
        for page_index in range(self.notebook.get_n_pages()):
            tab = self.notebook.get_nth_page(page_index)
            if isinstance(tab, TerminalTab):
                tab.set_font_scale(self.font_scale)

    def set_background_opacity(self, opacity: float, persist: bool = True) -> None:
        self.background_opacity = normalize_background_opacity(opacity)
        for page_index in range(self.notebook.get_n_pages()):
            tab = self.notebook.get_nth_page(page_index)
            if isinstance(tab, TerminalTab):
                tab.apply_background_opacity(self.background_opacity)

        if persist:
            self.settings["background_opacity"] = self.background_opacity
            save_settings(self.settings)

    def show_appearance_dialog(self) -> None:
        if self.appearance_dialog is not None:
            self.appearance_dialog.present()
            return

        dialog = Gtk.Dialog(title="Appearance", transient_for=self, flags=Gtk.DialogFlags.MODAL)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(360, -1)
        content_area = dialog.get_content_area()
        content_area.set_border_width(12)

        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        content_area.add(grid)

        title_label = Gtk.Label(label="Background opacity")
        title_label.set_xalign(0.0)
        grid.attach(title_label, 0, 0, 1, 1)

        opacity_adjustment = Gtk.Adjustment(
            value=round(self.background_opacity * 100),
            lower=0,
            upper=100,
            step_increment=1,
            page_increment=5,
            page_size=0,
        )
        opacity_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=opacity_adjustment)
        opacity_scale.set_digits(0)
        opacity_scale.set_draw_value(True)
        opacity_scale.set_hexpand(True)
        opacity_scale.connect("value-changed", self.on_opacity_scale_changed)
        grid.attach(opacity_scale, 0, 1, 1, 1)

        hint_label = Gtk.Label(label="0 means fully transparent background, 100 means fully opaque.")
        hint_label.set_xalign(0.0)
        grid.attach(hint_label, 0, 2, 1, 1)

        dialog.connect("response", self.on_appearance_dialog_response)
        dialog.show_all()

        self.appearance_dialog = dialog
        self.opacity_scale = opacity_scale

    def on_opacity_scale_changed(self, scale: Gtk.Scale) -> None:
        self.set_background_opacity(scale.get_value() / 100.0)

    def on_appearance_dialog_response(self, dialog: Gtk.Dialog, _response: int) -> None:
        dialog.destroy()
        self.appearance_dialog = None
        self.opacity_scale = None

    def show_ssh_manager_dialog(self) -> None:
        if self.ssh_manager_dialog is not None:
            self.populate_ssh_profile_list()
            self.ssh_manager_dialog.present()
            return

        dialog = Gtk.Dialog(title="SSH Connections", transient_for=self, flags=Gtk.DialogFlags.MODAL)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(560, 420)
        content_area = dialog.get_content_area()
        content_area.set_border_width(12)

        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content_area.add(container)

        hint_label = Gtk.Label(
            label=(
                "Save SSH servers and connect without re-entering the username or password.\n"
                "Passwords are stored locally in ~/.config/t-terminal/settings.json."
            )
        )
        hint_label.set_xalign(0.0)
        hint_label.set_line_wrap(True)
        container.pack_start(hint_label, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)

        profile_list = Gtk.ListBox()
        profile_list.set_activate_on_single_click(False)
        profile_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        profile_list.connect("row-selected", self.on_ssh_profile_selected)
        profile_list.connect("row-activated", self.on_ssh_profile_row_activated)
        scroller.add(profile_list)
        container.pack_start(scroller, True, True, 0)

        empty_label = Gtk.Label(label="No saved SSH connections yet.")
        empty_label.set_xalign(0.0)
        container.pack_start(empty_label, False, False, 0)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        add_button = Gtk.Button(label="Add")
        add_button.connect("clicked", lambda *_: self.add_ssh_profile())
        button_row.pack_start(add_button, False, False, 0)

        edit_button = Gtk.Button(label="Edit")
        edit_button.connect("clicked", lambda *_: self.edit_selected_ssh_profile())
        button_row.pack_start(edit_button, False, False, 0)

        delete_button = Gtk.Button(label="Delete")
        delete_button.connect("clicked", lambda *_: self.delete_selected_ssh_profile())
        button_row.pack_start(delete_button, False, False, 0)

        spacer = Gtk.Box()
        button_row.pack_start(spacer, True, True, 0)

        connect_button = Gtk.Button(label="Connect")
        connect_button.get_style_context().add_class("suggested-action")
        connect_button.connect("clicked", lambda *_: self.connect_selected_ssh_profile())
        button_row.pack_start(connect_button, False, False, 0)

        container.pack_start(button_row, False, False, 0)

        dialog.connect("response", self.on_ssh_manager_dialog_response)
        dialog.show_all()

        self.ssh_manager_dialog = dialog
        self.ssh_profile_list = profile_list
        self.ssh_empty_label = empty_label
        self.ssh_connect_button = connect_button
        self.ssh_edit_button = edit_button
        self.ssh_delete_button = delete_button

        self.populate_ssh_profile_list()

    def on_ssh_manager_dialog_response(self, dialog: Gtk.Dialog, _response: int) -> None:
        dialog.destroy()
        self.ssh_manager_dialog = None
        self.ssh_profile_list = None
        self.ssh_empty_label = None
        self.ssh_connect_button = None
        self.ssh_edit_button = None
        self.ssh_delete_button = None

    def populate_ssh_profile_list(self, selected_profile_id: str | None = None) -> None:
        if self.ssh_profile_list is None:
            return

        for child in list(self.ssh_profile_list.get_children()):
            self.ssh_profile_list.remove(child)

        profiles = self.get_ssh_profiles()
        for profile in profiles:
            row = Gtk.ListBoxRow()
            row.profile = profile

            name = str(profile.get("name", "SSH"))
            username = str(profile.get("username", "")).strip()
            host = str(profile.get("host", "")).strip()
            port = normalize_ssh_port(profile.get("port", DEFAULT_SSH_PORT))
            target = f"{username}@{host}" if username else host

            row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            row_box.set_border_width(8)

            name_label = Gtk.Label(label=name)
            name_label.set_xalign(0.0)
            row_box.pack_start(name_label, False, False, 0)

            detail_label = Gtk.Label(label=f"{target}:{port}")
            detail_label.set_xalign(0.0)
            detail_label.get_style_context().add_class("dim-label")
            row_box.pack_start(detail_label, False, False, 0)

            row.add(row_box)
            self.ssh_profile_list.add(row)

            if selected_profile_id and str(profile.get("id")) == selected_profile_id:
                self.ssh_profile_list.select_row(row)

        self.ssh_profile_list.show_all()

        if self.ssh_empty_label is not None:
            self.ssh_empty_label.set_visible(not profiles)

        if selected_profile_id is None and profiles and self.ssh_profile_list.get_selected_row() is None:
            first_row = self.ssh_profile_list.get_row_at_index(0)
            if first_row is not None:
                self.ssh_profile_list.select_row(first_row)

        self.update_ssh_profile_actions()

    def update_ssh_profile_actions(self) -> None:
        has_selection = self.get_selected_ssh_profile() is not None
        for button in (self.ssh_connect_button, self.ssh_edit_button, self.ssh_delete_button):
            if button is not None:
                button.set_sensitive(has_selection)

    def get_selected_ssh_profile(self) -> dict[str, object] | None:
        if self.ssh_profile_list is None:
            return None
        row = self.ssh_profile_list.get_selected_row()
        if row is None:
            return None
        return getattr(row, "profile", None)

    def on_ssh_profile_selected(self, _listbox: Gtk.ListBox, _row: Gtk.ListBoxRow | None) -> None:
        self.update_ssh_profile_actions()

    def on_ssh_profile_row_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        profile = getattr(row, "profile", None)
        if isinstance(profile, dict):
            self.connect_ssh_profile(profile)

    def show_ssh_profile_editor(self, existing_profile: dict[str, object] | None = None) -> dict[str, object] | None:
        dialog_title = "Add SSH Connection" if existing_profile is None else "Edit SSH Connection"
        dialog = Gtk.Dialog(title=dialog_title, transient_for=self, flags=Gtk.DialogFlags.MODAL)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.set_default_size(420, -1)

        content_area = dialog.get_content_area()
        content_area.set_border_width(12)

        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        content_area.add(grid)

        def add_labeled_widget(row_index: int, label_text: str, widget: Gtk.Widget) -> None:
            label = Gtk.Label(label=label_text)
            label.set_xalign(0.0)
            grid.attach(label, 0, row_index, 1, 1)
            grid.attach(widget, 1, row_index, 1, 1)

        name_entry = Gtk.Entry()
        name_entry.set_text(str(existing_profile.get("name", "")) if existing_profile else "")
        name_entry.set_activates_default(True)
        add_labeled_widget(0, "Name", name_entry)

        host_entry = Gtk.Entry()
        host_entry.set_text(str(existing_profile.get("host", "")) if existing_profile else "")
        host_entry.set_activates_default(True)
        add_labeled_widget(1, "Host", host_entry)

        port_adjustment = Gtk.Adjustment(
            value=float(normalize_ssh_port(existing_profile.get("port", DEFAULT_SSH_PORT)) if existing_profile else DEFAULT_SSH_PORT),
            lower=1,
            upper=65535,
            step_increment=1,
            page_increment=10,
            page_size=0,
        )
        port_spin = Gtk.SpinButton(adjustment=port_adjustment, climb_rate=1, digits=0)
        add_labeled_widget(2, "Port", port_spin)

        username_entry = Gtk.Entry()
        username_entry.set_text(str(existing_profile.get("username", "")) if existing_profile else "")
        username_entry.set_activates_default(True)
        add_labeled_widget(3, "Username", username_entry)

        password_entry = Gtk.Entry()
        password_entry.set_text(str(existing_profile.get("password", "")) if existing_profile else "")
        password_entry.set_visibility(False)
        password_entry.set_activates_default(True)
        if hasattr(password_entry, "set_input_purpose"):
            password_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        add_labeled_widget(4, "Password", password_entry)

        show_password = Gtk.CheckButton(label="Show password")
        show_password.connect("toggled", lambda button: password_entry.set_visibility(button.get_active()))
        grid.attach(show_password, 1, 5, 1, 1)

        note_label = Gtk.Label(
            label="Saved passwords are stored locally in ~/.config/t-terminal/settings.json."
        )
        note_label.set_xalign(0.0)
        note_label.set_line_wrap(True)
        grid.attach(note_label, 0, 6, 2, 1)

        dialog.show_all()

        while True:
            response = dialog.run()
            if response != Gtk.ResponseType.OK:
                dialog.destroy()
                return None

            host = host_entry.get_text().strip()
            if not host:
                self.show_error_dialog("Host is required for an SSH connection.")
                continue

            name = name_entry.get_text().strip() or host
            profile = normalize_ssh_profile(
                {
                    "id": str(existing_profile.get("id")) if existing_profile else uuid.uuid4().hex,
                    "name": name,
                    "host": host,
                    "port": port_spin.get_value_as_int(),
                    "username": username_entry.get_text().strip(),
                    "password": password_entry.get_text(),
                }
            )
            dialog.destroy()
            return profile

    def add_ssh_profile(self) -> None:
        profile = self.show_ssh_profile_editor()
        if profile is None:
            return
        self.get_ssh_profiles().append(profile)
        save_settings(self.settings)
        self.populate_ssh_profile_list(str(profile.get("id")))

    def edit_selected_ssh_profile(self) -> None:
        selected_profile = self.get_selected_ssh_profile()
        if selected_profile is None:
            return

        updated_profile = self.show_ssh_profile_editor(selected_profile)
        if updated_profile is None:
            return

        profiles = self.get_ssh_profiles()
        for index, profile in enumerate(profiles):
            if str(profile.get("id")) == str(updated_profile.get("id")):
                profiles[index] = updated_profile
                break

        save_settings(self.settings)
        self.populate_ssh_profile_list(str(updated_profile.get("id")))

    def delete_selected_ssh_profile(self) -> None:
        selected_profile = self.get_selected_ssh_profile()
        if selected_profile is None:
            return

        profile_name = str(selected_profile.get("name", "this SSH connection"))
        confirm_dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Delete saved SSH connection?",
        )
        confirm_dialog.format_secondary_text(f"{profile_name} will be removed from the saved SSH profiles.")
        response = confirm_dialog.run()
        confirm_dialog.destroy()

        if response != Gtk.ResponseType.OK:
            return

        profiles = self.get_ssh_profiles()
        profiles[:] = [profile for profile in profiles if str(profile.get("id")) != str(selected_profile.get("id"))]
        save_settings(self.settings)
        self.populate_ssh_profile_list()

    def build_ssh_command(self, profile: dict[str, object]) -> list[str] | None:
        ssh_path = resolve_executable("ssh")
        if ssh_path is None:
            self.show_error_dialog("OpenSSH client is not installed. Please install openssh-client first.")
            return None

        host = str(profile.get("host", "")).strip()
        username = str(profile.get("username", "")).strip()
        password = str(profile.get("password", "") or "")
        port = normalize_ssh_port(profile.get("port", DEFAULT_SSH_PORT))
        target = f"{username}@{host}" if username else host

        ssh_command = [
            ssh_path,
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-p",
            str(port),
            target,
        ]

        if not password:
            return ssh_command

        sshpass_path = resolve_executable("sshpass")
        if sshpass_path is None:
            self.show_error_dialog(
                "Saved-password SSH connections require sshpass.\n\nInstall it with: sudo apt install sshpass"
            )
            return None

        return [
            sshpass_path,
            "-p",
            password,
            ssh_path,
            "-o",
            "PreferredAuthentications=password,keyboard-interactive",
            "-o",
            "PubkeyAuthentication=no",
            "-o",
            "NumberOfPasswordPrompts=1",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-p",
            str(port),
            target,
        ]

    def connect_ssh_profile(self, profile: dict[str, object]) -> None:
        command = self.build_ssh_command(profile)
        if command is None:
            return

        profile_name = str(profile.get("name", "SSH"))
        tab = self.create_tab(
            command=command,
            working_directory=get_home_directory(),
            session_type="ssh",
            session_name=profile_name,
        )
        tab.update_title(str(profile.get("name", "SSH")))
        log_debug(f"opening ssh connection profile={profile.get('name')!r} host={profile.get('host')!r}")

        if self.ssh_manager_dialog is not None:
            self.ssh_manager_dialog.response(Gtk.ResponseType.CLOSE)

    def connect_selected_ssh_profile(self) -> None:
        selected_profile = self.get_selected_ssh_profile()
        if selected_profile is None:
            return
        self.connect_ssh_profile(selected_profile)

    def on_search_toggle_toggled(self, button: Gtk.ToggleButton) -> None:
        self.search_bar.set_search_mode(button.get_active())

    def on_search_mode_changed(self, *_args: object) -> None:
        active = self.search_bar.get_search_mode()
        if self.search_toggle.get_active() != active:
            self.search_toggle.set_active(active)
        if active:
            selected_text = ""
            active_tab = self.get_active_tab()
            if active_tab and active_tab.terminal.get_has_selection():
                selected_text = active_tab.get_selected_text().strip()
            if selected_text and not self.search_entry.get_text():
                self.search_entry.set_text(selected_text)
            self.search_entry.grab_focus()
            self.search_entry.select_region(0, -1)
        else:
            active_tab = self.get_active_tab()
            if active_tab:
                active_tab.set_search("", self.case_sensitive_toggle.get_active())
                active_tab.grab_focus_in_terminal()

    def on_search_changed(self, *_args: object) -> None:
        self.apply_search()

    def on_switch_page(self, _notebook: Gtk.Notebook, _page: Gtk.Widget, _page_num: int) -> None:
        self.refresh_window_title()
        self.update_action_sensitivity()
        if self.search_bar.get_search_mode():
            self.apply_search()

    def on_window_state_event(self, _window: Gtk.Window, event: Gdk.EventWindowState) -> bool:
        self.is_window_fullscreen = bool(event.new_window_state & Gdk.WindowState.FULLSCREEN)
        return False

    def action_new_tab(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        self.create_tab()

    def action_new_window(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        new_window = TTerminalWindow(self.get_application(), startup_directory=self.get_active_working_directory())
        new_window.present()

    def action_close_tab(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        active_tab = self.get_active_tab()
        if active_tab:
            self.close_tab(active_tab)

    def action_copy(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        active_tab = self.get_active_tab()
        if active_tab and active_tab.copy_selection(clear_selection=False):
            active_tab.grab_focus_in_terminal()

    def action_paste(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        active_tab = self.get_active_tab()
        if active_tab:
            active_tab.paste_clipboard()
            active_tab.grab_focus_in_terminal()

    def action_select_all(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        active_tab = self.get_active_tab()
        if active_tab:
            active_tab.select_all()
            active_tab.grab_focus_in_terminal()

    def action_find(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        self.search_bar.set_search_mode(True)

    def action_find_next(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        self.find_next()

    def action_find_previous(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        self.find_previous()

    def action_zoom_in(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        self.set_font_scale(self.font_scale + 0.1)

    def action_zoom_out(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        self.set_font_scale(self.font_scale - 0.1)

    def action_zoom_reset(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        self.set_font_scale(1.0)

    def action_show_appearance(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        self.show_appearance_dialog()

    def action_show_ssh_manager(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        self.show_ssh_manager_dialog()

    def action_next_tab(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        page_count = self.notebook.get_n_pages()
        if page_count <= 1:
            return
        next_page = (self.notebook.get_current_page() + 1) % page_count
        self.notebook.set_current_page(next_page)

    def action_previous_tab(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        page_count = self.notebook.get_n_pages()
        if page_count <= 1:
            return
        previous_page = (self.notebook.get_current_page() - 1) % page_count
        self.notebook.set_current_page(previous_page)

    def action_reset_terminal(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        active_tab = self.get_active_tab()
        if active_tab:
            active_tab.reset_terminal(clear_history=False)
            active_tab.grab_focus_in_terminal()

    def action_reset_and_clear(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        active_tab = self.get_active_tab()
        if active_tab:
            active_tab.reset_terminal(clear_history=True)
            active_tab.grab_focus_in_terminal()

    def action_toggle_fullscreen(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        if self.is_window_fullscreen:
            self.unfullscreen()
        else:
            self.fullscreen()


class TTerminalApp(Gtk.Application):
    def __init__(self, options: argparse.Namespace) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.options = options
        self.first_activation = True

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        log_debug("application startup")
        install_application_css()
        self.set_accels_for_action("win.new-tab", ["<Primary><Shift>T"])
        self.set_accels_for_action("win.new-window", ["<Primary><Shift>N"])
        self.set_accels_for_action("win.close-tab", ["<Primary><Shift>W"])
        self.set_accels_for_action("win.copy", ["<Primary><Shift>C"])
        self.set_accels_for_action("win.paste", ["<Primary>V", "<Primary><Shift>V"])
        self.set_accels_for_action("win.select-all", ["<Primary><Shift>A"])
        self.set_accels_for_action("win.find", ["<Primary>F", "<Primary><Shift>F"])
        self.set_accels_for_action("win.find-next", ["F3"])
        self.set_accels_for_action("win.find-previous", ["<Shift>F3"])
        self.set_accels_for_action("win.zoom-in", ["<Primary>plus", "<Primary>equal", "<Primary>KP_Add"])
        self.set_accels_for_action("win.zoom-out", ["<Primary>minus", "<Primary>KP_Subtract"])
        self.set_accels_for_action("win.zoom-reset", ["<Primary>0", "<Primary>KP_0"])
        self.set_accels_for_action("win.next-tab", ["<Primary>Page_Down"])
        self.set_accels_for_action("win.previous-tab", ["<Primary>Page_Up"])
        self.set_accels_for_action("win.toggle-fullscreen", ["F11"])

    def do_activate(self) -> None:
        log_debug("application activate")
        startup_command = self.options.command if self.first_activation else None
        startup_directory = self.options.working_directory if self.first_activation else None
        self.first_activation = False
        window = TTerminalWindow(self, startup_command=startup_command, startup_directory=startup_directory)
        window.present()


def main() -> int:
    options = parse_cli_arguments(sys.argv[1:])
    app = TTerminalApp(options)
    return app.run(sys.argv[:1])


if __name__ == "__main__":
    raise SystemExit(main())
