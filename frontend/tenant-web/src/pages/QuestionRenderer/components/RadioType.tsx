import React, { useEffect, useState } from "react";
import { FormControl, FormControlLabel, FormHelperText, Radio, RadioGroup } from "@mui/material";
import { ItemData } from "../../../interface/SdmInterfaces";
import { useTranslation } from "react-i18next";


const RadioType: React.FC<any> = ({ OID, itemDef, conditionDef, codeList, onItemchange, showStatus, itemDatas, error, isRepeatItemGrp, itgrpInd, methodDefs, readonly }) => {

    const { t } = useTranslation();
    const [itemData, setItemData] = useState<ItemData | null>(null);

    // useEffect(() => {     
    //     if (itemDatas?.length > 0) {
    //         itemDatas?.map((i: any) => {
    //             if (i?.ItemOID === itemDef?.OID) {
    //                 conditionDef.map((cnd: any) => {
    //                     showStatus(cnd.FormalExpression.text.replaceAll("\"", "").includes(i.ItemOID + " == " + (i.Value)), cnd.OID, OID, null, (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
    //                 })
    //             }
    //         })
    //     }
    // }, [itemDatas]);
    useEffect(() => {
        const existing = itemDatas?.find((d: any) => d.ItemOID === itemDef?.OID) || null;
        if (existing) {
            setItemData(existing);
        }
    }, [itemDatas, itemDef?.OID]);


    const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
        if (event.target.value === "Yes") {
            conditionDef.map((cd: any) => {
                let cdn = cd?.FormalExpression?.text.split("==");
                return showStatus(cdn[1].includes(event.target.value), cd.OID, OID, null, (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
            });
        } else {
            conditionDef.map((cd: any) => {
                let cdn = cd?.FormalExpression?.text.split("==");
                return showStatus(cdn[1].includes(event.target.value), cd.OID, OID, null, (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
            });
        }

        if (itemDatas.length > 0 && itemDatas[0] !== undefined) {
            setItemData({ ...itemDatas[0], Value: event.target.value });
            onItemchange({ ...itemDatas[0], Value: event.target.value });
        } else {
            let tempItemData: ItemData = { ItemOID: itemDef.OID, Value: event.target.value, CodeList: [], MetaKey: itemDef.MetaKey, ItemGroupRepeatKey: 0 };
            setItemData(tempItemData);
            onItemchange(tempItemData);
        }
    }

    return (
        <>
            <FormControl disabled={readonly}>
                <RadioGroup
                    row
                    aria-labelledby="demo-row-radio-buttons-group-label"
                    name="row-radio-buttons-group"
                    // sx={{ m: 1 }}
                    value={itemData?.Value ?? ""}
                    onChange={handleChange}
                >
                    <FormControlLabel value="Yes"  control={<Radio id={`sdm-radio-type-${itemDef?.OID}-Yes`} />} label={t("common.yes")} />
                    <FormControlLabel value="No" control={<Radio id={`sdm-radio-type-${itemDef?.OID}-No`} />} label={t("common.no")} />
                </RadioGroup>
                <FormHelperText>{(error?.OID === itemDef.OID) ? t("validation.selectAnOption") : null}</FormHelperText>
            </FormControl>
        </>
    )

}

export default RadioType;