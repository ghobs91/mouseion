from __future__ import annotations

from pathlib import Path

import pytest

from mouseion.config import Settings
from mouseion.registry import Registry

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def registry() -> Registry:
    return Registry.load(ROOT / "providers")


@pytest.fixture
def config(tmp_path: Path) -> Settings:
    return Settings(providers_dir=ROOT / "providers", db_path=tmp_path / "cache.db")


STUDY = {
    "hasResults": False,
    "protocolSection": {
        "identificationModule": {
            "nctId": "NCT04631731",
            "briefTitle": "Immune Checkpoint Toxicity Risk Factors",
        },
        "statusModule": {
            "overallStatus": "RECRUITING",
            "startDateStruct": {"date": "2020-12-15"},
            "primaryCompletionDateStruct": {"date": "2024-12-10", "type": "ESTIMATED"},
        },
        "designModule": {
            "studyType": "INTERVENTIONAL",
            "phases": ["PHASE1", "PHASE2"],
            "enrollmentInfo": {"count": 200, "type": "ESTIMATED"},
        },
        "conditionsModule": {"conditions": ["Melanoma"]},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Western Sydney LHD"}},
        "descriptionModule": {"briefSummary": "A study of immune-related toxicity."},
    },
}

STUDIES_BODY = {"studies": [STUDY], "nextPageToken": "TOKEN-2"}

FEDREG_BODY = {
    "count": 1605,
    "total_pages": 50,
    "results": [
        {
            "document_number": "2026-12345",
            "title": "Artificial Intelligence in Federal Agencies",
            "type": "RULE",
            "abstract": "An interim final rule.",
            "publication_date": "2026-10-01",
            "html_url": "https://www.federalregister.gov/d/2026-12345",
            "agencies": [{"name": "General Services Administration", "slug": "gsa"}],
        }
    ],
}

FDA_EMPTY_404 = {"error": {"code": "NOT_FOUND", "message": "No matches found!"}}
