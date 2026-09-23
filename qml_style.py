"""Read a QGIS paletted-raster QML style without requiring QGIS."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import xml.etree.ElementTree as ET


DEFAULT_QML_PATH = Path(__file__).with_name("SLIM_LC_LandCover_legend.qml")


@dataclass(frozen=True)
class PaletteEntry:
    value: int
    label: str
    rgba: tuple[int, int, int, int]


@dataclass(frozen=True)
class QmlRasterStyle:
    entries: tuple[PaletteEntry, ...]
    opacity: float

    @property
    def colormap(self) -> dict[int, tuple[int, int, int, int]]:
        colors = {entry.value: entry.rgba for entry in self.entries}
        colors.setdefault(0, (0, 0, 0, 0))
        return colors


def _hex_to_rgba(color: str, alpha: int) -> tuple[int, int, int, int]:
    value = color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"unsupported QML color '{color}'")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha


@lru_cache(maxsize=1)
def load_qml_style(path: str | Path = DEFAULT_QML_PATH) -> QmlRasterStyle:
    """Load palette values, labels, colors, and renderer opacity from QML."""
    root = ET.parse(Path(path)).getroot()
    renderer = root.find(".//rasterrenderer[@type='paletted']")
    if renderer is None:
        raise ValueError("QML does not contain a paletted raster renderer")

    entries = tuple(
        PaletteEntry(
            value=int(item.attrib["value"]),
            label=item.attrib.get("label", item.attrib["value"]),
            rgba=_hex_to_rgba(
                item.attrib["color"],
                int(item.attrib.get("alpha", "255")),
            ),
        )
        for item in renderer.findall("./colorPalette/paletteEntry")
    )
    if not entries:
        raise ValueError("QML paletted renderer has no palette entries")
    return QmlRasterStyle(
        entries=entries,
        opacity=float(renderer.attrib.get("opacity", "1")),
    )
