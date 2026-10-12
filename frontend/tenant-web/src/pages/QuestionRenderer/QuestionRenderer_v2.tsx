/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 2-Nov-2024
* @author      : Vaisnave I M
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Question Renderer Component
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* AB#30581       | 10-09-2026  | Mohamed Riyaz A | shouldDisableAllAssetFields asked
*                                                 whether this is the Bid Details page by
*                                                 comparing against /soldproduction/
*                                                 projectinformation alone, so the
*                                                 Contract-Approval field lock never
*                                                 applied on the same page's
*                                                 /salesestimate route. Now
*                                                 isBidDetailsRoute.
* AB#31383       | 28-09-2026  | Vaisnave        | Added a "Select All" toggle on multi-select
*                                                 questions, gated behind the new isAssetForm
*                                                 prop (Asset forms only, not Create Bid's own
*                                                 questionnaires) — avoids hardcoding this to one
*                                                 named question (e.g. Accessibility). Also: a
*                                                 literal "All" answer choice already present in
*                                                 a trade template's own CodeList now acts as a
*                                                 shortcut that selects every other option on
*                                                 Asset forms; "All" itself is never saved.
* AB#31383       | 29-09-2026  | Vaisnave        | Removed the separate "Select All" chip — the
*                                                 literal "All" answer-choice shortcut above is
*                                                 enough; isAssetForm now only gates that.
*/


import React, { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import { isBidDetailsRoute } from '../../utils/bidDetailsRoute';
import { Box, Card, Grid, Tab, Tabs, Typography, Dialog, DialogContent, IconButton } from '@mui/material';
import QuestionRendererCard from './components/QuestionRendererCard';
import ImageZoomDialog from './components/ImageZoomDialog';
import { CodeList, EventData, ItemDef, FormData, ItemGroupDef, SDM, BuildingData, ItemGroupData, ItemData, FormDef, EventDef, TemplateVersion, BasicDefinitions, Mappings, MethodDef, ItemGroupRef } from '../../interface/SdmInterfaces';
import { useLocation } from "react-router-dom";
import { getselectedTemplatesByVersion, getTemplatesByTradeName } from '../../services/TradeTemplateService';
import { useOidcAccessToken } from '@axa-fr/react-oidc';
import { forwardRef, useImperativeHandle } from 'react';
import { getProjectById } from '../../services/ProjectService';
import { getAssetsByAssetUid, getTradeDetails } from '../../services/BidService';
import {
    getActiveMetaDataVersionOID,
    getMergedTemplateVersionFromSdm,
} from '../../utils/sdmMergeUtils';
import { useHandleUnauthorized } from '../../hooks/UserSession';
import { getRequest } from '../../services/MeasurementService';
import { FEATURE_FLAGS } from '../../constants/FeatureFlag';
import { getImageUrl } from '../../utils/imageRenderer';
import './QuestionRenderer_v2.scss';

const getMergedTv = (sdmDoc: any) => getMergedTemplateVersionFromSdm(sdmDoc);

interface TabPanelProps {
    children?: React.ReactNode;
    index: number;
    value: number;
}

function CustomTabPanel(props: TabPanelProps) {
    const { children, value, index, ...other } = props;
    return (
        <div
            role="tabpanel"
            hidden={value !== index}
            id={`simple-tabpanel-${index}`}
            aria-labelledby={`simple-tab-${index}`}
            {...other}
        >
            {value === index && (
                <Box sx={{ p: 3 }}>
                    <Typography>{children}</Typography>
                </Box>
            )}
        </div>
    );
}

function a11yProps(index: number) {
    return {
        id: `simple-tab-${index}`,
        'aria-controls': `simple-tabpanel-${index}`,
    };
}

interface QuestionRendererProps {
    data?: any;
    state?: any;
    onSave?: (assetData?: any, tradeData?: any, tradeType?: any, tradeVersion?: any, assetVersion?: any, assetOID?: any, projectOID?: any) => void;
    projectId?: any;

    // New props for shared renderer mode (passed from parent)
    itemGroupDefs?: ItemGroupDef[];
    itemDefs?: ItemDef[];
    codeListDefs?: CodeList[];
    conditionDefs?: any[];
    methodDefs?: MethodDef[];
    measureDef?: BasicDefinitions;
    imageBaseUrl?: string; 
    activeStep?: number;
    existingData?: any;
    onDataChange?: (data: any) => void;
    /** AB#31383 — on a multi-select question in the ASSET questionnaire
     *  (Property/Building/Accessibility/Equipment/Logistics), treats a literal "All" answer
     *  choice already present in the question's own data as a select-the-rest shortcut, never
     *  itself a saved value — CreateAsset_v2/_v3.tsx pass this for their own standalone pages, and
     *  Create Bid's CreateBidForm_v2/_v3.tsx pass it too for their "Asset Information" step, since
     *  both render the SAME assetItemGroupDefs/assetItemDefs question set. Never passed for
     *  Create Bid's own trade-specific questionnaires elsewhere in the wizard. A per-question
     *  special case here would hardcode a business decision this repo's rules disallow, so the
     *  gate is which questions these are, not a question OID. */
    isAssetForm?: boolean;
}

const QuestionRenderer_v2 = forwardRef<unknown, QuestionRendererProps>(
    ({
        onSave,
        projectId,
        data,
        state,
        // Shared renderer mode props
        itemGroupDefs: propsItemGroupDefs,
        itemDefs: propsItemDefs,
        codeListDefs: propsCodeListDefs,
        conditionDefs: propsConditionDefs,
        methodDefs: propsMethodDefs,
        measureDef: propsMeasureDef,
        imageBaseUrl: propsImageBaseUrl,
        activeStep: propsActiveStep,
        existingData: propsExistingData,
        onDataChange: propsOnDataChange,
        isAssetForm
    }, ref) => {
        // Detect if component is being used in "shared renderer mode" (parent passes template data)
        const isSharedMode = propsItemGroupDefs !== undefined && propsItemDefs !== undefined;

        const [tabValue, setTabValue] = useState(0);
        const [template, setTemplate] = useState<TemplateVersion[]>();
        const [formDef, setFormDef] = useState<FormDef[]>([]);
        const [eventDef, setEventDef] = useState<EventDef[]>();
        const [itemGroup, setItemGroup] = useState<ItemGroupDef[]>([]);
        const [itemDef, setItemDef] = useState<ItemDef[]>([]);
        const [mappings, setMappings] = useState<Mappings[]>([]);
        const [codeList, setCodeList] = useState<CodeList[]>([]);
        const [response, setResponse] = useState<SDM | any>();
        const [projectOID, setProjectOID] = useState<string>("");
        const [measureUnit, setMeasureUnit] = useState<BasicDefinitions | any>();
        const [methodDef, setMethodDef] = useState<MethodDef[]>([]);

        const [assetTemplate, setAssetTemplate] = useState<TemplateVersion[]>();
        const [assetresponse, setAssetResponse] = useState<SDM | any>();
        const [assetformDef, setAssetFormDef] = useState<FormDef[]>([]);
        const [asseteventDef, setAssetEventDef] = useState<EventDef[]>();
        const [assetitemGroup, setAssetItemGroup] = useState<ItemGroupDef[]>([]);
        const [assetitemDef, setAssetItemDef] = useState<ItemDef[]>([]);
        const [assetcodeList, setAssetCodeList] = useState<CodeList[]>([]);
        const [assetconditionDef, setAssetConditionDef] = useState<any>();
        const [assetOID, setAssetOID] = useState<any>("");
        const [assetData, setAssetData] = useState<BuildingData>();
        const [assetMethodDef, setAssetMethodDef] = useState<MethodDef[]>([]);
        const [assetFormData, setAssetFormData] = useState<FormData[]>([]);
        const [assetEventData, setAssetEventData] = useState<EventData[]>([]);

        const [formData, setFormData] = useState<FormData[]>([]);
        const [eventData, setEventData] = useState<EventData[]>([]);
        const [projectData, setProjectData] = useState<BuildingData>();
        const [conditionDef, setConditionDef] = useState<any>();
        const [itemGroupData, setItemGroupData] = useState<ItemGroupData[]>([]);
        const [itemAssetGroupData, setItemAssetGroupData] = useState<ItemGroupData[]>([]);
        const trade = useRef<any>(null);
        const [tradeType, setTradeType] = useState<any>();
        const [tradeVersion, setTradeVersion] = useState<any>();
        const [assetVersion, setAssetVersion] = useState<any>();
        const [templateVersion, setTemplateVersion] = useState<any>();
        const [aasettemplateVersion, setAssetTemplateVersion] = useState<any>();

        // Loading state to track initial data fetch
        const [isInitialLoading, setIsInitialLoading] = useState(true);

        // Track if trade data has been loaded to prevent eager loading
        const [isTradeDataLoaded, setIsTradeDataLoaded] = useState(false);
        const tradeAnswerDataRef = useRef<any>(null);

        const [hide, setHide] = useState(false);
        const { accessToken } = useOidcAccessToken();
        const location = useLocation();
        const [itemList, setItemList] = useState<any[]>([]);
        const [assetDataValidation, setAssetDataValidation] = useState<any[]>([]);
        const [tradeData, setTradeData] = useState<any[]>([]);
        const [incompleteQuestions, setIncompleteQuestions] = useState<string[]>([]);
        const { handleUnauthorized } = useHandleUnauthorized();
        const ASSET_OID = "A.1";
        const QUESTION_STATUS = "publish";
        const DEFAULT_EVENTOID = "E.1";
        const DEFAULT_FORMOID = "F.1";

        // ========================================
        // SHARED RENDERER MODE - State hooks (must be called unconditionally)
        // ========================================
        const [dynamicQuestionData, setDynamicQuestionData] = useState<any>(propsExistingData || {});
        const isSyncingRef = useRef(false);

        // Image modal state
        const [imageModalOpen, setImageModalOpen] = useState(false);
        const [selectedImage, setSelectedImage] = useState<{ src: string, alt: string } | null>(null);
        const [imageBaseUrl, setImageBaseUrl] = useState<string>('');



        useEffect(() => {
            if (isSharedMode) {

                if (propsImageBaseUrl) {
                    setImageBaseUrl(propsImageBaseUrl);
                }
            }
        }, [isSharedMode, propsItemGroupDefs, propsItemDefs, propsCodeListDefs, propsConditionDefs, propsActiveStep, propsExistingData, propsImageBaseUrl]);

        // Sync with parent's existingData when it changes (only in shared mode)
        useEffect(() => {
            if (isSharedMode && !isSyncingRef.current && propsExistingData) {
                setDynamicQuestionData(propsExistingData);
            }
        }, [isSharedMode, propsExistingData]);

        // Initialize dynamicQuestionData from itemAssetGroupData in shared mode
        useEffect(() => {
            if (isSharedMode && itemAssetGroupData.length > 0 && Object.keys(dynamicQuestionData).length === 0) {
                const flatData: any = {};

                itemAssetGroupData.forEach((itemGroup: any) => {
                    itemGroup.ItemData?.forEach((item: any) => {
                        if (item.Value !== undefined && item.Value !== null && item.Value !== '') {
                            // For CodeList questions, use the selected codes
                            if (item.CodeList && Array.isArray(item.CodeList) && item.CodeList.length > 0) {
                                // Check if CodeList contains enriched objects or just code strings
                                if (typeof item.CodeList[0] === 'object' && item.CodeList[0].CodeListItem) {
                                    // Enriched format - extract enabled codes
                                    const enabledCodes = item.CodeList[0].CodeListItem
                                        .filter((cli: any) => cli.IsEnabled)
                                        .map((cli: any) => cli.CodedValue);

                                    if (enabledCodes.length > 0) {
                                        flatData[item.ItemOID] = enabledCodes.length === 1 ? enabledCodes[0] : enabledCodes.join(', ');
                                    }
                                } else {
                                    // Simple string array format
                                    flatData[item.ItemOID] = item.CodeList.length === 1 ? item.CodeList[0] : item.CodeList.join(', ');
                                }
                            } else {
                                // Regular text/number questions
                                flatData[item.ItemOID] = item.Value;
                            }
                        }
                    });
                });

                if (Object.keys(flatData).length > 0) {
                    setDynamicQuestionData(flatData);
                }
            }
        }, [isSharedMode, itemAssetGroupData, dynamicQuestionData]);

        // Notify parent of changes (with debounce to prevent loops, only in shared mode)
        useEffect(() => {
            if (isSharedMode && !isSyncingRef.current && propsOnDataChange) {
                const timer = setTimeout(() => {
                    propsOnDataChange(dynamicQuestionData);
                }, 50);
                return () => clearTimeout(timer);
            }
        }, [isSharedMode, dynamicQuestionData, propsOnDataChange]);

        // Re-enrich answer data when templates finish loading
        useEffect(() => {

            // Helper function to enrich CodeList in answer data
            const enrichCodeListInAnswerData = (itemData: any[], codeListDefs: any[], itemDefs: any[]) => {
                if (!itemData || !Array.isArray(itemData)) return [];
                if (!codeListDefs || !Array.isArray(codeListDefs) || codeListDefs.length === 0) {
                    return itemData;
                }

                return itemData.map((item: any) => {
                    if (item.CodeList && Array.isArray(item.CodeList) && item.CodeList.length > 0) {
                        const selectedCodes = item.CodeList;

                        const foundItemDef = itemDefs.find((id: any) => id.OID === item.ItemOID);

                        if (foundItemDef && foundItemDef.CodeListRef && foundItemDef.CodeListRef.CodeListOID) {
                            const codeListDef = codeListDefs.find((cl: any) => cl.OID === foundItemDef.CodeListRef!.CodeListOID);

                            if (codeListDef && codeListDef.CodeListItem && Array.isArray(codeListDef.CodeListItem)) {
                                const enrichedCodeList = {
                                    ...codeListDef,
                                    CodeListItem: codeListDef.CodeListItem.map((cli: any) => ({
                                        ...cli,
                                        IsEnabled: selectedCodes.includes(cli.CodedValue)
                                    }))
                                };

                                return {
                                    ...item,
                                    CodeList: [enrichedCodeList]
                                };
                            }
                        }
                    }
                    return item;
                });
            };

            // Re-enrich asset data if templates are now available
            if (assetcodeList.length > 0 && assetitemDef.length > 0 && itemAssetGroupData.length > 0) {
                const enrichedAssetData = itemAssetGroupData.map((igd: any) => {
                    // Check if data is already enriched (CodeList is an object array)
                    const isAlreadyEnriched = igd.ItemData?.some((item: any) =>
                        item.CodeList && Array.isArray(item.CodeList) && item.CodeList.length > 0 &&
                        typeof item.CodeList[0] === 'object' && item.CodeList[0].CodeListItem
                    );

                    if (isAlreadyEnriched) {
                        return igd;
                    }

                    return {
                        ...igd,
                        ItemData: enrichCodeListInAnswerData(igd.ItemData, assetcodeList, assetitemDef)
                    };
                });
                setItemAssetGroupData(enrichedAssetData);
            }

            // Re-enrich trade data if templates are now available and data exists
            if (codeList.length > 0 && itemDef.length > 0 && tradeAnswerDataRef.current) {
                const tradeData = tradeAnswerDataRef.current;

                // Check if tradeData is an array before using .some()
                if (!Array.isArray(tradeData)) {
                    return;
                }

                // Check if data is already enriched
                const isAlreadyEnriched = tradeData.some((igd: any) =>
                    igd.ItemData?.some((item: any) =>
                        item.CodeList && Array.isArray(item.CodeList) && item.CodeList.length > 0 &&
                        typeof item.CodeList[0] === 'object' && item.CodeList[0].CodeListItem
                    )
                );

                if (!isAlreadyEnriched) {
                    const enrichedTradeData = tradeData.map((igd: any) => ({
                        ...igd,
                        ItemData: enrichCodeListInAnswerData(igd.ItemData, codeList, itemDef)
                    }));
                    tradeAnswerDataRef.current = enrichedTradeData;
                }
            }
        }, [assetcodeList.length, codeList.length, assetitemDef.length, itemDef.length]);

        // Helper function to check if a conditional question should be visible
        // IMPORTANT: CollectionExceptionCondition = "Exception to collection" 
        // When condition is TRUE, question should be HIDDEN (it's an exception/exclusion)
        // When condition is FALSE, question should be SHOWN
        const shouldShowQuestion = useCallback((itemRef: any) => {
            // If no condition, always show
            if (!itemRef.CollectionExceptionConditionOID) {
                return true;
            }

            // Find the condition definition
            const conditionDef = propsConditionDefs?.find(cd => cd.OID === itemRef.CollectionExceptionConditionOID);
            if (!conditionDef) {
                return true;
            }

            // Parse the condition expression
            const expression = conditionDef.FormalExpression?.text;
            if (!expression) {
                return true;
            }

            // Handle negation: !(...) means negate the result
            const isNegated = expression.startsWith('!');
            const cleanExpression = expression.replace(/[!()]/g, '').trim();

            // Parse condition like "I.PARENT == 'Yes'" or "I.11 == Yes"
            if (cleanExpression.includes('==')) {
                const [itemOID, expectedValue] = cleanExpression.split('==').map(s =>
                    s.trim().replace(/"/g, '').replace(/'/g, '')
                );

                const currentValue = dynamicQuestionData[itemOID];

                let conditionMet = false;

                if (currentValue !== null && currentValue !== undefined && currentValue !== '') {
                    // Handle Yes/No/1/2 mapping (common in SDM)
                    if (expectedValue === 'Yes' && (currentValue === 'Yes' || currentValue === '1' || currentValue === 'true' || currentValue === true)) {
                        conditionMet = true;
                    } else if (expectedValue === 'No' && (currentValue === 'No' || currentValue === '2' || currentValue === 'false' || currentValue === false)) {
                        conditionMet = true;
                    } else if (Array.isArray(currentValue)) {
                        // Handle multi-select
                        conditionMet = currentValue.some(v =>
                            String(v).toLowerCase() === String(expectedValue).toLowerCase()
                        );
                    } else {
                        // Direct comparison (case-insensitive)
                        conditionMet = String(currentValue).toLowerCase() === String(expectedValue).toLowerCase();
                    }
                }

                // Apply negation if present
                const finalConditionResult = isNegated ? !conditionMet : conditionMet;

                // CRITICAL: CollectionException = TRUE means HIDE the question
                // So we need to INVERT the result: show when condition is FALSE
                const shouldShow = !finalConditionResult;

                return shouldShow;
            }

            // If we can't parse the expression, show by default
            return true;
        }, [propsConditionDefs, dynamicQuestionData]);

        // Get questions for current step with visibility filtering (only used in shared mode)
        const visibleQuestions = useMemo(() => {
            if (!isSharedMode || !propsItemGroupDefs || propsActiveStep === undefined) return [];
            const currentGroup = propsItemGroupDefs[propsActiveStep];
            if (!currentGroup || !currentGroup.ItemRef) return [];

            const allQuestions = currentGroup.ItemRef.map(ref => {
                const itemDef = propsItemDefs?.find(item => item.OID === ref.ItemOID);
                return itemDef ? { itemDef, itemRef: ref } : null;
            }).filter(Boolean) as Array<{ itemDef: ItemDef; itemRef: any }>;

            const visible = allQuestions.filter(({ itemRef }) => shouldShowQuestion(itemRef));

            return visible;
        }, [isSharedMode, propsItemGroupDefs, propsItemDefs, propsActiveStep, dynamicQuestionData, shouldShowQuestion]);

        // Track which fields have been edited by user (to distinguish DB-populated values)
        const [editedOids, setEditedOids] = useState<Record<string, boolean>>({});

        // Only apply DB-restored-field disabling on the Bid Details page
        const isBidDetailsPage = isBidDetailsRoute();
        const currentStatus = typeof window !== 'undefined' ? sessionStorage.getItem('currentStatus') : null;
        const isContractApproval = currentStatus === 'Contract Approval';
        const shouldDisableAllAssetFields = isBidDetailsPage && isContractApproval && tabValue === 0;

        // Handle question value change (only used in shared mode)
        const handleQuestionChange = useCallback((itemOID: string, value: any) => {
            const itemDef = propsItemDefs?.find(item => item.OID === itemOID);

            // Zipcode: allow typing/backspace (0-6 digits), block >6 or non-digits
            if (itemDef?.MetaKey === "zipCode") {
                const zipValue = String(value || '').replace(/[^0-9]/g, ''); // Only digits
                if (zipValue.length > 6) return; // Block only if >6 digits
            }

            // Mark field as edited
            setEditedOids(prev => ({ ...prev, [itemOID]: true }));

            //if user clicks same value, unselect it
            setDynamicQuestionData(prev => {
                if (prev[itemOID] === value) {
                    return { ...prev, [itemOID]: "" }; // unselect
                }
                return { ...prev, [itemOID]: value }; // set new value
            });
        }, [propsItemDefs]);


        // Expose methods via ref - different implementations for shared vs normal mode
        useImperativeHandle(ref, () => {
            if (isSharedMode && propsItemGroupDefs && propsItemDefs && propsCodeListDefs) {
                return {
                    getData: () => dynamicQuestionData,
                    getODMData: () => {
                        // CRITICAL FIX: Organize items by ItemGroupOID according to template structure
                        // Build a map: ItemGroupOID -> ItemData[]
                        const itemGroupMap = new Map<string, any[]>();

                        Object.keys(dynamicQuestionData).forEach(itemOID => {
                            const itemDef = propsItemDefs.find(item => item.OID === itemOID);
                            const value = dynamicQuestionData[itemOID];

                            if (itemDef && value !== undefined && value !== null && value !== '') {
                                // Find which ItemGroup this item belongs to
                                let itemGroupOID: string | null = null;
                                let itemRef: any = null;

                                for (const group of propsItemGroupDefs) {
                                    const found = group?.ItemRef?.find((ref: any) => ref.ItemOID === itemOID);
                                    if (found) {
                                        itemGroupOID = group.OID;
                                        itemRef = found;
                                        break;
                                    }
                                }

                                if (!itemGroupOID) {
                                    return;
                                }

                                const isMandatory = itemRef?.Mandatory === 'Yes';

                                // Use MetaKey from template if it exists AND is different from ItemOID
                                const hasCustomMetaKey = itemDef.MetaKey && itemDef.MetaKey !== itemOID;
                                const metaKeyValue = hasCustomMetaKey ? itemDef.MetaKey : itemOID;

                                const hasCodeList = itemDef.CodeListRef !== undefined;
                                let itemDataEntry: any;

                                if (hasCodeList) {
                                    const codeListOID = itemDef.CodeListRef?.CodeListOID;
                                    const codeList = propsCodeListDefs.find(cl => cl.OID === codeListOID);
                                    const codeListItems = codeList?.CodeListItem || [];
                                    const matchingCodes: string[] = [];

                                    // Handle both array and comma-separated string values
                                    let valuesToProcess: any[] = [];
                                    if (Array.isArray(value)) {
                                        valuesToProcess = value;
                                    } else if (typeof value === 'string' && value.includes(',')) {
                                        valuesToProcess = value.split(',').map(v => v.trim());
                                    } else {
                                        valuesToProcess = [value];
                                    }

                                    valuesToProcess.forEach(val => {
                                        const codeItem = codeListItems.find(
                                            item => item.CodedValue === val ||
                                                item.CodedValue === String(val) ||
                                                item.Decode?.TranslatedText?.text === val
                                        );
                                        if (codeItem) matchingCodes.push(codeItem.CodedValue);
                                    });

                                    itemDataEntry = {
                                        ItemOID: itemOID,
                                        Value: Array.isArray(value) ? value.join(', ') : String(value),
                                        CodeList: matchingCodes,
                                        MetaKey: metaKeyValue,
                                        ItemGroupRepeatKey: 0
                                    };
                                } else {
                                    itemDataEntry = {
                                        ItemOID: itemOID,
                                        Value: String(value),
                                        CodeList: [],
                                        MetaKey: metaKeyValue,
                                        ItemGroupRepeatKey: 0
                                    };
                                }

                                // Add to the appropriate ItemGroup
                                if (!itemGroupMap.has(itemGroupOID)) {
                                    itemGroupMap.set(itemGroupOID, []);
                                }
                                itemGroupMap.get(itemGroupOID)!.push(itemDataEntry);
                            }
                        });

                        // Convert map to ItemGroupData array
                        const itemGroupDataArray: any[] = [];
                        itemGroupMap.forEach((itemDataArray, itemGroupOID) => {
                            itemGroupDataArray.push({
                                ItemData: itemDataArray,
                                ItemGroupOID: itemGroupOID,
                                ItemGroupRepeatKey: 0
                            });
                        });

                        // Return complete ODM format with MULTIPLE ItemGroupData entries
                        return {
                            EventData: [{
                                FormData: [{
                                    ItemGroupData: itemGroupDataArray,  // MULTIPLE ITEMGROUPS!
                                    FormOID: "F.1",
                                    TransactionType: "Insert"
                                }],
                                EventOID: "E.1"
                            }],
                            OID: "A.1",
                            Key: "123",
                            MetaDataVersionOID: "MDV.1"
                        };
                    },
                    getCompletionStatus: () => {
                        if (!propsItemGroupDefs || propsActiveStep === undefined) return [];
                        const currentGroup = propsItemGroupDefs[propsActiveStep];
                        if (!currentGroup) return [];
                        const totalQuestions = currentGroup.ItemRef?.length || 0;
                        const answeredQuestions = currentGroup.ItemRef?.filter(ref => {
                            const value = dynamicQuestionData[ref.ItemOID];
                            return value !== undefined && value !== null && value !== '';
                        }).length || 0;
                        return [{ completed: answeredQuestions, total: totalQuestions }];
                    },
                    validateCurrentStep: () => {
                        // Basic validation: For now, all steps are valid (ItemDef doesn't have Mandatory field)
                        // Parent can implement custom validation logic if needed
                        return true;
                    },
                    updateData: (newData: any) => {
                        isSyncingRef.current = true;
                        setDynamicQuestionData((prev: any) => ({ ...prev, ...newData }));
                        setTimeout(() => { isSyncingRef.current = false; }, 100);
                    },
                    // ADD: getTemplateVersion method (for shared mode)
                    getTemplateVersion: () => {
                        // In shared mode, version might not be available
                        // Return undefined to indicate parent should provide version
                        return undefined;
                    }
                };
            } else {
                // Normal mode - return handleSaveFunctionality method for backward compatibility
                return {
                    handleSaveFunctionality: async () => {
                        if (location.pathname === '/asset') {
                            let itemOIDsInKeyItemGroups: any = assetitemGroup?.flatMap(group =>
                                group.ItemRef.map((ref) => {
                                    if (ref.Mandatory === 'Yes') {
                                        if (ref !== undefined) {
                                            return ref?.ItemOID;
                                        }
                                    }
                                })
                            );

                            const nbnb = assetDataValidation.flatMap((question: any) => {
                                return question.ItemData.flatMap((itemData: any) => {
                                    return itemData.ItemOID;
                                });
                            });
                            const incompleteQuestionOIDs = itemOIDsInKeyItemGroups?.filter((x: any) => !nbnb.includes(x))?.filter((x: any) => x !== undefined);
                            if (incompleteQuestionOIDs.length > 0) {
                                setIncompleteQuestions(incompleteQuestionOIDs);
                                validate(incompleteQuestionOIDs);
                                return;
                            }
                            setIncompleteQuestions([]);
                        }

                        const dataStr = JSON.stringify(assetData);
                        const dataString = JSON.stringify(projectData);
                        if (onSave) {
                            onSave(dataStr, dataString, tradeType, tradeVersion, assetVersion, assetOID, projectOID);
                        }
                    },
                    // ADD: getTemplateVersion method (for normal mode)
                    getTemplateVersion: () => {
                        // Get version from asset template state (loaded from API response)
                        const version = assetVersion || aasettemplateVersion || templateVersion;

                        // NO FALLBACK - If version is not found, return undefined
                        if (!version) {
                            return undefined;
                        }

                        return String(version); // Ensure it's returned as string
                    }
                };
            }
        }, [isSharedMode, dynamicQuestionData, propsItemGroupDefs, propsItemDefs, propsCodeListDefs, propsActiveStep, assetData, projectData, tradeType, tradeVersion, assetVersion, assetOID, projectOID, onSave, location.pathname, assetitemGroup, assetDataValidation, aasettemplateVersion, templateVersion]);

        // ========================================
        // SHARED RENDERER MODE - Rendering Logic
        // ========================================
        // Render a question based on its DataType (only used in shared mode)
        const renderQuestion = (itemDef: ItemDef, itemRef: any) => {
            const value = dynamicQuestionData[itemDef.OID];

            // Find CodeList if applicable
            const codeList = itemDef.CodeListRef ?
                propsCodeListDefs?.find(cl => cl.OID === itemDef.CodeListRef?.CodeListOID) :
                undefined;

            const questionLabel = itemDef.Question?.TranslatedText?.text || itemDef.Name;
            const isMandatory = itemRef?.Mandatory === 'Yes';

            switch (itemDef.DataType?.toLowerCase()) {
                case 'text':
                case 'string': {
                    const isDisabled = shouldDisableAllAssetFields;
                    return (
                        <Box key={itemDef.OID} sx={{ mb: 2 }}>
                            <Typography variant="body2" sx={{ mb: 0.5, fontWeight: 500, minHeight: '35px', display: 'flex', alignItems: 'flex-start' }}>
                                {questionLabel}
                                {isMandatory && <span style={{ color: 'red', marginLeft: '4px' }}>*</span>}
                            </Typography>
                            <input
                                id={`sdm-v2-text1-input-${itemDef?.OID}`}
                                type="text"
                                value={value || ''}
                                onChange={(e) => handleQuestionChange(itemDef.OID, e.target.value)}
                                disabled={isDisabled}
                                data-disabled-source={isDisabled ? 'contract-approval' : undefined}
                                style={{
                                    width: '100%',
                                    padding: '8px 12px',
                                    border: '1px solid #d0d0d0',
                                    borderRadius: '4px',
                                    fontSize: '14px',
                                    opacity: isDisabled ? 0.6 : 1
                                }}
                            />
                        </Box>
                    );
                }
                case 'integer':
                case 'float': {
                    const isDisabled = shouldDisableAllAssetFields;
                    return (
                        <Box key={itemDef.OID} sx={{ mb: 2 }}>
                            <Typography variant="body2" sx={{ mb: 0.5, fontWeight: 500, minHeight: '35px', display: 'flex', alignItems: 'flex-start' }}>
                                {questionLabel}
                                {isMandatory && <span style={{ color: 'red', marginLeft: '4px' }}>*</span>}
                            </Typography>
                            <input
                                id={`sdm-v2-integer-input--${itemDef?.OID}`}
                                type="text"
                                value={value ?? ''}
                                onKeyDown={(e) => {
                                    // Allow letters, numbers, and essential keys
                                    if (!/[a-zA-Z0-9]/.test(e.key)) {
                                        e.preventDefault();
                                    }
                                }}
                                onChange={(e) => {
                                    let val = e.target.value;
                                    if (val === '') {
                                        handleQuestionChange(itemDef.OID, '');
                                        return;
                                    }
                                    const onlyNumbers = val.replace(/[^0-9]/g, '');
                                    const num = Number(onlyNumbers);
                                    if (onlyNumbers && num <= 0) return;
                                    handleQuestionChange(itemDef.OID, val);
                                }}
                                disabled={isDisabled}
                                data-disabled-source={isDisabled ? 'contract-approval' : undefined}
                                style={{
                                    width: '100%',
                                    padding: '8px 12px',
                                    border: '1px solid #d0d0d0',
                                    borderRadius: '4px',
                                    fontSize: '14px',
                                    opacity: isDisabled ? 0.6 : 1
                                }}
                            />
                        </Box>
                    );
                }
                case 'date': {
                    const isDisabled = shouldDisableAllAssetFields;
                    return (
                        <Box key={itemDef.OID} sx={{ mb: 2 }}>
                            <Typography variant="body2" sx={{ mb: 0.5, fontWeight: 500, minHeight: '35px', display: 'flex', alignItems: 'flex-start' }}>
                                {questionLabel}
                                {isMandatory && <span style={{ color: 'red', marginLeft: '4px' }}>*</span>}
                            </Typography>
                            <input
                                id={`sdm-v2-date-input-${itemDef?.OID}`}
                                type="date"
                                value={value || ''}
                                onChange={(e) => handleQuestionChange(itemDef.OID, e.target.value)}
                                placeholder="Select a date"
                                disabled={isDisabled}
                                data-disabled-source={isDisabled ? 'contract-approval' : undefined}
                                style={{
                                    width: '100%',
                                    padding: '8px 12px',
                                    border: '1px solid #d0d0d0',
                                    borderRadius: '4px',
                                    fontSize: '14px',
                                    color: value ? '#000' : '#999',
                                    opacity: isDisabled ? 0.6 : 1
                                }}
                            />
                        </Box>
                    );
                }
                default:
                    if (codeList && codeList.CodeListItem) {
                        // Check if it's multi-choice (DataType includes "multi" or check Repeating property)
                        const isMultiChoice = itemDef.DataType?.toLowerCase().includes('multi');

                        if (isMultiChoice) {
                            // Multi-choice: Card-based selection with enhanced image display
                            const selectedValues = Array.isArray(value) ? value : (value ? [value] : []);
                            return (
                                <Box key={itemDef.OID} sx={{ mb: 2 }}>
                                    <Typography variant="body2" sx={{ mb: 0.5, fontWeight: 600, color: '#1a365d', minHeight: '35px', display: 'flex', alignItems: 'flex-start' }}>
                                        {questionLabel}
                                        {isMandatory && <span style={{ color: 'red', marginLeft: '4px' }}>*</span>}
                                    </Typography>
                                    <Box className="question-renderer-multi-select-grid">
                                        {codeList.CodeListItem.map(item => {
                                            // AB#31383 — some trade templates already carry a literal "All" answer
                                            // choice alongside the real options (e.g. Front/Left/Right/Back + All).
                                            // On Asset forms, clicking it is a shortcut that selects every other
                                            // option; "All" itself is never a saved value, so it never shows selected.
                                            const isLiteralAllOption = isAssetForm
                                                && (item.Decode?.TranslatedText?.text || '').trim().toLowerCase() === 'all';
                                            const isSelected = !isLiteralAllOption && selectedValues.includes(item.CodedValue);
                                            const isDisabled = shouldDisableAllAssetFields || (isBidDetailsPage && isContractApproval && (value !== undefined && value !== null && String(value) !== '') && !editedOids[itemDef.OID]);
                                            // Use thumbnail for card display
                                            const thumbnailSrc = getImageUrl(item.ThumbnailUrl, imageBaseUrl);
                                            // Full image URL (loaded only when zoom is clicked)
                                            const fullImageSrc = getImageUrl(item.ImageUrl, imageBaseUrl);


                                            return (
                                                <Box
                                                    id={`sdm-v2-multi-select-${itemDef?.OID}`}
                                                    key={item.CodedValue}
                                                    data-disabled-source={isDisabled ? 'contract-approval' : undefined}
                                                    onClick={() => {
                                                        if (isDisabled) return;
                                                        if (isLiteralAllOption) {
                                                            const otherCodedValues = codeList.CodeListItem
                                                                .filter(ci => ci.CodedValue !== item.CodedValue)
                                                                .map(ci => ci.CodedValue);
                                                            const allOthersSelected = otherCodedValues.length > 0
                                                                && otherCodedValues.every(cv => selectedValues.includes(cv));
                                                            handleQuestionChange(itemDef.OID, allOthersSelected ? [] : otherCodedValues);
                                                            return;
                                                        }
                                                        const newValues = isSelected
                                                            ? selectedValues.filter(v => v !== item.CodedValue)
                                                            : [...selectedValues, item.CodedValue];
                                                        handleQuestionChange(itemDef.OID, newValues);
                                                    }}
                                                    className={`question-option-card ${isSelected ? 'question-option-card--selected' : ''} ${isDisabled ? 'question-option-card--disabled' : ''}`}
                                                >
                                                    {(thumbnailSrc || fullImageSrc) && (
                                                        <Box className="question-option-image-container">
                                                            <img
                                                                src={thumbnailSrc || ''}
                                                                id={`sdm-questions-multi-select-image-${item.CodedValue}`}
                                                                alt={item.Decode?.TranslatedText?.text || item.CodedValue}
                                                                className="question-option-image"
                                                                onError={(e) => {
                                                                    console.error(`[QuestionRenderer MULTI] Failed to load thumbnail for ${item.CodedValue}:`, thumbnailSrc);
                                                                }}
                                                            />
                                                            <Box
                                                                className="question-option-zoom-btn"
                                                                onClick={(e) => {
                                                                    e.stopPropagation();
                                                                    setSelectedImage({ src: fullImageSrc || thumbnailSrc || '', alt: item.Decode?.TranslatedText?.text || item.CodedValue });
                                                                    setImageModalOpen(true);
                                                                }}
                                                            >
                                                            </Box>
                                                        </Box>
                                                    )}
                                                    <Box className="question-option-label">
                                                        <Typography
                                                            variant="body2"
                                                            className={`question-option-text ${isSelected ? 'question-option-text--selected' : ''}`}
                                                        >
                                                            {item.Decode?.TranslatedText?.text || item.CodedValue}
                                                        </Typography>
                                                        {isSelected && (
                                                            <Box className="question-option-checkmark"></Box>
                                                        )}
                                                    </Box>
                                                </Box>
                                            );
                                        })}
                                    </Box>
                                </Box>
                            );
                        } else {
                            // Single-choice: Card-based selection with horizontal layout
                            return (
                                <Box key={itemDef.OID} sx={{ mb: 2 }}>
                                    <Typography variant="body2" sx={{ mb: 0.5, fontWeight: 600, color: '#1a365d', minHeight: '35px', display: 'flex', alignItems: 'flex-start' }}>
                                        {questionLabel}
                                        {isMandatory && <span style={{ color: 'red', marginLeft: '4px' }}>*</span>}
                                    </Typography>
                                    <Box className="question-renderer-single-select-grid">
                                        {codeList.CodeListItem.map(item => {
                                            const isSelected = value === item.CodedValue;
                                            const isDisabled = shouldDisableAllAssetFields || (isBidDetailsPage && isContractApproval && (value !== undefined && value !== null && String(value) !== '') && !editedOids[itemDef.OID]);
                                            // Use thumbnail for card display
                                            const thumbnailSrc = getImageUrl(item.ThumbnailUrl);
                                            // Full image URL
                                            const fullImageSrc = getImageUrl(item.ImageUrl);

                                            return (
                                                <Box
                                                    id={`sdm-v2-single-select-${itemDef?.OID}`}
                                                    key={item.CodedValue}
                                                    data-disabled-source={isDisabled ? 'contract-approval' : undefined}
                                                    onClick={() => { if (isDisabled) return; handleQuestionChange(itemDef.OID, item.CodedValue); }}
                                                    className={`question-option-card ${isSelected ? 'question-option-card--selected' : ''} ${isDisabled ? 'question-option-card--disabled' : ''}`}
                                                >
                                                    {FEATURE_FLAGS.createbid_visualsupport__enable && (thumbnailSrc || fullImageSrc) && (
                                                        <Box className="question-option-image-container">
                                                            <img
                                                                src={thumbnailSrc || ''}
                                                                id={`sdm-questions-single-select-image-${item.CodedValue}`}
                                                                alt={item.Decode?.TranslatedText?.text || item.CodedValue}
                                                                className="question-option-image"
                                                                onError={(e) => {
                                                                    console.error(`[QuestionRenderer SINGLE] Failed to load thumbnail for ${item.CodedValue}:`, thumbnailSrc);
                                                                }}
                                                            />
                                                            <Box
                                                                className="question-option-zoom-btn"
                                                                onClick={(e) => {
                                                                    e.stopPropagation();
                                                                    setSelectedImage({ src: fullImageSrc || thumbnailSrc || '', alt: item.Decode?.TranslatedText?.text || item.CodedValue });
                                                                    setImageModalOpen(true);
                                                                }}
                                                            >
                                                            </Box>
                                                        </Box>
                                                    )}
                                                    <Box className="question-option-label">
                                                        <Typography
                                                            variant="body2"
                                                            className={`question-option-text ${isSelected ? 'question-option-text--selected' : ''}`}
                                                        >
                                                            {item.Decode?.TranslatedText?.text || item.CodedValue}
                                                        </Typography>
                                                        {isSelected && (
                                                            <Box className="question-option-checkmark"></Box>
                                                        )}
                                                    </Box>
                                                </Box>
                                            );
                                        })}
                                    </Box>
                                </Box>
                            );
                        }
                    }
                    // Default text input
                    {
                        const isDisabled = shouldDisableAllAssetFields || (isBidDetailsPage && isContractApproval && (value !== undefined && value !== null && String(value) !== '') && !editedOids[itemDef.OID]);
                        return (
                            <Box key={itemDef.OID} sx={{ mb: 2 }}>
                                <Typography variant="body2" sx={{ mb: 0.5, fontWeight: 500, minHeight: '35px', display: 'flex', alignItems: 'flex-start' }}>
                                    {questionLabel}
                                    {isMandatory && <span style={{ color: 'red', marginLeft: '4px' }}>*</span>}
                                </Typography>
                                <input
                                    id={`sdm-v2-text-input-${itemDef.OID}`}
                                    type="text"
                                    value={value || ''}
                                    onChange={(e) => handleQuestionChange(itemDef.OID, e.target.value)}
                                    disabled={isDisabled}
                                    data-disabled-source={isDisabled ? 'contract-approval' : undefined}
                                    style={{
                                        width: '100%',
                                        padding: '8px 12px',
                                        border: '1px solid #d0d0d0',
                                        borderRadius: '4px',
                                        fontSize: '14px',
                                        opacity: isDisabled ? 0.6 : 1
                                    }}
                                />
                            </Box>
                        );
                    }
            }
        };

        // Main useEffect for normal mode (must be called unconditionally, but only runs in normal mode)
        useEffect(() => {
            // Skip in shared mode
            if (isSharedMode) return;

            (async () => {
              try {
                if (location.pathname === '/asset') {
                    setHide(true);
                    if (location?.state?.assetObjectData !== undefined) {
                        getselectedTemplatesByVersion(ASSET_OID, location.state.assetObjectData.assetQuestionTemplateVersion).then((resp => {
                            if (resp?.data !== null && resp?.data !== undefined && resp?.data !== '') {
                                let response = JSON.parse(resp?.data?.questionTemplateData);
                                if (response !== null && response !== undefined) {
                                    setAssetResponse(response);
                                    setAssetFormDef(getMergedTv(response)?.FormDef);
                                    setAssetEventDef(getMergedTv(response)?.EventDef);
                                    setAssetTemplate([getMergedTv(response)]);
                                    setMeasureUnit(response?.SDM?.Building?.BasicDefinition);
                                    setAssetTemplateVersion(getActiveMetaDataVersionOID(response));
                                    setAssetOID(response?.SDM?.Building?.OID);
                                    setAssetVersion(resp?.data?.version);
                                    handleChange(null, response, 0);
                                }
                            }
                        }))
                    } else {
                        getTemplatesByTradeName(ASSET_OID, QUESTION_STATUS, accessToken).then((res => {
                            handleUnauthorized(res);
                            if (res?.ok && res?.data !== null && res?.data !== undefined && res?.data !== '') {
                                let response = JSON.parse(res?.data?.questionTemplateData);
                                if (response !== null && response !== undefined) {

                                    setAssetResponse(response);
                                    setAssetFormDef(getMergedTv(response)?.FormDef);
                                    setAssetEventDef(getMergedTv(response)?.EventDef);
                                    setAssetTemplate([getMergedTv(response)]);
                                    setMeasureUnit(response?.SDM?.Building?.BasicDefinition);
                                    setAssetTemplateVersion(getActiveMetaDataVersionOID(response));
                                    setAssetOID(response?.SDM?.Building?.OID);
                                    setAssetMethodDef(getMergedTv(response)?.MethodDef ?? []);
                                    setAssetVersion(res?.data?.version);
                                    handleChange(null, response, 0);
                                }
                            }
                        }));
                    }
                    if (data !== null && data !== undefined) {
                        handleSampleData(null, JSON.parse(data?.assetTemplateData), ASSET_OID, DEFAULT_EVENTOID, DEFAULT_FORMOID);
                        setAssetData(JSON.parse(data?.assetTemplateData));
                    }
                }
                else if (location.pathname === '/projectinformation' || location.pathname === '/soldproduction/projectinformation') {
                    const res = await getProjectById(accessToken, projectId);
                    handleUnauthorized(res);
                    if (res.status === 200) {
                        let tradename = res.data.trade;
                        let tradeOid = res.data.tradeOid;
                        let assetUid = res.data.assetUid;

                        trade.current = tradename;
                        if (res?.ok && res?.data !== null && res?.data !== undefined) {
                            res?.data?.answerData?.map((data: any) => {
                                if (!data?.tradeOid.includes('P')) {
                                    if (data?.answersData !== "") {
                                        handleSampleData(null, JSON.parse(data?.answersData), ASSET_OID, DEFAULT_EVENTOID, DEFAULT_FORMOID);
                                        setAssetData(JSON.parse(data?.answersData));
                                        getselectedTemplatesByVersion(ASSET_OID, data?.questionTemplateVersion).then((res => {
                                            if (res?.ok && res?.data !== null && res?.data !== undefined && res?.data !== '') {
                                                let response = JSON.parse(res.data.questionTemplateData);
                                                if (response !== null && response !== undefined) {
                                                    setAssetResponse(response);
                                                    setAssetFormDef(getMergedTv(response)?.FormDef);
                                                    setAssetEventDef(getMergedTv(response)?.EventDef);
                                                    setAssetTemplate([getMergedTv(response)]);
                                                    setMeasureUnit(response.SDM.Building.BasicDefinition);
                                                    setAssetOID(response?.SDM?.Building?.OID);
                                                    setAssetMethodDef(getMergedTv(response)?.MethodDef ?? []);
                                                    setAssetVersion(data?.questionTemplateVersion);
                                                    handleChange(null, response, 0);
                                                }
                                            } else {
                                                getTrade();
                                            }
                                        }));
                                    } else {
                                        getTemplatesByTradeName(ASSET_OID, QUESTION_STATUS, accessToken).then((res => {
                                            handleUnauthorized(res);
                                            if (res?.ok && res?.data !== null && res?.data !== undefined && res?.data !== '') {
                                                let response = JSON.parse(res?.data?.questionTemplateData);
                                                if (response !== null && response !== undefined) {
                                                    setAssetResponse(response);
                                                    setAssetFormDef(getMergedTv(response)?.FormDef);
                                                    setAssetEventDef(getMergedTv(response)?.EventDef);
                                                    setAssetTemplate([getMergedTv(response)]);
                                                    setMeasureUnit(response?.SDM?.Building?.BasicDefinition);
                                                    setAssetTemplateVersion(getActiveMetaDataVersionOID(response));
                                                    setAssetOID(response?.SDM?.Building?.OID);
                                                    setAssetMethodDef(getMergedTv(response)?.MethodDef ?? []);
                                                    handleChange(null, response, 0);

                                                }
                                            }
                                        }));

                                        getAssetsByAssetUid(accessToken, assetUid).then((res: any) => {
                                            handleUnauthorized(res);

                                            if (res.status === 200) {
                                                let tradename = res.data.trade;

                                                trade.current = tradename;
                                                if (res?.data?.assetTemplateData !== null && res?.data?.assetTemplateData !== undefined) {
                                                    handleSampleData(null, JSON.parse(res?.data?.assetTemplateData), ASSET_OID, DEFAULT_EVENTOID, DEFAULT_FORMOID);
                                                    setAssetData(JSON.parse(res?.data?.assetTemplateData));
                                                }
                                            }
                                        });

                                        getTemplatesByTradeName(tradeOid, QUESTION_STATUS, accessToken).then((res => {
                                            handleUnauthorized(res);
                                            if (res.data.questionTemplateData) {
                                                let response = JSON.parse(res.data.questionTemplateData);
                                                if (response !== null && response !== undefined) {
                                                    setResponse(response);
                                                    setFormDef(getMergedTv(response)?.FormDef);
                                                    setEventDef(getMergedTv(response)?.EventDef);
                                                    setTemplate(response?.SDM?.Building?.TemplateVersion);
                                                    setTemplateVersion(getActiveMetaDataVersionOID(response));
                                                    setMeasureUnit(response.SDM.Building.BasicDefinition);
                                                    setMethodDef(getMergedTv(response)?.MethodDef ?? []);
                                                    setProjectOID(response?.SDM?.Building?.OID);
                                                    setTradeVersion(res?.data?.version);
                                                    setMappings(getMergedTv(response)?.Mappings);

                                                }
                                            }
                                        }));
                                    }
                                } else {
                                    // Match v1 behavior: Only set trade data and template, don't process immediately
                                    setProjectData(JSON.parse(data?.answersData));
                                    getselectedTemplatesByVersion(data.tradeOid, data?.questionTemplateVersion).then((res => {
                                        handleUnauthorized(res);
                                        if (res?.ok && res?.data !== null && res?.data !== undefined && res?.data !== '') {
                                            let response = JSON.parse(res.data.questionTemplateData);
                                            if (response !== null && response !== undefined) {
                                                setResponse(response);
                                                setFormDef(getMergedTv(response)?.FormDef);
                                                setEventDef(getMergedTv(response)?.EventDef);
                                                setTemplate(response?.SDM?.Building?.TemplateVersion);
                                                setTemplateVersion(getActiveMetaDataVersionOID(response));
                                                setMeasureUnit(response.SDM.Building.BasicDefinition);
                                                setMethodDef(getMergedTv(response)?.MethodDef ?? []);
                                                setProjectOID(response?.SDM?.Building?.OID);
                                                setTradeVersion(data?.questionTemplateVersion);
                                                setMappings(getMergedTv(response)?.Mappings);
                                            }
                                        }
                                    }));
                                }
                            })
                        }
                    }
                }
                else {
                    if (state === "edit") {
                        await getAssetsByAssetUid(accessToken, data?.assetUid).then((res: any) => {
                            handleUnauthorized(res);

                            if (res.status === 200) {
                                let tradename = res.data.trade;

                                trade.current = tradename;
                                if (res?.data?.assetTemplateData !== null && res?.data?.assetTemplateData !== undefined) {
                                    handleSampleData(null, JSON.parse(res?.data?.assetTemplateData), ASSET_OID, DEFAULT_EVENTOID, DEFAULT_FORMOID);
                                    setAssetData(JSON.parse(res?.data?.assetTemplateData));
                                }
                            }
                        });

                        getTemplatesByTradeName(ASSET_OID, QUESTION_STATUS, accessToken).then((res => {
                            handleUnauthorized(res);

                            let response = JSON.parse(res.data.questionTemplateData);

                            if (response !== null && response !== undefined) {
                                setAssetResponse(response);
                                setAssetFormDef(getMergedTv(response)?.FormDef);
                                setAssetEventDef(getMergedTv(response)?.EventDef);
                                setAssetTemplate(response.SDM.Building.TemplateVersion);
                                setMeasureUnit(response?.SDM?.Building?.BasicDefinition);
                                setAssetTemplateVersion(getActiveMetaDataVersionOID(response));
                                setAssetOID(response?.SDM?.Building?.OID);
                                setAssetMethodDef(getMergedTv(response)?.MethodDef ?? []);
                                setAssetVersion(res?.data?.version);
                                handleChange(null, response, 0);
                            }
                        }));

                        const res = await getProjectById(accessToken, data.id);
                        handleUnauthorized(res);

                        if (res.status === 200) {
                            let tradename = res.data.trade;
                            trade.current = tradename;
                            setTradeType(res?.data?.tradeType)
                            if (res?.ok && res?.data !== null && res?.data !== undefined) {

                                res?.data?.answerData?.map((data: any) => {
                                    if (!data?.tradeOid.includes('P') && data?.tradeOid !== "") {

                                        getselectedTemplatesByVersion(ASSET_OID, data?.questionTemplateVersion).then((res => {
                                            handleUnauthorized(res);
                                            if (res?.ok && res?.data !== null && res?.data !== undefined && res?.data !== '') {
                                                let response = JSON.parse(res?.data?.questionTemplateData);
                                                if (response !== null && response !== undefined) {
                                                    setAssetResponse(response);
                                                    setAssetFormDef(getMergedTv(response)?.FormDef);
                                                    setAssetEventDef(getMergedTv(response)?.EventDef);
                                                    setAssetTemplate([getMergedTv(response)]);
                                                    setMeasureUnit(response.SDM.Building.BasicDefinition);
                                                    setAssetOID(response?.SDM?.Building?.OID);
                                                    setAssetMethodDef(getMergedTv(response)?.MethodDef ?? []);
                                                    setAssetVersion(data?.questionTemplateVersion);
                                                    handleChange(null, response, 0);
                                                }
                                            }
                                        }));
                                    }
                                    else if (data?.questionTemplateVersion !== null && data?.answerData !== "") {

                                        setProjectData(JSON.parse(data?.answersData));
                                        getselectedTemplatesByVersion(data?.tradeOid, data?.questionTemplateVersion).then((res => {
                                            handleUnauthorized(res);

                                            if (res?.ok && res?.data !== null && res?.data !== undefined && res?.data !== '') {
                                                let response = JSON.parse(res?.data?.questionTemplateData);
                                                if (response !== null && response !== undefined) {
                                                    setResponse(response);
                                                    setFormDef(getMergedTv(response)?.FormDef);
                                                    setEventDef(getMergedTv(response)?.EventDef);
                                                    setTemplate(response?.SDM?.Building?.TemplateVersion);
                                                    setTemplateVersion(getActiveMetaDataVersionOID(response));
                                                    setMeasureUnit(response.SDM.Building.BasicDefinition);
                                                    setMethodDef(getMergedTv(response)?.MethodDef ?? []);
                                                    setProjectOID(response?.SDM?.Building?.OID);
                                                    setTradeVersion(data?.questionTemplateVersion);
                                                    setMappings(getMergedTv(response)?.Mappings);

                                                }
                                            }
                                        }));
                                    } else {
                                        getTrade();
                                    }
                                })
                            }
                        }
                    }
                    else {
                        //Calls when the page is loaded for the first time in create bid...
                        if (data?.trade !== null && data?.trade !== undefined) {
                            getTradeDetails(data?.trade, accessToken).then(resType => {
                                setTradeType(resType.data.type);
                                getTemplatesByTradeName(resType.data?.tradeOid, QUESTION_STATUS, accessToken).then((res => {

                                    if (res.data.questionTemplateData) {
                                        let response = JSON.parse(res.data.questionTemplateData);
                                        if (response !== null && response !== undefined) {
                                            setResponse(response);
                                            setFormDef(getMergedTv(response)?.FormDef);
                                            setEventDef(getMergedTv(response)?.EventDef);
                                            setTemplate(response?.SDM?.Building?.TemplateVersion);
                                            setTemplateVersion(getActiveMetaDataVersionOID(response));
                                            setMeasureUnit(response.SDM.Building.BasicDefinition);
                                            setMethodDef(getMergedTv(response)?.MethodDef ?? []);
                                            setProjectOID(response?.SDM?.Building?.OID);
                                            setTradeVersion(res?.data?.version);
                                            setMappings(getMergedTv(response)?.Mappings);

                                        }
                                    }
                                }));
                            })
                        }

                        // need to knoow when it is called
                        if (data !== null && data !== undefined && data?.id !== null && data?.id !== undefined) {
                            data?.answerData?.map((dt: any) => {
                                if (!dt?.tradeOid.includes('P')) {
                                    getAssetsByAssetUid(accessToken, data?.assetUid)
                                } else {
                                    getselectedTemplatesByVersion(data?.tradeOid, dt?.questionTemplateVersion).then((res => {
                                        handleUnauthorized(res);

                                        let response = JSON.parse(res.data.questionTemplateData);
                                        if (response !== null && response !== undefined) {

                                            setResponse(response);
                                            setFormDef(getMergedTv(response)?.FormDef);
                                            setEventDef(getMergedTv(response)?.EventDef);
                                            setTemplate(response?.SDM?.Building?.TemplateVersion);
                                            setTemplateVersion(getActiveMetaDataVersionOID(response));
                                            setMeasureUnit(response.SDM.Building.BasicDefinition);
                                            setMethodDef(getMergedTv(response)?.MethodDef ?? []);
                                            setProjectOID(response?.SDM?.Building?.OID);
                                            setTradeVersion(res?.data?.version);
                                            setMappings(getMergedTv(response)?.Mappings);

                                        }
                                    }));
                                    setProjectData(JSON.parse(dt?.answersData));
                                    handleSampleData(null, JSON.parse(dt?.answersData), data?.tradeOid, DEFAULT_EVENTOID, DEFAULT_FORMOID);
                                }


                            })
                        }

                        getTemplatesByTradeName(ASSET_OID, QUESTION_STATUS, accessToken).then((res => {
                            handleUnauthorized(res);
                            let response = JSON.parse(res.data.questionTemplateData);
                            if (response !== null && response !== undefined) {

                                setAssetResponse(response);
                                setAssetFormDef(getMergedTv(response)?.FormDef);
                                setAssetEventDef(getMergedTv(response)?.EventDef);
                                setAssetTemplate(response.SDM.Building.TemplateVersion);
                                setMeasureUnit(response?.SDM?.Building?.BasicDefinition);
                                setAssetTemplateVersion(getActiveMetaDataVersionOID(response));
                                setAssetOID(response?.SDM?.Building?.OID);
                                setAssetVersion(res?.data?.version);
                                handleChange(null, response, 0);
                            }
                        }));

                        await getAssetsByAssetUid(accessToken, data?.assetUid).then((res: any) => {

                            handleUnauthorized(res);
                            if (res.status === 200) {
                                let tradename = res.data.trade;
                                trade.current = tradename;
                                if (res?.data?.assetTemplateData !== null && res?.data?.assetTemplateData !== undefined) {
                                    handleSampleData(null, JSON.parse(res?.data?.assetTemplateData), ASSET_OID, DEFAULT_EVENTOID, DEFAULT_FORMOID);
                                    setAssetData(JSON.parse(res?.data?.assetTemplateData));
                                }
                            }
                        });

                        // await getRequest(accessToken);
                        const asset = await getAssetsByAssetUid(accessToken, data?.assetUid);

                        // Guard the predecessor call too - if getAssetsByAssetUid itself fails,
                        // asset.data is the error body (no id field), so asset?.data?.id would be
                        // undefined and getRequest would be called with a malformed assetId.
                        if (asset.ok) {
                            const res = await getRequest(asset?.data?.id, accessToken);

                            // res.ok is false when the asset has no EagleView measurement report yet
                            // (e.g. an asset created directly via the CRM Assets tab, which never runs
                            // the EagleView flow) - execute() never throws on a 404, it returns the
                            // error body in res.data, which must not be treated as tradeData.
                            if (res.ok) {
                                setTradeData(res.data);
                            }
                        }
                        // await getReport(accessToken,data?assetUid).then(())
                        setHide(false);
                    }
                }
              } catch (e) {
                // None of the ~15 .then() chains above has a .catch() - execute()/executeGetData()
                // never reject on a failed call (they resolve with the error body as res.data), so
                // an unexpected shape (e.g. JSON.parse on an error body) throws here as the only
                // catch point in this whole effect. Without this, isInitialLoading (only ever
                // cleared reactively once real data populates - see the effect below) never
                // clears, and the UI is stuck on the loading skeleton forever with no feedback.
                console.error('QuestionRenderer_v2 initial load failed:', e);
                setIsInitialLoading(false);
              }
            })();
        }, []);

        // Track when initial data loading is complete
        useEffect(() => {
            // Check if at least one of the data sets has been populated
            const hasAssetData = assetitemGroup.length > 0 || assetitemDef.length > 0;
            const hasTradeData = itemGroup.length > 0 || itemDef.length > 0;

            if (hasAssetData || hasTradeData) {
                setIsInitialLoading(false);
            }
        }, [assetitemGroup, assetitemDef, itemGroup, itemDef]);

        // useEffect(() => { }, [itemGroupData]);

        // ========================================
        // SHARED MODE - Conditional Early Return (after all hooks are called)
        // ========================================
        if (isSharedMode && propsItemGroupDefs && propsItemDefs && propsCodeListDefs) {
            // Group questions: parent questions and their conditional children
            const renderQuestionsHierarchically = () => {
                const renderedOIDs = new Set<string>();
                const elements: JSX.Element[] = [];

                visibleQuestions.forEach(({ itemDef, itemRef }) => {
                    // Skip if already rendered as a child
                    if (renderedOIDs.has(itemDef.OID)) return;

                    // Check if this is a parent question (no condition) or a standalone question
                    const isParent = !itemRef.CollectionExceptionConditionOID;

                    if (isParent) {
                        // Render parent question in its own row (full width or half width)
                        elements.push(
                            <Grid item xs={12} sm={6} key={itemDef.OID}>
                                {renderQuestion(itemDef, itemRef)}
                            </Grid>
                        );
                        renderedOIDs.add(itemDef.OID);

                        // Find and render all conditional children that depend on this parent
                        const children = visibleQuestions.filter(({ itemDef: childDef, itemRef: childRef }) => {
                            if (!childRef.CollectionExceptionConditionOID) return false;

                            // Check if this child's condition references the current parent
                            const conditionDef = propsConditionDefs?.find(cd => cd.OID === childRef.CollectionExceptionConditionOID);
                            if (!conditionDef?.FormalExpression?.text) return false;

                            const expression = conditionDef.FormalExpression.text;
                            const cleanExpression = expression.replace(/[!()]/g, '').trim();

                            if (cleanExpression.includes('==')) {
                                const [parentOID] = cleanExpression.split('==').map(s => s.trim().replace(/"/g, '').replace(/'/g, ''));
                                return parentOID === itemDef.OID;
                            }

                            return false;
                        });

                        // Render children below parent (indented, full width)
                        children.forEach(({ itemDef: childDef, itemRef: childRef }) => {
                            if (!renderedOIDs.has(childDef.OID)) {
                                elements.push(
                                    <Grid item xs={12} key={childDef.OID}>
                                        <Box sx={{ pl: 4, borderLeft: '3px solid #e0e0e0', ml: 2 }}>
                                            {renderQuestion(childDef, childRef)}
                                        </Box>
                                    </Grid>
                                );
                                renderedOIDs.add(childDef.OID);
                            }
                        });
                    }
                });

                // Render any remaining questions that weren't caught (orphaned conditionals)
                visibleQuestions.forEach(({ itemDef, itemRef }) => {
                    if (!renderedOIDs.has(itemDef.OID)) {
                        elements.push(
                            <Grid item xs={12} sm={6} key={itemDef.OID}>
                                {renderQuestion(itemDef, itemRef)}
                            </Grid>
                        );
                        renderedOIDs.add(itemDef.OID);
                    }
                });

                return elements;
            };

            return (
                <>
                    <Grid container spacing={2}>
                        {renderQuestionsHierarchically()}
                    </Grid>

                    {/* Image Modal for Full-Size View - Using consistent ImageZoomDialog */}
                    <ImageZoomDialog
                        open={imageModalOpen}
                        onClose={() => setImageModalOpen(false)}
                        imageSrc={selectedImage?.src || null}
                        imageAlt={selectedImage?.alt || ''}
                    />
                </>
            );
        }

        // ========================================
        // NORMAL MODE - Continue with rest of component
        // ========================================
        const getTrade = async () => {
            getTradeDetails(data?.trade, accessToken).then(resType => {
                handleUnauthorized(data);
                setTradeType(resType.data?.type);
                //below null checked is removed I'm adding it again...
                //   if (resType.data !== null && resType.data !== undefined && resType.data.tradeOid !== null && resType.data.tradeOid !== undefined) {
                getTemplatesByTradeName(resType.data?.tradeOid, QUESTION_STATUS, accessToken).then((res => {
                    if (res.data.questionTemplateData) {
                        let response = JSON.parse(res.data.questionTemplateData);
                        if (response !== null && response !== undefined) {
                            setResponse(response);
                            setFormDef(getMergedTv(response)?.FormDef);
                            setEventDef(getMergedTv(response)?.EventDef);
                            setTemplate(response?.SDM?.Building?.TemplateVersion);
                            setTemplateVersion(getActiveMetaDataVersionOID(response));
                            setMeasureUnit(response.SDM.Building.BasicDefinition);
                            setMethodDef(getMergedTv(response)?.MethodDef ?? []);
                            setProjectOID(response?.SDM?.Building?.OID);
                            setTradeVersion(res?.data?.version);
                            setMappings(getMergedTv(response)?.Mappings);

                            // Extract imageBaseUrl from template Images config
                            const templateImages = getMergedTv(response)?.Images;
                            if (templateImages && templateImages.length > 0 && templateImages[0].baseUrl) {
                                const baseUrl = templateImages[0].baseUrl;
                                setImageBaseUrl(baseUrl);
                            }
                        }
                    }
                }));
                //  }
            })
        }


        const validate = async (validate: any) => {

            let itemDefsList: ItemDef[] = [];
            if (validate !== undefined) {
                const data = validate;
                if (data !== undefined) {

                    data?.map((id: any) => {
                        assetitemGroup?.map((ig) => {
                            ig?.ItemRef?.map((ir) => {
                                if (ir.Mandatory === 'Yes') {
                                    assetitemDef?.map((i) => {
                                        if (i.OID === id) {

                                            itemDefsList.push(i);
                                            setItemList(itemDefsList);
                                        }

                                    })
                                }
                            })
                        })
                    })
                }

            }
            else {
                assetitemGroup?.map((ig) => {
                    ig?.ItemRef?.map((ir) => {
                        if (ir.Mandatory === 'Yes') {
                            assetitemDef?.map((i) => {
                                if (i.OID === ir.ItemOID) {

                                    itemDefsList.push(i);
                                    setItemList(itemDefsList);
                                }
                            })
                        }
                    })
                })


            }
        }


        const handleChange = (event: any, res: any, newValue: number) => {

            // Declare fresh arrays for each handleChange call to avoid data pollution between asset and trade
            let itemGroupDefList: ItemGroupDef[] = [];
            let itemDefList: ItemDef[] = [];
            let codeDefList: CodeList[] = [];
            let assetsCodeDefList: CodeList[] = [];

            setTabValue(newValue);
            let x: any;
            if (res !== null) {
                x = res;
            } else if (assetresponse !== null && assetresponse !== undefined) {
                x = assetresponse;
            }

            if (newValue === 0 &&
                x !== null && x !== undefined &&
                x?.SDM?.Building.OID === ASSET_OID
            ) {
                let index = x?.SDM?.Building?.TemplateVersion.length;


                x?.SDM?.Building?.TemplateVersion.map((tv: any, i: any) => {

                    let eventDef = tv?.EventDef?.filter((event: any) => event.OID === DEFAULT_EVENTOID);
                    if (eventDef !== null && eventDef !== undefined && eventDef.length > 0) {
                        let formDef = tv?.FormDef?.filter((form: any) => form.OID === DEFAULT_FORMOID);
                        if (formDef !== null && formDef !== undefined && formDef.length > 0) {

                            if (formDef[0] !== null && formDef[0] !== undefined) {
                                if (
                                    formDef[0]?.ItemGroupRef !== null && formDef[0]?.ItemGroupRef !== undefined &&
                                    formDef[0]?.ItemGroupRef?.length > 0
                                ) {

                                    formDef[0]?.ItemGroupRef?.map((i: any) => {
                                        let ItemGroupDef = tv?.ItemGroupDef?.filter((ig: any) => ig.OID === i.ItemGroupOID);

                                        ItemGroupDef?.map((ig: any) => {
                                            ig?.ItemRef?.map((it: any) => {
                                                let ItemDef = tv?.ItemDef?.filter((id: any) => id.OID === it.ItemOID);
                                                ItemDef?.map((id: any) => {
                                                    // if (!i.CollectionExceptionConditionOID) {

                                                    if (itemGroupDefList !== null && itemGroupDefList !== undefined &&
                                                        itemGroupDefList.length !== 0
                                                    ) {
                                                        const index = itemGroupDefList?.findIndex((igd: any) => igd.OID === ig.OID);
                                                        if (index !== -1) {
                                                            const itd = [...itemGroupDefList[index].ItemRef];
                                                            const ind = itd.findIndex((obj: any) => obj.ItemOID === it.ItemOID);
                                                            if (ind !== -1) {
                                                                itd[ind] = it;
                                                            } else {
                                                                itd.push(it);
                                                            }
                                                            itemGroupDefList[index] = {
                                                                Description: itemGroupDefList[index]?.Description,
                                                                ItemRef: itd,
                                                                OID: itemGroupDefList[index]?.OID,
                                                                Name: itemGroupDefList[index]?.Name,
                                                                Repeating: itemGroupDefList[index]?.Repeating,
                                                                ItemDef: [],
                                                            };

                                                        } else {
                                                            itemGroupDefList.push({
                                                                Description: ig?.Description,
                                                                ItemRef: [it],
                                                                OID: ig?.OID,
                                                                Name: ig?.Name,
                                                                Repeating: ig?.Repeating,
                                                                ItemDef: [],
                                                            });
                                                        }
                                                        // setAssetItemGroup will be called after loop completes

                                                    } else {
                                                        itemGroupDefList.push({
                                                            Description: ig?.Description,
                                                            ItemRef: [it],
                                                            OID: ig?.OID,
                                                            Name: ig?.Name,
                                                            Repeating: ig?.Repeating,
                                                            ItemDef: [],
                                                        });
                                                        // setAssetItemGroup will be called after loop completes
                                                    }
                                                    // setAssetItemGroup will be called after loop completes
                                                    //}


                                                });
                                            });
                                        });

                                        if (ItemGroupDef !== null && ItemGroupDef !== undefined) {
                                            ItemGroupDef.map((ig: any) => {

                                                ig?.ItemRef?.map((it: any) => {

                                                    let ItemDef = tv?.ItemDef?.find((id: any) => id.OID === it.ItemOID);

                                                    if (ItemDef !== null && ItemDef !== undefined) {

                                                        // if (it.CollectionExceptionConditionOID) {
                                                        //     let xy = itemAssetGroupData?.find((igd: any) => igd.ItemGroupOID === ig?.OID);
                                                        //     let gf = xy?.ItemData?.find((id: any) => id.ItemOID === ItemDef?.OID);
                                                        //     if (gf !== null && gf !== undefined) {
                                                        //         if (itemDefList.length !== 0) {
                                                        //             const index = itemDefList?.findIndex(
                                                        //                 (ig: any) => ig.OID === it.ItemOID
                                                        //             );
                                                        //             const updatedItemDef = [...itemDefList];
                                                        //             if (index !== -1) {
                                                        //                 updatedItemDef[index] = ItemDef;
                                                        //             } else {
                                                        //                 updatedItemDef.push(ItemDef);
                                                        //             }
                                                        //             setAssetItemDef(updatedItemDef);
                                                        //         } else {
                                                        //             itemDefList.push(ItemDef);
                                                        //             setAssetItemDef(itemDefList);
                                                        //         }
                                                        //     }
                                                        // } else {
                                                        ItemDef.ItemGroupRepeatKey = 0;
                                                        if (itemDefList.length !== 0) {
                                                            const index = itemDefList?.findIndex((ig: any) => ig.OID === it.ItemOID);
                                                            const updatedItemDef = [...itemDefList];
                                                            if (index !== -1) {
                                                                updatedItemDef[index] = ItemDef;
                                                                itemDefList[index] = ItemDef;
                                                            } else {
                                                                updatedItemDef.push(ItemDef);
                                                                itemDefList.push(ItemDef);
                                                            }
                                                            if (ItemDef?.CodeListRef !== null && ItemDef?.CodeListRef !== undefined) {
                                                                let codeList = tv?.CodeList?.find((cl: any) => cl.OID === ItemDef?.CodeListRef?.CodeListOID);
                                                                if (codeList !== null && codeList !== undefined) {
                                                                    codeList.ItemGroupRepeatKey = 0;
                                                                    let codeLstIndex = assetsCodeDefList.findIndex(x => x.OID === codeList.OID && x.ItemGroupRepeatKey === 0);
                                                                    if (codeLstIndex === -1) {
                                                                        assetsCodeDefList.push(codeList);
                                                                    }
                                                                }
                                                            }
                                                            // setAssetItemDef will be called after loop completes
                                                        }
                                                        else {
                                                            itemDefList.push(ItemDef);
                                                            if (ItemDef?.CodeListRef !== null && ItemDef?.CodeListRef !== undefined) {
                                                                let codeList = tv?.CodeList?.find((cl: any) => cl.OID === ItemDef?.CodeListRef?.CodeListOID);
                                                                if (codeList !== null && codeList !== undefined) {
                                                                    codeList.ItemGroupRepeatKey = 0;
                                                                    let codeLstIndex = assetsCodeDefList.findIndex(x => x.OID === codeList.OID && x.ItemGroupRepeatKey === 0);
                                                                    if (codeLstIndex === -1) {
                                                                        assetsCodeDefList.push(codeList);
                                                                    }
                                                                }
                                                            }
                                                            // setAssetItemDef will be called after loop completes
                                                        }
                                                        //}
                                                    }

                                                    // if (ItemDef?.CodeListRef !== null && ItemDef?.CodeListRef !== undefined) {
                                                    //     let codeList = tv?.CodeList?.find(
                                                    //         (cl: any) =>
                                                    //             cl.OID === ItemDef?.CodeListRef?.CodeListOID
                                                    //     );
                                                    //     if (codeList !== null && codeList !== undefined) {
                                                    //         if (codeDefList.length !== 0) {
                                                    //             const index = codeDefList?.findIndex(
                                                    //                 (ig: any) => ig.OID === codeList.OID
                                                    //             );

                                                    //             if (index !== -1) {
                                                    //                 codeDefList[index] = codeList;
                                                    //             } else {
                                                    //                 codeDefList.push(codeList);
                                                    //             }
                                                    //         } else {
                                                    //             codeDefList.push(codeList);
                                                    //         }
                                                    //     }
                                                    // }
                                                });
                                            });
                                            // setAssetCodeList will be called after loop completes
                                        }


                                    });

                                    // After processing all ItemGroups, set the state
                                    setAssetItemGroup(itemGroupDefList);
                                    setAssetItemDef(itemDefList);
                                    setAssetCodeList(assetsCodeDefList);
                                } else {
                                    setAssetItemGroup(itemGroupDefList);
                                    setAssetItemDef(itemDefList);
                                    setAssetCodeList(codeDefList);
                                }
                            }
                            setAssetConditionDef(tv?.ConditionDef);
                        }
                    }
                });
            }
            else if (
                newValue === 1 &&
                response !== null && response !== undefined &&
                response?.SDM?.Building.OID.includes("P")
            ) {

                let index = response?.SDM?.Building?.TemplateVersion.length;
                response?.SDM?.Building?.TemplateVersion.reverse().map((tv: any, i: any) => {

                    let eventDef = tv?.EventDef?.filter((event: any) => event.OID === DEFAULT_EVENTOID);

                    if (eventDef !== null && eventDef !== undefined && eventDef.length > 0) {

                        let formDef = tv?.FormDef?.filter((form: any) => form.OID === DEFAULT_FORMOID);

                        if (formDef !== null && formDef !== undefined && formDef.length > 0) {
                            if (formDef[0] !== null && formDef[0] !== undefined) {
                                if (formDef[0]?.ItemGroupRef !== null && formDef[0]?.ItemGroupRef !== undefined && formDef[0]?.ItemGroupRef?.length > 0) {
                                    formDef[0]?.ItemGroupRef?.map((i: any) => {

                                        let ItemGroupDef = tv?.ItemGroupDef?.filter((ig: any) => ig.OID === i.ItemGroupOID);
                                        ItemGroupDef?.map((ig: any) => {
                                            ig?.ItemRef?.map((it: any) => {
                                                let ItemDef = tv?.ItemDef?.filter((id: any) => id.OID === it.ItemOID);
                                                ItemDef?.map((id: any) => {
                                                    // if (!i.CollectionExceptionConditionOID && !i.MethodOID) {

                                                    if ((itemGroupDefList !== null && itemGroupDefList !== undefined) && itemGroupDefList.length !== 0) {
                                                        const index = itemGroupDefList?.findIndex((igd: any) => igd.OID === ig.OID);
                                                        let updatedGroupDef = [...itemGroupDefList];

                                                        if (index !== -1) {
                                                            const itd = [...updatedGroupDef[index].ItemRef];

                                                            const ind = itd.findIndex((obj: any) => obj.ItemOID === id.OID);
                                                            if (ind !== -1) {
                                                                itd[ind] = it;
                                                            } else {
                                                                itd.push(it);
                                                            }
                                                            updatedGroupDef[index] = {
                                                                Description: updatedGroupDef[index]?.Description,
                                                                ItemRef: itd,
                                                                OID: updatedGroupDef[index]?.OID,
                                                                Name: updatedGroupDef[index]?.Name,
                                                                Repeating: updatedGroupDef[index]?.Repeating,
                                                                ItemDef: []
                                                            };
                                                        }
                                                        else {
                                                            updatedGroupDef.push({
                                                                Description: ig?.Description,
                                                                ItemRef: [it],
                                                                OID: ig?.OID,
                                                                Name: ig?.Name,
                                                                Repeating: ig?.Repeating,
                                                                ItemDef: []
                                                            });
                                                        }
                                                        //need to use updatedGroupDef....

                                                        //setItemGroup(itemGroupDefList);

                                                        itemGroupDefList = updatedGroupDef;
                                                        // setItemGroup(updatedGroupDef);
                                                    }
                                                    else {
                                                        itemGroupDefList.push(ig);

                                                        // setItemGroup(itemGroupDefList);
                                                    }
                                                    //can be moved to line no 1046...
                                                    // setItemGroup will be called after loop completes
                                                    //}

                                                })
                                            })
                                        })

                                        if (ItemGroupDef !== null && ItemGroupDef !== undefined) {

                                            ItemGroupDef.map((ig: any) => {
                                                ig?.ItemRef?.map((it: any) => {
                                                    let ItemDef = tv?.ItemDef?.find((id: any) => id.OID === it.ItemOID);
                                                    // not adding the repeat Item Group related itemDef...
                                                    //To Do: Add ItemDef only when the value related to it is present...
                                                    if (ItemDef !== null && ItemDef !== undefined) {
                                                        if (ig.Repeating !== "Yes") {
                                                            // if (it.CollectionExceptionConditionOID) {
                                                            //     let igdata = itemGroupData?.find((igd: any) => igd.ItemGroupOID === ig?.OID);
                                                            //     let iData = igdata?.ItemData?.find((id: any) => id.ItemOID === ItemDef?.OID);

                                                            //     if (iData !== null && iData !== undefined) {

                                                            //         if (itemDefList.length !== 0) {
                                                            //             const index = itemDefList?.findIndex((ig: any) => ig.OID === it.ItemOID);
                                                            //             const updatedItemDef = [...itemDefList];
                                                            //             if (index !== -1) {
                                                            //                 updatedItemDef[index] = ItemDef;
                                                            //             } else {
                                                            //                 updatedItemDef.push(ItemDef);
                                                            //             }
                                                            //             if (ItemDef?.CodeListRef !== null && ItemDef?.CodeListRef !== undefined) {
                                                            //                 let codelist = tv?.CodeList?.find((cl: any) => cl.OID === ItemDef?.CodeListRef?.CodeListOID);
                                                            //                 if (codelist !== null && codelist !== undefined) {
                                                            //                     codelist.ItemGroupRepeatKey = 0;
                                                            //                     codeDefList.push(codelist);
                                                            //                 }
                                                            //             }
                                                            //             setItemDef(updatedItemDef);
                                                            //         }
                                                            //         else {
                                                            //             itemDefList.push(ItemDef);
                                                            //             if (ItemDef?.CodeListRef !== null && ItemDef?.CodeListRef !== undefined) {
                                                            //                 let codeList = tv?.CodeList?.find((cl: any) => cl.OID === ItemDef?.CodeListRef?.CodeListOID);
                                                            //                 if (codeList !== null && codeList !== undefined) {
                                                            //                     codeList.ItemGroupRepeatKey = 0;
                                                            //                     codeDefList.push(codeList);
                                                            //                 }
                                                            //             }
                                                            //             setItemDef(itemDefList);
                                                            //         }
                                                            //     }
                                                            // }
                                                            //else {
                                                            ItemDef.ItemGroupRepeatKey = 0;
                                                            if (itemDefList.length !== 0) {
                                                                const index = itemDefList?.findIndex((ig: any) => ig.OID === it.ItemOID);
                                                                const updatedItemDef = [...itemDefList];
                                                                if (index !== -1) {
                                                                    updatedItemDef[index] = ItemDef;
                                                                    itemDefList[index] = ItemDef;
                                                                } else {
                                                                    updatedItemDef.push(ItemDef);
                                                                    itemDefList.push(ItemDef);
                                                                }

                                                                if (ItemDef?.CodeListRef !== null && ItemDef?.CodeListRef !== undefined) {
                                                                    let codeList = tv?.CodeList?.find((cl: any) => cl.OID === ItemDef?.CodeListRef?.CodeListOID);
                                                                    if (codeList !== null && codeList !== undefined) {
                                                                        codeList.ItemGroupRepeatKey = 0;
                                                                        let codeLstIndex = codeDefList.findIndex(x => x.OID === codeList.OID && x.ItemGroupRepeatKey === 0);
                                                                        if (codeLstIndex === -1) {
                                                                            codeDefList.push(codeList);
                                                                        }
                                                                    }
                                                                }
                                                                // setItemDef will be called after loop completes
                                                            }
                                                            else {
                                                                itemDefList.push(ItemDef);
                                                                if (ItemDef?.CodeListRef !== null && ItemDef?.CodeListRef !== undefined) {
                                                                    let codeList = tv?.CodeList?.find((cl: any) => cl.OID === ItemDef?.CodeListRef?.CodeListOID);
                                                                    if (codeList !== null && codeList !== undefined) {
                                                                        codeList.ItemGroupRepeatKey = 0;
                                                                        let codeLstIndex = codeDefList.findIndex(x => x.OID === codeList.OID && x.ItemGroupRepeatKey === 0);
                                                                        if (codeLstIndex === -1) {
                                                                            codeDefList.push(codeList);
                                                                        }
                                                                    }
                                                                }
                                                                // setItemDef will be called after loop completes
                                                            }
                                                            // }
                                                        }
                                                        else {
                                                            // to handle repeat scenario...
                                                            if (it.CollectionExceptionConditionOID) {
                                                                let igData = itemGroupData?.find((igd: any) => igd.ItemGroupOID === ig?.OID);
                                                                let iData = igData?.ItemData?.find((id: any) => id.ItemOID === ItemDef?.OID);

                                                                if (iData !== null && iData !== undefined) {

                                                                    if (itemDefList.length !== 0) {
                                                                        const index = itemDefList?.findIndex((ig: any) => ig.OID === it.ItemOID);
                                                                        const updatedItemDef = [...itemDefList];
                                                                        if (index !== -1) {
                                                                            ItemDef.ItemGroupRepeatKey = 1;
                                                                            updatedItemDef[index] = ItemDef;
                                                                        } else {
                                                                            ItemDef.ItemGroupRepeatKey = 1;
                                                                            updatedItemDef.push(ItemDef);
                                                                        }
                                                                        if (ItemDef?.CodeListRef !== null && ItemDef?.CodeListRef !== undefined) {
                                                                            let codeList = tv?.CodeList?.find((cl: any) => cl.OID === ItemDef?.CodeListRef?.CodeListOID);
                                                                            if (codeList != null && codeList !== undefined) {
                                                                                codeList.ItemGroupRepeatKey = 1;
                                                                                let codeLstIndex = codeDefList.findIndex(x => x.OID === codeList.OID && x.ItemGroupRepeatKey === 1);
                                                                                if (codeLstIndex === -1) {
                                                                                    codeDefList.push(codeList);
                                                                                }
                                                                                // codeDefList.push(codeList);
                                                                            }
                                                                        }
                                                                        // setItemDef will be called after loop completes
                                                                    }
                                                                    else {
                                                                        itemDefList.push(ItemDef);
                                                                        if (ItemDef?.CodeListRef !== null && ItemDef?.CodeListRef !== undefined) {
                                                                            let codeList = tv?.CodeList?.find((cl: any) => cl.OID === ItemDef?.CodeListRef?.CodeListOID);
                                                                            if (codeList != null && codeList !== undefined) {
                                                                                codeList.ItemGroupRepeatKey = 1;
                                                                                let codeLstIndex = codeDefList.findIndex(x => x.OID === codeList.OID && x.ItemGroupRepeatKey === 1);
                                                                                if (codeLstIndex === -1) {
                                                                                    codeDefList.push(codeList);
                                                                                }
                                                                                //codeDefList.push(codeList);
                                                                            }
                                                                        }
                                                                        // setItemDef will be called after loop completes
                                                                    }
                                                                }
                                                            }
                                                            else {
                                                                if (itemDefList.length !== 0) {
                                                                    const index = itemDefList?.findIndex((ig: any) => ig.OID === it.ItemOID);
                                                                    const updatedItemDef = [...itemDefList];
                                                                    if (index !== -1) {
                                                                        ItemDef.ItemGroupRepeatKey = 1;
                                                                        updatedItemDef[index] = ItemDef;
                                                                        itemDefList[index] = ItemDef;
                                                                    } else {
                                                                        ItemDef.ItemGroupRepeatKey = 1;
                                                                        updatedItemDef.push(ItemDef);
                                                                        itemDefList.push(ItemDef);
                                                                    }

                                                                    if (ItemDef?.CodeListRef !== null && ItemDef?.CodeListRef !== undefined) {
                                                                        let codeList = tv?.CodeList?.find((cl: any) => cl.OID === ItemDef?.CodeListRef?.CodeListOID);
                                                                        if (codeList !== null && codeList !== undefined) {
                                                                            codeList.ItemGroupRepeatKey = 1;
                                                                            let codeLstIndex = codeDefList.findIndex(x => x.OID === codeList.OID && x.ItemGroupRepeatKey === 1);
                                                                            if (codeLstIndex === -1) {
                                                                                codeDefList.push(codeList);
                                                                            }
                                                                            //codeDefList.push(codeList);
                                                                        }
                                                                    }
                                                                    // setItemDef will be called after loop completes
                                                                }
                                                                else {
                                                                    itemDefList.push(ItemDef);
                                                                    if (ItemDef?.CodeListRef !== null && ItemDef?.CodeListRef !== undefined) {
                                                                        let codeList = tv?.CodeList?.find((cl: any) => cl.OID === ItemDef?.CodeListRef?.CodeListOID);
                                                                        if (codeList !== null && codeList !== undefined) {
                                                                            codeList.ItemGroupRepeatKey = 1;
                                                                            let codeLstIndex = codeDefList.findIndex(x => x.OID === codeList.OID && x.ItemGroupRepeatKey === 1);
                                                                            if (codeLstIndex === -1) {
                                                                                codeDefList.push(codeList);
                                                                            }
                                                                            //codeDefList.push(codeList);
                                                                        }
                                                                    }
                                                                    // setItemDef will be called after loop completes
                                                                }
                                                            }
                                                        }
                                                    }


                                                })
                                            })
                                        }

                                        // setCodeList will be called after loop completes

                                    })

                                    //setItemGroup(itemGroupDefList);
                                }
                                // else {
                                //     setItemGroup(itemGroupDefList);
                                //     setItemDef(itemDefList);
                                //     setCodeList(codeDefList);
                                // }
                            }

                            // Set all state after loop completes (similar to asset section fix)
                            setItemGroup(itemGroupDefList);
                            setItemDef(itemDefList);
                            setCodeList(codeDefList);
                            setConditionDef(tv?.ConditionDef);

                        }
                    }
                })

            }
        }

        //To prepare FormData
        function handleState(itemGroupData: any) {


            if (itemGroupData?.TradeOID.includes('P')) {
                let formDataArray: FormData[] = [...formData];
                let updatedTradeCodeList = JSON.parse(JSON.stringify(codeList));
                if (itemGroupData?.ItemGroup?.length !== 0) {
                    formDef?.map((form: any) => {
                        //const updateFormData = [...formDataArray];//need to chnage to formDataArray...
                        form?.ItemGroupRef?.map((ir) => {
                            itemGroupData?.ItemGroup?.map((igd: any) => {
                                if (ir.ItemGroupOID === igd.ItemGroupOID) {
                                    if (formDataArray.length !== 0) {
                                        const index = formDataArray.findIndex((obj) => obj.FormOID === form.OID);

                                        if (index !== -1) {
                                            const frdt = [...formDataArray[index].ItemGroupData];
                                            const ind = frdt.findIndex((obj) => obj.ItemGroupOID === igd.ItemGroupOID && obj.ItemGroupRepeatKey === igd.ItemGroupRepeatKey);
                                            if (ind !== -1) {
                                                frdt[ind] = igd;
                                            } else {
                                                frdt.push(igd)
                                            }
                                            formDataArray[index] = { ItemGroupData: frdt, FormOID: formDataArray[index].FormOID, TransactionType: "Insert" };
                                        } else {
                                            formDataArray.push({ ItemGroupData: [igd], FormOID: form.OID, TransactionType: "Insert" });
                                        }
                                        // setFormData(updateFormData);
                                        // handleEventData(updateFormData, itemGroupData.TradeOID);
                                        // setItemGroupData(updateFormData[0].ItemGroupData);
                                    } else {
                                        formDataArray.push({ ItemGroupData: [igd], FormOID: form.OID, TransactionType: "Insert" });
                                    }



                                    if ((codeList !== null || undefined) && codeList.length > 0) {
                                        igd?.ItemData?.map((item: any) => {
                                            if ((item.CodeList !== null || undefined) && item.CodeList.length > 0) {
                                                let codeListOID = item.CodeList[0].OID;
                                                const index = codeList.findIndex((obj) => obj.OID === codeListOID && obj.ItemGroupRepeatKey === item.ItemGroupRepeatKey);
                                                updatedTradeCodeList[index] = JSON.parse(JSON.stringify(item.CodeList[0]));
                                            }
                                        })
                                        setCodeList(updatedTradeCodeList);
                                    }
                                }
                            })
                        })
                    })
                    setFormData(formDataArray);
                    handleEventData(formDataArray, itemGroupData.TradeOID);

                    // CRITICAL FIX: Collect ALL ItemGroupData from ALL FormData entries, not just the first one
                    // This preserves the multi-ItemGroup structure according to ODM standard
                    const allItemGroupData: ItemGroupData[] = [];
                    formDataArray.forEach((formDataEntry: FormData) => {
                        if (formDataEntry.ItemGroupData && Array.isArray(formDataEntry.ItemGroupData)) {
                            allItemGroupData.push(...formDataEntry.ItemGroupData);
                        }
                    });


                    setItemGroupData(allItemGroupData);
                }
            } else {
                let formDataArray: FormData[] = [...assetFormData];
                let updatedAssetsCodeList = JSON.parse(JSON.stringify(assetcodeList));
                setAssetDataValidation(itemGroupData?.ItemGroup)
                if (itemGroupData?.ItemGroup?.length !== 0) {
                    assetformDef?.map((form: any) => {
                        form?.ItemGroupRef?.map((ir) => {
                            itemGroupData?.ItemGroup?.map((igd: any) => {
                                if (ir.ItemGroupOID === igd.ItemGroupOID) {
                                    // if (formDataArray.length !== 0) {
                                    //     const index = formDataArray.findIndex((obj) => obj.FormOID === form.OID);
                                    //     const updateFormData = [...formData];

                                    //     if (index !== -1) {
                                    //         const frdt = [...updateFormData[index].ItemGroupData];
                                    //         const ind = frdt.findIndex((obj) => obj.ItemGroupOID === igd.ItemGroupOID);
                                    //         if (ind !== -1) {
                                    //             frdt[ind] = igd;
                                    //         } else {
                                    //             frdt.push(igd)
                                    //         }
                                    //         updateFormData[index] = { ItemGroupData: frdt, FormOID: updateFormData[index].FormOID, TransactionType: "Insert" };

                                    //     } else {
                                    //         updateFormData.push({ ItemGroupData: [igd], FormOID: form.OID, TransactionType: "Insert" });

                                    //     }
                                    //     setFormData(updateFormData);


                                    //     handleEventData(updateFormData, itemGroupData.TradeOID);
                                    // } else {
                                    //     formDataArray.push({ ItemGroupData: [igd], FormOID: form.OID, TransactionType: "Insert" });
                                    //     setFormData(formDataArray);
                                    //     handleEventData(formDataArray, itemGroupData.TradeOID);
                                    // }

                                    if (formDataArray.length !== 0) {
                                        const index = formDataArray.findIndex((obj) => obj.FormOID === form.OID);

                                        if (index !== -1) {
                                            const frdt = [...formDataArray[index].ItemGroupData];
                                            const ind = frdt.findIndex((obj) => obj.ItemGroupOID === igd.ItemGroupOID && obj.ItemGroupRepeatKey === igd.ItemGroupRepeatKey);
                                            if (ind !== -1) {
                                                frdt[ind] = igd;
                                            } else {
                                                frdt.push(igd)
                                            }
                                            formDataArray[index] = { ItemGroupData: frdt, FormOID: formDataArray[index].FormOID, TransactionType: "Insert" };
                                        } else {
                                            formDataArray.push({ ItemGroupData: [igd], FormOID: form.OID, TransactionType: "Insert" });
                                        }
                                    } else {
                                        formDataArray.push({ ItemGroupData: [igd], FormOID: form.OID, TransactionType: "Insert" });
                                    }

                                    if ((assetcodeList !== null || undefined) && assetcodeList.length > 0) {
                                        igd?.ItemData?.map((item: any) => {
                                            if (item.CodeList !== null && item.CodeList !== undefined && item.CodeList.length > 0) {
                                                let codeListOID = item.CodeList[0].OID;
                                                const index = assetcodeList.findIndex((obj) => obj.OID === codeListOID && obj.ItemGroupRepeatKey === item.ItemGroupRepeatKey);
                                                //To set the itemGroupRepeatKey in CodeList for the older data of assets if not set...
                                                item.CodeList[0].ItemGroupRepeatKey = item.ItemGroupRepeatKey;
                                                updatedAssetsCodeList[index] = JSON.parse(JSON.stringify(item.CodeList[0]));
                                            }
                                        })
                                        setAssetCodeList(updatedAssetsCodeList);
                                    }
                                }
                            })
                        })
                    })
                    setAssetFormData(formDataArray);
                    handleEventData(formDataArray, itemGroupData.TradeOID);

                    // CRITICAL FIX: Collect ALL ItemGroupData from ALL FormData entries, not just the first one
                    // This preserves the multi-ItemGroup structure according to ODM standard
                    const allItemGroupData: ItemGroupData[] = [];
                    formDataArray.forEach((formDataEntry: FormData) => {
                        if (formDataEntry.ItemGroupData && Array.isArray(formDataEntry.ItemGroupData)) {
                            allItemGroupData.push(...formDataEntry.ItemGroupData);
                        }
                    });


                    setItemAssetGroupData(allItemGroupData);
                }
            }
        }


        //To prepare EventData
        function handleEventData(formData: any, projectOID: string) {
            let eventDataArray: EventData[] = [];
            if (projectOID.includes('P')) {
                eventDef?.map((event: any) => {
                    event?.FormRef.map((fr: any) => {
                        formData.map((fd: any) => {
                            if (fr.FormOID === fd.FormOID) {
                                if (eventData.length !== 0) {
                                    const index = eventData.findIndex((obj) => obj.EventOID === event.OID);
                                    const updateEventData = [...eventData];
                                    if (index !== -1) {
                                        const etdt = [...updateEventData[index].FormData];
                                        const ind = etdt.findIndex((obj) => obj.FormOID === fd.FormOID);
                                        if (ind !== -1) {
                                            etdt[ind] = fd;
                                        } else {
                                            etdt.push(fd);
                                        }
                                        updateEventData[index] = { FormData: etdt, EventOID: updateEventData[index].EventOID }
                                    } else {
                                        updateEventData.push({ FormData: [fd], EventOID: event.OID });
                                    }
                                    setEventData(updateEventData);
                                    handleProjectData(updateEventData, projectOID);
                                } else {
                                    eventDataArray.push({ FormData: [fd], EventOID: event.OID });
                                    setEventData(eventDataArray);
                                    handleProjectData(eventDataArray, projectOID);
                                }
                            }
                        })
                    })
                })
            } else {
                asseteventDef?.map((event: any) => {
                    event?.FormRef.map((fr: any) => {
                        formData.map((fd: any) => {
                            if (fr.FormOID === fd.FormOID) {
                                if (assetEventData.length !== 0) {
                                    const index = assetEventData.findIndex((obj) => obj.EventOID === event.OID);
                                    const updateEventData = [...assetEventData];
                                    if (index !== -1) {
                                        const etdt = [...updateEventData[index].FormData];
                                        const ind = etdt.findIndex((obj) => obj.FormOID === fd.FormOID);
                                        if (ind !== -1) {
                                            etdt[ind] = fd;
                                        } else {
                                            etdt.push(fd);
                                        }
                                        updateEventData[index] = { FormData: etdt, EventOID: updateEventData[index].EventOID }
                                    } else {
                                        updateEventData.push({ FormData: [fd], EventOID: event.OID });
                                    }
                                    setAssetEventData(updateEventData);
                                    handleProjectData(updateEventData, projectOID);
                                } else {
                                    eventDataArray.push({ FormData: [fd], EventOID: event.OID });
                                    setAssetEventData(eventDataArray);
                                    handleProjectData(eventDataArray, projectOID);
                                }
                            }
                        })
                    })
                })
            }
        }

        function handleProjectData(eventData: any, projectOID: string) {

            if (projectOID.includes('P')) {
                let tempprojectData: BuildingData = { EventData: eventData, OID: projectOID, Key: "123", MetaDataVersionOID: templateVersion };
                setProjectData(tempprojectData);
            } else {
                let tempprojectData: BuildingData = { EventData: eventData, OID: projectOID, Key: "123", MetaDataVersionOID: aasettemplateVersion };
                setAssetData(tempprojectData);
            }
        }

        // Old useImperativeHandle moved to consolidated one earlier - this code is referenced in the consolidated implementation

        function showGroup(item: any) {
            let ItemGroupDef: any;
            if (item.TradeOID.includes('P')) {
                if (item.status === true) {
                    response?.SDM?.Building?.TemplateVersion.map((tv: any, i: any) => {
                        ItemGroupDef = tv?.ItemGroupDef?.find((ig: any) => ig.OID === item.GroupOID);
                        if (ItemGroupDef !== undefined) {
                            let index = tv?.ItemGroupDef?.findIndex((id: any) => id.OID === item.GroupOID);

                            if (item.IsRepeat && (item.RepeatItemData !== null && item.RepeatItemData !== undefined && item.RepeatItemData > 0)) {
                                //  check the itemGroupDef count with the id given in the itemGroup array before adding to it.
                                const total = itemGroup.filter((g: any) => g.OID === item.GroupOID).length;
                                const difference = item.RepeatItemData - total;


                                if (difference > 0) {

                                    //To add difference of itemGroupDef to the itemGroup array
                                    for (let i = 1; i <= difference; i++) {
                                        itemGroup.splice(index, 0, ItemGroupDef);
                                    }

                                    //To add itemDef to the itemDef array based on repeatItemData value.
                                    for (let i = 1; i <= item.RepeatItemData; i++) {
                                        for (let ind = 0; ind < ItemGroupDef.ItemRef.length; ind++) {
                                            const ir = ItemGroupDef.ItemRef[ind];
                                            // Check if itemDef with same id and ItemGrpIndex already exists
                                            const alreadyExists = itemDef.some(
                                                (def: any) => def.id === ir.ItemOID && def.ItemGrpIndex === i
                                            );
                                            if (alreadyExists) {
                                                break;
                                            }
                                            let itemDefObj = {
                                                id: ir.ItemOID,
                                                indexes: index,
                                                status: item.status,
                                                GroupOID: ItemGroupDef.OID,
                                                TradeOID: item.TradeOID,
                                                ItemGrpIndex: i
                                            };
                                            showQus(itemDefObj);

                                        }
                                    }

                                } else if (difference < 0) {//need to check
                                    let countToRemove = Math.abs(difference);
                                    let found = 0;

                                    const updatedGroup = itemGroup.filter((g: any) => {
                                        if (g.OID === item.GroupOID && found < countToRemove) {
                                            found++;
                                            return false;
                                        }
                                        return true;
                                    });

                                    setItemGroup(updatedGroup);
                                    //return;
                                }

                            } else if (itemGroup.length > 0 && itemGroup !== null && itemGroup !== undefined && itemGroup?.find((i: any) => i.OID === item.GroupOID)) {

                                //itemGroup.splice(index, 0, ItemGroupDef);
                            }
                            else {
                                itemGroup.splice(index, 0, ItemGroupDef);
                                // Sort by name in alphabetical order
                                itemGroup.sort((a, b) => a.OID.localeCompare(b.OID));
                            }
                            setItemGroup(itemGroup);
                        }
                    })
                }
                else {
                    let ItemGroupDef = itemGroup?.find((id: any) => id.OID === item.GroupOID);
                    if (ItemGroupDef !== null && ItemGroupDef !== undefined) {

                        if (item.IsRepeat) {
                            //removing the repeat itemGroup from the itemGroup array is ok for now but need to optimixze in future.
                            //need to remove itemGroupData too.
                            //need to clear the codelist... Need to refactor codeList implementation...
                            const repeatItemGrpData = itemGroupData.filter((group: any) => group.OID === item.GroupOID);
                            let tempCodeList = [...codeList];
                            repeatItemGrpData.forEach((itemGrpp: any) => {
                                itemGrpp.ItemData.forEach((iData: any) => {
                                    const index = tempCodeList.findIndex((code: any) => code.OID === iData.CodeList[0].OID && code.ItemGroupRepeatKey === item.ItemGrpIndex);
                                    if (index !== -1) {
                                        tempCodeList.splice(index, 1);
                                    }
                                });
                            });
                            setCodeList(tempCodeList);

                            const tempItemGrp = itemGroup.filter((group: any) => group.OID !== item.GroupOID); // item grp without repeat
                            setItemGroup(tempItemGrp);
                            let tempItemGrData = itemGroupData.filter(x => x.ItemGroupOID !== item.GroupOID);// item grp data without repeat
                            setItemGroupData([...tempItemGrData]);
                            setFormData(formData.filter((form: any) => form.ItemGroupData.every((igd: any) => igd.ItemGroupOID !== item.GroupOID)));


                        } else {
                            if (ItemGroupDef.Repeating === "Yes") {
                                let itemGrpLength = itemGroup.filter((e) => e.OID == item.GroupOID).length;
                                for (let i = 0; i < itemGrpLength; i++) {
                                    let index = itemGroup?.findIndex((id: any) => id.OID === item.GroupOID);
                                    if (itemGroup !== undefined) {
                                        itemGroup.splice(index, 1);
                                    }
                                }
                                setItemGroup(itemGroup);

                            } else {
                                let index = itemGroup?.findIndex((id: any) => id.OID === item.GroupOID);
                                if (itemGroup !== undefined) {
                                    itemGroup.splice(index, 1);
                                    setItemGroup(itemGroup);
                                }
                            }
                        }

                        // else if ((item.RepeatItemData !== null && item.RepeatItemData !== undefined && item.RepeatItemData == 0)) {
                        //     for (let i = 0; i < (item.RepeatItemData); i++) {
                        //         itemGroup.splice(index, 0, ItemGroupDef);
                        //     }
                        // }
                    }
                }

            } else {
                if (item.status === true) {
                    let ItemGroupDef = assetresponse?.SDM?.Building?.TemplateVersion?.ItemGroupDef?.find((id: any) => id.OID === item.GroupOID);
                    if (ItemGroupDef !== null && ItemGroupDef !== undefined) {
                        let index = assetresponse?.SDM?.Building?.TemplateVersion?.ItemDef?.findIndex((id: any) => id.OID === item.GroupOID);
                        if (assetitemGroup.length > 0 && assetitemGroup !== null && assetitemGroup !== undefined && assetitemGroup.find((i: any) => i.OID === item.GroupOID)) {
                            assetitemGroup.splice(index, 0, ItemGroupDef);
                        } else {
                            assetitemGroup.splice(index, 0, ItemGroupDef);
                        }
                        setAssetItemGroup(assetitemGroup);
                    }
                } else {
                    let ItemDef = assetitemGroup?.find((id: any) => id.OID === item.GroupOID);
                    if (ItemDef !== null && ItemDef !== undefined) {
                        let index = assetitemGroup?.findIndex((id: any) => id.OID === item.GroupOID);
                        assetitemGroup.splice(index, 1);
                        setAssetItemGroup(assetitemGroup);
                    }
                }
            }
        }

        // Conditional Rendering of Questions
        function showQus(item: any) {
            let ItemDef: any;
            const i = response?.SDM?.Building?.TemplateVersion.length;
            if (item.TradeOID.includes('P')) {
                if (item.status === true) {
                    response?.SDM?.Building?.TemplateVersion.reverse().map((tv: any, i: any) => {
                        if (tv?.ItemDef !== null && tv?.ItemDef !== undefined && tv?.ItemDef.length > 0) {
                            ItemDef = JSON.parse(JSON.stringify(tv?.ItemDef?.find((id: any) => id.OID === item.id)));
                            ItemDef.ItemGroupRepeatKey = item.ItemGrpIndex;
                            if (ItemDef !== null && ItemDef !== undefined) {
                                let index = tv?.ItemDef?.findIndex((id: any) => id.OID === item.id);
                                if (itemDef.length > 0 && itemDef !== null && itemDef !== undefined && itemDef.find((i: any) => i.OID === item.id && i.ItemGroupRepeatKey === item.ItemGrpIndex)) {
                                    // itemDef.splice(index, 0, ItemDef);
                                    if (ItemDef?.CodeListRef !== null && ItemDef?.CodeListRef !== undefined) {
                                        let templateCodeList = tv?.CodeList?.find((cl: any) => cl.OID === ItemDef?.CodeListRef?.CodeListOID);
                                        let tempCopyCodeList = JSON.parse(JSON.stringify(templateCodeList));
                                        if (tempCopyCodeList !== null && tempCopyCodeList !== undefined) {
                                            let codeDefList = codeList;
                                            tempCopyCodeList.ItemGroupRepeatKey = item.ItemGrpIndex;
                                            let codeLstIndex = codeDefList.findIndex(x => x.OID === tempCopyCodeList.OID && x.ItemGroupRepeatKey === item.ItemGrpIndex);
                                            //push into array if only not exists.
                                            if (codeLstIndex === -1) {
                                                codeDefList.push(tempCopyCodeList);
                                                setCodeList(codeDefList);
                                            }

                                        }
                                    }
                                } else {
                                    itemDef.splice(index, 0, ItemDef);
                                    if (ItemDef?.CodeListRef !== null && ItemDef?.CodeListRef !== undefined) {
                                        let templateCodeList = tv?.CodeList?.find((cl: any) => cl.OID === ItemDef?.CodeListRef?.CodeListOID);
                                        let tempCopyCodeList = JSON.parse(JSON.stringify(templateCodeList));
                                        tempCopyCodeList.CodeListItem.map((obj) => {
                                            obj.IsEnabled = false;//default code value is false.
                                        })
                                        if (tempCopyCodeList !== null && tempCopyCodeList !== undefined) {
                                            let codeDefList = codeList;
                                            tempCopyCodeList.ItemGroupRepeatKey = item.ItemGrpIndex;
                                            let codeLstIndex = codeDefList.findIndex(x => x.OID === tempCopyCodeList.OID && x.ItemGroupRepeatKey === item.ItemGrpIndex);
                                            if (codeLstIndex === -1) {
                                                codeDefList.push(tempCopyCodeList);
                                                setCodeList(codeDefList);
                                            }

                                        }
                                    }
                                }
                                //setCodeList(codeDefList);
                                setItemDef(itemDef);
                            }
                        }
                    })
                } else {
                    //Ananth : commeneted bcz for new implementation it is not required...
                    //Ananth: Just clear the ItemData....
                    // let ItemDef = itemDef?.find((id: any) => id.OID === item.id);
                    // if (ItemDef !== null && ItemDef !== undefined) {
                    //     let index = itemDef?.findIndex((id: any) => id.OID === item.id && id.ItemGroupRepeatKey === item.ItemGrpIndex);
                    //     if (index !== -1) {                        
                    //         itemDef.splice(index, 1);
                    //     }
                    //     setItemDef(itemDef);
                    // }

                    let tempItemGrData = itemGroupData.find(x => x.ItemGroupOID === item.GroupOID);
                    if (tempItemGrData !== undefined && tempItemGrData !== null) {
                        let itemIndex = tempItemGrData.ItemData.findIndex(x => x.ItemOID === item.id && x.ItemGroupRepeatKey === item.ItemGrpIndex);
                        if (itemIndex !== -1) {
                            tempItemGrData.ItemData.splice(itemIndex, 1);
                            setItemGroupData([...itemGroupData]);
                        }
                    }
                    //setItemGroupData();
                }
            } else {
                if (item.status === true) {
                    assetresponse?.SDM?.Building?.TemplateVersion.reverse().map((tv: any, i: any) => {
                        // ItemDef = tv?.ItemDef?.find((id: any) => id.OID === item.id);
                        // if (ItemDef !== null && ItemDef !== undefined) {
                        //     let index = tv?.ItemDef?.findIndex((id: any) => id.OID === item.id);
                        //     if (assetitemDef.length > 0 && assetitemDef !== null && assetitemDef !== undefined && assetitemDef.find((i: any) => i.OID === item.id)) {
                        //     } else {
                        //         assetitemDef.splice(index, 0, ItemDef);
                        //     }
                        //     setAssetItemDef(assetitemDef);

                        // }
                        if (tv?.ItemDef !== null && tv?.ItemDef !== undefined && tv?.ItemDef.length > 0) {
                            ItemDef = JSON.parse(JSON.stringify(tv?.ItemDef?.find((id: any) => id.OID === item.id)));
                            ItemDef.ItemGroupRepeatKey = item.ItemGrpIndex;
                            if (ItemDef !== null && ItemDef !== undefined) {
                                let index = tv?.ItemDef?.findIndex((id: any) => id.OID === item.id);
                                if (assetitemDef.length > 0 && assetitemDef !== null && assetitemDef !== undefined && assetitemDef.find((i: any) => i.OID === item.id && i.ItemGroupRepeatKey === item.ItemGrpIndex)) {
                                    // itemDef.splice(index, 0, ItemDef);
                                    if (ItemDef?.CodeListRef !== null && ItemDef?.CodeListRef !== undefined) {
                                        let templateCodeList = tv?.CodeList?.find((cl: any) => cl.OID === ItemDef?.CodeListRef?.CodeListOID);
                                        let tempCopyCodeList = JSON.parse(JSON.stringify(templateCodeList));
                                        if (tempCopyCodeList !== null && tempCopyCodeList !== undefined) {
                                            let codeDefList = assetcodeList;
                                            tempCopyCodeList.ItemGroupRepeatKey = item.ItemGrpIndex;
                                            let codeLstIndex = codeDefList.findIndex(x => x.OID === tempCopyCodeList.OID && x.ItemGroupRepeatKey === item.ItemGrpIndex);
                                            //push into array if only not exists.
                                            if (codeLstIndex === -1) {
                                                codeDefList.push(tempCopyCodeList);
                                                setAssetCodeList(codeDefList);
                                            }

                                        }
                                    }
                                } else {
                                    assetitemDef.splice(index, 0, ItemDef);
                                    if (ItemDef?.CodeListRef !== null && ItemDef?.CodeListRef !== undefined) {
                                        let templateCodeList = tv?.CodeList?.find((cl: any) => cl.OID === ItemDef?.CodeListRef?.CodeListOID);
                                        let tempCopyCodeList = JSON.parse(JSON.stringify(templateCodeList));
                                        tempCopyCodeList.CodeListItem.map((obj) => {
                                            obj.IsEnabled = false;//default code value is false.
                                        })
                                        if (tempCopyCodeList !== null && tempCopyCodeList !== undefined) {
                                            let codeDefList = assetcodeList;
                                            tempCopyCodeList.ItemGroupRepeatKey = item.ItemGrpIndex;
                                            let codeLstIndex = codeDefList.findIndex(x => x.OID === tempCopyCodeList.OID && x.ItemGroupRepeatKey === item.ItemGrpIndex);
                                            if (codeLstIndex === -1) {
                                                codeDefList.push(tempCopyCodeList);
                                                setAssetCodeList(codeDefList);
                                            }

                                        }
                                    }
                                }
                                //setCodeList(codeDefList);
                                setAssetItemDef(assetitemDef);
                            }
                        }
                    })
                } else {
                    //Ananth: need to be comment bcz for new implementation it is not required...
                    // let ItemDef = assetitemDef?.find((id: any) => id.OID === item.id);
                    // if (ItemDef !== null && ItemDef !== undefined) {
                    //     let index = assetitemDef?.findIndex((id: any) => id.OID === item.id);
                    //     assetitemDef.splice(index, 1);
                    //     setAssetItemDef(assetitemDef);
                    // }

                    let tempItemGrData = itemGroupData.find(x => x.ItemGroupOID === item.GroupOID);
                    if (tempItemGrData !== undefined && tempItemGrData !== null) {
                        let itemIndex = tempItemGrData.ItemData.findIndex(x => x.ItemOID === item.id && x.ItemGroupRepeatKey === item.ItemGrpIndex);
                        if (itemIndex !== -1) {
                            tempItemGrData.ItemData.splice(itemIndex, 1);
                            setItemAssetGroupData([...itemGroupData]);
                        }
                    }


                }
            }
        }

        // Repeat Questions
        function RepeatQus(itemGrp: any) {
            if (itemGrp !== undefined) {
                // itemGroup.splice(itemGrp.Index, 0, itemGrp.ItemGroup);           
                setItemGroup(itemGrp);
            }
        }

        //For Data Binding
        function handleSampleData(e: any, data: any, projectOID: string, eventOID: string, formOID: string) {

            let itemGroupDataList: ItemGroupData[] = [];

            // Helper function to enrich CodeList in answer data with full CodeList structure
            const enrichCodeListInAnswerData = (itemData: any[], codeListDefs: any[]) => {
                if (!itemData || !Array.isArray(itemData)) return [];
                if (!codeListDefs || !Array.isArray(codeListDefs) || codeListDefs.length === 0) {
                    // If no codeListDefs available, return original data
                    return itemData;
                }

                return itemData.map((item: any) => {
                    if (item.CodeList && Array.isArray(item.CodeList) && item.CodeList.length > 0) {
                        // CodeList contains selected values as strings (e.g., ["1", "2"])
                        const selectedCodes = item.CodeList;

                        // Find the corresponding ItemDef from template
                        const foundItemDef = assetitemDef.find((id: any) => id.OID === item.ItemOID) ||
                            itemDef.find((id: any) => id.OID === item.ItemOID);

                        if (foundItemDef && foundItemDef.CodeListRef && foundItemDef.CodeListRef.CodeListOID) {
                            const codeListDef = codeListDefs.find((cl: any) => cl.OID === foundItemDef.CodeListRef!.CodeListOID);

                            if (codeListDef && codeListDef.CodeListItem && Array.isArray(codeListDef.CodeListItem)) {
                                // Create enriched CodeList with IsEnabled flags based on selection
                                const enrichedCodeList = {
                                    ...codeListDef,
                                    CodeListItem: codeListDef.CodeListItem.map((cli: any) => ({
                                        ...cli,
                                        IsEnabled: selectedCodes.includes(cli.CodedValue)
                                    }))
                                };

                                return {
                                    ...item,
                                    CodeList: [enrichedCodeList]
                                };
                            }
                        }
                    }
                    return item;
                });
            };

            //let itemGroupDefList: ItemGroupDef[] = [];
            //let itemDefList: ItemDef[] = [];
            if (data !== null && data !== undefined) {
                if (data?.OID === ASSET_OID) {
                    let eventData = data?.EventData?.filter((ed: any) => ed.EventOID === eventOID);
                    if (eventData !== null && eventData !== undefined && eventData.length > 0) {
                        let formData = eventData[0]?.FormData?.filter((fd: any) => fd.FormOID === formOID);
                        if (formData[0] !== null && formData[0] !== undefined && formData.length > 0) {
                            // Directly use ItemGroupData array instead of redundant map/find
                            let ItemGroupData = formData[0]?.ItemGroupData;
                            if (ItemGroupData && Array.isArray(ItemGroupData)) {
                                // Only enrich if template CodeLists are available
                                const codeListsToUse = assetcodeList.length > 0 ? assetcodeList : codeList;
                                if (codeListsToUse && codeListsToUse.length > 0) {
                                    // Enrich the answer data with full CodeList structure
                                    const enrichedItemGroupData = ItemGroupData.map((igd: any) => ({
                                        ...igd,
                                        ItemData: enrichCodeListInAnswerData(igd.ItemData, codeListsToUse)
                                    }));
                                    itemGroupDataList.push(...enrichedItemGroupData);
                                } else {
                                    // Template not loaded yet, store raw data
                                    itemGroupDataList.push(...ItemGroupData);
                                }
                            }
                            // Set state after processing
                            setItemAssetGroupData(itemGroupDataList);
                        }
                    }
                }
                else if (projectOID !== null && projectOID !== undefined) {

                    let eventData = data?.EventData?.filter((ed: any) => ed.EventOID === eventOID);
                    if (eventData !== null && eventData !== undefined && eventData.length > 0) {
                        let formData = eventData[0]?.FormData?.filter((fd: any) => fd.FormOID === formOID);
                        if (formData[0] !== null && formData[0] !== undefined && formData.length > 0) {
                            let ItemGroupData = formData[0]?.ItemGroupData;
                            if (ItemGroupData && Array.isArray(ItemGroupData)) {
                                // Only enrich if template CodeLists are available
                                if (codeList && codeList.length > 0) {
                                    // Enrich the trade answer data with full CodeList structure
                                    const enrichedItemGroupData = ItemGroupData.map((igd: any) => ({
                                        ...igd,
                                        ItemData: enrichCodeListInAnswerData(igd.ItemData, codeList)
                                    }));
                                    itemGroupDataList.push(...enrichedItemGroupData);
                                } else {
                                    // Template not loaded yet, store raw data
                                    itemGroupDataList.push(...ItemGroupData);
                                }
                            }
                            setItemGroupData(itemGroupDataList);
                        }
                    }

                    //implementatioon to handle add repeat item  group based on their conditon item value...
                    methodDef?.map((method) => {
                        if (method.FormalExpression.text.replaceAll("\"", "").includes("RepeatCount")) {
                            let methd = method?.FormalExpression?.text.split(":")[0].replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');

                            let allCodeList: CodeList[] = [];
                            response?.SDM?.Building?.TemplateVersion.reverse().map((tv: any, i: any) => {
                                tv.CodeList.map((codeList: any, i: any) => {
                                    allCodeList.push(codeList);
                                });
                            });

                            let allItemGrpDefs: ItemGroupDef[] = [];
                            response?.SDM?.Building?.TemplateVersion.reverse().map((tv: any, i: any) => {
                                allItemGrpDefs = tv?.ItemGroupDef;
                            });


                            let allItemGrpRefs: ItemGroupRef[] = [];
                            response?.SDM?.Building?.TemplateVersion.reverse().map((tv: any, i: any) => {
                                tv.FormDef.map((formDef: any, i: any) => {
                                    allItemGrpRefs = formDef.ItemGroupRef;
                                });
                            });

                            let allItemDefs: ItemDef[] = [];
                            response?.SDM?.Building?.TemplateVersion.reverse().map((tv: any, i: any) => {
                                tv.ItemDef.map((itemDef: any, i: any) => {
                                    allItemDefs.push(itemDef);
                                });
                            });

                            let methodItemGrpRef = allItemGrpRefs.find((obj: ItemGroupRef) => obj.MethodOID !== null && obj.MethodOID !== undefined && obj.MethodOID === method.OID);
                            let methodItemGrpDef = allItemGrpDefs.find(x => x.OID === methodItemGrpRef?.ItemGroupOID);

                            if (itemGroupData !== null && itemGroupData !== undefined && methd[0] !== null && methd[0] !== undefined) {
                                itemGroupData.map((igd: any) => {
                                    let methodItemData = igd.ItemData.find(x => x.ItemOID === methd);
                                    if (methodItemData !== null && methodItemData !== undefined) {
                                        if (methodItemData.Value !== null && methodItemData.Value !== undefined && methodItemData.Value > 0 && methodItemGrpDef != null && methodItemGrpDef !== undefined) {
                                            let repeatItemGrpDef: ItemGroupDef[] = itemGroup;
                                            let repeatItemDefs: ItemDef[] = itemDef;
                                            let repeatCodeList: CodeList[] = codeList;
                                            const total = itemGroup.filter((g: any) => g.OID === methodItemGrpRef?.ItemGroupOID).length;
                                            const difference = methodItemData.Value - total;
                                            if (difference > 0) {
                                                for (let i = 1; i <= difference; i++) {
                                                    repeatItemGrpDef.push(methodItemGrpDef);

                                                    //to do: may be need to splice to be in psition on rendering
                                                    methodItemGrpDef?.ItemRef?.map((it: any) => {
                                                        let ItemDef = allItemDefs?.find((id: any) => id.OID === it.ItemOID);
                                                        let itemDefCoppy = JSON.parse(JSON.stringify(ItemDef));
                                                        let index = repeatItemDefs.findIndex((x) => x.OID === it.ItemOID && x.ItemGroupRepeatKey === i);

                                                        if (index === -1 && itemDefCoppy !== undefined && itemDefCoppy !== null) {
                                                            itemDefCoppy.ItemGroupRepeatKey = i;
                                                            repeatItemDefs.push(itemDefCoppy)
                                                        }

                                                        if (itemDefCoppy !== undefined && itemDefCoppy !== null) {
                                                            if (itemDefCoppy.CodeListRef !== null) {
                                                                let codeList = allCodeList?.find((cl: any) => cl.OID === itemDefCoppy.CodeListRef?.CodeListOID);
                                                                let codeListCopy = JSON.parse(JSON.stringify(codeList));
                                                                let codeListIndex = repeatCodeList.findIndex((x) => x.OID === itemDefCoppy.CodeListRef?.CodeListOID && x.ItemGroupRepeatKey === i);
                                                                if (codeListIndex === -1 && codeListCopy !== null && codeListCopy !== undefined) {
                                                                    codeListCopy.ItemGroupRepeatKey = i;
                                                                    repeatCodeList.push(codeListCopy);
                                                                }
                                                            }
                                                        }


                                                    });


                                                }
                                            }
                                            // for (let i = 1; i <= methodItemData.Value; i++) {
                                            //     repeatItemGrpDef.push(methodItemGrpDef);
                                            // }
                                            //let xyz = codeDefList.filter(x => x.OID === "CL.157")
                                            //let xy = codeList.filter(x => x.OID === "CL.157")
                                            setCodeList(repeatCodeList);
                                            setItemDef(repeatItemDefs);
                                            setItemGroup(repeatItemGrpDef);
                                        }

                                    }

                                })
                            }
                            // showStatus(cdn[1].includes(event.target.value), cd.OID, OID);
                        }


                    });


                }
                else {
                    let eventData = data?.EventData?.filter((ed: any) => ed.EventOID === eventOID);
                    if (eventData !== null && eventData !== undefined && eventData.length > 0) {
                        let formData = eventData[0]?.FormData?.filter((fd: any) => fd.FormOID === formOID);
                        if (formData[0] !== null && formData[0] !== undefined && formData.length > 0 && formData[0].ItemGroupData !== null && formData[0].ItemGroupData !== undefined && formData[0].ItemGroupData.length > 0) {
                            itemGroupDataList = formData[0].ItemGroupData;
                            setItemGroupData(itemGroupDataList);
                            // formData[0]?.ItemGroupData?.map((i: any) => {
                            //     let ItemGroupData = formData[0]?.ItemGroupData?.find((ig: any) => ig.ItemGroupOID === i.ItemGroupOID);
                            //     itemGroupDataList.push(ItemGroupData);
                            //     setItemGroupData(itemGroupDataList);
                            // })
                        }
                    }

                    let allItemGrpDefs: ItemGroupDef[] = [];
                    response?.SDM?.Building?.TemplateVersion.reverse().map((tv: any, i: any) => {
                        allItemGrpDefs = tv?.ItemGroupDef; //this may not contains all version itemGroupDefs ... need to fix...
                    });


                    let allItemGrpRefs: ItemGroupRef[] = [];
                    response?.SDM?.Building?.TemplateVersion.reverse().map((tv: any, i: any) => {
                        tv.FormDef.map((formDef: any, i: any) => {
                            allItemGrpRefs = formDef.ItemGroupRef;
                        });
                    });

                    let allItemDefs: ItemDef[] = [];
                    response?.SDM?.Building?.TemplateVersion.reverse().map((tv: any, i: any) => {
                        tv.ItemDef.map((itemDef: any, i: any) => {
                            allItemDefs.push(itemDef);
                        });
                    });

                    let allCodeList: CodeList[] = [];
                    response?.SDM?.Building?.TemplateVersion.reverse().map((tv: any, i: any) => {
                        tv.CodeList.map((codeList: any, i: any) => {
                            allCodeList.push(codeList);
                        });
                    });

                    // response?.SDM?.Building?.TemplateVersion.reverse().map((tv: any, i: any) => {
                    //     tv?.ItemGroupDef?.map((igdef: any, i: any) => {
                    //         if (itemGroupDataList !== null && itemGroupDataList.length > 0) {
                    //             let index = itemGroupDataList.findIndex((obj) => obj.ItemGroupOID === igdef.OID);
                    //             if (index !== -1) {
                    //                 if (itemGroupDefList !== null && itemGroupDefList.length > 0) {
                    //                     let defIndex = itemGroupDefList.findIndex((obj) => obj.OID === igdef.OID);
                    //                     if (defIndex === -1) {
                    //                         itemGroupDefList.push(igdef);
                    //                         igdef.ItemRef.map((x) => {
                    //                             if(!x.CollectionExceptionConditionOID){
                    //                                 let itemDefinition = allItemDefs.find(y=>y.OID === x.ItemOID);
                    //                                 if(itemDefinition !== null && itemDefinition !== undefined){
                    //                                     itemDefList.push(itemDefinition);
                    //                                 }
                    //                             }                                        
                    //                         })
                    //                     }
                    //                 } else {
                    //                     itemGroupDefList.push(igdef)
                    //                 }
                    //             }
                    //         }

                    //     });
                    // });

                    //implementation to handle add repeat itemgroup based on their conditon item value...
                    let currentMethodDef = getMergedTv(response)?.MethodDef
                    currentMethodDef?.map((method) => {
                        if (method.FormalExpression.text.replaceAll("\"", "").includes("RepeatCount")) {
                            let methd = method?.FormalExpression?.text.split(":")[0].replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');



                            let methodItemGrpRef = allItemGrpRefs.find((obj: ItemGroupRef) => obj.MethodOID !== null && obj.MethodOID !== undefined && obj.MethodOID === method.OID);
                            let methodItemGrpDef = allItemGrpDefs.find(x => x.OID === methodItemGrpRef?.ItemGroupOID);

                            let methodItemGrpIndex = allItemGrpDefs?.findIndex((id: any) => id.OID === methodItemGrpRef?.ItemGroupOID);

                            if ((itemGroupDataList !== null && itemGroupDataList !== undefined) && (methd[0] !== null && methd[0] !== undefined)) {
                                itemGroupDataList.map((igd: any) => {
                                    let methodItemData = igd.ItemData.find(x => x.ItemOID === methd);
                                    if (methodItemData !== null && methodItemData !== undefined) {
                                        if (methodItemData.Value !== null && methodItemData.Value !== undefined && methodItemData.Value > 0 && methodItemGrpDef != null && methodItemGrpDef !== undefined) {
                                            let repeatItemGrpDef: ItemGroupDef[] = itemGroup;
                                            let repeatItemDefs: ItemDef[] = [...itemDef];
                                            let repeatCodeList: CodeList[] = [...codeList];
                                            const total = itemGroup.filter((g: any) => g.OID === methodItemGrpRef?.ItemGroupOID).length;
                                            const difference = methodItemData.Value - total;
                                            if (difference > 0) {
                                                //based on the repeat itmedata value, add itemGroupDef to the itemGroupDefList
                                                for (let i = 1; i <= difference; i++) {
                                                    repeatItemGrpDef.splice(methodItemGrpIndex, 0, methodItemGrpDef);
                                                }

                                                //To add itemDef to the itemDef array based on repeatItemData value.
                                                for (let i = 1; i <= methodItemData.Value; i++) {
                                                    for (let ind = 0; ind < methodItemGrpDef.ItemRef.length; ind++) {
                                                        const ir = methodItemGrpDef.ItemRef[ind];

                                                        let ItemDef = allItemDefs?.find((id: any) => id.OID === ir.ItemOID);
                                                        let itemDefCoppy = JSON.parse(JSON.stringify(ItemDef));
                                                        let index = repeatItemDefs.findIndex((x) => x.OID === ir.ItemOID && x.ItemGroupRepeatKey === i);

                                                        // Add if itemDef with same id and ItemGrpIndex not exists
                                                        if (index === -1 && itemDefCoppy !== undefined && itemDefCoppy !== null) {
                                                            itemDefCoppy.ItemGroupRepeatKey = i;
                                                            repeatItemDefs.push(itemDefCoppy);
                                                        }

                                                        if (itemDefCoppy !== undefined && itemDefCoppy !== null) {
                                                            if (itemDefCoppy.CodeListRef !== null) {
                                                                let foundCodeList = allCodeList?.find((cl: any) => cl.OID === itemDefCoppy.CodeListRef?.CodeListOID);
                                                                if (foundCodeList !== null && foundCodeList !== undefined) {
                                                                    let codeListCopy = JSON.parse(JSON.stringify(foundCodeList));
                                                                    let codeListIndex = repeatCodeList.findIndex((x) => x.OID === itemDefCoppy.CodeListRef?.CodeListOID && x.ItemGroupRepeatKey === i);
                                                                    if (codeListIndex === -1 && codeListCopy !== null && codeListCopy !== undefined) {
                                                                        codeListCopy.ItemGroupRepeatKey = i;
                                                                        repeatCodeList.push(codeListCopy);
                                                                    }
                                                                }
                                                            }
                                                        }
                                                    }
                                                }
                                            }

                                            setItemGroup(repeatItemGrpDef);
                                            setItemDef(repeatItemDefs);
                                            setCodeList(repeatCodeList);
                                        }
                                    }
                                });
                            };
                        }
                        // showStatus(cdn[1].includes(event.target.value), cd.OID, OID);
                    });
                }
            }
        }

        const handleTabChange = (event: React.SyntheticEvent, newValue: number) => {
            setTabValue(newValue);
            // No lazy loading needed - trade data is already loaded on page load
        };

        // Loading check: Don't render until essential data is loaded (only in normal mode)
        if (!isSharedMode && isInitialLoading) {
            return (
                <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
                    <Typography>Loading questions...</Typography>
                </Box>
            );
        }




        return (
            <Box>
                <Card className='POCard' variant="outlined" >
                    <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
                        <Tabs value={tabValue} onChange={handleTabChange} aria-label="basic tabs example">
                            <Tab sx={{ textTransform: "none" }} label="Asset Info" {...a11yProps(0)} onClick={(e) => {
                                handleSampleData(e, assetData, ASSET_OID, DEFAULT_EVENTOID, DEFAULT_FORMOID);
                            }} />
                            {!hide ? <Tab sx={{ textTransform: "none" }} label="Trade Questions" {...a11yProps(1)} onClick={(e) => {
                                handleSampleData(e, projectData, data?.tradeOid, DEFAULT_EVENTOID, DEFAULT_FORMOID);
                                // Also call handleChange to populate itemGroup, itemDef, codeList arrays
                                if (response) {
                                    handleChange(null, response, 1);
                                }
                            }} /> : null}
                        </Tabs>
                    </Box>
                    <CustomTabPanel value={tabValue} index={0}>
                        {assetitemGroup.length > 0 && assetitemDef.length > 0 &&
                            assetcodeList.length > 0 &&
                            assetitemGroup[0]?.ItemRef && Array.isArray(assetitemGroup[0].ItemRef) ? (
                            <>
                                <QuestionRendererCard OID={assetOID}
                                    formDefs={assetformDef}
                                    itemGroupDefs={assetitemGroup}
                                    itemDefs={assetitemDef}
                                    codeListDefs={assetcodeList}
                                    conditionDefs={assetconditionDef}
                                    measureDef={measureUnit}
                                    OnItemGrpDataChange={handleState}
                                    show={showQus}
                                    showRepeat={RepeatQus}
                                    showGroupQus={showGroup}
                                    itemGroupDatas={itemAssetGroupData}
                                    validate={itemList}
                                    mappings={mappings}
                                    tradeData={tradeData}
                                    methodDefs={assetMethodDef}
                                    readonly={isBidDetailsPage && isContractApproval && tabValue === 0} />
                            </>
                        ) : (
                            <Box display="flex" justifyContent="center" p={4}>
                                <Typography>Loading asset questions...</Typography>
                            </Box>
                        )}
                    </CustomTabPanel>
                    <CustomTabPanel value={tabValue} index={1} >
                        {(() => {
                            return null;
                        })()}
                        {itemGroup.length > 0 && itemDef.length > 0 &&
                            codeList.length > 0 &&
                            itemGroup[0]?.ItemRef && Array.isArray(itemGroup[0].ItemRef) ? (
                            <QuestionRendererCard
                                OID={projectOID}
                                formDefs={formDef}
                                itemGroupDefs={itemGroup}
                                itemDefs={itemDef}
                                codeListDefs={codeList}
                                conditionDefs={conditionDef}
                                measureDef={measureUnit}
                                OnItemGrpDataChange={handleState}
                                show={showQus}
                                showRepeat={RepeatQus}
                                showGroupQus={showGroup}
                                itemGroupDatas={itemGroupData}
                                validate={itemList}
                                mappings={mappings}
                                tradeData={tradeData}
                                methodDefs={methodDef}
                                readonly={isBidDetailsPage && isContractApproval && tabValue === 1} />
                        ) : (
                            <Box display="flex" justifyContent="center" p={4}>
                                <Typography>Loading trade questions...</Typography>
                            </Box>
                        )}
                    </CustomTabPanel>
                </Card>

                {/* Image Modal for Full-Size View */}
                {/* Image Modal for Full-Size View */}
                <ImageZoomDialog
                    open={imageModalOpen}
                    onClose={() => setImageModalOpen(false)}
                    imageSrc={selectedImage?.src || null}
                    imageAlt={selectedImage?.alt || ''}
                />
            </Box>
        );

    });

export default QuestionRenderer_v2;
