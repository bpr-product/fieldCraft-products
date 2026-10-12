/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 2-Nov-2024
* @author      : Vaisnave I M
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Single Select Input Component
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 21928  | 2026-06-24 | Karthika S | Fix stale validation errors persisting after field is filled in Boolean/Q&A renderer
*/

import { Box, FormControl, FormHelperText, Dialog, DialogContent, IconButton } from "@mui/material";
import React, { useContext, useEffect, useState, useCallback } from "react";
import { CodeList, ItemData } from "../../../interface/SdmInterfaces";
import { FEATURE_FLAGS } from '../../../constants/FeatureFlag';
import { getImageUrl } from '../../../utils/imageRenderer';
import ImageZoomDialog from './ImageZoomDialog';
import { validateRangeCheck } from '../../../utils/rangeCheckValidator';
import { CatalogueImageContext } from '../../../context/CatalogueImageContext';
import './SingleSelect_v2.scss';

// PERFORMANCE: Memoize component to prevent unnecessary re-renders
const SingleSelect_v2Component: React.FC<any> = ({ OID, itemDef, conditionDef, codeList, onItemchange, showStatus, itemDatas, error, isRepeatItemGrp, itgrpInd, methodDefs, readonly = false, imageBaseUrl = '' }) => {



    const catalogueSasMap = useContext(CatalogueImageContext);

    const [state, setState] = useState('');
    const [itemData, setItemData] = useState<ItemData>();
    const [show, setShow] = useState(false);
    const [hide, setHide] = useState(false);
    const [zoomedImage, setZoomedImage] = useState<{ url: string, label: string } | null>(null);
    const [softWarning, setSoftWarning] = useState<string | null>(null);

    const [codeListData, setcodeData] = useState<CodeList[]>(() =>
        codeList && codeList.length > 0 ? JSON.parse(JSON.stringify(codeList)) : []
    );

    // Track the last hash to prevent redundant updates
    const lastHashRef = React.useRef<string>('');

    React.useEffect(() => {
        if (!codeList || codeList.length === 0) return;

        try {
            const currentHash = JSON.stringify(codeList);

            // Only update if hash is different
            if (currentHash !== lastHashRef.current) {
                lastHashRef.current = currentHash;
                setcodeData(JSON.parse(JSON.stringify(codeList)));
            }
        } catch (error) {
            // console.error('[CODELIST_UPDATE] Error processing codeList:', error);
        }
    }, [codeList, itemDef?.OID]); // Run when codeList prop changes

    const restoredRef = React.useRef(false);
    const userInteractedRef = React.useRef(false); // Track if user has made a selection

    useEffect(() => {
        if (!itemDef?.RangeCheck || !Array.isArray(itemDef.RangeCheck)) return;
        const savedData = itemDatas?.find((item: any) => item?.ItemOID === itemDef?.OID);
        let initialValue: string | null = null;
        savedData?.CodeList?.forEach((cl: any) => {
            cl.CodeListItem?.forEach((item: any) => {
                if (item.IsEnabled && !initialValue) initialValue = item.CodedValue;
            });
        });
        if (!initialValue) initialValue = savedData?.Value ?? null;
        if (!initialValue) return;
        const result = validateRangeCheck(initialValue, itemDef.RangeCheck);
        setSoftWarning(result.isSoftWarning ? (result.errorMessage ?? null) : null);
    }, []);

    // Effect to restore saved data ONCE when component mounts
    useEffect(() => {
        // Check if there's any saved data for this specific question
        const savedItemData = itemDatas?.find((item: any) => item?.ItemOID === itemDef?.OID);


        const hasCodeListSelection = savedItemData?.CodeList?.some((cl: any) =>
            cl.CodeListItem?.some((item: any) => item.IsEnabled)
        );

        const hasActualSelection = hasCodeListSelection || (savedItemData?.Value && savedItemData?.Value !== '');


        if (savedItemData && hasActualSelection) {
            // Only restore if not already restored or user hasn't interacted
            if (!restoredRef.current && !userInteractedRef.current) {
                restoredRef.current = true; // Mark as restored

                // Restore the item data
                setItemData(savedItemData);

                // For CodeList questions, the selected value is indicated by IsEnabled flag, not Value
                // Value is empty string when CodeList is present
                if (savedItemData.CodeList && savedItemData.CodeList.length > 0) {
                    setcodeData(savedItemData.CodeList);

                    // Find which option has IsEnabled=true and set that as the selected state
                    savedItemData.CodeList.forEach((cl: any) => {
                        if (cl.CodeListItem) {
                            cl.CodeListItem.forEach((item: any) => {
                                if (item.IsEnabled) {
                                    // Set state to the CodedValue of the enabled item
                                    setState(item.CodedValue);
                                    setShow(true);

                                    requestAnimationFrame(() => {
                                        conditionDef?.forEach((cd: any) => {
                                            if (cd?.FormalExpression?.text) {
                                                let conditionText = cd.FormalExpression.text;
                                                let hasOrOperator = conditionText.includes("||");

                                                if (hasOrOperator) {
                                                    let conditions = conditionText.split("||");
                                                    conditions.forEach((condition: string) => {
                                                        let parts = condition.trim().split("==");
                                                        if (parts.length >= 2) {
                                                            let questionId = parts[0].trim().replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                                            let expectedValue = parts[1].trim().replace(/"/g, '');

                                                            if (questionId === itemDef.OID && expectedValue.includes(item.CodedValue)) {
                                                                showStatus(true, cd.OID, OID, item.CodedValue, (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
                                                            }
                                                        }
                                                    });
                                                } else {
                                                    let parts = conditionText.split("==");
                                                    if (parts.length >= 2) {
                                                        let questionId = parts[0].trim().replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                                        let expectedValue = parts[1].trim().replace(/"/g, '');

                                                        if (questionId === itemDef.OID && expectedValue.includes(item.CodedValue)) {
                                                            showStatus(true, cd.OID, OID, item.CodedValue, (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
                                                        }
                                                    }
                                                }
                                            }
                                        });
                                    });
                                }
                            });
                        }
                    });
                } else if (savedItemData.Value) {
                    // Fallback: For questions that use Value directly (not CodeList)
                    setState(savedItemData.Value);
                    setShow(true);
                }
            }
        } else {

            const hasNoCodeListSelection = !savedItemData?.CodeList?.some((cl: any) =>
                cl.CodeListItem?.some((item: any) => item.IsEnabled)
            );

            const wasCleanedUp = (savedItemData && hasNoCodeListSelection) ||
                (!savedItemData && (restoredRef.current || userInteractedRef.current)) ||
                (hasNoCodeListSelection && state !== '');

            // Only reset if data was explicitly cleaned up
            if (wasCleanedUp) {
                restoredRef.current = false;
                userInteractedRef.current = false;
                setState('');
                setShow(false);

                // Reset codeData to template defaults (all unchecked)
                if (codeList && codeList.length > 0) {
                    const resetCodeList = codeList.map(cl => ({
                        ...cl,
                        CodeListItem: cl.CodeListItem.map((item: any) => ({
                            ...item,
                            IsEnabled: false
                        }))
                    }));
                    setcodeData(resetCodeList);
                }
            }
        }
    }, [itemDatas, itemDef?.OID, codeList]);



    const handleChange = useCallback((event: React.ChangeEvent<HTMLInputElement>, indexes: any) => {
        if (event.target.disabled) return;
        try {
            const checked = event.target.checked;
            const value = event.target.value;

            userInteractedRef.current = true;

            if (checked && itemDef?.RangeCheck && Array.isArray(itemDef.RangeCheck)) {
                const result = validateRangeCheck(value, itemDef.RangeCheck);
                setSoftWarning(result.isSoftWarning ? (result.errorMessage ?? null) : null);
            } else {
                setSoftWarning(null);
            }

            // const updatedTodos = [...codeList];
            const updatedTodos = JSON.parse(JSON.stringify(codeListData));
            if (checked) {
                setShow(checked);
                setState(value);

                // This allows the UI to update immediately, then processes conditions in next frame
                requestAnimationFrame(() => {
                    // Process conditional logic for showing/hiding dependent questions
                    conditionDef?.forEach((cd: any) => {
                        if (cd?.FormalExpression?.text) {

                            let conditionText = cd.FormalExpression.text;
                            let hasOrOperator = conditionText.includes("||");

                            if (hasOrOperator) {
                                // Handle multiple conditions with OR operator
                                let conditions = conditionText.split("||");
                                conditions.forEach((condition: string) => {
                                    let parts = condition.trim().split("==");
                                    if (parts.length >= 2) {
                                        let questionId = parts[0].trim().replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                        let expectedValue = parts[1].trim().replace(/"/g, '');

                                        // Check if this condition applies to the current question
                                        if (questionId === itemDef.OID) {
                                            let conditionMet = expectedValue.includes(value);
                                            showStatus(conditionMet, cd.OID, OID, value, (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
                                        }
                                    }
                                });
                            } else {
                                // Handle single condition
                                let parts = conditionText.split("==");
                                if (parts.length >= 2) {
                                    let questionId = parts[0].trim().replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                    let expectedValue = parts[1].trim().replace(/"/g, '');

                                    // Check if this condition applies to the current question
                                    if (questionId === itemDef.OID) {
                                        let conditionMet = expectedValue.includes(value);
                                        showStatus(conditionMet, cd.OID, OID, value, (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
                                    }
                                }
                            }
                        }
                    });
                });

                // Find the correct CodeList for this question
                const questionCodeList = updatedTodos.find(cl =>
                    cl.OID === itemDef.CodeListRef?.CodeListOID ||
                    cl.OID === itemDef.CodeListOID
                );

                if (questionCodeList && questionCodeList.CodeListItem) {
                    questionCodeList.CodeListItem.map((x, i) => x.CodedValue === event.target.value ? x.IsEnabled = event.target.checked : x.IsEnabled = !event.target.checked);
                }
            } else {
                setShow(event.target.checked);
                setState(''); // Clear the state when unchecked

                // Process conditional logic for hiding dependent questions when unchecked
                conditionDef?.forEach((cd: any) => {
                    if (cd?.FormalExpression?.text) {

                        let conditionText = cd.FormalExpression.text;
                        let hasOrOperator = conditionText.includes("||");

                        if (hasOrOperator) {
                            // Handle multiple conditions with OR operator
                            let conditions = conditionText.split("||");
                            conditions.forEach((condition: string) => {
                                let parts = condition.trim().split("==");
                                if (parts.length >= 2) {
                                    let questionId = parts[0].trim().replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                    let expectedValue = parts[1].trim().replace(/"/g, '');

                                    // Check if this condition applies to the current question
                                    if (questionId === itemDef.OID && expectedValue.includes(event.target.value)) {
                                        showStatus(false, cd.OID, OID, '', (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
                                    }
                                }
                            });
                        } else {
                            // Handle single condition
                            let parts = conditionText.split("==");
                            if (parts.length >= 2) {
                                let questionId = parts[0].trim().replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                let expectedValue = parts[1].trim().replace(/"/g, '');

                                // Check if this condition applies to the current question
                                if (questionId === itemDef.OID && expectedValue.includes(event.target.value)) {
                                    showStatus(false, cd.OID, OID, '', (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
                                }
                            }
                        }
                    }
                });
                // Find the correct CodeList for this question
                const questionCodeList = updatedTodos.find(cl =>
                    cl.OID === itemDef.CodeListRef?.CodeListOID ||
                    cl.OID === itemDef.CodeListOID
                );

                if (questionCodeList && questionCodeList.CodeListItem) {
                    questionCodeList.CodeListItem.map((x, i) => x.CodedValue === event.target.value ? x.IsEnabled = event.target.checked : x.IsEnabled = event.target.checked);
                }
            }
            setcodeData(updatedTodos);

            // Find the correct CodeList for this question to avoid undefined errors
            const questionCodeListForCheck = codeList?.find(cl =>
                cl.OID === itemDef.CodeListRef?.CodeListOID ||
                cl.OID === itemDef.CodeListOID
            );


            if (questionCodeListForCheck && (itemDef.CodeListRef?.CodeListOID === questionCodeListForCheck.OID || itemDef.CodeListOID === questionCodeListForCheck.OID)) {
                // Get the selected value from the enabled CodeListItem
                const selectedValue = checked ? event.target.value : "";

                // Extract only the relevant CodeList for this question, not all 10 CodeLists
                const relevantCodeList = updatedTodos.filter(cl =>
                    cl.OID === itemDef.CodeListRef?.CodeListOID ||
                    cl.OID === itemDef.CodeListOID
                );

                // When CodeList is present, Value should be empty string
                // Selection is indicated by IsEnabled flag in CodeListItem array
                if (itemDatas && itemDatas.length > 0 && itemDatas[0] !== undefined) {
                    setItemData({ ...itemDatas[0], Value: "", CodeList: relevantCodeList });
                    onItemchange({ ...itemDatas[0], Value: "", CodeList: relevantCodeList });
                }
                else {
                    // ItemGroupRepeatKey should ONLY be at ItemData level, not inside CodeList
                    let tempItemData: ItemData = { ItemOID: itemDef.OID, Value: "", CodeList: relevantCodeList, MetaKey: itemDef.MetaKey, ItemGroupRepeatKey: isRepeatItemGrp === 'Yes' ? itgrpInd : 0, ItemGroupOID: OID };
                    setItemData(tempItemData);
                    onItemchange(tempItemData);
                }
            }
        } catch (error) {
            // console.error('Error in SingleSelect handleChange:', error);
            // console.error('Error details:', {
            //     itemDef: itemDef,
            //     codeList: codeList,
            //     codeListData: codeListData,
            //     value: event.target.value,
            //     checked: event.target.checked
            // });
        }
    }, [codeListData, conditionDef, itemDef, OID, isRepeatItemGrp, itgrpInd, showStatus, onItemchange]); // ⚡ Add dependencies for useCallback

    // const error = [state].filter((v) => v).length !== 1;

    return (
        <>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <FormControl
                    required
                    error={error}
                    component="fieldset"
                    variant="standard"
                    sx={{ width: '100%' }}
                    disabled={readonly}
                >
                    <Box className="single-select-options-grid">
                        {(() => {
                            // Find the correct CodeList for this specific question
                            const questionCodeList = codeListData?.find(cl =>
                                cl.OID === itemDef.CodeListRef?.CodeListOID ||
                                cl.OID === itemDef.CodeListOID
                            );

                            if (!questionCodeList || !questionCodeList.CodeListItem) {
                                // console.warn(`No CodeList found for question ${itemDef.OID}`);
                                return null;
                            }

                            // Render options from the specific CodeList for this question
                            return questionCodeList.CodeListItem.map((item: any, index: number) => {
                                // itemDatas contains OLD data from parent and causes switching issues
                                const isSelected = state === item.CodedValue;
                                const labelText = item.Decode?.TranslatedText?.text || item.CodedValue;

                                // Prefer fresh SAS URL from context; fall back to constructed URL
                                const thumbnailSrc = (item.ThumbnailUrl && catalogueSasMap[item.ThumbnailUrl]) || getImageUrl(item.ThumbnailUrl, imageBaseUrl);
                                const fullImageSrc = (item.ImageUrl && catalogueSasMap[item.ImageUrl]) || getImageUrl(item.ImageUrl, imageBaseUrl);

                                return (
                                    <label
                                        key={`${itemDef.OID}-${item.CodedValue}-${index}`}
                                        className="single-select-option-wrapper"
                                    >
                                        <input
                                            type="checkbox"
                                            onChange={e => handleChange(e, index)}
                                            checked={isSelected}
                                            value={item.CodedValue}
                                            className="single-select-hidden-checkbox"
                                            disabled={readonly}
                                        />
                                        <div
                                            id={`sdm-single-select-v2-checkbox-${itemDef?.OID}`}
                                            className={`single-select-option-card ${isSelected ? 'single-select-option-card--selected' : ''} ${readonly ? 'single-select-option-card--readonly' : ''}`}
                                        >
                                            {FEATURE_FLAGS.createbid_visualsupport__enable && (thumbnailSrc || fullImageSrc) && (
                                                <div className="single-select-image-container">
                                                    <img
                                                        src={thumbnailSrc || fullImageSrc || ''}
                                                        alt={labelText}
                                                        id={`sdm-single-select-v2-image-${itemDef?.OID}`}
                                                        className="single-select-image"
                                                        onError={(e) => {
                                                            console.error(`[SingleSelect] Failed to load thumbnail for ${item.CodedValue}:`, thumbnailSrc);
                                                        }}
                                                    />
                                                    <div
                                                        className="single-select-zoom-btn"
                                                        onClick={(e) => {
                                                            e.preventDefault();
                                                            e.stopPropagation();
                                                            setZoomedImage({ url: fullImageSrc || thumbnailSrc || '', label: labelText });
                                                        }}
                                                    >
                                                    </div>
                                                </div>
                                            )}
                                            <div className={`single-select-option-label ${isSelected ? 'single-select-option-label--selected' : 'single-select-option-label--unselected'}`}>
                                                {labelText}
                                            </div>
                                        </div>
                                    </label>
                                );
                            });
                        })()}
                    </Box>
                    <FormHelperText sx={{ mt: 1 }}>
                        {(error?.OID === itemDef.OID && !state) ? 'Please select an option' : null}
                    </FormHelperText>
                    {softWarning && (
                        <FormHelperText sx={{ color: 'warning.main' }}>
                            {softWarning}
                        </FormHelperText>
                    )}
                </FormControl>
            </Box >

            {/* Zoom Dialog for full-size image */}
            <ImageZoomDialog
                open={!!zoomedImage}
                onClose={() => setZoomedImage(null)}
                imageSrc={zoomedImage?.url || null}
                imageAlt={zoomedImage?.label || ''}
            />
        </>
    );
}; // End SingleSelect_v2Component

// ⚡ PERFORMANCE: Export memoized component
export default React.memo(SingleSelect_v2Component);
