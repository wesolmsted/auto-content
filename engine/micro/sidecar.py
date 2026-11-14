from engine.micro.models import Sidecar


class SidecarError(ValueError):
    """The sidecar text doesn't follow the format in DESIGN.md §7.10."""


def parse_sidecar(text: str) -> Sidecar:
    """Parse a micro set's `.txt`: first non-empty line `song: <name>`, then one text option
    per non-empty line. Raises SidecarError."""
    raise NotImplementedError
