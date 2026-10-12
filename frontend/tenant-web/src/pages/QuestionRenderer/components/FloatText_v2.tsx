/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 2-Nov-2024
* @author      : Vaisnave I M
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Floating Point Text Input Component
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

import { Stack, TextField, Typography } from "@mui/material";
import React, { useState, useRef, useCallback, useEffect } from "react";
import { ItemData } from "../../../interface/SdmInterfaces";
import { validateRangeCheck, ValidationResult } from "../../../utils/rangeCheckValidator";



const FloatText: React.FC<any> = ({ itemDef, measureDef, onItemchange, itemDatas, error, readonly = false, mapping, tradeData }) => {

    const [itemData, setItemData] = useState<ItemData>((itemDatas && itemDatas[0] && itemDef?.OID === itemDatas[0]?.ItemOID) ? itemDatas[0] : {} as ItemData);
    const [rangeCheckError, setRangeCheckError] = useState<ValidationResult>({ isValid: true });

    //  PERFORMANCE FIX: Debounce parent callback to prevent lag
    const debounceTimerRef = useRef<NodeJS.Timeout | null>(null);
    const DEBOUNCE_DELAY = 300; // 300ms delay for parent updates

    //  PERFORMANCE FIX: Debounced callback to parent
    const debouncedOnItemChange = useCallback((data: ItemData) => {
        // Clear existing timer
        if (debounceTimerRef.current) {
            clearTimeout(debounceTimerRef.current);
        }

        // Set new timer
        debounceTimerRef.current = setTimeout(() => {
            onItemchange(data);
        }, DEBOUNCE_DELAY);
    }, [onItemchange]);

    // Cleanup timer on unmount
    useEffect(() => {
        return () => {
            if (debounceTimerRef.current) {
                clearTimeout(debounceTimerRef.current);
            }
        };
    }, []);

    // Auto-map from tradeData if present (EagleView integration)
    useEffect(() => {
        // Use mapping.Name if available, otherwise fall back to itemDef.MetaKey
        const tradeKey = mapping?.Name || itemDef?.MetaKey;

        // Skip if no tradeKey or tradeData
        if (!tradeKey || !tradeData) {
            return;
        }

        // Check if user has already entered a value in itemDatas - respect user edits
        const existingItem = itemDatas?.find((d: any) => d.ItemOID === itemDef?.OID);
        if (existingItem) {
            const existingValue = existingItem.Value ?? existingItem.value;
            if (existingValue !== undefined && existingValue !== '' && existingValue !== null) {
                return;
            }
        }

        // Look up value using the tradeKey
        const rawValue = tradeData[tradeKey] ?? tradeData.TotalMeasurements?.[tradeKey];

        if (rawValue !== undefined && rawValue !== null) {
            const parsed = parseFloat(rawValue);
            if (!isNaN(parsed)) {
                let tempItemData: ItemData = { ItemOID: itemDef.OID, Value: parsed.toString(), CodeList: [], MetaKey: itemDef.MetaKey, ItemGroupRepeatKey: 0 };
                setItemData(tempItemData);

                setTimeout(() => {
                    if (typeof onItemchange === 'function') {
                        onItemchange(tempItemData);
                    }
                }, 100);
            }
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [mapping, tradeData, itemDef.OID, itemDef.MetaKey, itemDatas]);




    const inputchange = (e: any) => {
        const inputValue = e.target.value;

        //  STRICT NUMERIC VALIDATION: Only allow numbers, decimal point, and negative sign
        const numericPattern = /^-?\d*\.?\d*$/; // NOSONAR: Safe — deterministic parsing of feet-inches format using literal separators

        // Block invalid characters immediately
        if (inputValue !== '' && !numericPattern.test(inputValue)) {
            return;
        }

        // Prevent multiple decimal points
        if ((inputValue.match(/\./g) || []).length > 1) {
            return;
        }

        // Prevent multiple negative signs or negative signs not at the start
        if (inputValue.includes('-') && (inputValue.indexOf('-') !== 0 || inputValue.lastIndexOf('-') !== 0)) {
            return;
        }

        let tempItemData: ItemData;

        if (itemDatas && itemDatas.length > 0) {
            tempItemData = { ...itemDatas[0], Value: inputValue };
            setItemData(tempItemData);
        } else {
            tempItemData = { ItemOID: itemDef.OID, Value: inputValue, CodeList: [], MetaKey: itemDef.MetaKey, ItemGroupRepeatKey: 0 };
            setItemData(tempItemData);
        }

        if (itemDef?.RangeCheck && Array.isArray(itemDef.RangeCheck)) {
            const validationResult = validateRangeCheck(inputValue, itemDef.RangeCheck);
            setRangeCheckError(validationResult);
        }

        //  Use debounced callback instead of immediate
        debouncedOnItemChange(tempItemData);
    }

    return (
        <>
            <Stack direction="row" spacing={2}>
                <TextField
                    error={(error?.OID === itemDef.OID) || !rangeCheckError.isValid}
                    id={`sdm-float-v2-input-${itemDef?.OID}`}
                    label=""
                    name="name"
                    size="small"
                    sx={{ m: 1 }}
                    type="text"
                    inputMode="decimal"
                    placeholder="Enter number"
                    fullWidth
                    value={itemData?.Value}
                    inputProps={{
                        step: "0.1",
                        pattern: '[0-9]*\\.?[0-9]*'
                    }}
                    required
                    helperText={
                        error?.OID === itemDef.OID ? "Please fill in this field." : !rangeCheckError.isValid ? rangeCheckError.errorMessage : ""
                    }
                    onChange={inputchange}
                    onKeyPress={(e) => {
                        // PREVENT NON-NUMERIC KEYBOARD INPUT
                        const char = e.key;
                        const currentValue = (e.target as HTMLInputElement).value;

                        // Allow control keys
                        if (['Backspace', 'Delete', 'ArrowLeft', 'ArrowRight', 'Tab', 'Enter'].includes(char)) {
                            return;
                        }

                        // Allow digits, decimal point, and negative sign
                        if (!/[0-9.-]/.test(char)) {
                            e.preventDefault();
                            return;
                        }

                        // Prevent multiple decimal points
                        if (char === '.' && currentValue.includes('.')) {
                            e.preventDefault();
                        }

                        // Prevent negative sign if not at start or already exists
                        if (char === '-' && (currentValue.length > 0 || currentValue.includes('-'))) {
                            e.preventDefault();
                        }
                    }}
                    disabled={readonly}
                />
                <Typography sx={{ textAlign: 'center' }}>{measureDef?.Symbol?.TranslatedText?.text}</Typography>
            </Stack>
        </>
    )
}

export default FloatText;