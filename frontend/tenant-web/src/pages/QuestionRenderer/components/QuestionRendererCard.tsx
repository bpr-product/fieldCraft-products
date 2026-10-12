/* eslint-disable array-callback-return, no-useless-escape */
import React, { useEffect, useState } from 'react';
import { Box, Card, Grid, Stack, Typography } from '@mui/material';
import MultiChoice from './MultiChoice';
import FreeText from './FreeText';
import Combo from './Combo';
import { BasicDefinitions, CodeList, ConditionDef, FormDef, ItemData, ItemDef, ItemGroupData, ItemGroupDef, Mappings, MethodDef } from '../../../interface/SdmInterfaces';
import SingleSelect from './SingleSelect';
import RadioType from './RadioType';
import DateSelector from './DateSelector';
import DateTime from './DateTime';
import MultilineText from './MultilineText';
import FloatText from './FloatText';
import IntegerText from './IntegerText';

interface Props {
    OID: string;
    measureDef: BasicDefinitions;
    itemDefs: ItemDef[];
    itemGroupDefs: ItemGroupDef[];
    mappings: Mappings[];
    formDefs: FormDef[];
    codeListDefs: CodeList[];
    conditionDefs: ConditionDef[];
    methodDefs: MethodDef[];
    OnItemGrpDataChange: React.Dispatch<React.SetStateAction<any>>;
    show: React.Dispatch<React.SetStateAction<any>>;
    showRepeat: React.Dispatch<React.SetStateAction<any>>;
    showGroupQus: React.Dispatch<React.SetStateAction<any>>;
    itemGroupDatas: ItemGroupData[];
    validate: ItemDef[];
    tradeData: any;
    /** NEW: global readonly toggle */
    readonly?: boolean;
}

const QuestionRendererCard: React.FC<Props> = ({ OID, formDefs, itemGroupDefs, itemDefs, codeListDefs, conditionDefs, measureDef, methodDefs,
    OnItemGrpDataChange, show, showRepeat, showGroupQus, itemGroupDatas, validate, mappings, tradeData, readonly = false }) => {

    const [itemData, setItemData] = useState<ItemData[]>([]);
    const [itemGroupData, setItemGroupData] = useState<ItemGroupData[]>([]);
    const [itemGroupDatases, setItemGroupDatas] = useState<ItemGroupData[]>([]);
    const repeatCount = {};

    useEffect(() => {
        setItemGroupDatas(itemGroupDatas);
    }, [itemGroupDatas])

    // ---------------- your original helper functions (UNTOUCHED) ----------------
    function OnItemDataChange(item: any) {
        if (itemData.length !== 0 && item !== undefined && item !== null) {
            const index = itemData.findIndex((obj) => obj.ItemOID === item.ItemOID && obj.ItemGroupRepeatKey === item.ItemGroupRepeatKey);

            const updatedTextData = [...itemData];
            if (index !== -1) {
                updatedTextData[index] = item;
            }
            else {
                updatedTextData.push(item)
            }
            setItemData(updatedTextData);
            handleItemGroupData(updatedTextData);
        } else {
            setItemData([...itemData, item]);
            handleItemGroupData([item]);
        }
    }

    let itemGroupDataArray: ItemGroupData[] = [];

    const handleItemGroupData = (itemData: any) => {
        let repeatCount = {};
        if (itemGroupDatas.length === 0) {
            if (itemData.length !== 0 && itemData !== undefined && itemData !== null) {
                itemGroupDefs?.map((igd: any, i: any) => {
                    if (!repeatCount[igd.OID]) {
                        repeatCount[igd.OID] = 1;
                    } else {
                        repeatCount[igd.OID]++;
                    }
                    igd?.ItemRef?.map((ir: any) => {
                        itemData?.map((td: any) => {
                            let repeatIndexValue = 0;
                            if (igd.Repeating === 'Yes') {
                                repeatIndexValue = repeatCount[igd.OID];
                            }
                            if (ir.ItemOID === td.ItemOID && repeatIndexValue === td.ItemGroupRepeatKey) {
                                if (itemGroupData.length !== 0) {
                                    const index = itemGroupData.findIndex((obj) => obj.ItemGroupOID === igd.OID && obj.ItemGroupRepeatKey === repeatIndexValue);
                                    const updatedItemGrpData = [...itemGroupData];
                                    if (index !== -1) {
                                        const itd = [...updatedItemGrpData[index].ItemData];
                                        const ind = itd.findIndex((obj) => obj.ItemOID === td.ItemOID && obj.ItemGroupRepeatKey === td.ItemGroupRepeatKey);
                                        if (ind !== -1) {
                                            itd[ind] = td;
                                        } else {
                                            itd.push(td)
                                        }
                                        updatedItemGrpData[index] = { ItemData: itd, ItemGroupOID: updatedItemGrpData[index].ItemGroupOID, ItemGroupRepeatKey: repeatIndexValue, ParentItemGroupRepeatKey: igd.ParentItemGroupRepeatKey };
                                    }
                                    else {
                                        if (td.ItemGroupRepeatKey === repeatIndexValue) {
                                            updatedItemGrpData.push({ ItemData: [td], ItemGroupOID: igd.OID, ItemGroupRepeatKey: repeatIndexValue, ParentItemGroupRepeatKey: igd.ParentItemGroupRepeatKey });
                                        }
                                    }
                                    setItemGroupData(updatedItemGrpData);
                                    let itGrp = {
                                        "ItemGroup": updatedItemGrpData,
                                        "TradeOID": OID
                                    }
                                    OnItemGrpDataChange(itGrp);
                                } else {
                                    itemGroupDataArray.push({ ItemData: [td], ItemGroupOID: igd.OID, ItemGroupRepeatKey: 0 });
                                    setItemGroupData(itemGroupDataArray);
                                    let itGrp = {
                                        "ItemGroup": itemGroupDataArray,
                                        "TradeOID": OID
                                    }
                                    OnItemGrpDataChange(itGrp);
                                }
                            }
                        })
                    })
                })
            }
        } else {
            if (itemData.length !== 0 && itemData !== undefined && itemData !== null) {
                const updatedItemGrpData = [...itemGroupDatases];
                itemGroupDefs?.map((igd) => {
                    igd?.ItemRef?.map((ir) => {
                        itemData?.map((td: any) => {
                            if (ir.ItemOID === td.ItemOID) {
                                if (itemGroupDatases.length !== 0) {
                                    const index = itemGroupDatases.findIndex((obj) => obj.ItemGroupOID === igd.OID && obj.ItemGroupRepeatKey === td.ItemGroupRepeatKey && obj.ParentItemGroupRepeatKey === igd.ParentItemGroupRepeatKey);
                                    if (index !== -1) {
                                        const itd = [...updatedItemGrpData[index].ItemData];
                                        const ind = itd.findIndex((obj) => obj.ItemOID === td.ItemOID && obj.ItemGroupRepeatKey === td.ItemGroupRepeatKey);
                                        if (ind !== -1) {
                                            itd[ind] = td;
                                        } else {
                                            itd.push(td)
                                        }
                                        updatedItemGrpData[index] = { ItemData: itd, ItemGroupOID: updatedItemGrpData[index].ItemGroupOID, ItemGroupRepeatKey: td.ItemGroupRepeatKey, ParentItemGroupRepeatKey: igd.ParentItemGroupRepeatKey };
                                    }
                                    else {
                                        updatedItemGrpData.push({ ItemData: [td], ItemGroupOID: igd.OID, ItemGroupRepeatKey: td.ItemGroupRepeatKey, ParentItemGroupRepeatKey: igd.ParentItemGroupRepeatKey });
                                    }

                                }

                            }
                        })
                    })
                })
                setItemGroupDatas(updatedItemGrpData);
                let itGrp = {
                    "ItemGroup": updatedItemGrpData,
                    "TradeOID": OID
                }
                OnItemGrpDataChange(itGrp);
            }
        }
    }

    function showGroup(status: any, cOID: any, OID: any, itemDataValue: any, IsRepeat: boolean, itemGrpIndex: any) {
        if (status) {
            formDefs?.map((fd) => {
                fd?.ItemGroupRef?.map((ir, index) => {
                    if (ir.CollectionExceptionConditionOID === cOID) {
                        let item = {
                            "indexes": index,
                            "status": status,
                            "GroupOID": ir.ItemGroupOID,
                            "TradeOID": OID,
                            "RepeatItemData": itemDataValue,
                            "IsRepeat": IsRepeat
                        }
                        showGroupQus(item);
                    } else if (ir.MethodOID === cOID) {
                        let item = {
                            "indexes": itemGrpIndex,
                            "status": status,
                            "GroupOID": ir.ItemGroupOID,
                            "TradeOID": OID,
                            "RepeatItemData": itemDataValue,
                            "IsRepeat": IsRepeat
                        }
                        showGroupQus(item);
                    }
                });
            })
        }
        else {
            formDefs?.map((fd) => {
                fd?.ItemGroupRef?.map((ir, index) => {
                    if (ir.CollectionExceptionConditionOID === cOID) {
                        let item = {
                            "indexes": index,
                            "status": status,
                            "GroupOID": ir.ItemGroupOID,
                            "TradeOID": OID,
                            "RepeatItemData": itemDataValue,
                            "IsRepeat": IsRepeat
                        }

                        if (Array.isArray(methodDefs)) {
                            methodDefs.map((res) => {
                                const expressionText = res?.FormalExpression?.text;
                                if (expressionText && expressionText.includes(':')) {
                                    const [itemOID] = expressionText
                                        .replace(/[!()]/g, '')
                                        .split(':')
                                        .map(str => str.trim());

                                    let itemGrpDef = itemGroupDefs.find((obj) => obj.OID === ir.ItemGroupOID);
                                    let itemIndex = itemGrpDef?.ItemRef.findIndex((obj) => obj.ItemOID === itemOID);
                                    //to check whether item of methodOID is present in the removed itemGroup...
                                    if ((itemIndex !== -1 && itemIndex !== undefined) || null) {
                                        //if Yes...
                                        let itemGrpRef = fd?.ItemGroupRef?.find((x) => x.MethodOID === res.OID);
                                        if ((itemGrpRef !== null) || undefined) {
                                            let hideItemGrp = {
                                                "indexes": index,
                                                "status": status,
                                                "GroupOID": itemGrpRef?.ItemGroupOID,
                                                "TradeOID": OID,
                                                "RepeatItemData": itemDataValue,
                                                "IsRepeat": IsRepeat
                                            }
                                            showGroupQus(hideItemGrp);
                                        }
                                    }
                                }
                            })
                        }

                        showGroupQus(item);
                    } else if (ir.MethodOID === cOID) {
                        let item = {
                            "indexes": index,
                            "status": status,
                            "GroupOID": ir.ItemGroupOID,
                            "TradeOID": OID,
                            "RepeatItemData": itemDataValue,
                            "IsRepeat": IsRepeat
                        }
                        showGroupQus(item);
                    }
                });
            })
        }
    }

    //To show question based on condition
    function showQuestion(status: any, itemOID: any, OID: any, itemDataValue: any, IsRepeat: boolean, itemGrpIndex: number) {
        if (status) {
            itemGroupDefs?.map((igd) => {
                igd?.ItemRef?.map((ir, index) => {
                    showGroup(status, itemOID, OID, itemDataValue, IsRepeat, itemGrpIndex);
                    if (ir.CollectionExceptionConditionOID === itemOID) {
                        let item = {
                            "id": ir.ItemOID,
                            "indexes": index,
                            "status": status,
                            "GroupOID": igd.OID,
                            "TradeOID": OID,
                            "ItemGrpIndex": itemGrpIndex
                        }
                        show(item);
                    }
                })
            })
        } else {
            itemGroupDefs?.map((igd) => {
                igd?.ItemRef?.map((ir, index) => {
                    //no need to be inside itemRef for loop (showGroup)
                    showGroup(status, itemOID, OID, itemDataValue, IsRepeat, itemGrpIndex);
                    if (ir.CollectionExceptionConditionOID === itemOID) {
                        let item = {
                            "id": ir.ItemOID,
                            "indexes": index,
                            "status": status,
                            "GroupOID": igd.OID,
                            "TradeOID": OID,
                            "ItemGrpIndex": itemGrpIndex
                        }
                        show(item);
                        resetItemData(item)
                        conditionDefs?.map((cd: any, indexes: any) => {
                            let id = cd.FormalExpression.text.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                            if (ir.ItemOID === id) {
                                itemGroupDefs?.map((igd) => {
                                    igd?.ItemRef?.map((irf, abc) => {
                                        if (irf.CollectionExceptionConditionOID === cd.OID) {
                                            let item = {
                                                "id": irf.ItemOID,
                                                "indexes": abc,
                                                "status": status,
                                                "GroupOID": igd.OID,
                                                "TradeOID": OID,
                                                "ItemGrpIndex": itemGrpIndex
                                            }
                                            show(item);
                                            resetItemData(item)
                                            conditionDefs?.map((cdn: any, indexes: any) => {
                                                let Itid = cdn.FormalExpression.text.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                                if (irf.ItemOID === Itid) {
                                                    itemGroupDefs?.map((igd) => {
                                                        igd?.ItemRef?.map((itrf, xyz) => {
                                                            if (itrf.CollectionExceptionConditionOID === cdn.OID) {
                                                                let item = {
                                                                    "id": itrf.ItemOID,
                                                                    "indexes": xyz,
                                                                    "status": status,
                                                                    "TradeOID": OID,
                                                                    "ItemGrpIndex": itemGrpIndex
                                                                }
                                                                show(item);
                                                                resetItemData(itrf.ItemOID)
                                                                conditionDefs?.map((cnd: any, indexes: any) => {
                                                                    let Itrfid = cnd.FormalExpression.text.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                                                    if (itrf.ItemOID === Itrfid) {
                                                                        itemGroupDefs?.map((igd) => {
                                                                            igd?.ItemRef?.map((itref, cdf) => {
                                                                                if (itref.CollectionExceptionConditionOID === cnd.OID) {
                                                                                    let item = {
                                                                                        "id": itref.ItemOID,
                                                                                        "indexes": cdf,
                                                                                        "status": status,
                                                                                        "TradeOID": OID,
                                                                                        "ItemGrpIndex": itemGrpIndex
                                                                                    }
                                                                                    show(item);
                                                                                    resetItemData(itref.ItemOID)
                                                                                }
                                                                            })
                                                                        })
                                                                    }
                                                                })
                                                            }
                                                        })
                                                    })
                                                }
                                            })
                                        }
                                    })
                                })
                            }
                        })
                    }
                })
            })
        }
    }

    function resetItemData(item: any) {

        //  let item = {
        // "id": ir.ItemOID,
        // "indexes": index,
        // "status": status,
        // "TradeOID": OID,
        // "ItemGrpIndex": itemGrpIndex
        // }
        itemData?.map((i: any) => {
            if (i.ItemOID === item.id && i.ItemGroupRepeatKey === item.ItemGrpIndex) {
                i.CodeList?.map((c: any) => {
                    c.CodeListItem.map((cli: any) => {
                        cli.IsEnabled = false
                    })
                })
            }
        })
    }

    //For repeat item
    function handleRepeat(itemGroup: any, indexes: any) {
        if (itemGroup !== null && itemGroup !== undefined) {
            showRepeat(itemGroupDefs);
        }
    }

    //For delete item in Repeat
    // handleDelete function removed - not used

    return (
        <>
            <Grid container rowSpacing={1} columnSpacing={{ xs: 1, sm: 2, md: 3 }}>
                <Grid item xs={12} sm={10}>
                    <Box>
                        {itemGroupDefs.map((itemGroup, indexes) => {

                            let showItemGroup = true;

                            //Below conditions may be moved to QuestionRender page for code improvement and can be made as reusable functions.
                            //Render the item based on the conditional OID 
                            let iGrpReferen = formDefs[0].ItemGroupRef.find(x => x.ItemGroupOID === itemGroup.OID);
                            if (iGrpReferen !== null && iGrpReferen !== undefined && iGrpReferen.CollectionExceptionConditionOID !== null && iGrpReferen.CollectionExceptionConditionOID !== undefined) {
                                let itemGrpConDef = conditionDefs?.find((cd) => cd.OID === iGrpReferen?.CollectionExceptionConditionOID);
                                if (itemGrpConDef) {
                                    let cdnItemOID = itemGrpConDef.FormalExpression.text.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                    let cdnValue = itemGrpConDef.FormalExpression.text.split("==")[1].replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');

                                    //  let itemGrpData = itemGroupDatas?.find((igd) => igd?.ItemData?.find((id) => id?.ItemOID === cdnItemOID) && igd?.ItemGroupRepeatKey === itemGroup.RepeatKey);
                                    let itemGrpData = itemGroupDatas?.find((igd) => igd?.ItemData?.find((id) => id?.ItemOID === cdnItemOID));
                                    if (itemGrpData !== null && itemGrpData !== undefined) {
                                        let iData = itemGrpData?.ItemData?.find((id) => id?.ItemOID === cdnItemOID);
                                        // current these conditional defs are linked through only singleselect and multiselect.
                                        // So I'm checking with codelist. Later on these needed to be verified with other item types as well.
                                        if (iData != null && iData !== undefined) {
                                            if (iData.CodeList !== null && iData.CodeList !== undefined && iData.CodeList.length > 0) {
                                                if (iData && iData.CodeList) {
                                                    iData.CodeList.forEach((cd: any) => {
                                                        let codeListItem = cd.CodeListItem.find((c: any) => c.CodedValue === cdnValue);
                                                        if (!codeListItem.IsEnabled) {
                                                            showItemGroup = false;
                                                        }
                                                    });
                                                }
                                            } else if (iData.Value !== null && iData.Value !== undefined && iData.Value !== cdnValue) {
                                                showItemGroup = false;
                                            }
                                        } else {
                                            showItemGroup = false;
                                        }
                                    } else {
                                        showItemGroup = false;
                                    }
                                }
                            } else if (iGrpReferen !== null && iGrpReferen !== undefined && iGrpReferen.MethodOID !== null && iGrpReferen.MethodOID !== undefined) {
                                let itemGrpMethodDef = methodDefs?.find((md) => md.OID === iGrpReferen?.MethodOID);
                                if (itemGrpMethodDef) {
                                    const expressionText = itemGrpMethodDef?.FormalExpression?.text;
                                    if (expressionText && expressionText.includes(':')) {
                                        const [mdItemOID] = expressionText
                                            .replace(/[!()]/g, '')
                                            .split(':')
                                            .map(str => str.trim());

                                        let itemGrpData = itemGroupDatas?.find((igd) => igd?.ItemData?.find((id) => id?.ItemOID === mdItemOID));
                                        if (itemGrpData !== null && itemGrpData !== undefined) {
                                            let iData = itemGrpData?.ItemData?.find((id) => id?.ItemOID === mdItemOID);
                                            // current methodDefs are linked through only interger datatype. 
                                            // So I'm checking with value. Later on these needed to be verified with other item types as well.
                                            if ((iData === null || iData === undefined) ||
                                                (iData !== null && iData !== undefined &&
                                                    (iData?.Value === null || iData.Value === undefined || iData.Value === ""))) {
                                                showItemGroup = false;
                                            }
                                        } else {
                                            showItemGroup = false;
                                        }
                                    }
                                }
                            }


                            let itgrpDt: any;
                            if (itemGroup?.Repeating === 'Yes') {
                                if (!repeatCount[itemGroup.OID]) {
                                    repeatCount[itemGroup.OID] = 1;
                                } else {
                                    repeatCount[itemGroup.OID]++;
                                }
                                itgrpDt = itemGroupDatas?.filter((igd) => igd.ItemGroupOID === itemGroup.OID && igd.ItemGroupRepeatKey === repeatCount[itemGroup.OID]);
                            }
                            else {
                                itgrpDt = itemGroupDatas?.filter((igd) => igd.ItemGroupOID === itemGroup.OID);
                            }

                            return (
                                <React.Fragment key={`${itemGroup.OID}-${repeatCount[itemGroup.OID]}`}>
                                    {showItemGroup ?
                                        <Card className='POCard' variant="outlined" sx={{ p: 3, mt: 2 }}>
                                            <Stack direction='row'>
                                                <Grid item xs={6}>
                                                    <Typography sx={{ fontWeight: "bold" }}>{itemGroup?.Description?.TranslatedText?.text}
                                                        &nbsp;{itemGroup?.Repeating === 'Yes' ? repeatCount[itemGroup.OID] : null}
                                                    </Typography>
                                                </Grid>
                                            </Stack>
                                            {itemGroup?.ItemRef?.map((ir, index) => {
                                                let item: any;

                                                if (itemGroup?.Repeating === "Yes") {
                                                    item = itemDefs?.find((id: any) => id?.OID === ir?.ItemOID);
                                                } else {
                                                    item = itemDefs?.find((id: any) => id?.OID === ir?.ItemOID);
                                                }

                                                let measureUnit = measureDef?.MeasurementUnit?.find((mu) => mu?.OID === item?.MeasurementOid);
                                                //let itemData = itgrpDt?.map((igd) => igd?.ItemData?.find((id) => id?.ItemOID === item?.OID));
                                                let itemData = itgrpDt?.map((igd) => igd?.ItemData?.find((id) => id?.ItemOID === item?.OID)).filter(Boolean);
                                                let itdt = validate?.find((v) => v?.OID === item?.OID);

                                                let codeList: any;
                                                if (itemGroup?.Repeating === "Yes") {
                                                    let xcodeList = codeListDefs?.find((codeList) => codeList?.OID === item?.CodeListRef?.CodeListOID);
                                                    codeList = [xcodeList];
                                                } else {
                                                    codeList = codeListDefs?.filter((codeList) => codeList?.OID === item?.CodeListRef?.CodeListOID);
                                                }

                                                //Render the item based on the conditional OID 

                                                let showItem = true;

                                                if (ir.CollectionExceptionConditionOID !== null && ir.CollectionExceptionConditionOID !== undefined) {
                                                    let itemConDef = conditionDefs?.find((cd) => cd.OID === ir.CollectionExceptionConditionOID);
                                                    if (itemConDef) {
                                                        let cdf = itemConDef?.FormalExpression?.text.includes("||");

                                                        if (cdf === true) {
                                                            let cdd = itemConDef?.FormalExpression?.text.split("||");
                                                            for (let i = 0; i < cdd.length; i++) {
                                                                let cdnItemOID = cdd[i].split("==")[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                                                let cdnValue = cdd[i].split("==")[1]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                                                let itemDataArr = itgrpDt?.map((igd) => igd?.ItemData?.find((id) => id?.ItemOID === cdnItemOID));
                                                                if (itemDataArr && itemDataArr.length > 0) {
                                                                    let iData = itemDataArr[0];
                                                                    if (iData != null && iData !== undefined) {
                                                                        if (iData.CodeList !== null && iData.CodeList !== undefined && iData.CodeList.length > 0) {
                                                                            if (iData && iData.CodeList) {
                                                                                let codeListItems = iData.CodeList[0].CodeListItem;
                                                                                let isEnabled = codeListItems.find((c: any) => c.CodedValue === cdnValue && c.IsEnabled);
                                                                                if (isEnabled) {
                                                                                    showItem = true;
                                                                                    break;
                                                                                } else {
                                                                                    showItem = false;
                                                                                }
                                                                            }
                                                                        }
                                                                        else if (iData.Value !== null && iData.Value !== undefined && iData.Value === cdnValue) {
                                                                            showItem = false;
                                                                        }
                                                                    }
                                                                    else {
                                                                        showItem = false;
                                                                    }
                                                                } else {
                                                                    showItem = false;
                                                                }
                                                            }
                                                        }
                                                        else {
                                                            let cdnItemOID = itemConDef.FormalExpression.text.split("==")[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                                            let cdnValue = itemConDef.FormalExpression.text.split("==")[1]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                                            let itemDataArr = itgrpDt?.map((igd) => igd?.ItemData?.find((id) => id?.ItemOID === cdnItemOID));
                                                            if (itemDataArr && itemDataArr.length > 0) {
                                                                let iData = itemDataArr[0];
                                                                // itemDataArr is an array, so loop through it
                                                                if (iData != null && iData !== undefined) {
                                                                    if (iData.CodeList !== null && iData.CodeList !== undefined && iData.CodeList.length > 0) {
                                                                        if (iData && iData.CodeList) {
                                                                            iData.CodeList.forEach((cd: any) => {
                                                                                let codeListItem = cd.CodeListItem.find((c: any) => c.CodedValue === cdnValue);
                                                                                if (codeListItem !== null && codeListItem !== undefined && !codeListItem.IsEnabled) {
                                                                                    showItem = false;
                                                                                }
                                                                            });
                                                                        }
                                                                    }
                                                                    else if (iData.Value !== null && iData.Value !== undefined && iData.Value !== cdnValue) {
                                                                        showItem = false;
                                                                    }
                                                                }
                                                                else {
                                                                    showItem = false;
                                                                }
                                                            } else {
                                                                showItem = false;
                                                            }
                                                        }
                                                    }
                                                }

                                                const metaKeys: string[] = [];

                                                if (
                                                    Array.isArray(mappings) &&
                                                    mappings.length > 0 &&
                                                    Array.isArray(mappings[0]?.Versions) &&
                                                    mappings[0].Versions.length > 0
                                                ) {
                                                    const version = mappings[0].Versions[0];
                                                    version?.ItemGroupDef?.forEach((group: any) => {
                                                        group?.ItemDef?.forEach((item: any) => {
                                                            if (item?.MetaKey) {
                                                                metaKeys.push(item.MetaKey);
                                                            }
                                                        });
                                                    });
                                                }

                                                // ✅ Check if current item's MetaKey exists in extracted metaKeys
                                                let matchedMapping: any = null;

                                                if (
                                                    Array.isArray(mappings) &&
                                                    mappings.length > 0 &&
                                                    Array.isArray(mappings[0]?.Versions) &&
                                                    mappings[0].Versions.length > 0 &&
                                                    Array.isArray(mappings[0].Versions[0]?.ItemGroupDef)
                                                ) {
                                                    mappings[0].Versions[0].ItemGroupDef.forEach((group: any) => {
                                                        group?.ItemDef?.forEach((def: any) => {
                                                            if (def?.MetaKey === item?.MetaKey) {
                                                                matchedMapping = def;
                                                            }
                                                        });
                                                    });
                                                }




                                                if (
                                                    Array.isArray(mappings) &&
                                                    mappings.length > 0
                                                ) {
                                                    const matchedItemDefs = mappings
                                                        .flatMap((mapping) =>
                                                            Array.isArray(mapping?.Versions)
                                                                ? mapping.Versions.flatMap((version) =>
                                                                    Array.isArray(version?.ItemGroupDef)
                                                                        ? version.ItemGroupDef.flatMap((group) =>
                                                                            Array.isArray(group?.ItemDef)
                                                                                ? group.ItemDef.filter((def) => def?.MetaKey === item?.MetaKey)
                                                                                : []
                                                                        )
                                                                        : []
                                                                )
                                                                : []
                                                        )
                                                        .filter(Boolean);


                                                    // Optional: log all MetaKeys (for debugging)
                                                    const allMetaKeys = mappings
                                                        .flatMap((mapping) =>
                                                            Array.isArray(mapping?.Versions)
                                                                ? mapping.Versions.flatMap((version) =>
                                                                    Array.isArray(version?.ItemGroupDef)
                                                                        ? version.ItemGroupDef.flatMap((group) =>
                                                                            Array.isArray(group?.ItemDef)
                                                                                ? group.ItemDef.map((def) => def?.MetaKey)
                                                                                : []
                                                                        )
                                                                        : []
                                                                )
                                                                : []
                                                        )
                                                        .filter(Boolean);

                                                }
                                                let condition = conditionDefs?.filter((cd) => {
                                                    let cdn = cd.FormalExpression.text.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{ }! ]/g, '');
                                                    if (cdn === item?.OID)
                                                        return cdn
                                                });

                                                return (
                                                    <>
                                                        {showItem ?
                                                            (() => {
                                                                const isBidDetailsPage = window.location.pathname === '/soldproduction/projectinformation';
                                                                const typographySx = { mt: 2 };

                                                                return ir.Mandatory === 'Yes' && item !== undefined
                                                                    ? <Typography sx={typographySx}>{item?.Question?.TranslatedText?.text} <span style={{ color: 'red' }}>*</span></Typography>
                                                                    : <Typography sx={typographySx}>{item?.Question?.TranslatedText?.text}</Typography>
                                                            })()
                                                            : null}

                                                        {item?.DataType === 'text' && item !== undefined && showItem ?
                                                            <FreeText itemDef={item} measureDef={measureUnit} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} readonly={readonly} /> : null}

                                                        {item?.DataType === 'integer' && item !== undefined && showItem ?
                                                            <IntegerText OID={OID} itemDef={item} measureDef={measureUnit} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} showStatus={showQuestion}
                                                                isRepeatItemGrp={itemGroup?.Repeating} itgrpInd={repeatCount[itemGroup.OID]} itemGrp={itemGroup} callRepeat={handleRepeat} methodDefs={methodDefs} mapping={matchedMapping} tradeData={tradeData} readonly={readonly} /> : null}

                                                        {item?.DataType === 'float' && item !== undefined && showItem ?
                                                            <FloatText itemDef={item} measureDef={measureUnit} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} readonly={readonly} /> : null}

                                                        {item?.DataType === 'multilinetext' && item !== undefined && showItem ?
                                                            <MultilineText itemDef={item} measureDef={measureUnit} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} readonly={readonly} /> : null}

                                                        {item?.DataType === 'singleselect' && item !== undefined && showItem ?
                                                            <SingleSelect OID={OID} itemDef={item} conditionDef={condition} codeList={codeList}
                                                                onItemchange={OnItemDataChange} showStatus={showQuestion} itemDatas={itemData} error={itdt} index={index} isRepeatItemGrp={itemGroup?.Repeating} itgrpInd={repeatCount[itemGroup.OID]} methodDefs={methodDefs} readonly={readonly} /> : null}

                                                        {item?.DataType === 'multiselect' && item !== undefined && showItem ?
                                                            <MultiChoice OID={OID} itemDef={item} conditionDef={condition} codeList={codeList}
                                                                onItemchange={OnItemDataChange} showStatus={showQuestion} itemDatas={itemData} error={itdt} index={index} isRepeatItemGrp={itemGroup?.Repeating} itgrpInd={repeatCount[itemGroup.OID]} methodDefs={methodDefs} readonly={readonly} /> : null}

                                                        {item?.DataType === 'date' && item !== undefined && showItem ?
                                                            <DateSelector itemDef={item} conditionDef={condition} codeList={codeList} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} readonly={readonly} /> : null}

                                                        {item?.DataType === 'datetime' && item !== undefined && showItem ?
                                                            <DateTime itemDef={item} conditionDef={condition} codeList={codeList} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} readonly={readonly} /> : null}

                                                        {item?.DataType === 'combobox' && item !== undefined && showItem ?
                                                            <Combo itemDef={item} conditionDef={condition} codeList={codeList} onItemchange={OnItemDataChange} showStatus={showQuestion} itemDatas={itemData} error={itdt} readonly={readonly} /> : null}

                                                        {item?.DataType === 'boolean' && item !== undefined && showItem ?
                                                            <RadioType OID={OID} itemDef={item} conditionDef={condition} codeList={codeList} onItemchange={OnItemDataChange}
                                                                showStatus={showQuestion} itemDatas={itemData} error={itdt} index={index} isRepeatItemGrp={itemGroup?.Repeating} itgrpInd={repeatCount[itemGroup.OID]} methodDefs={methodDefs} readonly={readonly} /> : null}
                                                    </>
                                                )
                                            })}
                                        </Card> : null}
                                </React.Fragment>
                            )
                        })}
                    </Box>
                </Grid>
            </Grid>
        </>
    )
}

export default QuestionRendererCard;
