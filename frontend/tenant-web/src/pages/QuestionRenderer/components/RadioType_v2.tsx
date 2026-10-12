/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 2-Nov-2024
* @author      : Vaisnave I M
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Radio Button Input Component
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 21928  | 2026-06-24 | Karthika S | Fix stale validation errors persisting after field is filled in Boolean/Q&A renderer
*/


import React, { useEffect, useState, useCallback } from "react";
import { FormHelperText, Radio, RadioGroup } from "@mui/material";
import { ItemData, ItemDef } from "../../../interface/SdmInterfaces";
import { StyledFormControl } from "./StyledComponents_v2";
import { validateRangeCheck } from "../../../utils/rangeCheckValidator";

interface Props {
    itemDef: ItemDef;
    // setTextData: React.Dispatch<React.SetStateAction<any>>;
    onItemchange: React.Dispatch<React.SetStateAction<any>>;
}

// PERFORMANCE: Memoize component to prevent unnecessary re-renders
const RadioType_v2Component: React.FC<any> = ({ OID, itemDef, conditionDef, codeList, onItemchange, showStatus, itemDatas, error, isRepeatItemGrp, itgrpInd, methodDefs, readonly = false }) => {



    const [clicked, setClicked] = useState(false);
    const [softWarning, setSoftWarning] = useState<string | null>(null);
    const [itemData, setItemData] = useState<ItemData>(() => {
        // Find the correct itemData for this question
        const foundItemData = itemDatas?.find(item => item?.ItemOID === itemDef?.OID);
        if (foundItemData) {
            return foundItemData;
        }
        // Initialize with CodeList from prop
        const initialCodeList = codeList && codeList.length > 0 ? codeList.map((cl: any) => ({
            ...cl,
            CodeListItem: cl.CodeListItem?.map((item: any) => ({
                ...item,
                IsEnabled: false // No selection initially
            })) || []
        })) : [];

        return {
            ItemOID: itemDef?.OID,
            Value: '',
            CodeList: initialCodeList,
            MetaKey: itemDef?.MetaKey || '',
            ItemGroupRepeatKey: 0
        };
    });

    const itemDataRef = React.useRef(itemData);
    useEffect(() => {
        itemDataRef.current = itemData;
    }, [itemData]);

    const prevItemDatasRef = React.useRef(itemDatas);

    useEffect(() => {
        if (!itemDef?.RangeCheck || !Array.isArray(itemDef.RangeCheck)) return;
        const savedData = itemDatas?.find((item: any) => item?.ItemOID === itemDef?.OID);
        const initialValue = savedData?.Value;
        if (!initialValue) return;
        const result = validateRangeCheck(initialValue, itemDef.RangeCheck);
        setSoftWarning(result.isSoftWarning ? (result.errorMessage ?? null) : null);
    }, []);

    // Update itemData when itemDatas prop changes (for saved data restoration)
    useEffect(() => {
        // Skip if itemDatas ref hasn't changed
        if (prevItemDatasRef.current === itemDatas) return;
        prevItemDatasRef.current = itemDatas;

        const foundItemData = itemDatas?.find(item => item?.ItemOID === itemDef?.OID);
        if (foundItemData && foundItemData.Value !== itemData.Value) {
            if (itemData.Value === '' && foundItemData.Value !== '') {
                return;
            }
            setItemData(foundItemData);
        }
    }, [itemDatas, itemDef?.OID]);


    const handleChange = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
        const selected = event.target.value;
        const value = itemDataRef.current?.Value === selected ? "" : selected;

        requestAnimationFrame(() => {
            //setClicked(value === "Yes" ? true : false);
            if (value === "Yes") {
                conditionDef.map((cd: any) => {
                    let cdn = cd?.FormalExpression?.text.split("==");
                    showStatus(cdn[1].includes(value), cd.OID, OID, null, (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
                });
            } else {
                conditionDef.map((cd: any) => {
                    let cdn = cd?.FormalExpression?.text.split("==");
                    showStatus(cdn[1].includes(value), cd.OID, OID, null, (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
                });
            }
        });

        if (itemDef?.RangeCheck && Array.isArray(itemDef.RangeCheck)) {
            const result = validateRangeCheck(value, itemDef.RangeCheck);
            setSoftWarning(result.isSoftWarning ? (result.errorMessage ?? null) : null);
        } else {
            setSoftWarning(null);
        }

        const existingItemData = itemDatas?.find((d: any) => d?.ItemOID === itemDef?.OID);

        if (existingItemData !== undefined) {
            // Update CodeList with IsEnabled flags based on selected value
            const updatedCodeList = codeList && codeList.length > 0 ? codeList.map((cl: any) => ({
                ...cl,
                CodeListItem: cl.CodeListItem?.map((item: any) => ({
                    ...item,
                    IsEnabled: item.CodedValue === value
                })) || []
            })) : [];

            const updatedItemData = {
                ...existingItemData,
                Value: value,
                CodeList: updatedCodeList
            };
            itemDataRef.current = updatedItemData;
            setItemData(updatedItemData);
            onItemchange(updatedItemData);
        } else {
            // Create CodeList with IsEnabled flags based on selected value
            const newCodeList = codeList && codeList.length > 0 ? codeList.map((cl: any) => ({
                ...cl,
                CodeListItem: cl.CodeListItem?.map((item: any) => ({
                    ...item,
                    IsEnabled: item.CodedValue === value
                })) || []
            })) : [];

            let tempItemData: ItemData = {
                ItemOID: itemDef.OID,
                Value: value,
                CodeList: newCodeList,
                MetaKey: itemDef.MetaKey || '',
                ItemGroupRepeatKey: isRepeatItemGrp === 'Yes' ? itgrpInd : 0
            };
            itemDataRef.current = tempItemData;
            setItemData(tempItemData);
            onItemchange(tempItemData);
        }
    }, [conditionDef, showStatus, OID, isRepeatItemGrp, itgrpInd, itemDatas, codeList, itemDef, onItemchange]);

    return (
        <>
            <StyledFormControl error={error?.OID === itemDef.OID && (!itemData?.Value || itemData.Value === '')} disabled={readonly}>
                <RadioGroup
                    row
                    aria-labelledby="demo-row-radio-buttons-group-label"
                    name="row-radio-buttons-group"
                    value={itemData?.Value || ''}
                    onChange={(e) => { if (!readonly) handleChange(e); }}
                    sx={{ gap: '16px', display: 'flex', flexWrap: 'wrap' }}
                >
                    {['Yes', 'No'].map((option) => (
                        <label
                            key={option}
                            style={{
                                flex: '1 1 calc(50% - 8px)',
                                minWidth: '150px',
                                maxWidth: '250px',
                                margin: 0,
                                cursor: 'pointer'
                            }}
                        >
                            <div
                                onClick={() => { if (!readonly) handleChange({ target: { value: option } } as any); }}
                                id={`sdm-radio-type-v2-input-${itemDef?.OID}-${option}`}
                                style={{
                                    padding: '12px 16px',
                                    borderRadius: '8px',
                                    border: itemData?.Value === option
                                        ? '2px solid primary.main'
                                        : '2px solid #e0e0e0',
                                    backgroundColor: itemData?.Value === option
                                        ? '#f0f4f8'
                                        : '#ffffff',
                                    transition: 'all 0.2s ease',
                                    textAlign: 'center',
                                    fontWeight: itemData?.Value === option ? 600 : 400,
                                    fontSize: '14px',
                                    color: itemData?.Value === option ? 'primary.main' : '#424242',
                                    boxShadow: itemData?.Value === option
                                        ? '0 2px 8px rgba(17, 45, 78, 0.15)'
                                        : '0 1px 3px rgba(0, 0, 0, 0.08)',
                                    pointerEvents: readonly ? 'none' : 'auto',
                                    cursor: readonly ? 'default' : 'pointer'
                                }}
                                onMouseEnter={(e) => {
                                    if (itemData?.Value !== option) {
                                        e.currentTarget.style.borderColor = 'primary.main';
                                        e.currentTarget.style.backgroundColor = '#fafafa';
                                    }
                                }}
                                onMouseLeave={(e) => {
                                    if (itemData?.Value !== option) {
                                        e.currentTarget.style.borderColor = '#e0e0e0';
                                        e.currentTarget.style.backgroundColor = '#ffffff';
                                    }
                                }}                            >
                                {option}
                            </div>
                        </label>
                    ))}
                </RadioGroup>
                <FormHelperText error={error?.OID === itemDef.OID && (!itemData?.Value || itemData.Value === '')}>
                    {error?.OID === itemDef.OID && (!itemData?.Value || itemData.Value === '') ? 'Please select an option' : ''}
                </FormHelperText>
                {softWarning && (
                    <FormHelperText sx={{ color: 'warning.main' }}>
                        {softWarning}
                    </FormHelperText>
                )}
            </StyledFormControl>
        </>
    );
}; // End RadioType_v2Component

// PERFORMANCE: Export memoized component
export default React.memo(RadioType_v2Component);