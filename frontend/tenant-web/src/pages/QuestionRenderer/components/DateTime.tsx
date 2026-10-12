import { LocalizationProvider, DateTimePicker } from '@mui/x-date-pickers';
import { AdapterDayjs } from '@mui/x-date-pickers/AdapterDayjs';
import { DemoContainer } from '@mui/x-date-pickers/internals/demo';
import { useTranslation } from 'react-i18next';
import {  ItemData } from '../../../interface/SdmInterfaces';
import dayjs from 'dayjs';

const DateTime: React.FC<any> = ({ itemDef, conditionDef, codeList, onItemchange, itemDatas, readonly }) => {
    const { t } = useTranslation();

    const handleChange = (event: any) => {
        let tempItemData: ItemData = { ItemOID: itemDef.OID, Value: dayjs(event.$d).format('YYYY-MM-DD') + "T00:00:00.000Z", CodeList: [], MetaKey: itemDef.MetaKey, ItemGroupRepeatKey:0 };
        onItemchange(tempItemData);
    }

    return (
        <>
            <LocalizationProvider dateAdapter={AdapterDayjs}>
                <DemoContainer components={['DatePicker']} >
                    <DateTimePicker label={t('common.date')} disabled={readonly} slotProps={{ textField: { fullWidth: true, size: 'small' } }} onChange={handleChange} />
                    {/* <DateRangePicker label="Date Range" slotProps={{ textField: { fullWidth: true, size: 'small' } }} onChange={handleChange} /> */}
                </DemoContainer>
            </LocalizationProvider>
        </>
    )
}

export default DateTime;
