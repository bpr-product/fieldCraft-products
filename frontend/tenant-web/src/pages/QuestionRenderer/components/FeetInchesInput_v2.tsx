/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2-Jun-2026
 * @author      : Implementation
 * @since       : 2.0.0
 * Requirement# : BID-2.0
 * Purpose      : Feet and Inches Input Component for DataType "measure_ft_in"
 *                Supports SDMs generated from the new Naomi Builder where
 *                Feet & Inches is stored as a DataType instead of a MeasurementUnit.
 *                Backward compatible with old SDMs using MeasurementOid = "MU.32".
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 */

import { TextField, Typography } from "@mui/material";
import React, { useEffect, useState, useRef, useCallback } from "react";
import { ItemData } from "../../../interface/SdmInterfaces";
import { Stack } from "@mui/system";

// Typed props interface (was React.FC<any>) ────────────────────────
interface ItemDef {
  OID: string;
  MetaKey?: string;
  [key: string]: any;
}

export interface FeetInchesInputProps {
  OID: string;
  itemDef: ItemDef;
  onItemchange?: (data: ItemData) => void;
  onChange?: (oid: string, value: string) => void;
  value?: string;
  itemDatas?: ItemData[];
  error?: any;
  itgrpInd?: number;
  isRepeatItemGrp?: string;
  readonly?: boolean;
}

/**
 * FeetInchesInput_v2
 *
 * Renders a split Feet + Inches input control.
 *
 * Triggered when:
 *  - DataType === "measure_ft_in"  (new Naomi Builder SDM)
 *
 * The old path (DataType === "integer" && MeasurementOid === "MU.32") is
 * handled inside IntegerText_v2 and remains untouched for backward compatibility.
 */
const FeetInchesInput_v2: React.FC<FeetInchesInputProps> = ({
  OID,
  itemDef,
  onItemchange,
  onChange,
  value,
  itemDatas,
  error,
  itgrpInd,
  isRepeatItemGrp,
  readonly = false,
}) => {
  // ─── State ───────────────────────────────────────────────────────────────────
  const [itemData, setItemData] = useState<ItemData>(
    itemDatas && itemDatas[0] && itemDef?.OID === itemDatas[0]?.ItemOID
      ? itemDatas[0]
      : {
          ItemOID: itemDef?.OID || "",
          Value: value || "",
          CodeList: [],
          MetaKey: itemDef?.MetaKey || "",
          ItemGroupRepeatKey: 0,
        }
  );

  const [feet, setFeet] = useState<string>("");
  const [inches, setInches] = useState<string>("");

  // Debounce refs – mirrors IntegerText_v2 pattern
  const debounceTimerRef = useRef<NodeJS.Timeout | null>(null);
  const DEBOUNCE_DELAY = 300;
  const isTypingRef = useRef(false);

  // ─── Helpers ─────────────────────────────────────────────────────────────────

  /** Parse stored value string  e.g. "5'11\"" → { feet: "5", inches: "11" } */
  const parseValue = (val: string) => {
    if (!val) return { feet: "", inches: "" };
    // NOSONAR: Safe — deterministic parsing of feet-inches format using literal separators
    const match = val.match(/^(\d*\.?\d*)'?(\d*\.?\d*)"?$/);
    if (match) {
      return { feet: match[1] || "", inches: match[2] || "" };
    }
    return { feet: "", inches: "" };
  };

  // ─── Sync incoming itemDatas ──────────────────────────────────────────────────
  useEffect(() => {
    if (itemDatas && itemDatas.length > 0 && itemDatas[0] !== undefined) {
      const incoming = itemDatas.find(
        (d: ItemData) => d.ItemOID === itemDef?.OID
      );
      if (
        incoming &&
        incoming.Value !== itemData?.Value &&
        !isTypingRef.current
      ) {
        setItemData(incoming);
      }
    } else if (
      !itemDatas ||
      itemDatas.length === 0 ||
      (itemDatas[0] && itemDatas[0].Value === "")
    ) {
      if (itemData?.Value !== "" && itemData?.Value !== undefined) {
        setItemData({
          ItemOID: itemDef?.OID || "",
          Value: "",
          CodeList: [],
          MetaKey: itemDef?.MetaKey || "",
          ItemGroupRepeatKey: 0,
        });
      }
    }
  }, [itemDatas, itemDef?.OID]);

  // ─── Keep feet/inches display in sync with itemData.Value ────────────────────
  useEffect(() => {
    if (itemData?.Value) {
      const parsed = parseValue(itemData.Value);
      setFeet(parsed.feet);
      setInches(parsed.inches);
    } else {
      setFeet("");
      setInches("");
    }
  }, [itemData]);

  // ─── Cleanup debounce on unmount ─────────────────────────────────────────────
  useEffect(() => {
    return () => {
      if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
    };
  }, []);

  // ─── Debounced parent notification ───────────────────────────────────────────
  const debouncedOnItemChange = useCallback(
    (data: ItemData) => {
      if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
      debounceTimerRef.current = setTimeout(() => {
        // Fix #3: Reset isTypingRef so the component resumes listening to parent
        // data changes (e.g. form resets) after the debounce window closes.
        isTypingRef.current = false;
        if (typeof onItemchange === "function") {
          onItemchange(data);
        } else if (typeof onChange === "function") {
          onChange(itemDef.OID, data.Value);
        }
      }, DEBOUNCE_DELAY);
    },
    [onItemchange, onChange, itemDef?.OID]
  );

  // ─── Feet change handler ──────────────────────────────────────────────────────
  const handleFeetChange = (f: string) => {
    isTypingRef.current = true;
    // Only positive integers for feet
    if (f !== "" && !/^[0-9]*$/.test(f)) return;

    setFeet(f);
    const combined = `${f}'${inches}"`;

    const base: ItemData =
      itemDatas && itemDatas.length > 0 && itemDatas[0] !== undefined
        ? { ...itemDatas[0] }
        : {
            ItemOID: itemDef.OID,
            Value: "",
            CodeList: [],
            MetaKey: itemDef.MetaKey ?? "",
            ItemGroupRepeatKey: isRepeatItemGrp === "Yes" ? (itgrpInd ?? 0) : 0,
          };

    const updated: ItemData = { ...base, Value: combined };
    setItemData(updated);
    debouncedOnItemChange(updated);
  };

  // ─── Inches change handler ────────────────────────────────────────────────────
  const handleInchesChange = (i: string) => {
    isTypingRef.current = true;
    // Allow decimal numbers 0–11.99
    // NOSONAR: Safe — deterministic parsing of feet-inches format using literal separators
    if (i !== "" && !/^[0-9]*\.?[0-9]*$/.test(i)) return;
    if ((i.match(/\./g) || []).length > 1) return;

    // Only block values unambiguously out of range while typing.
    // "1" and "11" must stay allowed so the user can type "11.5".
    // Full >= 12 check is done onBlur; during typing only reject negatives
    // and values >= 100 (impossible to become valid by adding more digits).
    const numVal = parseFloat(i);
    if (i !== "" && numVal < 0) return;
    if (i !== "" && numVal >= 100) return;

    setInches(i);
    const combined = `${feet}'${i}"`;

    const base: ItemData =
      itemDatas && itemDatas.length > 0 && itemDatas[0] !== undefined
        ? { ...itemDatas[0] }
        : {
            ItemOID: itemDef.OID,
            Value: "",
            CodeList: [],
            MetaKey: itemDef.MetaKey ?? "",
            ItemGroupRepeatKey: isRepeatItemGrp === "Yes" ? (itgrpInd ?? 0) : 0,
          };

    const updated: ItemData = { ...base, Value: combined };
    setItemData(updated);
    debouncedOnItemChange(updated);
  };

  // ─── Render ───────────────────────────────────────────────────────────────────
  const hasError = error?.OID === itemDef?.OID;

  return (
    <Stack direction="row" spacing={2} alignItems="center">
      {/* Feet */}
      <TextField
        id={`sdm-ft-in-feet-input-${itemDef?.OID}`}
        error={hasError}
        size="small"
        sx={{ m: 1 }}
        type="text"
        inputMode="numeric"
        placeholder="Feet"
        value={feet}
        onChange={(e) => handleFeetChange(e.target.value)}
        inputProps={{ maxLength: 6 }}
        onKeyDown={(e) => {
          if (
            !/[0-9]/.test(e.key) &&
            !["Backspace", "Delete", "ArrowLeft", "ArrowRight", "Tab"].includes(
              e.key
            )
          ) {
            e.preventDefault();
          }
        }}
        disabled={readonly}
      />
      <Typography>ft</Typography>

      {/* Inches */}
      <TextField
        id={`sdm-ft-in-inches-input-${itemDef?.OID}`}
        error={hasError}
        size="small"
        sx={{ m: 1 }}
        type="text"
        inputMode="decimal"
        placeholder="Inches"
        value={inches}
        onChange={(e) => handleInchesChange(e.target.value)}
        onBlur={(e) => {
          // Clamp to 0–11.99 on blur: allows multi-digit typing (e.g. "11")
          // while still enforcing the valid range when the user leaves the field.
          const val = e.target.value;
          const num = parseFloat(val);
          if (val !== "" && num >= 12) {
            handleInchesChange("11");
          }
        }}
        inputProps={{ maxLength: 5 }}
        onKeyDown={(e) => {
          const char = e.key;
          const currentValue = (e.target as HTMLInputElement).value;
          if (
            ["Backspace", "Delete", "ArrowLeft", "ArrowRight", "Tab"].includes(
              char
            )
          )
            return;
          if (!/[0-9.]/.test(char)) {
            e.preventDefault();
            return;
          }
          if (char === "." && currentValue.includes(".")) {
            e.preventDefault();
          }
        }}
        disabled={readonly}
      />
      <Typography>in</Typography>
    </Stack>
  );
};

export default FeetInchesInput_v2;