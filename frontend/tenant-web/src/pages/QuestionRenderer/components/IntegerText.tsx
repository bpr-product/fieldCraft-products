import { TextField, Typography } from "@mui/material";
import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ItemData } from "../../../interface/SdmInterfaces";
import { Stack } from "@mui/system";

const IntegerText: React.FC<any> = ({ OID, itemDef, measureDef, onItemchange, itemDatas, error, itemGrp, itgrpInd, callRepeat, showStatus, isRepeatItemGrp, methodDefs, mapping, tradeData, readonly }) => {
  const { t } = useTranslation();


  const [itemData, setItemData] = useState<ItemData>((itemDef?.OID === itemDatas[0]?.ItemOID) ? itemDatas[0] : []);
  const [feet, setFeet] = useState<string>("");
  const [inches, setInches] = useState<string>("");

  const inputchange = (e: any) => {
    const Pattern = /^[0-9'"/.?*&%#@+= ]*$/;
    // ReDoS-safe regex for feet/inches
    const match = itemData.Value.match(/^(\d{1,3}(?:\.\d{1,3})?)?'?(\d{1,3}(?:\.\d{1,3})?)?"?$/);
    if (itemDef?.MetaKey === "zipCode") {
      let raw = e.target.value.replace(/\D/g, ""); // only digits
      if (raw.length > 6) raw = raw.slice(0, 6);   // max 6 digits
      e.target.value = raw;
      setItemData({ ...itemData, Value: raw });

      if (raw.length !== 6) return;
    }

    if (itemDatas.length > 0 && itemDatas[0] !== undefined) {
      setItemData({ ...itemDatas[0], Value: e.target.value });
      onItemchange({ ...itemDatas[0], Value: e.target.value });
    } else {
      let tempItemData: ItemData = { ItemOID: itemDef.OID, Value: e.target.value, CodeList: [], MetaKey: itemDef.MetaKey, ItemGroupRepeatKey: isRepeatItemGrp === 'Yes' ? itgrpInd : 0 };
      setItemData(tempItemData);
      onItemchange(tempItemData);
    }

    if (Array.isArray(methodDefs)) {
      methodDefs.map((res) => {
        if (res.OID.includes("M.")) {
          const expressionText = res?.FormalExpression?.text;
          if (expressionText && expressionText.includes(":")) {
            const [itemOID, type] = expressionText
              .replace(/[!()]/g, '')
              .split(':')
              .map(str => str.trim());
            if (type === 'RepeatCount' && itemOID === itemDef.OID) {
              if (e.target.value > 0) {

                showStatus(true, res.OID, OID, e.target.value, true, itgrpInd);

                // for (let i = 0; i < (e.target.value); i++) {
                //     showStatus(true, res.OID, OID,e.target.value)
                // }
              } else {
                showStatus(false, res.OID, OID, e.target.value, true, itgrpInd);
              }
            }
          }
        }
        return null;
      });
    }
  };

  // Auto-map from tradeData if present
  useEffect(() => {
    if (!mapping?.Name || !tradeData) return;
    const tradeKey = mapping.Name;
    const rawValue = tradeData[tradeKey] ?? tradeData.TotalMeasurements?.[tradeKey];
    if (rawValue !== undefined && rawValue !== null) {
      const parsed = parseFloat(rawValue);
      if (!isNaN(parsed)) {
        let tempItemData: ItemData = { ItemOID: itemDef.OID, Value: parsed.toString(), CodeList: [], MetaKey: itemDef.MetaKey, ItemGroupRepeatKey: 0 };

        setItemData(tempItemData);
        // setItemData({ ...itemDatas[0], Value: parsed });
      } else {
        console.warn(`Could not parse a number from:`, rawValue);
      }
    }
  }, [mapping, tradeData, itemDef.OID, itemDef.MetaKey]);

  // useEffect(() => {
  //   if (itemDatas?.length > 0 && itemDatas[0]?.ItemOID === itemDef?.OID) {
  //     setItemData(itemDatas[0]);
  //   }
  // }, [itemDatas, itemDef]);

  useEffect(() => {
    if (itemData?.Value) {
      // ReDoS-safe regex for feet/inches
      const match = itemData.Value.match(/^(\d{1,3}(?:\.\d{1,3})?)?'?(\d{1,3}(?:\.\d{1,3})?)?"?$/);
      if (match) {
        setFeet(match[1] || "");
        setInches(match[2] || "");

      }
    }
  }, [itemData]);

  const handleFeetChange = (f: string) => {
    setFeet(f);

    const combined = `${f}'${inches}"`;
    const updated: ItemData = {
      ...itemData,
      Value: combined,
      ItemOID: itemDef.OID,
      MetaKey: itemDef.MetaKey,
      ItemGroupRepeatKey: isRepeatItemGrp === "Yes" ? itgrpInd : 0,
      CodeList: itemData?.CodeList ?? []
    };
    setItemData(updated);
    onItemchange(updated);
  };

  const handleInchesChange = (i: string) => {
    setInches(i);
    const combined = `${feet}'${i}"`;
    const updated: ItemData = {
      ...itemData,
      Value: combined,
      ItemOID: itemDef.OID,
      MetaKey: itemDef.MetaKey,
      ItemGroupRepeatKey: isRepeatItemGrp === "Yes" ? itgrpInd : 0,
      CodeList: itemData?.CodeList ?? []
    };
    setItemData(updated);
    onItemchange(updated);
  };

  // return (
  //     <>
  //         <Stack direction="row" spacing={2}>
  //             <TextField
  //                 error={(error?.OID === itemDef.OID) ? true : false}
  //                 id="integer-text-textfield-name"
  //                 label=""
  //                 name="name"
  //                 size="small"
  //                 sx={{ m: 1 }}
  //                 type="text"
  //                 fullWidth
  //                 value={itemData?.Value}
  //                 inputProps={{ step: "1", min: 0 }}
  //                 helperText={(error?.OID === itemDef.OID) ? 'Please fill this field' : ''}
  //                 onChange={inputchange}
  //             />
  //             <Typography sx={{ textAlign: 'center' }}>{measureDef?.Symbol?.TranslatedText?.text}</Typography>
  //         </Stack>
  //     </>
  // )
  return (
    <Stack direction="row" spacing={2}>
      {itemDef?.MeasurementOid === "MU.32" ? (
        <>
          {/* Feet box */}
          <TextField
            error={error?.OID === itemDef.OID}
            id={`sdm-integer-feet-input-${itemDef?.OID}`}
            size="small"
            sx={{ m: 1 }}
            type="text"
            placeholder='Feet'
            value={feet}
            onChange={(e) => handleFeetChange(e.target.value)}
            disabled={readonly}
          />
          <Typography>ft</Typography>

          {/* Inches box */}
          <TextField
            error={error?.OID === itemDef.OID}
            id={`sdm-integer-inches-input-${itemDef?.OID}`}
            size="small"
            sx={{ m: 1 }}
            type="text"
            placeholder='Inches'
            value={inches}
            onChange={(e) => handleInchesChange(e.target.value)}
            disabled={readonly}
          />
          <Typography>in</Typography>
        </>
      ) : (
        <>
          {/* Normal single box */}
          <TextField
            error={error?.OID === itemDef.OID ? true : false}
            id={`sdm-integer-input--${itemDef?.OID}`}
            name="name"
            size="small"
            sx={{ m: 1 }}
            type="text"
            placeholder={t('sdm.enterNumber')}
            fullWidth
            value={itemData?.Value}
            inputProps={{ step: "0.01", min: 0 }}
            helperText={
              error?.OID === itemDef.OID ? t('validation.fillThisField') : ""
            }
            onChange={inputchange}
            disabled={readonly}
          />
          <Typography sx={{ textAlign: "center" }}>
            {measureDef?.Symbol?.TranslatedText?.text}
          </Typography>
        </>
      )}
    </Stack>
  );
};

export default IntegerText;
