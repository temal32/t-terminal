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

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")

from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte


APP_ID = "com.temal.tterminal"
APP_NAME = "t-terminal"
APP_VERSION = "1.2.1"
DEFAULT_FONT = "Monospace 12"
URL_PATTERN = r"(?:https?|ftp)://[^\s\"'<>]+|mailto:[^\s\"'<>]+|file://[^\s\"'<>]+"
CONFIG_DIR = pathlib.Path.home() / ".config" / "t-terminal"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
DEFAULT_BACKGROUND_OPACITY = 1.0
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


def normalize_background_opacity(value: object) -> float:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        numeric_value = DEFAULT_BACKGROUND_OPACITY
    return max(0.0, min(1.0, numeric_value))


def load_settings() -> dict[str, float]:
    settings = {"background_opacity": DEFAULT_BACKGROUND_OPACITY}
    try:
        if SETTINGS_PATH.exists():
            raw_settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw_settings, dict):
                settings["background_opacity"] = normalize_background_opacity(
                    raw_settings.get("background_opacity", DEFAULT_BACKGROUND_OPACITY)
                )
    except (OSError, ValueError, TypeError) as error:
        log_debug(f"could not load settings: {error}")
    return settings


def save_settings(settings: dict[str, float]) -> None:
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
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.command = list(command) if command else None
        self.working_directory = working_directory
        self.title = "Shell"
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
        log_debug(
            f"child exited status={_status} runtime_seconds={runtime_seconds:.3f} command={self.current_command_description!r}"
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
    ) -> TerminalTab:
        directory = working_directory or self.get_active_working_directory() or self.get_default_working_directory()
        tab = TerminalTab(self, command=command, working_directory=directory)

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
