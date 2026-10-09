"""Browser workspace for the SLIM Fire API."""
from pathlib import Path

MAP_HTML = (Path(__file__).parent / "static" / "map.html").read_text(encoding="utf-8")
