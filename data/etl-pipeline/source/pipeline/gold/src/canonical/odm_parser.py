"""Shared ODM/SDM question-template parsing.

Both gold_canonical.question (load_question.py) and gold_eden.bid_context
(load_bid_context.py) need to turn silver.fieldcraft_provider_question_template's
question_template_data blob into a flat list of questions. This module is the
single parser both loaders import — canonical is the authoritative owner of
the template model; kept here (not duplicated in eden) so a future change to
the ODM shape only needs one fix.

Structure: SDM.Building.TemplateVersion[].{ItemDef[], ItemGroupDef[].ItemRef[]}.
ItemDef carries the question text + DataType; ItemRef carries Mandatory per
ItemOID. The latest TemplateVersion block is used.
"""

from __future__ import annotations

import json


def translated_text(node) -> "str | None":
    """Extract .TranslatedText.text from an ODM Question/Description node.

    TranslatedText may be a dict {lang,text} or a list of such dicts.
    """
    if not isinstance(node, dict):
        return None
    tt = node.get("TranslatedText")
    if isinstance(tt, list):
        tt = tt[0] if tt else None
    if isinstance(tt, dict):
        return tt.get("text")
    return None


def parse_template(template_data) -> list[dict]:
    """Parse question_template_data (ODM/SDM JSON) into a flat list of
    questions: [{itemOid, question, type, required}].
    """
    if not template_data:
        return []
    if isinstance(template_data, str):
        try:
            template_data = json.loads(template_data)
        except json.JSONDecodeError:
            return []
    if not isinstance(template_data, dict):
        return []
    try:
        versions = template_data["SDM"]["Building"]["TemplateVersion"]
    except (KeyError, TypeError):
        return []
    if not isinstance(versions, list) or not versions:
        return []
    tv = versions[-1]
    if not isinstance(tv, dict):
        return []

    # ItemOID → required (Mandatory == "Yes") from ItemGroupDef[].ItemRef[]
    mandatory: dict[str, bool] = {}
    for ig in tv.get("ItemGroupDef") or []:
        for ref in (ig.get("ItemRef") or []) if isinstance(ig, dict) else []:
            oid = ref.get("ItemOID") if isinstance(ref, dict) else None
            if oid:
                mandatory[oid] = str(ref.get("Mandatory")).lower() == "yes"

    items: list[dict] = []
    for idef in tv.get("ItemDef") or []:
        if not isinstance(idef, dict):
            continue
        oid = idef.get("OID")
        if not oid:
            continue
        items.append({
            "itemOid":  oid,
            "question": translated_text(idef.get("Question")) or translated_text(idef.get("Description")),
            "type":     idef.get("DataType"),
            "required": mandatory.get(oid, False),
        })
    return items
