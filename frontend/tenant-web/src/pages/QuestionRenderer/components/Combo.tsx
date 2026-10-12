import { FormControl, MenuItem, Select } from "@mui/material";
import { ItemData } from "../../../interface/SdmInterfaces";
import { useState } from "react";



const Combo: React.FC<any> = ({ itemDef, conditionDef, codeList, onItemchange, showStatus, itemDatas, error, readonly }) => {
    

    const [itemData, setItemData] = useState<ItemData>();

    const handleChange = (event: any) => {
        let tempItemData: ItemData = { ItemOID: itemDef?.OID, Value: event.target.value, CodeList: [], MetaKey: itemDef?.MetaKey, ItemGroupRepeatKey:0 };
        setItemData(tempItemData);
        onItemchange(tempItemData);
    }


    return (
        <>
            <FormControl variant="outlined" fullWidth size="small" sx={{ m: 1 }} disabled={readonly}>
                {/* <InputLabel id="demo-select-small-label">Customers</InputLabel> */}
                <Select id="combo-select-customers"
                    // error={selectedData.customer === undefined && formValidator === true}
                    value={(itemDef?.OID === itemDatas[0]?.ItemOID) ? itemDatas[0]?.Value : itemData?.Value}
                    //label="Customers"
                    onChange={(e) => handleChange(e)}
                    name='customer'
                >
                    {/* <MenuItem value="">
                        <em>None</em>
                    </MenuItem> */}
                    {codeList?.map((item: any) => {
                        return (
                            item?.CodeListItem?.map((i: any, index: any) => {
                                return (
                                    <MenuItem value={i?.Decode?.TranslatedText?.text}>
                                        {/* <Checkbox id="combo-checkbox-checkbox" value={i?.CodedValue} checked={i.IsEnabled} />
                                        <ListItemText primary={i?.Decode?.TranslatedText?.text} /> */}

                                        <MenuItem value={i?.Decode?.TranslatedText?.text}>{i?.Decode?.TranslatedText?.text}</MenuItem>
                                    </MenuItem>
                                )
                            })
                        )
                    })}
                </Select>
            </FormControl>
        </>
    )
}

export default Combo;
