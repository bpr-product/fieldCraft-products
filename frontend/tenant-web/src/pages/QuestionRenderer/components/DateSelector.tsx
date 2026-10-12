/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 2-Nov-2024
* @author      :
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Date Selector Input Component
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 21928  | 2026-06-24 | Karthika S | Fix stale validation errors persisting after field is filled in Boolean/Q&A renderer
*/

import { LocalizationProvider, DatePicker } from '@mui/x-date-pickers';
import { AdapterDayjs } from '@mui/x-date-pickers/AdapterDayjs';
import { DemoContainer } from '@mui/x-date-pickers/internals/demo';
import { ItemData } from '../../../interface/SdmInterfaces';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import dayjs from 'dayjs';


const DateSelector: React.FC<any> = ({ itemDef, onItemchange, itemDatas, error, readonly }) => {
    const { t } = useTranslation();


    const [itemData, setItemData] = useState<ItemData>();

    const handleChange = (event: any) => {
        let tempItemData: ItemData = { ItemOID: itemDef.OID, Value: dayjs(event.$d).format('YYYY-MM-DD') + "T00:00:00.000Z", CodeList: [], MetaKey: itemDef.MetaKey, ItemGroupRepeatKey:0 };
        setItemData(tempItemData);
        onItemchange(tempItemData);
    }

    return (
        <>
            <LocalizationProvider dateAdapter={AdapterDayjs}>
                <DemoContainer components={['DatePicker']} >
                    <DatePicker label={t('common.date')}
                        disabled={readonly}
                        slotProps={{ textField: { fullWidth: true, size: 'small', helperText: (error?.OID === itemDef.OID && !itemData?.Value && !itemDatas?.[0]?.Value) ? t('validation.fillThisField') : '' } }}
                        value={(itemDef?.OID === itemDatas[0]?.ItemOID) ? dayjs(itemDatas[0]?.Value) : itemData?.Value !== undefined ? dayjs(itemData?.Value): null}
                        onChange={handleChange}
                    />
                </DemoContainer>
            </LocalizationProvider>
        </>
    )
}

export default DateSelector;
