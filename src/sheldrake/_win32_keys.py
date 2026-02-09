"""Monkeypatch: make modifier+Enter distinguishable from plain Enter on Windows.

Textual's Win32 EventMonitor uses ENABLE_VIRTUAL_TERMINAL_INPUT, which zeroes
out dwControlKeyState and wVirtualKeyCode in KEY_EVENT_RECORD. This makes
Shift+Enter indistinguishable from Enter.

Fix: read one input record at a time so that when '\r' arrives we can
immediately poll GetAsyncKeyState for modifier keys. Reading single records
minimizes the window between the physical keypress and the poll, reducing
the race condition where a user releases Shift before the batch is processed.
If Shift/Ctrl/Alt is held, inject a Kitty CSI u sequence (\x1b[13;Nu)
so the XTermParser produces 'shift+enter' etc.

Based on: textual 7.5.0 (textual.drivers.win32.EventMonitor.run)
Tracks: https://github.com/Textualize/textual/issues/6074
"""

from __future__ import annotations

import sys


def apply() -> None:
    """Patch EventMonitor.run to detect modifier+Enter via GetAsyncKeyState."""
    if sys.platform != "win32":
        return

    import ctypes
    from ctypes import byref
    from ctypes.wintypes import DWORD

    from textual import constants
    from textual._xterm_parser import XTermParser
    from textual.drivers.win32 import (
        INPUT_RECORD,
        KERNEL32,
        STD_INPUT_HANDLE,
        EventMonitor,
        GetStdHandle,
        wait_for_handles,
    )

    # GetAsyncKeyState lives in user32, not kernel32.
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _GetAsyncKeyState = _user32.GetAsyncKeyState
    _GetAsyncKeyState.argtypes = [ctypes.c_int]
    _GetAsyncKeyState.restype = ctypes.c_short
    _VK_SHIFT = 0x10
    _VK_CONTROL = 0x11
    _VK_MENU = 0x12  # Alt

    def _poll_modifier_param() -> int:
        """Poll current modifier key state, return Kitty CSI u parameter."""
        bits = 0
        if _GetAsyncKeyState(_VK_SHIFT) & 0x8000:
            bits |= 1
        if _GetAsyncKeyState(_VK_MENU) & 0x8000:
            bits |= 2
        if _GetAsyncKeyState(_VK_CONTROL) & 0x8000:
            bits |= 4
        return bits + 1 if bits else 0

    def _patched_run(self: EventMonitor) -> None:
        exit_requested = self.exit_event.is_set
        parser = XTermParser(debug=constants.DEBUG)
        KEY_EVENT = 0x0001
        WINDOW_BUFFER_SIZE_EVENT = 0x0004

        try:
            read_count = DWORD(0)
            hIn = GetStdHandle(STD_INPUT_HANDLE)
            # Read one record at a time to minimize the race window between
            # the physical keypress and the GetAsyncKeyState poll.
            input_record = INPUT_RECORD()
            ReadConsoleInputW = KERNEL32.ReadConsoleInputW
            keys: list[str] = []
            append_key = keys.append

            while not exit_requested():
                for event in parser.tick():
                    self.process_event(event)  # type: ignore[arg-type]

                if wait_for_handles([hIn], 100) is None:
                    continue

                del keys[:]
                new_size: tuple[int, int] | None = None

                # Drain all pending input records one at a time.
                while True:
                    ReadConsoleInputW(hIn, byref(input_record), 1, byref(read_count))
                    if read_count.value == 0:
                        break

                    event_type = input_record.EventType

                    if event_type == KEY_EVENT:
                        key_event = input_record.Event.KeyEvent
                        key = key_event.uChar.UnicodeChar
                        if key_event.bKeyDown:
                            if key_event.dwControlKeyState and key_event.wVirtualKeyCode == 0:
                                pass
                            elif key == "\r":
                                mod = _poll_modifier_param()
                                if mod > 1:
                                    keys.extend(f"\x1b[13;{mod}u")
                                else:
                                    append_key(key)
                            else:
                                append_key(key)
                    elif event_type == WINDOW_BUFFER_SIZE_EVENT:
                        size = input_record.Event.WindowBufferSizeEvent.dwSize
                        new_size = (size.X, size.Y)

                    # Check if more records are pending.
                    pending = DWORD(0)
                    KERNEL32.GetNumberOfConsoleInputEvents(hIn, byref(pending))
                    if pending.value == 0:
                        break

                if keys:
                    for event in parser.feed(
                        "".join(keys).encode("utf-16", "surrogatepass").decode("utf-16")
                    ):
                        self.process_event(event)  # type: ignore[arg-type]
                if new_size is not None:
                    self.on_size_change(*new_size)

        except Exception as error:
            self.app.log.error("EVENT MONITOR ERROR", error)

    EventMonitor.run = _patched_run  # type: ignore[assignment]
