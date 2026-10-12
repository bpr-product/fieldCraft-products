/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * SDM CollectionExceptionCondition helpers — evaluates FormDef / ItemRef exclusion
 * from ConditionDef.FormalExpression using live answers (no template-specific OIDs).
 */

import { ConditionDef, ItemGroupDef } from '../../../../interface/SdmInterfaces';

export function getPlainAnswerForItem(
  itemOID: string,
  answersData: Record<string, any>
): string | undefined {
  const a = answersData[itemOID];
  if (a === undefined || a === null) return undefined;
  if (typeof a === 'object') {
    // Check CodeList first — singleselect stores selection here, value is ""
    if (a.CodeList?.[0]?.CodeListItem?.length) {
      const enabled = a.CodeList[0].CodeListItem.filter((c: any) => c.IsEnabled);
      // FIX: if CodeList exists but NOTHING is enabled, treat as no answer
      if (enabled.length === 0) return undefined;
      if (enabled.length === 1) return String(enabled[0].CodedValue);
      if (enabled.length > 1) return enabled.map((c: any) => c.CodedValue).sort().join(',');
    }
    // FIX: if the answer object exists but has empty/null value AND no CodeList,
    // treat as no answer so condition expressions see it as unset
    if (a.value !== undefined && a.value !== null && a.value !== '') {
      return String(a.value);
    }
  }
  return undefined;
}
/**
 * SDM semantics: when the FormalExpression evaluates to TRUE, the collection is EXCLUDED (hidden).
 * Expressions use OpenEDC-style item refs e.g. !(I.1 == "1").
 */
export function isCollectionExcludedByFormalExpression(
  expression: string | undefined,
  answersData: Record<string, any>
): boolean {
  if (!expression?.trim()) return false;
  try {
    const substituted = expression.replace(/\b(I\.\d+)\b/g, (match) => {
      const v = getPlainAnswerForItem(match, answersData);
      if (v === undefined) return 'undefined';
      return JSON.stringify(v);
    });
    const fn = new Function(`return (${substituted});`);
    return Boolean(fn());
  } catch {
    return false;
  }
}

export function collectItemOIDsFromItemGroupDef(ig: ItemGroupDef | undefined): string[] {
  if (!ig?.ItemRef?.length) return [];
  return ig.ItemRef.map((r: any) => r.ItemOID).filter(Boolean);
}

export function getItemOIDsHiddenAfterBranchChange(
  steps: Array<{
    id: string;
    collectionExceptionConditionOID?: string | null;
    itemGroupDef?: ItemGroupDef;
  }>,
  conditionDefs: ConditionDef[],
  previousAnswers: Record<string, any>,
  nextAnswers: Record<string, any>
): string[] {
  const toClear = new Set<string>();

  steps.forEach(step => {
    if (step.id === 'summary' || !step.collectionExceptionConditionOID || !step.itemGroupDef) {
      return;
    }
    const cond = conditionDefs.find(c => c.OID === step.collectionExceptionConditionOID);
    const expr = cond?.FormalExpression?.text;
    const wasExcluded = isCollectionExcludedByFormalExpression(expr, previousAnswers);
    const nowExcluded = isCollectionExcludedByFormalExpression(expr, nextAnswers);
    if (!wasExcluded && nowExcluded) {
      collectItemOIDsFromItemGroupDef(step.itemGroupDef).forEach(oid => toClear.add(oid));
    }
  });

  return Array.from(toClear);
}
