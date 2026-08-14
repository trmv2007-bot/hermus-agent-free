"""Offline tests for public-apis catalog discovery."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.public_apis import (
    PublicAPICatalog,
    build_catalog_payload,
    parse_public_apis_markdown,
)


SAMPLE_MARKDOWN = """
### Security
API | Description | Auth | HTTPS | CORS
|:---|:---|:---|:---|:---|
| [Safe One](https://example.test/safe) | Threat intelligence and URL reputation | No | Yes | Yes |
| [Key Guard](https://example.test/key) | Malware scanner | `apiKey` | Yes | Unknown | |
| malformed | This must be ignored | No | Yes | Yes |

### Weather
API | Description | Auth | HTTPS | CORS
|:---|:---|:---|:---|:---|
| [Sky Data](https://example.test/weather) | Current forecasts | No | Yes | No |
| [Old Weather](http://example.test/old) | Historical forecasts | No | No | Unknown |
"""


def _catalog(tmp: Path) -> PublicAPICatalog:
    entries = parse_public_apis_markdown(SAMPLE_MARKDOWN)
    bundled = tmp / "catalog.json"
    bundled.write_text(json.dumps(build_catalog_payload(entries)), encoding="utf-8")
    return PublicAPICatalog(bundled_path=bundled, cache_path=tmp / "cache.json")


def test_markdown_parser_extracts_category_tables_and_normalizes_values():
    entries = parse_public_apis_markdown(SAMPLE_MARKDOWN)
    assert len(entries) == 4
    assert entries[0] == {
        "name": "Safe One",
        "description": "Threat intelligence and URL reputation",
        "auth": "No",
        "https": "Yes",
        "cors": "Yes",
        "category": "Security",
        "documentation_url": "https://example.test/safe",
    }
    assert entries[1]["auth"] == "apiKey"


def test_search_ranks_keywords_and_applies_free_https_filters():
    with tempfile.TemporaryDirectory() as td:
        catalog = _catalog(Path(td))
        result = catalog.search(query="threat reputation", auth="No", https_only=True)
        assert result["count"] == 1
        assert result["results"][0]["name"] == "Safe One"
        assert result["catalog"]["loaded_from"] == "bundled_snapshot"

        weather = catalog.search(query="forecast", category="Weather", https_only=True)
        assert [item["name"] for item in weather["results"]] == ["Sky Data"]


def test_categories_report_no_auth_https_and_cors_counts():
    with tempfile.TemporaryDirectory() as td:
        result = _catalog(Path(td)).categories()
        by_name = {item["category"]: item for item in result["categories"]}
        assert by_name["Security"] == {
            "category": "Security",
            "total": 2,
            "no_auth": 1,
            "https": 2,
            "cors_yes": 1,
        }
        assert by_name["Weather"]["no_auth"] == 2
        assert by_name["Weather"]["https"] == 1


def test_invalid_runtime_cache_falls_back_to_bundled_snapshot():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        catalog = _catalog(root)
        catalog.cache_path.write_text("not json", encoding="utf-8")
        result = catalog.search(query="scanner", auth="apiKey")
        assert result["results"][0]["name"] == "Key Guard"
        assert result["catalog"]["loaded_from"] == "bundled_snapshot"
