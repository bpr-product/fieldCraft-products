import { Box, Checkbox, FormControl, FormControlLabel, FormGroup, FormHelperText, FormLabel, Grid } from "@mui/material";
import React, { useEffect, useState } from "react";
import { CodeList, CodeListItem, ConditionDef, ItemData, ItemDef } from "../../../interface/SdmInterfaces";
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';


interface SingleSelectProps {
    itemDef: ItemDef;
    codeList: CodeList[];
    conditionDef: ConditionDef[]
    onItemchange: React.Dispatch<React.SetStateAction<any>>;
    showStatus: React.Dispatch<React.SetStateAction<any>>;
}

const SingleSelect: React.FC<any> = ({ OID, itemDef, conditionDef, codeList, onItemchange, showStatus, itemDatas, error, isRepeatItemGrp, itgrpInd, methodDefs, readonly }) => {

    // if((codeList !== null || undefined) && codeList.length > 0 && codeList[0].OID === "CL.158"){

    const [state, setState] = useState('');
    const [itemData, setItemData] = useState<ItemData>();
    const [codeListData, setcodeData] = useState<CodeList[]>(JSON.parse(JSON.stringify(codeList)));
    const [show, setShow] = useState(false);
    const [hide, setHide] = useState(false);



    // useEffect(() => {       
    //     if (itemDatas?.length > 0) {
    //         itemDatas?.map((i: any) => {
    //             if (i?.ItemOID === itemDef?.OID) {
    //                 i.CodeList.map((cd: any) => {
    //                     cd.CodeListItem.map((c: any) => {
    //                         codeList.map((cl: any) => {
    //                             cl.CodeListItem.map((cli: any) => {
    //                                 if (cli.CodedValue === c.CodedValue) {
    //                                     // cli.IsEnabled = c.IsEnabled;
    //                                     if (c.IsEnabled) {
    //                                         conditionDef.map((cnd: any) => {
    //                                             showStatus(cnd.FormalExpression.text.replaceAll("\"", "").includes(i.ItemOID + " == " + (c.CodedValue).toString()), cnd.OID, OID, null, (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
    //                                         })
    //                                     }
    //                                 }
    //                             }
    //                             )
    //                         })
    //                     })
    //                 })
    //             }
    //         })
    //     }
    // }, [itemDatas]);


    const handleChange = (event: React.ChangeEvent<HTMLInputElement>, indexes: any) => {
        const checked = event.target.checked;
        // const updatedTodos = [...codeList];
        const updatedTodos = JSON.parse(JSON.stringify(codeListData));
        if (checked) {
            setShow(event.target.checked);
            setState(event.target.value);

            conditionDef?.map((cd: any) => {
                let cdf = cd?.FormalExpression?.text.includes("||");
                if (cdf === true) {
                    let cdd = cd?.FormalExpression?.text.split("||");
                    let cdn: [];
                    cdd.map((x: any) => {
                        let cdn = x.split("==");
                        if (cdn[1].includes(event.target.value)) {
                            showStatus(cdn[1].includes(event.target.value), cd.OID, OID, null, (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
                        }
                    })
                } else {
                    let cdn = cd?.FormalExpression?.text.split("==");
                    showStatus(cdn[1].includes(event.target.value), cd.OID, OID, null, (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
                }
            });

            updatedTodos[0].CodeListItem.map((x, i) => x.CodedValue === event.target.value ? x.IsEnabled = event.target.checked : x.IsEnabled = !event.target.checked);
        } else {
            setShow(event.target.checked);
            setState(event.target.value);
            conditionDef.map((cd: any) => {
                let cdn = cd?.FormalExpression?.text.split("==");
                if (cdn[1].includes(event.target.value)) {
                    showStatus(false, cd.OID, OID, null, (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
                }
            });
            updatedTodos[0].CodeListItem.map((x, i) => x.CodedValue === event.target.value ? x.IsEnabled = event.target.checked : x.IsEnabled = event.target.checked);
        }
        setcodeData(updatedTodos);

        // let codeListItems: CodeListItem[] = codeList[0].CodeListItem;
        //const indexToUpdate = codeListItems.findIndex((x) => x.CodedValue === event.target.value);
        // creates a copy of the array
        //updatedTodos[0].CodeListItem[indexToUpdate].IsEnabled = event.target.checked; // modifies the copy
        // setcodeData(updatedTodos);
        // codeList = updatedTodos;

        if (itemDef.CodeListOID = codeList[0].OID) {
            if (itemDatas.length > 0 && itemDatas[0] !== undefined) {
                setItemData({ ...itemDatas[0], CodeList: updatedTodos });
                onItemchange({ ...itemDatas[0], CodeList: updatedTodos });
            }
            else {
                if (updatedTodos !== null && updatedTodos.length > 0) {
                    updatedTodos[0].ItemGroupRepeatKey = isRepeatItemGrp === 'Yes' ? itgrpInd : 0;
                }
                let tempItemData: ItemData = { ItemOID: itemDef.OID, Value: "", CodeList: updatedTodos, MetaKey: itemDef.MetaKey, ItemGroupRepeatKey: isRepeatItemGrp === 'Yes' ? itgrpInd : 0 };
                setItemData(tempItemData);
                onItemchange(tempItemData);
            }
        }
    };

    // const error = [state].filter((v) => v).length !== 1;

    return (
        <>
            <Box sx={{ display: 'flex' }}>
                <FormControl
                    required
                    error={error}
                    component="fieldset"
                    disabled={readonly}
                    // sx={{ m: 1 }}
                    sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1 }}
                    variant="standard"
                >

                    {/* {!show ? <FormLabel component="legend">Pick Any one</FormLabel> : null} */}
                    {codeListData?.map((code: any) => {
                        let cdlt = itemDatas?.find((i: any) => i?.CodeList[0]?.OID === code.OID)
                        return (
                            code.CodeListItem.map((i: any, index: any) => {
                                cdlt?.CodeList?.map((cd: any) => {
                                    cd?.CodeListItem?.map((c: any) => {
                                        if (c?.CodedValue === i?.CodedValue) {
                                            i.IsEnabled = c.IsEnabled
                                        }
                                    })
                                })
                                return (

                                    <FormGroup>
                                        <FormControlLabel
                                            control={
                                                <Checkbox  id={`sdm-single-select-radio-${itemDef?.OID}`} icon={<RadioButtonUncheckedIcon />}
                                                    checkedIcon={<CheckCircleIcon />} onChange={e => handleChange(e, index)} checked={i.IsEnabled} value={i.CodedValue} />
                                            }
                                            label={i.Decode.TranslatedText.text}
                                        />
                                    </FormGroup>

                                )
                            })
                        )

                    })
                    }
                    <FormHelperText>{(error?.OID === itemDef.OID) ? 'Please select an option' : null}</FormHelperText>
                </FormControl>
            </Box >
        </>
    )
}

export default SingleSelect;
