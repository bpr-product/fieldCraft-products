/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-21
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : GENERIC (trade-agnostic) per-facet questionnaire autofill helpers.
 *                Given a RoofLens per-facet MetaKey map + a template's repeating
 *                ItemGroupDef, build the answersData / itemGroupData patches for the
 *                repeating group by matching each member question's ItemDef.MetaKey
 *                to the measurement vocabulary — NEVER by hardcoded OID. Works for
 *                any trade whose template tags per-facet questions with a MetaKey.
 *
 *                Coded (select) measurement questions (e.g. a pitch band dropdown)
 *                are resolved to a CodeList CodedValue by parsing the CodeList item
 *                labels' numeric ranges — no hardcoded CodeList ids or codes.
 *
 *                Pure — no I/O, no React, no side effects. Extracted so the subtle
 *                repeating-group answer model is unit-testable without rendering the
 *                ~8000-line Requirements_v3 component.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * RoofLens-Integration | 2026-07-21 | FIELDCRAFT Web | Initial version (generic per-facet)
 */

import { PER_FACET_METAKEYS } from '../../asset/rooflens/measurementMetaKeys';

/**
 * Max repeating instances the questionnaire will DISPLAY — must match the cap in
 * repeatingGroupUtils.getRepeatingInstanceCount (`Math.min(count, 20)`). Seeding a
 * higher count than the UI displays makes the count-sync effect see stored != current
 * and prompt to "delete" the excess (wiping just-autofilled data), so we clamp both
 * the materialized instances AND the seeded count to this maximum.
 */
export const MAX_FACET_INSTANCES = 20;

/** One ODM ItemGroupData row (loose — mirrors the runtime shape written elsewhere). */
export interface PerFacetItemGroupRow {
  ItemGroupOID: string;
  ItemGroupRepeatKey: number;
  ParentItemGroupRepeatKey: number;
  ItemData: any[];
}

/** Result of building one repeating group's per-facet autofill. */
export interface PerFacetBuildResult {
  /** answersData patch, keyed like the app: `${ItemOID}_${repeatKey}` (repeatKey ≥ 1). */
  answers: { [answerKey: string]: any };
  /** itemGroupData rows: one per materialized facet instance. */
  itemGroupData: PerFacetItemGroupRow[];
  /** Clamped facet count to seed the control item / repeatingInstanceCounts. */
  clampedCount: number;
  /** Raw per-facet count before clamping (for capped-warning diagnostics). */
  facetCount: number;
  /** The measurement MetaKeys found on this group's member questions. */
  matchedMetaKeys: string[];
  /** OIDs this build owns/writes (drives merge/skip bookkeeping). */
  ownedOIDs: string[];
}

/** Parse the integer rise from a raw pitch like "6/12" or "6:12" -> 6. null if none. */
export function parsePitchRise(rawPitch: string | number | null | undefined): number | null {
  if (rawPitch == null) return null;
  const s = String(rawPitch).trim();
  const ratio = /(\d+)\s*[:/]\s*12/.exec(s);
  if (ratio) return parseInt(ratio[1], 10);
  const bare = /^\d+$/.exec(s);
  return bare ? parseInt(s, 10) : null;
}

/** A numeric pitch band derived from a CodeList item's label. */
interface PitchBand {
  min: number;
  max: number;
  codedValue: string;
}

/** Read a CodeList item's human label (Decode) from either shape SDM emits. */
function codeListItemLabel(item: any): string {
  const tt = item?.Decode?.TranslatedText;
  if (Array.isArray(tt)) return tt[0]?.text ?? '';
  return tt?.text ?? '';
}

/**
 * Derive a numeric pitch band from a CodeList item's label. Understands the label
 * families used by production pitch CodeLists (ranges are in rise-per-12):
 *   "< 4:12 (moderate)"        -> (-inf, 4]
 *   "5:12 - 7:12 (standard)"   -> [5, 7]
 *   "8:12 - 11:12 (steep)"     -> [8, 11]
 *   "12:12+ (very steep)"      -> [12, +inf)
 *   "4/12" / "6/12"            -> [4,4] / [6,6]
 *   "7-8/12"                   -> [7, 8]
 *   "<3/12"                    -> (-inf, 3]
 * The rise numbers are the numerators; the "/12" or ":12" run denominators are
 * stripped first so they are never mistaken for a rise. Returns null when no rise
 * number can be parsed. Nothing here is hardcoded to a specific CodeList id.
 */
function parsePitchBand(item: any): PitchBand | null {
  const label = codeListItemLabel(item);
  if (!label) return null;
  // Strip the "/12" | ":12" (or any) run-denominator so only the rise numbers remain.
  const stripped = label.replace(/[:/]\s*\d+/g, ' ');
  const nums = (stripped.match(/\d+/g) || []).map((x) => parseInt(x, 10));
  if (nums.length === 0) return null;

  const codedValue = String(item.CodedValue ?? '');
  const hasLess = /</.test(label);
  const hasPlus = /\+/.test(label);

  if (hasLess) return { min: -Infinity, max: nums[nums.length - 1], codedValue };
  if (hasPlus) return { min: nums[0], max: Infinity, codedValue };
  if (nums.length >= 2) return { min: nums[0], max: nums[1], codedValue };
  return { min: nums[0], max: nums[0], codedValue };
}

/**
 * Resolve a raw pitch ("X/12") to the matching CodeList item's CodedValue by parsing
 * each item's label into a numeric rise band and returning the band that contains the
 * rise. Returns null when the pitch is unparseable, the list is empty, or no band
 * contains the rise. Pure — derives bands purely from the passed CodeList item labels.
 */
export function resolvePitchToCodeListValue(
  rawPitch: string | number | null | undefined,
  codeListItems: any[] | null | undefined
): string | null {
  const rise = parsePitchRise(rawPitch);
  if (rise === null || !Array.isArray(codeListItems)) return null;
  for (const item of codeListItems) {
    const band = parsePitchBand(item);
    if (band && rise >= band.min && rise <= band.max) {
      return band.codedValue;
    }
  }
  return null;
}

/**
 * Build the ODM CodeList array (with IsEnabled flags) for a coded answer — mirrors
 * Requirements_v3.buildEntryForTemplateOid. Returns null when the CodeList can't be
 * resolved (caller then stores the raw value only).
 */
export function buildCodeListForValue(
  codeListOID: string,
  codeListDef: any,
  codedValue: string,
  repeatKey: number
): any[] | null {
  if (!codeListDef?.CodeListItem) return null;
  const selected = new Set(
    String(codedValue).split(',').map((s) => s.trim()).filter(Boolean)
  );
  return [{
    OID: codeListOID,
    Name: codeListOID,
    DataType: codeListDef.DataType,
    CodeListItem: codeListDef.CodeListItem.map((it: any) => ({
      ...it,
      IsEnabled: selected.has(String(it.CodedValue ?? '')),
    })),
    ItemGroupRepeatKey: repeatKey,
  }];
}

/**
 * Resolve a raw measurement value for one coded (select) question. When the value is
 * a pitch that maps into a CodeList band, returns the coded value + built CodeList;
 * otherwise returns null so the caller stores the raw value. Generic — the only
 * coded measurement in the per-facet vocabulary is pitch, but this is keyed off the
 * question having a CodeListRef, not off any OID/trade.
 */
export function resolveCodedMeasurement(
  itemDef: any,
  rawValue: string | number,
  codeLists: any[],
  repeatKey: number
): { value: string; codeList: any[] } | null {
  const codeListOID = itemDef?.CodeListRef?.CodeListOID;
  if (!codeListOID) return null;
  const codeListDef = (codeLists || []).find((cl: any) => cl?.OID === codeListOID);
  const coded = resolvePitchToCodeListValue(rawValue, codeListDef?.CodeListItem);
  if (coded === null) return null;
  const codeList = buildCodeListForValue(codeListOID, codeListDef, coded, repeatKey);
  if (!codeList) return null;
  return { value: coded, codeList };
}

/** Build one answersData entry + matching ODM ItemData row for a resolved value. */
function buildAnswerEntry(
  oid: string,
  itemGroupOID: string,
  repeatKey: number,
  valueStr: string,
  dataType: string,
  codeList: any[] | null
): { entry: any; itemData: any } {
  const entry: any = {
    value: valueStr,
    itemOID: oid,
    itemGroupOID,
    repeatKey,
    dataType,
    originalItemData: { ItemOID: oid, Value: valueStr, ItemGroupRepeatKey: repeatKey },
    isRoofLensPrePopulated: true,
  };
  const itemData: any = { ItemOID: oid, Value: valueStr, ItemGroupRepeatKey: repeatKey };
  if (codeList) {
    entry.CodeList = codeList;
    itemData.CodeList = codeList;
  }
  return { entry, itemData };
}

/**
 * GENERIC per-facet builder. Given the per-facet MetaKey maps and ONE repeating
 * ItemGroupDef, produce the answersData / itemGroupData patches for that group by
 * matching each member question's ItemDef.MetaKey to the per-facet vocabulary.
 *
 * Returns null when the group declares no member question with a per-facet MetaKey
 * (i.e. it isn't a measurement facet group) — the caller then skips it. No trade
 * check, no OID constants: any repeating group whose members carry per-facet
 * MetaKeys is filled.
 *
 * - repeatKey is 1-based (facet index + 1), matching how the app keys instances.
 * - Coded member questions (CodeListRef) get their pitch resolved to a CodeList
 *   CodedValue; other members store the (already rounded) raw value.
 * - Instances beyond MAX_FACET_INSTANCES are dropped (clamped) to match the UI cap.
 */
export function buildPerFacetAnswers(
  perFacet: Array<Record<string, string | number>>,
  repeatingGroupDef: any,
  itemDefsByOid: Record<string, any>,
  codeLists: any[]
): PerFacetBuildResult | null {
  if (!repeatingGroupDef?.ItemRef || !Array.isArray(perFacet) || perFacet.length === 0) {
    return null;
  }
  const repeatingGroupOID: string = repeatingGroupDef.OID;

  // Member questions of this group that carry a per-facet measurement MetaKey.
  const members = repeatingGroupDef.ItemRef
    .map((ref: any) => itemDefsByOid[ref.ItemOID])
    .filter((def: any) => def && def.MetaKey && PER_FACET_METAKEYS.has(def.MetaKey));

  if (members.length === 0) {
    return null; // Not a measurement facet group — generic skip.
  }

  const matchedMetaKeys = Array.from(new Set<string>(members.map((d: any) => d.MetaKey)));
  const clampedCount = Math.min(perFacet.length, MAX_FACET_INSTANCES);

  const answers: { [answerKey: string]: any } = {};
  const itemGroupData: PerFacetItemGroupRow[] = [];
  const ownedOIDs = new Set<string>();

  for (let i = 0; i < clampedCount; i++) {
    const repeatKey = i + 1;
    const facetVals = perFacet[i] || {};
    const itemData: any[] = [];

    for (const def of members) {
      const metaKey: string = def.MetaKey;
      const raw = facetVals[metaKey];
      if (raw === undefined || raw === null || String(raw) === '') {
        continue;
      }
      const oid: string = def.OID;
      const dataType: string = def.DataType || 'integer';

      let valueStr = String(raw);
      let codeList: any[] | null = null;
      if (def.CodeListRef?.CodeListOID) {
        const coded = resolveCodedMeasurement(def, raw, codeLists, repeatKey);
        if (coded) {
          valueStr = coded.value;
          codeList = coded.codeList;
        } else {
          // Coded question but no CodeList band covers this value — skip it rather than
          // write an un-coded raw string the select renderer/backend can't interpret.
          continue;
        }
      }

      const { entry, itemData: row } = buildAnswerEntry(
        oid, repeatingGroupOID, repeatKey, valueStr, dataType, codeList
      );
      answers[`${oid}_${repeatKey}`] = entry;
      itemData.push(row);
      ownedOIDs.add(oid);
    }

    if (itemData.length > 0) {
      itemGroupData.push({
        ItemGroupOID: repeatingGroupOID,
        ItemGroupRepeatKey: repeatKey,
        ParentItemGroupRepeatKey: 0,
        ItemData: itemData,
      });
    }
  }

  return {
    answers,
    itemGroupData,
    clampedCount,
    facetCount: perFacet.length,
    matchedMetaKeys,
    ownedOIDs: Array.from(ownedOIDs),
  };
}
