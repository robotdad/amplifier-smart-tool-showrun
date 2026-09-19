"""Library-first Showrun. Importing this module never starts an agent or browser."""

from .errors import ShowrunError
from .lib import Showrun

__all__ = ["Showrun", "ShowrunError"]