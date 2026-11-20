"""Small ANSI palette; plain text for redirected output and NO_COLOR."""
import os
import sys

CODES = {'success': '32', 'error': '31', 'warning': '33',
         'info': '36', 'progress': '34', 'muted': '90'}


def supports_color(stream=None):
    stream = sys.stdout if stream is None else stream
    if 'NO_COLOR' in os.environ or os.environ.get('TERM') == 'dumb' or not stream.isatty():
        return False
    if os.name == 'nt':
        try:
            import ctypes
            handle = ctypes.windll.kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            return bool(ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 4))
        except (AttributeError, OSError):
            return False
    return True


class Palette:
    def __init__(self, enabled=None):
        self.enabled = supports_color() if enabled is None else enabled

    def paint(self, text, tone='info'):
        value = str(text)
        return f'\x1b[{CODES[tone]}m{value}\x1b[0m' if self.enabled else value


def size_change(before, after):
    """Return saved bytes and percentage of the ORIGINAL file size."""
    if before < 0 or after < 0:
        raise ValueError('File sizes cannot be negative')
    saved = before - after
    percent = saved * 100 / before if before else 0.0
    return saved, percent
