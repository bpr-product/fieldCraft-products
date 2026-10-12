import { Box, Checkbox, FormControl, FormControlLabel, FormGroup, FormHelperText } from "@mui/material";
import { useState } from "react";
import {CodeListItem, ItemData } from "../../../interface/SdmInterfaces";


const MultiChoice: React.FC<any> = ({ OID, itemDef, conditionDef, codeList, onItemchange, showStatus, itemDatas, error, isRepeatItemGrp, itgrpInd, methodDefs, readonly }) => {


    const [, setItemData] = useState<ItemData>();
    const [state, setState] = useState<any>([] as any[]);

    // useEffect(() => {
    //     if (itemDatas?.length > 0) {
    //         itemDatas?.map((i: any) => {
    //             if (i?.ItemOID === itemDef?.OID) {
    //                 i.CodeList.map((cd: any) => {
    //                     cd.CodeListItem.map((c: any) => {
    //                         // codeList.map((cl: any) => {
    //                         //  cl.CodeListItem.map((cli: any) => {
    //                         // if (cli.CodedValue === c.CodedValue) {
    //                         // cli.IsEnabled = c.IsEnabled;
    //                         if (c.IsEnabled) {                          
    //                             conditionDef.map((cd: any) => {
    //                                 let formalExp = cd?.FormalExpression?.text;
    //                                 if (formalExp !== null && formalExp !== undefined) {
    //                                     let cdn = formalExp.split("==");                                       
    //                                     if (cdn[1].includes(c.CodedValue)) {
    //                                         showStatus(cdn[1].includes(c.CodedValue), cd.OID, OID, null, (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
    //                                     }
    //                                 }

    //                                 // showStatus(cnd.FormalExpression.text.includes(i.ItemOID), cnd.OID);
    //                                 //showStatus(cnd.FormalExpression.text.replaceAll("\"", "").includes(i.ItemOID + " == " + (c.CodedValue).toString()), cnd.OID, OID, null, false, (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
    //                             })
    //                         }
    //                         // }
    //                         // })
    //                         // })
    //                     })
    //                 })
    //             }
    //         })
    //     }
    // }, [itemDatas]);

    const handleChange = (event: React.ChangeEvent<HTMLInputElement>, indexes: any) => {
        const checked = event.target.checked;
        if (checked) {
            setState([...state, event.target.value]);
            conditionDef.map((cd: any) => {
                let cdf = cd?.FormalExpression?.text.includes("||");
                if (cdf === true) {
                    let cdd = cd?.FormalExpression?.text.split("||");
                    cdd.map((x: any) => {
                        let cdn = x.split("==");
                        if (cdn[1].includes(event.target.value)) {
                            showStatus(cdn[1].includes(event.target.value), cd.OID, OID, null, (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
                        }
                        return null;
                    })
                }
                return null;
            });
            //setCheckedState(new Array(state.length).fill(true));
        } else {
            setState([...state, event.target.value]);
            conditionDef.map((cd: any) => {
                let cdn = cd?.FormalExpression?.text.split("==");
                if (cdn[1].includes(event.target.value)) {
                    showStatus(false, cd.OID, OID, null, (isRepeatItemGrp === 'Yes' ? true : false), (isRepeatItemGrp === 'Yes' ? itgrpInd : 0));
                }
                return null;
            });
            //setCheckedState(new Array(state.length).fill(false));
        }

        let codeListItems: CodeListItem[] = codeList[0].CodeListItem;
        const indexToUpdate = codeListItems.findIndex((x) => x.CodedValue === event.target.value);
        const updatedTodos = [...codeList]; // creates a copy of the array
        updatedTodos[0].CodeListItem[indexToUpdate].IsEnabled = event.target.checked; // modifies the copy
        // codeList = updatedTodos;
        // setcodeData(updatedTodos);
        if (itemDef.CodeListOID === codeList[0].OID) {
            if (itemDatas.length > 0 && itemDatas[0] !== undefined) {
                setItemData({ ...itemDatas[0], CodeList: updatedTodos });
                onItemchange({ ...itemDatas[0], CodeList: updatedTodos });
            }
            else {
                let tempItemData: ItemData = { ItemOID: itemDef.OID, Value: "", CodeList: updatedTodos, MetaKey: itemDef.MetaKey, ItemGroupRepeatKey: 0 };
                setItemData(tempItemData);
                onItemchange(tempItemData);
            }
        }
    };

    // const error = [state].filter((v) => v).length > 1;

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
                    {/* <FormLabel component="legend">Pick two</FormLabel> */}
                    {codeList?.map((item: any) => {
                        let cdlt = itemDatas.find((id: any) => id?.CodeList[0]?.OID === item.OID)
                        return (
                            item.CodeListItem.map((i: any, index: any) => {
                                cdlt?.CodeList?.map((cd: any) => {
                                    cd?.CodeListItem?.map((c: any) => {
                                        if (c?.CodedValue === i?.CodedValue) {
                                            i.IsEnabled = c.IsEnabled
                                        }
                                        return null;
                                    })
                                    return null;
                                })
                                return (
                                    <FormGroup>
                                        <FormControlLabel
                                            control={
                                                // checked={checkedState[index]}
                                                <Checkbox  id={`sdm-multi-choice-checkbox-${itemDef?.OID}`} onChange={e => handleChange(e, index)} checked={i.IsEnabled} value={i.CodedValue} />
                                                // <Checkbox id="multi-choice-checkbox-checkbox-2" checked={i.IsEnabled} value={i?.CodedValue}  onChange={e => handleChange(e, index)} />
                                            }
                                            label={i.Decode.TranslatedText.text}
                                        />

                                        {/* <FormControlLabel control={<Checkbox id="multi-choice-checkbox-checkbox-3" checked={cdlt?.CodeList[0].CodeListItem[index].IsEnabled} />} label={i.Decode.TranslatedText.text} /> */}
                                    </FormGroup>
                                )
                            })
                        )
                    })
                    }
                    <FormHelperText>{(error?.OID === itemDef.OID) ? 'Please select an option' : null}</FormHelperText>
                </FormControl>
            </Box>
        </>
    )
}

export default MultiChoice;
