import { Stack, TextField, Typography } from "@mui/material";
import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { ItemData } from "../../../interface/SdmInterfaces";


const FloatText: React.FC<any> = ({ itemDef, measureDef, onItemchange, itemDatas, error, readonly }) => {
    const { t } = useTranslation();


    const [itemData, setItemData] = useState<ItemData>((itemDef?.OID === itemDatas[0]?.ItemOID) ? itemDatas[0] : []);

    const inputchange = (e: any) => {
        if (itemDatas.length > 0) {
            setItemData({ ...itemDatas[0], Value: e.target.value });
            onItemchange({ ...itemDatas[0], Value: e.target.value });
        } else {
            let tempItemData: ItemData = { ItemOID: itemDef.OID, Value: e.target.value, CodeList: [], MetaKey: itemDef.MetaKey, ItemGroupRepeatKey:0 };
            setItemData(tempItemData);
            onItemchange(tempItemData);
        }
    }

    return (
        <>
            <Stack direction="row" spacing={2}>
                <TextField
                    error={(error?.OID === itemDef.OID) ? true : false}
                    id={`sdm-float-input-${itemDef?.OID}`}
                    label=""
                    name="name"
                    size="small"
                    sx={{ m: 1 }}
                    type="number"
                    fullWidth
                    value={itemData?.Value}
                    inputProps={{ step: "0.1" }}
                    required
                    helperText={(error?.OID === itemDef.OID) ? t('validation.fillThisField') : ''}
                    onChange={inputchange}
                    disabled={readonly}
                />
                <Typography sx={{ textAlign: 'center' }}>{measureDef?.Symbol?.TranslatedText?.text}</Typography>
            </Stack>
        </>
    )
}

export default FloatText;
