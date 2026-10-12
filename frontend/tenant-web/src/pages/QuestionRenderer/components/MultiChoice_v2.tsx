/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 2-Nov-2024
* @author      : Vaisnave I M
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Multi Choice Input Component
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 21928  | 2026-06-24 | Karthika S | Fix stale validation errors persisting after field is filled in Boolean/Q&A renderer
*/

import React, { useContext, useEffect, useState, useRef } from "react";
import { Box, FormControl, FormHelperText, Dialog, DialogContent, IconButton } from "@mui/material";
import { CodeList, CodeListItem, ItemData } from "../../../interface/SdmInterfaces";
import { FEATURE_FLAGS } from '../../../constants/FeatureFlag';
import { getImageUrl } from '../../../utils/imageRenderer';
import ImageZoomDialog from './ImageZoomDialog';
import { validateRangeCheck } from '../../../utils/rangeCheckValidator';
import { CatalogueImageContext } from '../../../context/CatalogueImageContext';
import './MultiChoice_v2.scss';



function parseExclusiveOptionGuard(expression: string): string | null {
    if (!expression) return null;
    const match = expression.match(/!\s*\(\s*SetSelectedOption\s*:\s*(\S+)\s*\)/);
    return match ? match[1] : null;
}

function resolveExclusiveCodedValue(
    methodDefs: any[] | undefined,
    itemRefMethodOID?: string   // ADD PARAMETER
): string | null {
    if (!methodDefs || methodDefs.length === 0) return null;

    // If we have a specific MethodOID for this itemRef, only check that one
    if (itemRefMethodOID) {
        const specificMd = methodDefs.find(md => md?.OID === itemRefMethodOID);
        if (!specificMd || specificMd.MethodType !== 'ExclusiveOptionGuard') return null;
        return parseExclusiveOptionGuard(specificMd?.FormalExpression?.text ?? '');
    }

    // Fallback: no MethodOID means no exclusive behavior
    return null;
}

export function computeNextState(
    currentState: string[],
    value: string,
    checked: boolean,
    exclusiveValue: string | null
): string[] {
    const isExclusive = exclusiveValue !== null && value === exclusiveValue;
    if (checked) {
        if (isExclusive) {
            return [exclusiveValue!];
        } else {
            const without = currentState.filter(v => v !== exclusiveValue);
            return without.includes(value) ? without : [...without, value];
        }
    } else {
        return currentState.filter(v => v !== value);
    }
}
const MultiChoice_v2: React.FC<any> = ({ OID, itemDef, conditionDef, codeList, onItemchange,
    showStatus, itemDatas, error, isRepeatItemGrp, itgrpInd, methodDefs, readonly = false,
    imageBaseUrl = '', itemRefMethodOID }) => {  // ADD itemRefMethodOID

    const exclusiveCodedValue = React.useMemo(
        () => resolveExclusiveCodedValue(methodDefs, itemRefMethodOID),  // PASS IT
        [methodDefs, itemRefMethodOID]
    );

    const catalogueSasMap = useContext(CatalogueImageContext);

    //Need to change to itemData
    const [, setItemData] = useState<ItemData>(() => {
        // Find the correct itemData for this question
        const foundItemData = itemDatas?.find(item => item?.ItemOID === itemDef?.OID);
        return foundItemData || { ItemOID: itemDef?.OID, Value: '', CodeList: codeList, MetaKey: '', ItemGroupRepeatKey: 0 };
    });
    const [codeData, setcodeData] = useState<CodeList[]>(() => {


        if (codeList && codeList.length > 0) {
            codeList.forEach((cl, idx) => {

                cl.CodeListItem?.forEach((item, itemIdx) => {

                });
            });
        }
        return codeList;
    });
    const [state, setState] = useState<any>([] as any[]);
    const [zoomedImage, setZoomedImage] = useState<{ url: string, label: string } | null>(null);
    const [softWarning, setSoftWarning] = useState<string | null>(null);

    const hasInitializedRef = useRef(false);
    const wasResetRef = useRef(false);
    // Root-caused 2026-09-08: deselecting an option (e.g. Cleaning on the I.66 job-type
    // question) immediately reverted back to selected. The `itemDatas`-sync effects below
    // restore `state` from the `itemDatas` prop whenever that prop's reference changes — but
    // they have no way to know whether the incoming `itemDatas` reflects an edit NEWER than
    // the one this component just made, or an OLDER (stale) echo of the parent's state still
    // catching up. A deselect (fewer values) always loses that race against a stale, fuller
    // `itemDatas`, while a select (more values) never conflicts with it — matching exactly
    // what was reported: only unselecting failed. I.66 drives the most conditional child
    // questions of any question in this form, so it triggers the most parent re-renders per
    // click, making a stale-prop echo far more likely — explaining why this surfaced ONLY on
    // this question.
    //
    // REVISED 2026-09-08: a first attempt gave local edits a fixed settle window (mirroring
    // the DB-seed/hydration race fixes elsewhere this session). Confirmed live via console
    // that this was NOT sufficient — the parent's real catch-up round-trip for I.66
    // (cascading through its many conditional child questions) took LONGER than the window
    // in the actual app, so the window simply expired and the stale restore still went
    // through. No FIXED duration can bound an arbitrarily slow parent round-trip.
    //
    // Fixed properly, without any timing assumption: record the value this component held
    // JUST BEFORE a local edit (`pendingEditFromValueRef`). Any `itemDatas` restore attempt
    // that still echoes exactly that pre-edit value is, by definition, a stale echo — skip
    // it, no matter how long it takes to catch up. The moment an incoming value differs from
    // the pre-edit one — whether because the parent has caught up to OUR edit, or because a
    // genuinely different external change arrived — accept it and clear the guard; there is
    // no longer a stale value left to filter out.
    const pendingEditFromValueRef = useRef<string | null>(null);

    useEffect(() => {
        if (!itemDef?.RangeCheck || !Array.isArray(itemDef.RangeCheck)) return;
        const savedData = itemDatas?.find((item: any) => item?.ItemOID === itemDef?.OID);
        const initialValues: string[] = [];
        savedData?.CodeList?.forEach((cl: any) => {
            cl.CodeListItem?.forEach((item: any) => {
                if (item.IsEnabled) initialValues.push(item.CodedValue);
            });
        });
        if (!initialValues.length) return;
        let warning: string | null = null;
        for (const v of initialValues) {
            const result = validateRangeCheck(v, itemDef.RangeCheck);
            if (result.isSoftWarning && result.errorMessage) { warning = result.errorMessage; break; }
        }
        setSoftWarning(warning);
    }, []);

    // the component updates and can render the options properly
    useEffect(() => {
        if (codeList && codeList.length > 0) {
            const savedData = itemDatas?.find((item: any) => item?.ItemOID === itemDef?.OID);
            let hasSavedSelections = false;
            const savedSelectedValues: string[] = [];
            if (savedData?.CodeList && savedData.CodeList.length > 0) {
                savedData.CodeList.forEach((cl: any) => {
                    cl.CodeListItem?.forEach((item: any) => {
                        if (item.IsEnabled) {
                            hasSavedSelections = true;
                            savedSelectedValues.push(item.CodedValue);
                        }
                    });
                });
            }
            // See pendingEditFromValueRef's own doc. READ-ONLY here — this effect has
            // `codeList` as an EXTRA dependency the sibling itemDatas-sync effect below does
            // not, so it can fire on a codeList-only change while itemDatas itself is still
            // mid-cascade. If it were also responsible for CLEARING the guard, an extra firing
            // like that (whose derived selections don't happen to match the pending value for
            // reasons unrelated to the parent genuinely catching up) could clear the guard out
            // from under the sibling effect, right before a later stale echo of the SAME
            // pre-edit value arrives and slips through unguarded — confirmed live via console
            // (a SKIPPED line immediately followed by a RESTORING line for the identical
            // value). Only the dedicated effect below (deps: itemDatas + OID only, matching
            // exactly what the guard is meant to track) may clear it.
            const isStaleEchoOfPendingEdit =
                pendingEditFromValueRef.current !== null
                && [...savedSelectedValues].sort().join(',') === pendingEditFromValueRef.current;

            if (!hasSavedSelections) {
                if (!hasInitializedRef.current) {

                    hasInitializedRef.current = true;
                    const resetCodeList = codeList.map(cl => ({
                        ...cl,
                        CodeListItem: cl.CodeListItem.map((item: any) => ({
                            ...item,
                            IsEnabled: false
                        }))
                    }));
                    setcodeData(resetCodeList);

                    // Clear state if it has stale values
                    if (state.length > 0) {
                        setState([]);
                    }

                    wasResetRef.current = true;
                    // NOTE: Don't call onItemchange here - let user's first click be the trigger
                    // This prevents the reset loop that was deleting all child data
                }
            } else {
                // Has saved data - restore it
                hasInitializedRef.current = true;
                if (!wasResetRef.current && !isStaleEchoOfPendingEdit) {

                    setcodeData(codeList);

                    // Also restore state from saved selections
                    const selectedValues: string[] = [];
                    savedData.CodeList.forEach((cl: any) => {
                        cl.CodeListItem?.forEach((item: any) => {
                            if (item.IsEnabled) {
                                selectedValues.push(item.CodedValue);
                            }
                        });
                    });
                    if (selectedValues.length > 0) {
                        setState(selectedValues);
                    }
                }
            }
        }
    }, [codeList, itemDef?.OID, itemDatas]);

    // Update itemData when itemDatas prop changes (for saved data restoration)
    useEffect(() => {
        if (wasResetRef.current) {
            return;
        }

        const foundItemData = itemDatas?.find(item => item?.ItemOID === itemDef?.OID);

        // Check if foundItemData has actual selected values
        let hasActualSelections = false;
        const foundSelectedValues: string[] = [];
        if (foundItemData?.CodeList && foundItemData.CodeList.length > 0) {
            foundItemData.CodeList.forEach((codeList: any) => {
                codeList.CodeListItem?.forEach((item: any) => {
                    if (item.IsEnabled) {
                        hasActualSelections = true;
                        foundSelectedValues.push(item.CodedValue);
                    }
                });
            });
        }
        // See pendingEditFromValueRef's own doc — READ-ONLY here (the dedicated effect below
        // is the sole writer that clears it). Skip restoring from an itemDatas value that
        // still exactly echoes what this component held just before its own last local edit,
        // no matter how long it takes to arrive.
        const incomingValueKey = [...foundSelectedValues].sort().join(',');
        if (pendingEditFromValueRef.current !== null && incomingValueKey === pendingEditFromValueRef.current) {
            return;
        }

        if (foundItemData && hasActualSelections) {
            setItemData(foundItemData);

            setcodeData(foundItemData.CodeList);

            // Only update state if it's actually different and not empty
            if (foundSelectedValues.length > 0 && JSON.stringify([...foundSelectedValues].sort()) !== JSON.stringify([...state].sort())) {
                setState(foundSelectedValues);
            }
        } else {
            if (state.length > 0) {

                setState([]);
            }
            // Also reset codeData to template defaults (all unchecked)
            if (codeList && codeList.length > 0) {
                const resetCodeList = codeList.map(cl => ({
                    ...cl,
                    CodeListItem: cl.CodeListItem.map(item => ({
                        ...item,
                        IsEnabled: false
                    }))
                }));
                setcodeData(resetCodeList);
            }
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [itemDatas, itemDef?.OID]); // Removed state dependency to prevent infinite loops

    // Sole writer that clears pendingEditFromValueRef — see its own doc, and the doc on the
    // itemDatas-sync effect above, for why this must NOT be duplicated across effects with
    // different dependency arrays (codeList in particular): deps here are deliberately just
    // itemDatas + OID, exactly matching what the guard is meant to track (has the parent's
    // answer round-trip caught up to our edit yet?), decoupled from codeList's own,
    // independent re-renders.
    useEffect(() => {
        if (pendingEditFromValueRef.current === null) return;
        const foundItemData = itemDatas?.find((item: any) => item?.ItemOID === itemDef?.OID);
        const foundSelectedValues: string[] = [];
        foundItemData?.CodeList?.forEach((cl: any) => {
            cl.CodeListItem?.forEach((item: any) => {
                if (item.IsEnabled) foundSelectedValues.push(item.CodedValue);
            });
        });
        const incomingValueKey = [...foundSelectedValues].sort().join(',');
        if (incomingValueKey !== pendingEditFromValueRef.current) {
            pendingEditFromValueRef.current = null;
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [itemDatas, itemDef?.OID]);



    const handleChange = (event: React.ChangeEvent<HTMLInputElement>, indexes: any) => {
        try {
            const checked = event.target.checked;
            const value = event.target.value;

            // NOTE: Don't clear wasResetRef here - the parent's itemDatas still has stale data
            // Keep it set until component remounts (parent toggles again)

            // Prevent event bubbling that might interfere with nested components
            event.stopPropagation();

            // Find the correct CodeList for this question
            const questionCodeList = codeList?.find((cl: any) =>
                cl.OID === itemDef.CodeListRef?.CodeListOID ||
                cl.OID === itemDef.CodeListOID
            );

            if (!questionCodeList || !questionCodeList.CodeListItem) {
                console.error(`Cannot update CodeList - no valid CodeList found for question ${itemDef.OID}`);
                return;
            }

            // ── ExclusiveOptionGuard: compute next selection via pure function ──
            const newState: string[] = computeNextState(state, value, checked, exclusiveCodedValue);

            // No-op if selection unchanged
            if (JSON.stringify([...state].sort()) === JSON.stringify([...newState].sort())) {
                return;
            }

            // Values that were selected before but not after → their children must be hidden
            const removedValues = state.filter((v: string) => !newState.includes(v));

            const updatedTodos = codeData.map((cl: any) => ({
                ...cl,
                CodeListItem: cl.CodeListItem.map((item: any) => ({ ...item }))
            })); // Deep copy of codeData, not codeList
            const questionCodeListIndex = updatedTodos.findIndex((cl: any) => cl.OID === questionCodeList.OID);
            if (questionCodeListIndex === -1) {
                console.error(`Cannot find CodeList with OID ${questionCodeList.OID}`);
                return;
            }

            updatedTodos[questionCodeListIndex].CodeListItem = updatedTodos[questionCodeListIndex].CodeListItem.map((it: any) => ({
                ...it,
                IsEnabled: newState.includes(it.CodedValue)
            }));

            setState(newState);
            setcodeData(updatedTodos);
            wasResetRef.current = false;
            // Record the value we're editing AWAY FROM — see pendingEditFromValueRef's own
            // doc. Any itemDatas restore that still echoes exactly this value is stale,
            // however long it takes to arrive. Only ARM it on the first unconfirmed edit in
            // a rapid sequence (skip if already set): if the user clicks again before the
            // parent catches up to the FIRST click, overwriting the baseline to the
            // in-between state would let a stale echo of that first click slip through as
            // "not stale" (it would no longer match the newer baseline). Keeping the
            // original baseline until something other than it is observed covers any number
            // of rapid clicks correctly.
            if (pendingEditFromValueRef.current === null) {
                pendingEditFromValueRef.current = [...state].sort().join(',');
            }

            if (itemDef?.RangeCheck && Array.isArray(itemDef.RangeCheck)) {
                let warning: string | null = null;
                for (const selectedValue of newState) {
                    const result = validateRangeCheck(selectedValue, itemDef.RangeCheck);
                    if (result.isSoftWarning && result.errorMessage) {
                        warning = result.errorMessage;
                        break;
                    }
                }
                setSoftWarning(warning);
            } else {
                setSoftWarning(null);
            }

            // Keep Value in lockstep with the CodeList IsEnabled flags. Previously this
            // emitted fresh IsEnabled flags but a STALE (or empty) Value, so the submitted
            // ODM had Value disagreeing with the enabled options, AND the derived-field
            // recompute (whose dependency hash reads Value) never re-ran — leaving derived
            // MetaKey items (e.g. I.69 isDeleted) stale or dropped. The Create Bid (_v3)
            // path already syncs Value; this brings the legacy _v2 path in line.
            const newValue = newState.join(',');
            const currentItemData = itemDatas?.find((item: any) => item?.ItemOID === itemDef.OID);
            const updatedItemData: ItemData = currentItemData
                ? { ...currentItemData, Value: newValue, CodeList: updatedTodos }
                : {
                    ItemOID: itemDef.OID,
                    Value: newValue,
                    CodeList: updatedTodos,
                    MetaKey: itemDef.MetaKey || '',
                    ItemGroupRepeatKey: isRepeatItemGrp === 'Yes' ? itgrpInd : 0
                };

            setItemData(updatedItemData);
            // Root-caused 2026-09-08: QuestionRendererCard_v2's restore effect (which feeds
            // this component's own `itemDatas` prop) needs a way to tell a genuine,
            // deliberate user click apart from every OTHER caller of OnItemDataChange
            // (hydration/restoration included) — otherwise a "recently edited" guard meant
            // to protect a fresh click from a stale itemGroupDatas echo ends up ALSO
            // protecting (freezing) values from unrelated hydration calls, breaking initial
            // answer loading (confirmed live: Asset Info/Trade answers stopped populating
            // once such a guard treated every onItemchange call as a user edit). This is the
            // only call site permitted to pass `isUserEdit: true` — it is reached ONLY from
            // this component's own real checkbox `onChange` handler above.
            onItemchange(updatedItemData, { isUserEdit: true });

            setTimeout(() => {
                // 1. Hide children of every removed value (covers exclusive→normal and normal→exclusive)
                removedValues.forEach((removedValue: string) => {
                    conditionDef?.forEach((cd: any) => {
                        if (!cd?.FormalExpression?.text) return;
                        const conditionText = cd.FormalExpression.text;
                        const hasOr = conditionText.includes('||');
                        if (hasOr) {
                            conditionText.split('||').forEach((x: string) => {
                                const cdn = x.split('==');
                                if (cdn[1]?.includes(removedValue)) {
                                    showStatus(false, cd.OID, OID, null, isRepeatItemGrp === 'Yes', isRepeatItemGrp === 'Yes' ? itgrpInd : 0);
                                }
                            });
                        } else {
                            const cdn = conditionText.split('==');
                            if (cdn[1]?.includes(removedValue)) {
                                showStatus(false, cd.OID, OID, null, isRepeatItemGrp === 'Yes', isRepeatItemGrp === 'Yes' ? itgrpInd : 0);
                            }
                        }
                    });
                });

                // 2. Show/hide children based on newly selected value
                conditionDef?.forEach((cd: any) => {
                    if (!cd?.FormalExpression?.text) return;
                    let conditionText = cd.FormalExpression.text;
                    let currentQuestionId = itemDef.OID;

                    // Skip processing conditions that would affect the current question
                    // This prevents the current question from being hidden while user is selecting options
                    if (conditionText.includes(`!(${currentQuestionId}`) || conditionText.includes(`!${currentQuestionId}`)) {
                        return;
                    }

                    let hasOr = conditionText.includes('||');
                    if (hasOr) {
                        conditionText.split("||").forEach((x: string) => {
                            let cdn = x.split("==");
                            if (cdn[1]?.includes(value)) {
                                showStatus(checked && cdn[1].includes(value), cd.OID, OID, null, isRepeatItemGrp === 'Yes', isRepeatItemGrp === 'Yes' ? itgrpInd : 0);
                            }
                        });
                    } else {
                        let cdn = conditionText.split("==");
                        if (cdn[1]?.includes(value)) {
                            showStatus(checked && cdn[1].includes(value), cd.OID, OID, null, isRepeatItemGrp === 'Yes', isRepeatItemGrp === 'Yes' ? itgrpInd : 0);
                        }
                    }
                });
            }, 100); // Increased timeout to ensure data is fully updated first

        } catch (error) {
            console.error('Error in MultiChoice handleChange:', error);
            console.error('Error details:', {
                itemDef: itemDef,
                codeList: codeList,
                value: event.target.value,
                checked: event.target.checked
            });
        }

    };

    // const error = [state].filter((v) => v).length > 1;

    return (
        <>
            <FormControl
                required
                error={error}
                component="fieldset"
                variant="standard"
                sx={{ width: '100%' }}
                disabled={readonly}
            >
                <Box className="multi-choice-options-container">
                    {(() => {
                        const questionCodeList = codeData?.find(cl =>
                            cl.OID === itemDef.CodeListRef?.CodeListOID ||
                            cl.OID === itemDef.CodeListOID
                        );

                        if (!questionCodeList || !questionCodeList.CodeListItem) {
                            console.warn(`No CodeList found for question ${itemDef.OID}`);
                            return null;
                        }



                        // Render options from the specific CodeList for this question

                        return questionCodeList.CodeListItem.map((item: any, index: number) => {
                            const isSelected = state.includes(item.CodedValue);
                            const labelText = item.Decode?.TranslatedText?.text || item.CodedValue;




                            // Prefer fresh SAS URL from context; fall back to constructed URL
                            const thumbnailSrc = (item.ThumbnailUrl && catalogueSasMap[item.ThumbnailUrl]) || getImageUrl(item.ThumbnailUrl, imageBaseUrl);
                            const fullImageSrc = (item.ImageUrl && catalogueSasMap[item.ImageUrl]) || getImageUrl(item.ImageUrl, imageBaseUrl);

                            // ExclusiveOptionGuard: disable non-exclusive cards when exclusive is active
                            const exclusiveIsActive = exclusiveCodedValue !== null && state.includes(exclusiveCodedValue);
                            const isDisabledByExclusive = !readonly && exclusiveIsActive && item.CodedValue !== exclusiveCodedValue;
                            const isEffectivelyReadonly = readonly || isDisabledByExclusive;

                            return (
                                <label
                                    key={`${itemDef.OID}-${item.CodedValue}-${index}`}
                                    className="multi-choice-option-wrapper"
                                >
                                    <input
                                        type="checkbox"
                                        onChange={e => handleChange(e, index)}
                                        checked={isSelected}
                                        value={item.CodedValue}
                                        className="multi-choice-hidden-checkbox"
                                        disabled={isEffectivelyReadonly}
                                    />
                                    <div
                                        id={`sdm-multi-choice-v2-checkbox-${itemDef?.OID}`}
                                        className={[
                                            'multi-choice-option-card',
                                            isSelected ? 'multi-choice-option-card--selected' : '',
                                            isEffectivelyReadonly ? 'multi-choice-option-card--readonly' : '',
                                            isDisabledByExclusive ? 'multi-choice-option-card--exclusive-disabled' : '',
                                        ].filter(Boolean).join(' ')}
                                    >
                                        {FEATURE_FLAGS.createbid_visualsupport__enable && (thumbnailSrc || fullImageSrc) && (
                                            <div className="multi-choice-image-container">
                                                <img
                                                    src={thumbnailSrc || fullImageSrc || ''}
                                                    id={`sdm-multi-select-v2-image-${itemDef?.OID}`}
                                                    alt={labelText}
                                                    className="multi-choice-image"
                                                    onError={(e) => {
                                                        console.error(`[MultiChoice] Failed to load thumbnail for ${item.CodedValue}:`, thumbnailSrc);
                                                    }}
                                                />
                                                <div
                                                    className="multi-choice-zoom-btn"
                                                    onClick={(e) => {
                                                        e.preventDefault();
                                                        e.stopPropagation();
                                                        setZoomedImage({ url: fullImageSrc || thumbnailSrc || '', label: labelText });
                                                    }}
                                                >
                                                </div>
                                            </div>
                                        )}
                                        <div className="multi-choice-option-label">
                                            <span className={isSelected ? 'selected' : 'unselected'}>
                                                {labelText}
                                            </span>
                                        </div>
                                    </div>
                                </label>
                            );
                        });
                    })()}
                </Box>
                <FormHelperText sx={{ mt: 1 }}>
                    {(error?.OID === itemDef.OID && (!state || state.length === 0)) ? 'Please select at least one option' : null}
                </FormHelperText>
                {softWarning && (
                    <FormHelperText sx={{ color: 'warning.main' }}>
                        {softWarning}
                    </FormHelperText>
                )}
            </FormControl>

            {/* Image Zoom Modal */}
            <ImageZoomDialog
                open={!!zoomedImage}
                onClose={() => setZoomedImage(null)}
                imageSrc={zoomedImage?.url || null}
                imageAlt={zoomedImage?.label || ''}
            />
        </>
    )
}

export default React.memo(MultiChoice_v2);