from __future__ import annotations

from .atom import AtomAdapter
from .base import Adapter, AdapterResult, PreparedRequest, extract_records
from .cdx import CdxAdapter
from .csv_adapter import CsvAdapter
from .faa_nas import FaaNasAdapter
from .html_table import HtmlTableAdapter
from .rest_json import RestJsonAdapter
from .rss import RssAdapter
from .sdmx_json import SdmxJsonAdapter
from .socrata import SocrataAdapter

ADAPTERS: dict[str, Adapter] = {
    adapter.name: adapter
    for adapter in (
        RestJsonAdapter(),
        SocrataAdapter(),
        AtomAdapter(),
        RssAdapter(),
        SdmxJsonAdapter(),
        CdxAdapter(),
        CsvAdapter(),
        HtmlTableAdapter(),
        FaaNasAdapter(),
    )
}


def get_adapter(name: str) -> Adapter:
    try:
        return ADAPTERS[name]
    except KeyError:
        raise ValueError(
            f"unknown adapter '{name}'; available: {sorted(ADAPTERS)}"
        ) from None


__all__ = [
    "ADAPTERS",
    "Adapter",
    "AdapterResult",
    "PreparedRequest",
    "extract_records",
    "get_adapter",
]
