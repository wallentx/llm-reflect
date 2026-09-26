"""Small, dependency-free terminal UI for provider installation and removal."""
import os
import sys
import textwrap


STYLES = {
    "title": "\x1b[1;36m",
    "muted": "\x1b[2m",
    "selected": "\x1b[32m",
    "warning": "\x1b[33m",
    "detail": "\x1b[36m",
    "focus": "\x1b[1;7;36m",
    "focus_selected": "\x1b[1;7;32m",
    "focus_warning": "\x1b[1;7;33m",
    "upstream": "\x1b[38;5;208m",
}
RESET = "\x1b[0m"
UPSTREAM_MARKER = "\u2055"


class Line(str):
    """Plain display text with trusted styles kept separate from display data."""
    def __new__(cls, *parts):
        spans = [(None, part) if isinstance(part, str) else part for part in parts]
        line = super().__new__(cls, "".join(text for _, text in spans))
        line.spans = spans
        return line


class Terminal:
    """Raw input and ANSI output, restored even after cancellation or failure."""
    def __init__(self):
        self.input = sys.stdin
        self.output = sys.stderr
        self.saved = None
        self.console = None
        self.color = not bool(os.environ.get("NO_COLOR"))
        self.styles = dict(STYLES)
        if "256" not in os.environ.get("TERM", "") and os.environ.get("COLORTERM") not in ("truecolor", "24bit"):
            self.styles["upstream"] = STYLES["warning"]

    def __enter__(self):
        if not self.input.isatty() or not self.output.isatty() or os.environ.get("TERM") == "dumb":
            raise ValueError("The provider picker needs a terminal; use --provider NAME for scripted setup")
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetStdHandle.argtypes = [wintypes.DWORD]
            kernel.GetStdHandle.restype = wintypes.HANDLE
            kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            handle = kernel.GetStdHandle(wintypes.DWORD(-12))
            mode = wintypes.DWORD()
            if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                raise ValueError("The picker needs a Windows console; use --provider NAME")
            if not kernel.SetConsoleMode(handle, mode.value | 0x0004):
                raise ValueError("ANSI output unavailable; use --provider NAME")
            self.console = kernel, handle, mode.value
        else:
            import termios
            import tty
            self.saved = termios.tcgetattr(self.input.fileno())
            tty.setraw(self.input.fileno(), when=termios.TCSANOW)
        try:
            self.output.write("\x1b[?1049h\x1b[?25l")
            self.output.flush()
        except BaseException:
            self.restore()
            raise
        return self

    def restore(self):
        try:
            self.output.write((RESET if self.color else "") + "\x1b[?25h\x1b[?1049l")
            self.output.flush()
        finally:
            if self.saved is not None:
                import termios
                termios.tcsetattr(self.input.fileno(), termios.TCSANOW, self.saved)
                self.saved = None
            if self.console is not None:
                kernel, handle, mode = self.console
                kernel.SetConsoleMode(handle, mode)
                self.console = None

    def __exit__(self, *exc):
        self.restore()

    def size(self):
        try:
            size = os.get_terminal_size(self.output.fileno())
            return size if size.columns > 0 and size.lines > 0 else os.terminal_size((80, 24))
        except OSError:
            return os.terminal_size((80, 24))

    def draw(self, lines):
        width, height = self.size()
        rendered = []
        for line in lines[:max(1, height)]:
            remaining = max(1, width - 1)
            parts = []
            for role, text in getattr(line, "spans", [(None, line)]):
                # Sanitize and clip display data before adding trusted ANSI styles.
                text = "".join(c if c.isprintable() else " " for c in text)[:remaining]
                remaining -= len(text)
                style = self.styles.get(role, "") if self.color else ""
                if text:
                    parts.append(style + text + (RESET if style else ""))
                if not remaining:
                    break
            rendered.append("".join(parts))
        self.output.write("\x1b[H\x1b[2J" + "\r\n".join(rendered))
        self.output.flush()

    def key(self):
        if os.name == "nt":
            import msvcrt
            char = msvcrt.getwch()
            if char in ("\x00", "\xe0"):
                return {"H": "up", "P": "down", "K": "left", "M": "right",
                        "G": "home", "O": "end"}.get(msvcrt.getwch(), "unknown")
        else:
            import select
            char = os.read(self.input.fileno(), 1).decode("ascii", errors="replace")
            if not char:
                return "cancel"
            if char == "\x1b":
                sequence = ""
                while len(sequence) < 12 and select.select([self.input], [], [], 0.08)[0]:
                    sequence += os.read(self.input.fileno(), 1).decode("ascii", errors="replace")
                    if (len(sequence) > 1 and sequence[-1].isalpha()) or sequence.endswith("~"):
                        break
                return {"[A": "up", "OA": "up", "[B": "down", "OB": "down",
                        "[C": "right", "OC": "right", "[D": "left", "OD": "left",
                        "[H": "home", "OH": "home", "[F": "end", "OF": "end",
                        "[1~": "home", "[4~": "end", "": "escape"}.get(sequence, "unknown")
        return {"\r": "enter", "\n": "enter", " ": "toggle", "\t": "tab",
                "\x1b": "escape", "\x03": "cancel", "\x04": "cancel",
                "q": "cancel", "j": "down", "k": "up"}.get(char, char.lower())


def checklist(terminal, rows, selected, removing=False, changed=None):
    checked = set(selected)
    changed = set() if changed is None else changed
    focus = 0
    while True:
        width, height = terminal.size()
        legend = []
        if any(row.get("upstream") for row in rows):
            note = ("Check to uninstall; data stays." if removing else "Uncheck: remove; recheck: LLM Reflect.")
            legend = [Line(("upstream", text)) for text in textwrap.wrap(
                UPSTREAM_MARKER + " Upstream claude-reflect. " + note, max(1, width - 1))]
        visible = max(1, height - 9 - len(legend))
        start = max(0, min(focus - visible + 1, len(rows) - visible))
        lines = [Line(("title", "LLM Reflect | " + ("Uninstall providers" if removing else "Manage providers"))),
                 "", Line(("muted", "Up/Down: move  Space: toggle")),
                 Line(("muted", "Enter: review  Esc/q: cancel")), ""]
        for index in range(start, min(len(rows), start + visible)):
            row = rows[index]
            focused = index == focus
            selected = row["id"] in checked
            action_style = "warning" if removing else "selected"
            check_style = ("focus_" + action_style if focused else action_style) if selected else (
                "focus" if focused else "muted")
            row_style = "focus" if focused else None
            status_style = "focus" if focused else ("selected" if row["status"].startswith("installed") else "muted")
            status = row["status"]
            if row.get("upstream"):
                status_style = "upstream"
                if not removing and not selected:
                    status, status_style = "remove Reflect integrations", "warning"
                elif not removing and row["id"] in changed:
                    status, status_style = "replace with LLM Reflect", "selected"
            lines.append(Line(
                (row_style, "> " if focused else "  "),
                (check_style, "[{}]".format("x" if selected else " ")),
                (row_style, " {:<18} ".format(row["label"])),
                ("upstream", UPSTREAM_MARKER + " " if row.get("upstream") else "  "),
                (status_style, status)))
        lines.extend(["", Line(("warning" if removing else "selected", "Checked: {} ({})".format(
                          len(checked), "remove" if removing else "keep/install/update" if legend else "install/update"))),
                      Line(("muted", "Queues are retained." if removing else "Unchecked installed: remove.")),
                      Line(("detail", rows[focus]["detail"]))])
        lines.extend(legend)
        terminal.draw(lines)
        key = terminal.key()
        if key in ("cancel", "escape"):
            return None
        if key == "enter":
            return [row["id"] for row in rows if row["id"] in checked]
        if key == "toggle":
            name = rows[focus]["id"]
            changed.add(name)
            if name in checked:
                checked.remove(name)
            else:
                checked.add(name)
        elif key in ("up", "down"):
            focus = (focus + (1 if key == "down" else -1)) % len(rows)
        elif key in ("home", "end"):
            focus = 0 if key == "home" else len(rows) - 1


def confirm(terminal, installing, removing, upstream=False):
    apply = False
    while True:
        width, height = terminal.size()
        lines = [Line(("title", "LLM Reflect | Review changes")), ""]
        for role, label, names in (("selected", "Install/update: ", installing), ("warning", "Remove: ", removing)):
            lines.extend(Line((role if names else "muted", text)) for text in
                         textwrap.wrap(label + (", ".join(names) or "none"), max(1, width - 1)))
        retained = "Upstream data retained; queues are not migrated." if upstream else "Queues/settings are retained."
        lines.append("")
        lines.extend(Line(("muted", text)) for text in textwrap.wrap(retained, max(1, width - 1)))
        lines.extend(["",
                      Line(("focus_selected" if apply else "selected", "> [Apply]" if apply else "  [Apply]"),
                           "  ", ("muted" if apply else "focus", "  [Cancel]" if apply else "> [Cancel]")),
                      Line(("muted", "Arrows/Tab: choose Enter: apply")),
                      Line(("muted", "y: apply n/Esc: back q: cancel"))])
        fits = width >= 32 and len(lines) <= height
        if not fits:
            lines = [Line(("title", "LLM Reflect | Review changes")),
                     Line(("warning", "Resize to show all changes.")),
                     Line(("muted", "Then press any key.")), Line(("muted", "Esc: back  q: cancel"))]
        terminal.draw(lines)
        key = terminal.key()
        if key == "cancel":
            return None
        if key in ("n", "escape"):
            return False
        if not fits:
            continue
        if key in ("left", "right", "tab", "toggle"):
            apply = not apply
        elif key == "y" or key == "enter" and apply:
            return True
        elif key == "enter":
            return False


def choose_providers(rows, installed, removing=False, dry_run=False):
    """Return install/remove sets, or None. No filesystem mutation happens here."""
    selected = [] if removing else list(installed)
    changed = set()
    with Terminal() as terminal:
        while True:
            chosen = checklist(terminal, rows, selected, removing, changed)
            if chosen is None:
                return None
            selected = chosen
            # An untouched upstream row keeps its original integration.
            add = [] if removing else [name for name in chosen
                                       if not installed.get(name, {}).get("upstream") or name in changed]
            remove = chosen if removing else [name for name in installed if name not in chosen]
            if not add and not remove:
                return [], []
            if dry_run:
                return add, remove
            installing_labels = [name + " (LLM Reflect)" if installed.get(name, {}).get("upstream") else name
                                 for name in add]
            removing_labels = []
            for name in remove:
                if installed[name].get("method") != "upstream":
                    removing_labels.append(name + " (LLM Reflect)" if installed[name].get("upstream") else name)
            for name in dict.fromkeys(add + remove):
                for entry in installed.get(name, {}).get("upstream", []):
                    label = entry["plugin"] + " (" + entry["scope"] + ")"
                    if label not in removing_labels:
                        removing_labels.append(label)
            decision = confirm(terminal, installing_labels, removing_labels,
                               upstream=any(installed.get(name, {}).get("upstream") for name in add + remove))
            if decision is None:
                return None
            if decision:
                return add, remove
