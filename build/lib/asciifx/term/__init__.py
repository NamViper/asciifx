"""Terminal I/O. The only package that touches the real terminal."""

from .color import ColorMode, detect_color_mode, rgb_to_256
from .keys import KeyReader, parse_keys
from .terminal import Terminal

__all__ = ["ColorMode", "KeyReader", "Terminal", "detect_color_mode", "parse_keys", "rgb_to_256"]
