/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 2-Nov-2024
* @author      : Vaisnave I M
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Integer Text Input Component
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 21928  | 2026-06-24 | Karthika S | Fix stale validation errors persisting after field is filled in Boolean/Q&A renderer
*/

import { TextField, Typography } from "@mui/material";
import React, { useEffect, useState, useRef, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { ItemData } from "../../../interface/SdmInterfaces";
import { Stack } from "@mui/system";
import { validateRangeCheck, ValidationResult } from "../../../utils/rangeCheckValidator";

const IntegerText: React.FC<any> = ({ OID, itemDef, measureDef, onItemchange, onChange, value, itemDatas, error, itgrpInd, showStatus, isRepeatItemGrp, methodDefs, mapping, tradeData, readonly = false }) => {

  const { t } = useTranslation();

  const [itemData, setItemData] = useState<ItemData>((itemDatas && itemDatas[0] && itemDef?.OID === itemDatas[0]?.ItemOID) ? itemDatas[0] : { ItemOID: itemDef?.OID || '', Value: value || '', CodeList: [], MetaKey: itemDef?.MetaKey || '', ItemGroupRepeatKey: 0 });
  const [feet, setFeet] = useState<string>("");
  const [inches, setInches] = useState<string>("");
  const [rangeCheckError, setRangeCheckError] = useState<ValidationResult>({ isValid: true });

  useEffect(() => {
    if (itemDef?.MeasurementOid === 'MU.32') return;
    if (itemDef?.RangeCheck && Array.isArray(itemDef.RangeCheck) && itemData?.Value !== '' && itemData?.Value !== undefined) {
      const result = validateRangeCheck(itemData.Value, itemDef.RangeCheck);
      setRangeCheckError(result);
    } else {
      setRangeCheckError({ isValid: true });
    }
  }, [itemData?.Value, itemDef?.OID, itemDef?.RangeCheck]);

  const debounceTimerRef = useRef<NodeJS.Timeout | null>(null);
  const DEBOUNCE_DELAY = 300; // 300ms delay for parent updates
  const isTypingRef = useRef(false);

  const debouncedOnItemChange = useCallback((data: ItemData) => {
    // Clear existing timer
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }


    // Set new timer
    debounceTimerRef.current = setTimeout(() => {

      // Support both callback formats
      if (typeof onItemchange === 'function') {
        onItemchange(data);
      } else if (typeof onChange === 'function') {
        onChange(itemDef.OID, data.Value);
      }
    }, DEBOUNCE_DELAY);
  }, [onItemchange, onChange, itemDef.OID]);

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (itemDatas && itemDatas.length > 0 && itemDatas[0] !== undefined) {
      const incoming = itemDatas.find((d: ItemData) => d.ItemOID === itemDef?.OID);
      if (incoming && incoming.Value !== itemData?.Value && !isTypingRef.current
      ) {
        setItemData(incoming);
      }
    } else if (!itemDatas || itemDatas.length === 0 || (itemDatas[0] && itemDatas[0].Value === '')) {
      // If itemDatas is empty or cleared (after deletion), reset local state
      if (itemData?.Value !== '' && itemData?.Value !== undefined) {
        setItemData({ ItemOID: itemDef?.OID || '', Value: '', CodeList: [], MetaKey: itemDef?.MetaKey || '', ItemGroupRepeatKey: 0 });
      }
    }
  }, [itemDatas, itemDef?.OID]);

  const inputchange = (e: any) => {
    isTypingRef.current = true;
    const inputValue = e.target.value;

    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
      debounceTimerRef.current = null;
    }

    // Strict numeric validation - only allow numbers and decimal point
    const numericPattern = /^[0-9]*\.?[0-9]*$/; // NOSONAR: Safe — deterministic parsing of feet-inches format using literal separators

    // Prevent non-numeric characters
    if (inputValue !== '' && !numericPattern.test(inputValue)) {
      return; // Reject invalid input
    }

    // Prevent multiple decimal points
    const decimalCount = (inputValue.match(/\./g) || []).length;
    if (decimalCount > 1) {
      return; // Reject multiple decimal points
    }

    let tempItemData: ItemData;

    if (itemDatas && itemDatas.length > 0 && itemDatas[0] !== undefined) {
      tempItemData = { ...itemDatas[0], Value: inputValue };
      setItemData(tempItemData);
    } else {
      tempItemData = { ItemOID: itemDef.OID, Value: inputValue, CodeList: [], MetaKey: itemDef.MetaKey, ItemGroupRepeatKey: isRepeatItemGrp === 'Yes' ? itgrpInd : 0 };
      setItemData(tempItemData);
    }

    let isRangeCheckValid = true;
    if (itemDef?.RangeCheck && Array.isArray(itemDef.RangeCheck) && itemDef?.MeasurementOid !== 'MU.32') {
      const validationResult = validateRangeCheck(inputValue, itemDef.RangeCheck);
      setRangeCheckError(validationResult);
      isRangeCheckValid = validationResult.isValid;
    }

    if (Array.isArray(methodDefs)) {
      methodDefs.forEach((res) => {
        if (res.OID.includes("M.")) {
          const expressionText = res?.FormalExpression?.text;
          if (expressionText && expressionText.includes(":")) {
            const [itemOID, type] = expressionText
              .replace(/[!()]/g, '')
              .split(':')
              .map((str: string) => str.trim());
            if (type === 'RepeatCount' && itemOID === itemDef.OID) {
              const numericInputValue = parseFloat(inputValue);
              if (!isNaN(numericInputValue) && numericInputValue > 0 && isRangeCheckValid) {
                showStatus(true, res.OID, OID, numericInputValue, true, itgrpInd);
              } else {
                showStatus(false, res.OID, OID, 0, true, itgrpInd);
              }
            }
          }
        }
      });
    }

    if (!isRangeCheckValid) {
      tempItemData.rangeCheckFailed = true;
    }
    debouncedOnItemChange(tempItemData);
  };

  // Auto-map from tradeData if present
  useEffect(() => {
    // Use mapping.Name if available, otherwise fall back to itemDef.MetaKey
    const tradeKey = mapping?.Name || itemDef?.MetaKey;

    // Skip if no tradeKey or tradeData
    if (!tradeKey || !tradeData) {
      return;
    }

    // Check if user has already entered a value in itemDatas - respect user edits
    // itemDatas comes from the parent and contains the persisted/session values
    const existingItem = itemDatas?.find((d: any) => d.ItemOID === itemDef?.OID);

    if (existingItem) {
      // Check for Value (uppercase - ItemData format) or value (lowercase - session format)
      const existingValue = existingItem.Value ?? existingItem.value;
      if (existingValue !== undefined && existingValue !== '' && existingValue !== null) {
        return;
      }
    }

    // Look up value using the tradeKey (from mapping.Name or itemDef.MetaKey)
    const rawValue = tradeData[tradeKey] ?? tradeData.TotalMeasurements?.[tradeKey];

    if (rawValue !== undefined && rawValue !== null) {
      const parsed = parseFloat(rawValue);
      if (!isNaN(parsed)) {
        let tempItemData: ItemData = { ItemOID: itemDef.OID, Value: parsed.toString(), CodeList: [], MetaKey: itemDef.MetaKey, ItemGroupRepeatKey: 0 };
        setItemData(tempItemData);

        // Also propagate to itemGroupData for correct submission payload
        // Use setTimeout to avoid potential state update conflicts
        setTimeout(() => {
          if (typeof onItemchange === 'function') {
            onItemchange(tempItemData);
          }
        }, 100);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapping, tradeData, itemDef.OID, itemDef.MetaKey, itemDatas]);

  useEffect(() => {
    if (itemData?.Value) {
      const match = itemData.Value.match(/^(\d*\.?\d*)'?(\d*\.?\d*)"?$/); // NOSONAR: Safe — deterministic parsing of feet-inches format using literal separators
      if (match) {
        setFeet(match[1] || "");
        setInches(match[2] || "");
      }
    }
  }, [itemData]);

  const handleFeetChange = (f: string) => {
    // Validate feet input - only allow positive integers
    const feetPattern = /^[0-9]*$/;
    if (f !== '' && !feetPattern.test(f)) {
      return; // Reject invalid input
    }

    setFeet(f);

    const combined = `${f}'${inches}"`;
    const updated: ItemData = {
      ...itemData,
      Value: combined,
      ItemOID: itemDef.OID,
      MetaKey: itemDef.MetaKey,
      ItemGroupRepeatKey: isRepeatItemGrp === "Yes" ? itgrpInd : 0,
      CodeList: itemData?.CodeList ?? []
    };
    setItemData(updated);

    //  Use debounced callback for feet input
    debouncedOnItemChange(updated);
  };

  const handleInchesChange = (i: string) => {
    // Validate inches input - allow decimal numbers (0-11.99)
    const inchesPattern = /^[0-9]*\.?[0-9]*$/; // NOSONAR: Safe — deterministic parsing of feet-inches format using literal separators
    if (i !== '' && !inchesPattern.test(i)) {
      return; // Reject invalid input
    }

    // Prevent multiple decimal points
    const decimalCount = (i.match(/\./g) || []).length;
    if (decimalCount > 1) {
      return;
    }

    // Optional: Validate inches range (0-11.99)
    const inchesValue = parseFloat(i);
    if (i !== '' && (inchesValue < 0 || inchesValue >= 12)) {
      return; // Reject inches >= 12
    }

    setInches(i);
    const combined = `${feet}'${i}"`;
    const updated: ItemData = {
      ...itemData,
      Value: combined,
      ItemOID: itemDef.OID,
      MetaKey: itemDef.MetaKey,
      ItemGroupRepeatKey: isRepeatItemGrp === "Yes" ? itgrpInd : 0,
      CodeList: itemData?.CodeList ?? []
    };
    setItemData(updated);

    //  Use debounced callback for inches input
    debouncedOnItemChange(updated);
  };

  return (
    <Stack direction="row" spacing={2}>
      {itemDef?.MeasurementOid === "MU.32" ? (
        <>
          {/* Feet box */}
          <TextField
            id={`sdm-integer-feet-input-${itemDef?.OID}`}
            error={error?.OID === itemDef.OID && (!itemData?.Value || itemData.Value === '')}
            size="small"
            sx={{ m: 1 }}
            type="text"
            inputMode="numeric"
            placeholder="Feet"
            value={feet}
            onChange={(e) => handleFeetChange(e.target.value)}
            inputProps={{ maxLength: 6 }} // Allow up to 6 digits (e.g., "999999")
            onKeyPress={(e) => {
              // Only allow digits for feet
              if (!/[0-9]/.test(e.key) && !['Backspace', 'Delete', 'ArrowLeft', 'ArrowRight', 'Tab'].includes(e.key)) {
                e.preventDefault();
              }
            }}
            disabled={readonly}
          />
          <Typography>ft</Typography>

          {/* Inches box */}
          <TextField
            id={`sdm-integer-inches-input-${itemDef?.OID}`}
            error={error?.OID === itemDef.OID && (!itemData?.Value || itemData.Value === '')}
            size="small"
            sx={{ m: 1 }}
            type="text"
            inputMode="decimal"
            placeholder="Inches"
            value={inches}
            onChange={(e) => handleInchesChange(e.target.value)}
            inputProps={{ maxLength: 5 }} // Allow up to 5 characters (e.g., "11.99")
            onKeyPress={(e) => {
              // Allow digits and decimal point for inches
              const char = e.key;
              const currentValue = (e.target as HTMLInputElement).value;

              if (['Backspace', 'Delete', 'ArrowLeft', 'ArrowRight', 'Tab'].includes(char)) {
                return;
              }

              if (!/[0-9.]/.test(char)) {
                e.preventDefault();
                return;
              }

              // Prevent multiple decimal points
              if (char === '.' && currentValue.includes('.')) {
                e.preventDefault();
              }
            }}
            disabled={readonly}
          />
          <Typography>in</Typography>
        </>
      ) : (
        <>
          {/* Normal single box */}
          <TextField
            error={(error?.OID === itemDef.OID && (!itemData?.Value || itemData.Value === '')) || !rangeCheckError.isValid}
            id={`sdm-integer-v2-input-${itemDef?.OID}`}
            label=""
            name="name"
            size="small"
            sx={{ m: 1 }}
            type="text"
            inputMode="decimal"
            placeholder={t("sdm.enterNumber")}
            fullWidth
            value={itemData?.Value}
            inputProps={{ step: "0.01", min: 0 }}
            helperText={
              (error?.OID === itemDef.OID && (!itemData?.Value || itemData.Value === '')) ? t("validation.fillIn") : !rangeCheckError.isValid ? rangeCheckError.errorMessage : ""
            }
            onChange={inputchange}
            onKeyPress={(e) => {
              // PREVENT NON-NUMERIC KEYBOARD INPUT
              const char = e.key;
              const currentValue = (e.target as HTMLInputElement).value;

              // Allow control keys (backspace, delete, arrow keys, etc.)
              if (['Backspace', 'Delete', 'ArrowLeft', 'ArrowRight', 'Tab', 'Enter'].includes(char)) {
                return;
              }

              // For integer fields, only allow digits
              if (itemDef?.DataType === 'integer') {
                if (!/[0-9]/.test(char)) {
                  e.preventDefault();
                }
                return;
              }

              // For decimal fields, allow digits and one decimal point
              if (!/[0-9.]/.test(char)) {
                e.preventDefault();
                return;
              }

              // Prevent multiple decimal points
              if (char === '.' && currentValue.includes('.')) {
                e.preventDefault();
              }
            }}
            disabled={readonly}
          />
          <Typography sx={{ textAlign: "center" }}>
            {measureDef?.Symbol?.TranslatedText?.text}
          </Typography>
        </>
      )}
    </Stack>
  );
};

export default IntegerText;
