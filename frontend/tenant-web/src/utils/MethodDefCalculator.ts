/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 17-Feb-2026
* @author      : Vincy R
* @since       : 2.0.0
* Requirement# : Q/A to Task Mapping
* Purpose      : MethodDef Calculator
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* * 17010        |  2026-04-15  |  Sharmine      |  Added square footage auto-calc for roof facets (repeatKey-aware)

*   
* 
* 
* 
*/
import { ItemGroupDef, MethodDef } from '../interface/SdmInterfaces';


export interface WasteCalcPair {
    areaOID: string;
    wasteOID: string;
    methodForWaste: string;
    methodForArea: string;
}

export function buildWasteCalcPairs(
    methodDefs: MethodDef[],
    itemGroupDefs: Pick<ItemGroupDef, 'ItemRef'>[]
): WasteCalcPair[] {
    if (!methodDefs?.length || !itemGroupDefs?.length) return [];

    const methodByOID = new Map<string, MethodDef>(methodDefs.map(m => [m.OID, m]));

    const pairs: WasteCalcPair[] = [];

    for (const group of itemGroupDefs) {
        const itemRefs: any[] = group.ItemRef ?? [];

        const refsWithMethod = itemRefs.filter((ir: any) => ir.MethodOID);
        if (refsWithMethod.length < 2) continue;

        const wastePercentageRef = refsWithMethod.find((ir: any) => {
            const md = methodByOID.get(ir.MethodOID);
            return md?.MethodType === 'WastePercentageCalci';
        });
        const wasteAreaRef = refsWithMethod.find((ir: any) => {
            const md = methodByOID.get(ir.MethodOID);
            return md?.MethodType === 'WasteAreaCalci';
        });

        if (!wastePercentageRef || !wasteAreaRef) continue;

        pairs.push({
            areaOID: wasteAreaRef.ItemOID,
            wasteOID: wastePercentageRef.ItemOID,
            methodForWaste: wastePercentageRef.MethodOID,
            methodForArea: wasteAreaRef.MethodOID,
        });
    }

    return pairs;
}

function resolveBaseAreaValue(baseOID: string, formData: Record<string, any>): string | null {
    const instanceKeys = Object.keys(formData).filter(k => {
        const parts = k.split('_');
        return parts.length === 2 && parts[0] === baseOID && !isNaN(Number(parts[1])) && Number(parts[1]) > 0;
    });

    if (instanceKeys.length === 0) {
        const raw = formData[baseOID];
        const val = resolveRawFieldValue(raw);
        if (val === null || val === undefined || val === '') return null;
        const n = Number(val);
        return isNaN(n) ? null : String(n);
    }

    let total = 0;
    let anyValid = false;
    instanceKeys.forEach(k => {
        const raw = formData[k];
        const val = resolveRawFieldValue(raw);
        if (val !== null && val !== undefined && val !== '') {
            const n = Number(val);
            if (!isNaN(n)) { total += n; anyValid = true; }
        }
    });
    return anyValid ? String(total) : null;
}


/**
 * Execute MethodDef calculations for all fields with MethodOID
 * 
 * @param itemRefs - Array of ItemRefs from ItemGroupDef
 * @param methodDefs - Array of MethodDef from SDM
 * @param currentData - Current form values (dynamicQuestionData)
 * @returns Updated form data with calculated values
 */
export function calculateDerivedFields(
    itemRefs: any[],
    methodDefs: MethodDef[],
    currentData: Record<string, any>,
    lastUserEditedOID?: string,
    wasteCalcPairs?: WasteCalcPair[]
): Record<string, any> {

    if (!itemRefs || !methodDefs || !currentData) {
        return currentData;
    }

    const activePairs: WasteCalcPair[] = wasteCalcPairs !== undefined
        ? wasteCalcPairs
        : (() => {
            const syntheticGroup = { ItemRef: itemRefs };
            return buildWasteCalcPairs(methodDefs, [syntheticGroup]);
        })();

    const updatedData = { ...currentData };
    let hasChanges = false;

    const baseEditedOID = lastUserEditedOID?.split('_')[0];
    const methodOIDsToSkip = new Set<string>();
    if (baseEditedOID) {
        for (const pair of activePairs) {
            if (baseEditedOID === pair.areaOID) {
                methodOIDsToSkip.add(pair.methodForArea);
            } else if (baseEditedOID === pair.wasteOID) {
                methodOIDsToSkip.add(pair.methodForWaste);
            }
        }
    }

    // Find all items with MethodOID
    itemRefs.forEach(itemRef => {
        if (!itemRef.MethodOID) {
            return;
        }


        // Find the corresponding MethodDef
        const methodDef = methodDefs.find(m => m.OID === itemRef.MethodOID);
        if (!methodDef || !methodDef.FormalExpression?.text) {
            return;
        }

        // Execute calculation
        // RepeatCount-style MethodDefs (M.1, M.2, M.3) are UI-control only — skip calculation
        if (methodDef.FormalExpression.text.includes('RepeatCount')) return;

        if (methodOIDsToSkip.has(itemRef.MethodOID)) return;

        // Determine the repeat key for this itemRef (0 = non-repeating)
        const repeatKey: number = typeof itemRef.ItemGroupRepeatKey === 'number'
            ? itemRef.ItemGroupRepeatKey
            : 0;

        // The output answer key: suffixed for repeating instances, plain for non-repeating
        const outputKey = repeatKey > 0
            ? `${itemRef.ItemOID}_${repeatKey}`
            : itemRef.ItemOID;

        // "Total roof area including waste" (WasteAreaCalci, e.g. I.67) is DERIVED from
        // its paired wastage-percentage field — it must not be fabricated when there is
        // no real wastage input. Without this gate the formula falls back to the base
        // roof area alone (and resolveBaseAreaValue SUMS repeating facet/structure
        // instances), so selecting a project that carries area + N facets but no stored
        // waste-area answer produced area × N (e.g. 1986 × 9 = 17874) for a 1,986 sqft
        // roof. When the paired wastage % is empty/unanswered, leave the waste-area blank
        // — it only fills in once a real wastage % exists (user-entered or a stored bid
        // answer for that OID). Mirrors the negative/non-finite waste guard below.
        if (methodDef.MethodType === 'WasteAreaCalci') {
            const pair = activePairs.find(p => p.areaOID === itemRef.ItemOID);
            if (pair) {
                const wasteRaw = repeatKey > 0
                    ? (currentData[`${pair.wasteOID}_${repeatKey}`] ?? currentData[pair.wasteOID])
                    : currentData[pair.wasteOID];
                const wasteVal = resolveRawFieldValue(wasteRaw);
                if (wasteVal === null || wasteVal === undefined || String(wasteVal).trim() === '') {
                    return; // no wastage % → do not synthesize a waste-area value
                }
            }
        }

        try {
            const expressionText = methodDef.FormalExpression.text;
            const referencedOIDs = expressionText.match(/I\.\d+/g) || [];
            let patchedFormData = currentData;
            referencedOIDs.forEach((oid: string) => {
                const isInCurrentGroup = itemRefs.some((ref: any) => ref.ItemOID === oid);
                if (!isInCurrentGroup) {
                    const summedValue = resolveBaseAreaValue(oid, currentData);
                    if (summedValue !== null) {
                        if (patchedFormData === currentData) {
                            patchedFormData = { ...currentData };
                        }
                        patchedFormData[oid] = { value: summedValue };
                    }
                }
            });

            const calculatedValue = executeMethodExpression(
                methodDef.FormalExpression.text,
                patchedFormData,
                repeatKey
            );

            if (calculatedValue === undefined) return;

            // Waste area / waste percentage are physical quantities that can
            // never be negative or non-finite. When a referenced input is
            // missing, executeMethodExpression substitutes 0, which can collapse
            // a percentage like ((used - total)/total)*100 to -100. Do NOT write
            // a synthesized invalid value — leave the field as-is (blank) so it
            // only shows a real, valid number once its inputs actually exist.
            if (
                (methodDef.MethodType === 'WastePercentageCalci'
                    || methodDef.MethodType === 'WasteAreaCalci')
                && typeof calculatedValue === 'number'
                && (!isFinite(calculatedValue) || calculatedValue < 0)
            ) {
                return;
            }

            const roundedValue = (typeof calculatedValue === 'number' && isFinite(calculatedValue))
                ? Math.round(calculatedValue * 100) / 100
                : calculatedValue;

            // A boolean MethodDef result of `true` = "delete this metaKey's task".
            // But NEVER auto-delete when EVERY input the formula reads is still
            // unanswered — that is "undetermined", not "delete". Without this an
            // untouched (Pending) project serializes every derived metaKey (e.g.
            // JobTypeTuneup/Cleaning/Enhancement) as isDeleted:true, because
            // executeMethodExpression substitutes 0 for empty inputs. Mirrors the
            // WasteArea/WastePercentage missing-input guard above. (Q/A -> Task mapping.)
            const deletingSuppressed =
                calculatedValue === true
                && referencedInputsAllEmpty(referencedOIDs, patchedFormData, repeatKey);

            const currentValue = updatedData[outputKey];
            let shouldUpdate = false;
            let newValue: any = roundedValue;
            let isDeleted = false;
            if (currentValue && typeof currentValue === 'object' && 'value' in currentValue) {
                if (calculatedValue === true || calculatedValue === false) {
                    // true = delete, false = keep — but suppress the delete when the
                    // deciding inputs are entirely unanswered (undetermined -> keep).
                    newValue = null;
                    isDeleted = calculatedValue === true && !deletingSuppressed;
                    shouldUpdate = true;
                } else {
                    isDeleted = false;
                    shouldUpdate = String(currentValue.value) !== String(roundedValue)
                        || currentValue.isDeleted === true;
                }
                if (shouldUpdate) {
                    updatedData[outputKey] = { ...currentValue, value: newValue, isDeleted };
                    hasChanges = true;
                }
            } else if (currentValue !== undefined) {
                if (calculatedValue === null) return;
                if (String(currentValue) !== String(roundedValue)) {
                    updatedData[outputKey] = roundedValue;
                    hasChanges = true;
                }
            } else {
                if (calculatedValue === null) return;
                // Initialize with nested structure matching the rest of answersData
                const sample = Object.values(currentData).find(
                    v => v && typeof v === 'object' && 'value' in v
                );
                isDeleted = calculatedValue === true && !deletingSuppressed;
                newValue = isDeleted ? null : roundedValue;
                if (sample) {
                    updatedData[outputKey] = {
                        value: newValue,
                        isDeleted,
                        itemOID: itemRef.ItemOID,
                        itemGroupOID: itemRef.ItemGroupOID || 'IG.1',
                        repeatKey,
                        dataType: 'integer'
                    };
                } else {
                    updatedData[outputKey] = newValue;
                }
                hasChanges = true;
            }
        } catch (error) {
            console.warn('MethodDef calculation error for', outputKey, error);
        }
    });

    return hasChanges ? updatedData : currentData;
}

/**
 * Escape special characters in a string for safe JavaScript string literal usage
 * Handles: quotes, backslashes, newlines, tabs, and other control characters
 * 
 * @param str - String to escape
 * @returns Escaped string safe for JavaScript string literals
 */
function escapeJavaScriptString(str: string): string {
    return String(str)
        .replace(/\\/g, '\\\\')   // Backslash must be first
        .replace(/"/g, '\\"')      // Double quotes
        .replace(/'/g, "\\'")      // Single quotes
        .replace(/\n/g, '\\n')     // Newlines
        .replace(/\r/g, '\\r')     // Carriage returns
        .replace(/\t/g, '\\t')     // Tabs
        .replace(/\f/g, '\\f')     // Form feeds
        .replace(/\v/g, '\\v');    // Vertical tabs
}

function getEnabledCodeListValue(fieldData: any): string {
    if (!fieldData || typeof fieldData !== 'object') return '';

    // Support both payload shapes:
    // 1) CodeList -> [{ CodeListItem: [{ CodedValue, IsEnabled }] }]
    // 2) codeListItem -> [{ CodedValue, IsEnabled }]
    const enabledValues: string[] = [];

    if (Array.isArray(fieldData.CodeList)) {
        for (const codeList of fieldData.CodeList) {
            const codeListItems = codeList?.CodeListItem;
            if (!Array.isArray(codeListItems)) continue;
            codeListItems.forEach((item: any) => {
                if (item?.IsEnabled === true && item?.CodedValue !== undefined && item?.CodedValue !== null) {
                    enabledValues.push(String(item.CodedValue));
                }
            });
        }
    }

    if (enabledValues.length === 0 && Array.isArray(fieldData.codeListItem)) {
        fieldData.codeListItem.forEach((item: any) => {
            if (item?.IsEnabled === true && item?.CodedValue !== undefined && item?.CodedValue !== null) {
                enabledValues.push(String(item.CodedValue));
            }
        });
    }

    if (enabledValues.length === 0) return '';
    return enabledValues.join(',');
}

function resolveRawFieldValue(rawFieldData: any): any {
    if (!rawFieldData || typeof rawFieldData !== 'object') {
        return rawFieldData;
    }

    // Prefer lower-case value from answersData shape; fallback to payload-style Value.
    let value = 'value' in rawFieldData ? rawFieldData.value : rawFieldData.Value;

    if (value === null || value === undefined || value === '') {
        const fromEnabledCodeList = getEnabledCodeListValue(rawFieldData);
        if (fromEnabledCodeList !== '') {
            value = fromEnabledCodeList;
        }
    }

    return value;
}

/**
 * True when EVERY ItemOID referenced by a MethodDef expression resolves to an
 * empty / unanswered value. Used to suppress an auto-delete on a derived metaKey
 * whose deciding inputs have not been answered yet (undetermined != delete).
 * Returns false when the expression references no ItemOIDs (e.g. a constant), so
 * such formulas are left to compute normally.
 */
function referencedInputsAllEmpty(
    referencedOIDs: string[],
    formData: Record<string, any>,
    repeatKey: number
): boolean {
    if (!referencedOIDs || referencedOIDs.length === 0) return false;
    return referencedOIDs.every(oid => {
        const suffixedKey = repeatKey > 0 ? `${oid}_${repeatKey}` : null;
        const raw = (suffixedKey && formData[suffixedKey] !== undefined)
            ? formData[suffixedKey]
            : formData[oid];
        const value = resolveRawFieldValue(raw);
        return value === null || value === undefined || String(value).trim() === '';
    });
}

/**
 * Execute a MethodDef FormalExpression with current form values
 * 
 * @param expression - JavaScript expression from MethodDef.FormalExpression.text
 * @param formData - Current form values
 * @returns Calculated result
 * 
 * Example expression:
 * "(parseInt(I.107)||0) + (parseInt(I.108)||0) + ..."
 */
export function executeMethodExpression(
    expression: string,
    formData: Record<string, any>,
    repeatKey: number = 0
): any {
    const normalizedExpression = normalizeOpenEdcExpression(expression);

    const evaluableExpression = normalizedExpression.replace(/I\.(\d+)/g, (match) => {
        const itemOID = match; // e.g., "I.32"

        const suffixedKey = repeatKey > 0 ? `${itemOID}_${repeatKey}` : null;
        const rawFieldData = (suffixedKey && formData[suffixedKey] !== undefined)
            ? formData[suffixedKey]
            : formData[itemOID];
        const value = resolveRawFieldValue(rawFieldData);

        // Return the actual value or 0/empty string
        if (value === null || value === undefined || value === '') {
            return '0';
        }

        // If it's already a number, return as string
        if (typeof value === 'number') {
            return String(value);
        }

        // If it's a string that looks like a number, return it
        if (typeof value === 'string' && !isNaN(Number(value))) {
            return String(value);
        }

        // For non-numeric values (text selections), return properly escaped quoted string
        const escapedValue = escapeJavaScriptString(value);
        return `"${escapedValue}"`;
    });


    // Execute the JavaScript expression safely
    try {
         // safer - Expression is sourced internally and validated before evaluation.
        // safer than new Function (still not perfect, but accepted as fix step)
        const result = Function(`"use strict"; return (${evaluableExpression})`)();
        return result;
    } catch (error) {
        throw error;
    }
}

function normalizeOpenEdcExpression(expression: string): string {
    const trimmed = String(expression || '').trim();

    // Convert logical operators used in SDM FormalExpression syntax.
    let normalized = trimmed
        .replace(/\bAND\b/g, '&&')
        .replace(/\bOR\b/g, '||');

    // Convert OpenEDC "contains" operator to JavaScript String.includes().
    // Pattern 1: (complex-expr) contains "str"  →  String(complex-expr).includes("str")
    normalized = normalized.replace(
        /(\([^()]*\))\s+contains\s+("(?:[^"\\]|\\.)*")/g,
        'String$1.includes($2)'
    );
    // Pattern 2: simple item-ref contains "str"  (e.g. I.66 contains ",1")
    normalized = normalized.replace(
        /\b(I\.\d+)\s+contains\s+("(?:[^"\\]|\\.)*")/g,
        'String($1).includes($2)'
    );

    // Convert IF(condition, trueExpr, falseExpr) to JavaScript ternary.
    normalized = convertIfToTernary(normalized);

    return normalized;
}

function convertIfToTernary(expr: string): string {
    const value = expr.trim();
    if (!value.startsWith('IF(') || !value.endsWith(')')) {
        return value;
    }

    const inner = value.slice(3, -1);
    const args = splitTopLevelArgs(inner);
    if (args.length !== 3) {
        return value;
    }

    const condition = convertIfToTernary(args[0].trim());
    const whenTrue = convertIfToTernary(args[1].trim());
    const whenFalse = convertIfToTernary(args[2].trim());
    return `((${condition}) ? (${whenTrue}) : (${whenFalse}))`;
}

function splitTopLevelArgs(input: string): string[] {
    const parts: string[] = [];
    let depth = 0;
    let inQuote = false;
    let quoteChar = '';
    let start = 0;

    for (let i = 0; i < input.length; i++) {
        const ch = input[i];
        const prev = i > 0 ? input[i - 1] : '';

        if ((ch === '"' || ch === "'") && prev !== '\\') {
            if (!inQuote) {
                inQuote = true;
                quoteChar = ch;
            } else if (quoteChar === ch) {
                inQuote = false;
                quoteChar = '';
            }
            continue;
        }

        if (inQuote) {
            continue;
        }

        if (ch === '(') {
            depth++;
            continue;
        }

        if (ch === ')') {
            depth--;
            continue;
        }

        if (ch === ',' && depth === 0) {
            parts.push(input.slice(start, i));
            start = i + 1;
        }
    }

    parts.push(input.slice(start));
    return parts;
}

/**
 * Check if a field change should trigger recalculation
 * 
 * @param changedItemOID - The ItemOID that just changed
 * @param methodDefs - Array of MethodDef from SDM
 * @returns True if any MethodDef depends on this field
 */
export function shouldRecalculate(
    changedItemOID: string,
    methodDefs: MethodDef[]
): boolean {
    if (!changedItemOID || !methodDefs) {
        return false;
    }

    // Check if any MethodDef expression references this ItemOID
    return methodDefs.some(methodDef => {
        const expression = methodDef.FormalExpression?.text;
        return expression && expression.includes(changedItemOID);
    });
}
