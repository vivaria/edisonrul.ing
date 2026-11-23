import json
from argparse import Namespace
from unittest.mock import Mock

import pytest

import fetch


@pytest.fixture
def output_root(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "OUTPUT_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def historical_page(output_root):
    page = fetch.output_path("example")
    page.parent.mkdir()
    page.write_text(
        "## Edisonformat.com (Historical, Pre-UTW Rulings)\n\n"
        "### Card Text\n\n> Existing text\n\n"
        "### Card Rulings\n\nExisting ruling\n",
        encoding="utf-8",
    )
    return page


def test_output_path_uses_lowercase_directories(output_root):
    assert fetch.output_path("alpha") == output_root / "a" / "alpha.md"
    assert fetch.output_path("7") == output_root / "0-9" / "7.md"


def test_loads_edison_fields_and_preserves_placeholders(tmp_path):
    (tmp_path / "123.json").write_text(json.dumps({
        "id": 123,
        "name": "Example",
        "PSCT": {"Edison": "Edison text", "Base": "Base text"},
        "Rulings": {"Edison": "No rulings found for this card.", "Base": "Base ruling"},
    }), encoding="utf-8")
    cards = {"example": {"id": 123, "url_efnet": "https://example.test"}}
    result = fetch.load_efnet_cards(str(tmp_path), cards)["example"]
    assert result.psct == "Edison text"
    assert result.rulings == "No rulings found for this card."
    assert fetch._efnet_field({"PSCT": {"Base": "Base"}}, "PSCT") == "Base"


def test_missing_and_invalid_json_fail(tmp_path):
    cards = {"example": {"id": 123, "url_efnet": "https://example.test"}}
    with pytest.raises(ValueError, match="example"):
        fetch.load_efnet_cards(str(tmp_path), cards)
    (tmp_path / "123.json").write_text("{", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        fetch.load_efnet_cards(str(tmp_path), cards)


def test_invalid_upstream_schema_fails(tmp_path):
    cards = {"example": {"id": 123, "url_efnet": "https://example.test"}}
    path = tmp_path / "123.json"
    path.write_text(json.dumps({"id": 124}), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid card ID"):
        fetch.load_efnet_cards(str(tmp_path), cards)
    path.write_text(json.dumps({"id": 123, "PSCT": {}, "Rulings": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="Missing PSCT"):
        fetch.load_efnet_cards(str(tmp_path), cards)


def test_historical_content_cannot_disappear(historical_page):
    cards = {"example": {}}
    for efcom in (
        {},
        {"example": fetch.EfcomData("Example", "", "Text", "")},
        {"example": fetch.EfcomData("Example", "", "Text", fetch._NO_RULING)},
    ):
        with pytest.raises(ValueError, match="example"):
            fetch.validate_efcom_coverage(cards, efcom)
    fetch.validate_efcom_coverage(
        cards, {"example": fetch.EfcomData("Example", "", "Text", "Ruling")}
    )


def test_http_failure_is_not_treated_as_a_successful_page():
    session = Mock()
    session.get.return_value.raise_for_status.side_effect = fetch.requests.HTTPError("503")
    with pytest.raises(fetch.requests.HTTPError, match="503"):
        fetch._efcom_fetch_page(session, "https://example.test")


def test_failed_scrape_does_not_write_pages(historical_page, monkeypatch):
    original = historical_page.read_text(encoding="utf-8")
    args = Namespace(input="unused", json_dir="unused", skip_efcom=False)
    monkeypatch.setattr(fetch, "csv_to_dict", lambda _: {"example": {}})
    monkeypatch.setattr(fetch, "load_efnet_cards", lambda *_: {})
    monkeypatch.setattr(fetch, "fetch_efcom_cards", lambda **_: {})
    with pytest.raises(ValueError, match="example"):
        fetch._run(args)
    assert historical_page.read_text(encoding="utf-8") == original


def test_repeated_generation_is_identical(output_root):
    cards = {"example": {"card_name": "Example"}}
    efnet = {"example": fetch.EfnetData("Example", "https://example.test", "Text", "Ruling")}
    efcom = {"example": fetch.EfcomData("Example", "", "Text", "Ruling")}
    fetch.merge_and_write(cards, efnet, efcom)
    page = fetch.output_path("example")
    first = page.read_bytes()
    fetch.merge_and_write(cards, efnet, efcom)
    assert page.read_bytes() == first
