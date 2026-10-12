"""Unit tests for gold.src.canonical.odm_parser — the shared ODM/SDM question-
template parser used by both load_question.py (canonical) and
load_bid_context.py (eden), extracted so a future ODM shape change only needs
one fix.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from gold.src.canonical.odm_parser import parse_template, translated_text  # noqa: E402


def _template(item_defs, item_refs):
    return {"SDM": {"Building": {"TemplateVersion": [{
        "ItemDef": item_defs,
        "ItemGroupDef": [{"ItemRef": item_refs}],
    }]}}}


class TestTranslatedText:
    def test_dict_form(self):
        node = {"TranslatedText": {"lang": "en", "text": "Roof type?"}}
        assert translated_text(node) == "Roof type?"

    def test_list_form_uses_first_entry(self):
        node = {"TranslatedText": [{"lang": "en", "text": "Roof type?"},
                                    {"lang": "es", "text": "Tipo de techo?"}]}
        assert translated_text(node) == "Roof type?"

    def test_empty_list_returns_none(self):
        assert translated_text({"TranslatedText": []}) is None

    def test_not_a_dict_returns_none(self):
        assert translated_text("not a dict") is None

    def test_missing_translated_text_returns_none(self):
        assert translated_text({}) is None


class TestParseTemplate:
    def test_realistic_fixture(self):
        data = _template(
            item_defs=[
                {"OID": "Q1", "Question": {"TranslatedText": {"text": "Roof type?"}},
                 "DataType": "text"},
                {"OID": "Q2", "Question": {"TranslatedText": {"text": "Sq footage?"}},
                 "DataType": "number"},
            ],
            item_refs=[
                {"ItemOID": "Q1", "Mandatory": "Yes"},
                {"ItemOID": "Q2", "Mandatory": "No"},
            ],
        )
        items = parse_template(data)
        assert len(items) == 2
        by_oid = {i["itemOid"]: i for i in items}
        assert by_oid["Q1"]["question"] == "Roof type?"
        assert by_oid["Q1"]["type"] == "text"
        assert by_oid["Q1"]["required"] is True
        assert by_oid["Q2"]["required"] is False

    def test_falls_back_to_description_when_no_question(self):
        data = _template(
            item_defs=[{"OID": "Q1", "Description": {"TranslatedText": {"text": "Fallback text"}},
                        "DataType": "text"}],
            item_refs=[],
        )
        items = parse_template(data)
        assert items[0]["question"] == "Fallback text"

    def test_missing_itemref_defaults_required_false(self):
        data = _template(
            item_defs=[{"OID": "Q1", "Question": {"TranslatedText": {"text": "Q?"}},
                        "DataType": "text"}],
            item_refs=[],
        )
        items = parse_template(data)
        assert items[0]["required"] is False

    def test_uses_latest_template_version_when_multiple(self):
        data = {"SDM": {"Building": {"TemplateVersion": [
            {"ItemDef": [{"OID": "OLD", "Question": {"TranslatedText": {"text": "old"}},
                          "DataType": "text"}], "ItemGroupDef": []},
            {"ItemDef": [{"OID": "NEW", "Question": {"TranslatedText": {"text": "new"}},
                          "DataType": "text"}], "ItemGroupDef": []},
        ]}}}
        items = parse_template(data)
        assert len(items) == 1
        assert items[0]["itemOid"] == "NEW"

    def test_json_string_input_is_parsed(self):
        import json
        data = _template(
            item_defs=[{"OID": "Q1", "Question": {"TranslatedText": {"text": "Q?"}},
                        "DataType": "text"}],
            item_refs=[{"ItemOID": "Q1", "Mandatory": "Yes"}],
        )
        items = parse_template(json.dumps(data))
        assert items[0]["itemOid"] == "Q1"
        assert items[0]["required"] is True

    def test_malformed_json_string_returns_empty(self):
        assert parse_template("{not valid json") == []

    def test_none_returns_empty(self):
        assert parse_template(None) == []

    def test_empty_dict_returns_empty(self):
        assert parse_template({}) == []

    def test_wrong_shape_returns_empty(self):
        assert parse_template({"SDM": {"Building": {}}}) == []

    def test_item_missing_oid_is_skipped(self):
        data = _template(
            item_defs=[{"Question": {"TranslatedText": {"text": "no oid"}}, "DataType": "text"}],
            item_refs=[],
        )
        assert parse_template(data) == []

    def test_non_dict_item_def_is_skipped(self):
        data = _template(item_defs=["not a dict"], item_refs=[])
        assert parse_template(data) == []
