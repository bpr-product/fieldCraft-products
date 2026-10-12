/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 2-Nov-2024
* @author      :
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Multiline Text Input Component
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 21928  | 2026-06-24 | Karthika S | Fix stale validation errors persisting after field is filled in Boolean/Q&A renderer
*/

import { TextField, Stack, Typography } from "@mui/material";
import React, { useEffect, useState } from "react";
import { ItemData, ItemDef } from "../../../interface/SdmInterfaces";

interface Props {
  itemDef: ItemDef;
  onItemchange: React.Dispatch<React.SetStateAction<any>>;
  itemDatas: ItemData[];
  measureDef?: any;
  error?: any;
  readonly?: boolean;
}

const MultilineText: React.FC<Props> = ({ itemDef, measureDef, onItemchange, itemDatas, error, readonly }) => {
  // Start with null (empty) so the input stays controlled.
  const [itemData, setItemData] = useState<ItemData | null>(null);

  // Then use useEffect to update state when itemDatas is actually filled.
  useEffect(() => {
    // Try to find matching ItemData by ItemOID, If we found matching data → set it
    const existing = itemDatas?.find((d) => d.ItemOID === itemDef?.OID) || null;
    if (existing) {
      setItemData(existing);
    }
  }, [itemDatas, itemDef?.OID]);

  const inputchange = (e: any) => {
    if (itemDatas.length > 0 && itemDatas[0] !== undefined) {
      setItemData({ ...itemDatas[0], Value: e.target.value });
      onItemchange({ ...itemDatas[0], Value: e.target.value });
    } else {
      let tempItemData: ItemData = { ItemOID: itemDef.OID, Value: e.target.value, CodeList: [], MetaKey: itemDef.MetaKey ?? "", ItemGroupRepeatKey: 0 };
      setItemData(tempItemData);
      onItemchange(tempItemData);
    }
  }

  return (
    <>
      <Stack direction="row" spacing={2}>
        <TextField
          error={(error?.OID === itemDef.OID && (!itemData?.Value || itemData.Value === '')) ? true : false}
          id={itemDef?.OID}
          label=""
          name="Value"
          size="small"
          sx={{ m: 1 }}
          fullWidth
          multiline
          rows={4}
          value={itemData?.Value ?? ""}
          required
          helperText={(error?.OID === itemDef.OID && (!itemData?.Value || itemData.Value === '')) ? 'Please fill this field' : ''}
          onChange={inputchange}
          disabled={readonly}
        />
        <Typography sx={{ textAlign: 'center' }}>{measureDef?.Symbol?.TranslatedText?.text}</Typography>
      </Stack>
      {/* <TextField
                error={(error?.OID === itemDef.OID) ? true : false}
                id={itemDef?.OID}
                label=""
                name="Value"
                size="small"
                sx={{ m: 1 }}
                fullWidth
                value={ itemData?.Value}
                multiline
                rows={4}
                required
                helperText={(error?.OID === itemDef.OID) ? 'Please fill this field' : ''}
                onChange={inputchange}
            /> */}
    </>
  )
}
export default MultilineText;
