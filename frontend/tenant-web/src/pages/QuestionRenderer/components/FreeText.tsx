import { Stack, TextField, Typography } from "@mui/material";
import React, { useEffect, useState } from "react";
import { ItemData, ItemDef } from "../../../interface/SdmInterfaces";

interface Props {
  itemDef: ItemDef;
  measureDef?: any;
  onItemchange: React.Dispatch<React.SetStateAction<any>>;
  itemDatas: ItemData[];
  error?: ItemDef;
  readonly?: boolean;
  mapping?: any;
  tradeData?: any;
}

const FreeText: React.FC<Props> = ({ itemDef, measureDef, onItemchange, itemDatas, error, readonly, mapping, tradeData }) => {
  const [itemData, setItemData] = useState<ItemData | null>(null);
  const isTypingRef = React.useRef(false);


  //use useEffect to update state when itemDatas is actually filled.
  useEffect(() => {
    const existing = itemDatas?.find((d) => d.ItemOID === itemDef?.OID) || null;
    if (existing && !isTypingRef.current) {
      setItemData(existing);
    }
  }, [itemDatas, itemDef?.OID]);

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
      const existingValue = existingItem.Value ?? (existingItem as any).value;
      if (existingValue !== undefined && existingValue !== '' && existingValue !== null) {
        return;
      }
    }

    // Look up value using the tradeKey
    const rawValue = tradeData[tradeKey] ?? tradeData.TotalMeasurements?.[tradeKey];

    if (rawValue !== undefined && rawValue !== null && rawValue !== '') {
      let tempItemData: ItemData = { ItemOID: itemDef.OID, Value: String(rawValue), CodeList: [], MetaKey: itemDef.MetaKey ?? "", ItemGroupRepeatKey: 0 };
      setItemData(tempItemData);

      setTimeout(() => {
        if (typeof onItemchange === 'function') {
          onItemchange(tempItemData);
        }
      }, 100);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapping, tradeData, itemDef.OID, itemDef.MetaKey, itemDatas]);




  const inputchange = (e: React.ChangeEvent<HTMLInputElement>) => {
    isTypingRef.current = true;
    if (itemDatas.length > 0) {
      setItemData({ ...itemDatas[0], Value: e.target.value });
      onItemchange({ ...itemDatas[0], Value: e.target.value });
    } else {
      let tempItemData: ItemData = { ItemOID: itemDef.OID, Value: e.target.value, CodeList: [], MetaKey: itemDef.MetaKey ?? "", ItemGroupRepeatKey: 0 };
      setItemData(tempItemData);
      onItemchange(tempItemData);
    }
  };


  return (
    <>
      <Stack direction="row" spacing={2}>
        <TextField
          // error={(error?.OID === itemDef.OID) ? ((itemDatas.length === 0 || itemDatas[0]?.Value === '') ? true : false) : false}
          error={(error?.OID === itemDef.OID) ? true : false}
          id={itemDef?.OID}
          label=""
          name="Value"
          size="small"
          sx={{ m: 1 }}
          fullWidth
          value={itemData?.Value ?? ""}
          required
          disabled={readonly}
          //key={itemDef?.OID}
          helperText={(error?.OID === itemDef.OID) ? 'Please fill this field' : ''}
          // helperText={(error?.OID === itemDef.OID) ? ((itemDatas.length === 0 || itemDatas[0]?.Value === '') ? 'Please fill this field' : '') : ''}
          onChange={inputchange}
        />
        <Typography sx={{ textAlign: 'center' }}>{measureDef?.Symbol?.TranslatedText?.text}</Typography>
      </Stack>
    </>
  );
};

export default FreeText;  