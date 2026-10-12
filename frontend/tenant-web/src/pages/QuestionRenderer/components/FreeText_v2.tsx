/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 2-Nov-2024
* @author      : Vaisnave I M
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Free Text Input Component
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 21928  | 2026-06-24 | Karthika S | Fix stale validation errors persisting after field is filled in Boolean/Q&A renderer
*/


import { Stack, Typography } from "@mui/material";
import React, { useState, useEffect, useCallback, useRef } from "react";
import { ItemDef } from "../../../interface/SdmInterfaces";
import { StyledTextField, UnitTypography } from "./StyledComponents_v2";

interface FreeTextProps {
  itemDef: ItemDef;
  value?: string;
  onChange: (itemOID: string, value: string) => void;
  measureDef?: any;
  error?: any;
  readonly?: boolean;
}

const FreeText_v2: React.FC<FreeTextProps> = ({
  itemDef,
  value,
  onChange,
  measureDef,
  error,
  readonly = false
}) => {
  // Local state for immediate UI updates
  const [localValue, setLocalValue] = useState(value || '');
  const debounceTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const isTypingRef = useRef(false); // Track if user is actively typing

  // Sync local value with external value prop (for programmatic updates only)
  useEffect(() => {
    // Only update if user is NOT actively typing and values differ
    if (!isTypingRef.current && value !== undefined && value !== localValue) {
      setLocalValue(value || '');
    }
  }, [value]); // Only depend on value prop, not localValue to avoid loops

  // Debounced onChange function
  const debouncedOnChange = useCallback((newValue: string) => {
    // Clear previous timeout
    if (debounceTimeoutRef.current) {
      clearTimeout(debounceTimeoutRef.current);
    }

    // Set new timeout for debounced update
    debounceTimeoutRef.current = setTimeout(() => {
      onChange(itemDef.OID, newValue);
    }, 300); // 300ms debounce delay
  }, [onChange, itemDef.OID]);

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (debounceTimeoutRef.current) {
        clearTimeout(debounceTimeoutRef.current);
      }
    };
  }, []);

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newValue = e.target.value;

    // Mark that user is actively typing
    isTypingRef.current = true;

    // Update local state immediately for responsive UI
    setLocalValue(newValue);

    // Trigger debounced parent update
    debouncedOnChange(newValue);
  };

  // Reset typing flag when input loses focus
  const handleBlur = () => {
    isTypingRef.current = false;
  };

  return (
    <Stack direction="row" spacing={2} alignItems="center">
      <StyledTextField
        error={error?.OID === itemDef.OID && (!localValue || localValue === '')}
        id={`sdm-text-v2-input-${itemDef?.OID}`}
        name="Value"
        size="small"
        fullWidth
        value={localValue}
        helperText={error?.OID === itemDef.OID && (!localValue || localValue === '') ? 'Please fill this field' : ''}
        onChange={handleInputChange}
        onBlur={handleBlur}
        disabled={readonly}
      />
      {measureDef?.Symbol?.TranslatedText?.text && (
        <Typography sx={UnitTypography}>
          {measureDef.Symbol.TranslatedText.text}
        </Typography>
      )}
    </Stack>
  );
};

export default React.memo(FreeText_v2);
