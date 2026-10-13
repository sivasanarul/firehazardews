"""Browser workspace for the SLIM Fire API."""
from pathlib import Path

MAP_HTML_PATH = Path(__file__).parent / "static" / "map.html"


def read_map_html() -> str:
    """Read the map shell from disk so edits appear without restarting the API."""
    return MAP_HTML_PATH.read_text(encoding="utf-8")
