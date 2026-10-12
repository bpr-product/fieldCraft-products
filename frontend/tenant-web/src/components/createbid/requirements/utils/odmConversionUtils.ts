/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : ODM Conversion Utilities - Converts ItemGroupData to ODM format for backend submission
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

/**
 * ODM Conversion Utilities
 * Converts ItemGroupData array to proper ODM (Operational Data Model) format
 * for backend submission
 */

export interface ItemData {
    ItemOID: string;
    Value: string | number;
    CodeList?: any[];
    MetaKey?: string;
    ItemGroupRepeatKey: number;
    ItemGroupOID?: string;
    DataType?: string;
    isDeleted?: boolean | string;
}

export function isDeletedFlag(value: any): boolean {
  return value === true || value === 'true';
}

export interface ItemGroupData {
    ItemData: ItemData[];
    ItemGroupOID: string;
    ItemGroupRepeatKey: number;
    ParentItemGroupRepeatKey?: number;
}

export interface ODMStructure {
    EventData: Array<{
        FormData: Array<{
            ItemGroupData: ItemGroupData[];
            FormOID: string;
            TransactionType: string;
        }>;
        EventOID: string;
    }>;
    OID: string;
    Key: string;
    MetaDataVersionOID?: string;
}

export function convertItemGroupDataToODM(
    itemGroupData: ItemGroupData[],
    projectOID: string,
    metaDataVersionOID?: string
): string {
    
    // Log input groups with ParentItemGroupRepeatKey
    const inputNestedGroups = itemGroupData.filter(g => g.ParentItemGroupRepeatKey !== undefined);
    if (inputNestedGroups.length === 0) {
        // console.warn('[ODM_INPUT] NO groups with ParentItemGroupRepeatKey received!');
    }
    
    if (!itemGroupData || itemGroupData.length === 0) {
        return '';
    }  
    const cleanedItemGroupData = itemGroupData.map((group, index) => {        const cleanedItemData = group.ItemData
            .filter(item => {
                // Keep items that have non-empty values
                const hasValue = item.Value !== '' && item.Value !== null && item.Value !== undefined;
                // OR keep items that have CodeList with at least one IsEnabled=true
                const hasCodeListSelection = item.CodeList && item.CodeList.length > 0 && 
                    item.CodeList.some((cl: any) => 
                        cl.CodeListItem && cl.CodeListItem.some((cli: any) => cli.IsEnabled === true)
                    );
                
                const hasDeletedFlag = item.isDeleted === true || item.isDeleted === 'true';
                const shouldKeep = hasValue || hasCodeListSelection || hasDeletedFlag;
                
                return shouldKeep;
            })
            .map(item => {
                // Use type assertion to handle fields that shouldn't be there but might exist
                const itemWithExtraFields = item as any;
                const { ItemGroupOID, ParentItemGroupRepeatKey, _parentItemGroupRepeatKey, ...cleanItem } = itemWithExtraFields;
                
                // Log if we're removing ParentItemGroupRepeatKey from ItemData (which is correct)
                if (ParentItemGroupRepeatKey !== undefined || _parentItemGroupRepeatKey !== undefined) {
                }
                
                if (!cleanItem.MetaKey || cleanItem.MetaKey === '') {
                    delete cleanItem.MetaKey;
                }
                
                return cleanItem;
            });

        const cleanedGroup: any = {
            ...group,
            ItemData: cleanedItemData
        };
        
        // Log what we're keeping at the ItemGroupData level
        if (cleanedGroup.ParentItemGroupRepeatKey !== undefined) {
        }
        
        return cleanedGroup;
    });

const odmStructure: ODMStructure = {
  EventData: [
    {
      FormData: [
        {
          ItemGroupData: cleanedItemGroupData,
          FormOID: "F.1",
          TransactionType: "Insert"
        }
      ],
      EventOID: "E.1"
    }
  ],
  OID: projectOID,
  Key: "123",
  MetaDataVersionOID: metaDataVersionOID,
};

    const odmString = JSON.stringify(odmStructure);
    
    // Log specifically nested instances with ParentItemGroupRepeatKey
    const nestedGroups = cleanedItemGroupData.filter(g => g.ParentItemGroupRepeatKey !== undefined);
    if (nestedGroups.length === 0) {
        // no-op
    }

    return odmString;
}


export function validateODMStructure(odmString: string): { 
    isValid: boolean; 
    errors: string[] 
} {
    const errors: string[] = [];

    try {
        const odm = JSON.parse(odmString);

        // Check required top-level fields
        if (!odm.EventData) errors.push('Missing EventData');
        if (!odm.OID) errors.push('Missing OID');
        if (!odm.Key) errors.push('Missing Key');
        if (!odm.MetaDataVersionOID) errors.push('Missing MetaDataVersionOID');

        if (odm.EventData) {
            if (!Array.isArray(odm.EventData)) {
                errors.push('EventData must be an array');
            } else if (odm.EventData.length === 0) {
                errors.push('EventData array is empty');
            } else {
                const eventData = odm.EventData[0];
                
                if (!eventData.EventOID) errors.push('Missing EventOID');
                if (!eventData.FormData) errors.push('Missing FormData');
                
                if (eventData.FormData) {
                    if (!Array.isArray(eventData.FormData)) {
                        errors.push('FormData must be an array');
                    } else if (eventData.FormData.length === 0) {
                        errors.push('FormData array is empty');
                    } else {
                        const formData = eventData.FormData[0];
                        
                        if (!formData.FormOID) errors.push('Missing FormOID');
                        if (!formData.TransactionType) errors.push('Missing TransactionType');
                        if (!formData.ItemGroupData) errors.push('Missing ItemGroupData');
                        
                        if (formData.ItemGroupData && !Array.isArray(formData.ItemGroupData)) {
                            errors.push('ItemGroupData must be an array');
                        }
                    }
                }
            }
        }

        return {
            isValid: errors.length === 0,
            errors
        };
    } catch (error) {
        return {
            isValid: false,
            errors: [`Invalid JSON: ${error instanceof Error ? error.message : String(error)}`]
        };
    }
}
