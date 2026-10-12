/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2024
* @author      : 
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Question Renderer Card v2 component
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
  20106          | 2026-06-03  |  Danish Prabhu K V  | Issue fix #20106
* 21928          | 2026-06-24  |  Karthika S         | Fix stale validation errors persisting after field is filled in Boolean/Q&A renderer
* AB#30581       | 10-09-2026  |  Mohamed Riyaz A    | Two fixes. (1) The "am I on Bid Details?" guards compared only against
*                                                      /soldproduction/projectinformation, so on this page's other route
*                                                      (/salesestimate/projectinformation, same component per Routes.tsx) the
*                                                      Contract-Approval skip never fired while the next-question autosave ran
*                                                      anyway; they now ask isBidDetailsRoute. (2) handleNextQuestion's autosave
*                                                      payload is spread from the page-open currentBidData snapshot, so it carried
*                                                      a status that is stale after any in-page status change -- measured on
*                                                      staging, that posted a bid back from awaitingEstimation to bidReview.
*                                                      stripSnapshotStatus removes it: an autosave saves answers.
*/

import React, { useEffect, useState, useMemo } from 'react';
import { isBidDetailsRoute, stripSnapshotStatus } from '../../../utils/bidDetailsRoute';
import { getTemplatesByTradeName } from '../../../services/TradeTemplateService';
import { Box, Button, Card, Tooltip, Typography, Tabs, Tab, CircularProgress, Dialog, DialogContent, IconButton, Switch, FormControlLabel, TextField } from '@mui/material';
import { useOidcAccessToken } from '@axa-fr/react-oidc';
import MultiChoice_v2 from './MultiChoice_v2';
import FreeText from './FreeText';
import Combo from './Combo';
import { BasicDefinitions, CodeList, ConditionDef, FormDef, ItemData, ItemDef, ItemGroupData, ItemGroupDef, Building, BuildingData, Mappings, MethodDef } from '../../../interface/SdmInterfaces';
import { isDeletedFlag } from '../../../components/createbid/requirements/utils/odmConversionUtils';
import SingleSelect_v2 from './SingleSelect_v2';
import RadioType_v2 from './RadioType_v2';
import DateSelector from './DateSelector';
import DateTime from './DateTime';
import MultilineText from './MultilineText';
import FloatText from './FloatText_v2';
import IntegerText from './IntegerText_v2';
import FreeText_v2 from './FreeText_v2';
import { DependencyGraphParser } from '../../../components/createbid/requirements/parsers/DependencyGraphParser';
import { MultiInstanceParser } from '../../../components/createbid/requirements/parsers/MultiInstanceParser';
import { buildWasteCalcPairs, calculateDerivedFields, executeMethodExpression } from '../../../utils/MethodDefCalculator';
import { restoreAnswerData, hasAnswerDataChanged, recordLastSavedAnswerData } from '../../../utils/createBidStateManager';
import {
    shouldRenderMultiInstance,
    MultiInstanceContext
} from '../../../components/createbid/requirements/utils/multiInstanceRenderingFix';
import { getImageUrl } from '../../../utils/imageRenderer';
import './QuestionRendererCard_v2.scss';
import ImageZoomDialog from './ImageZoomDialog';
import { validateRangeCheck } from '../../../utils/rangeCheckValidator';
import FeetInchesInput_v2 from './FeetInchesInput_v2';


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
    // READONLY: when true, all inputs inside this card should be non-editable
    readonly?: boolean;
    // New props for question pagination
    currentQuestionIndex?: number;
    onQuestionChange?: (index: number) => void;
    onQuestionComplete?: (questionData: any) => void;
    // Sidebar "jump to question": resolve the target main-question OID against THIS
    // card's own visible-question list, so direct selection lands on the right page
    // regardless of how the sidebar counts indexes.
    navigateToMainOID?: { stepId: string; oid: string; timestamp: number } | null;
    // New props for instance questions with tabs
    repeatingChildren?: any[];
    stepId?: string;
    allQuestionsData?: any;
    getCurrentQuestionIndex?: (stepId: string) => number;
    getRepeatingInstanceCount?: (controlItemOID: string, parentSuffix?: string) => number; //  UPDATED: Support parent suffix for nested
    updateRepeatingInstanceCount?: (controlItemOID: string, newCount: number, repeatingGroupOID: string, parentSuffix?: string) => void; //  NEW: Create nested ItemGroup instances
    getMethodDefRepeatCount?: (methodOIDOrQuestionId: string) => number;
    multiInstanceParser?: any; // NEW: Pass parser from parent to ensure consistent data
    dependencyParser?: any; // 
    // New props for navigation
    activeStep?: number;
    totalSteps?: number;
    handleBack?: () => void;
    handleNext?: () => void;
    handleComplete?: () => void;
    canProceed?: () => boolean;
    parentAnswersData?: any;
    parentInstanceSuffix?: string;
    parentRepeatingGroupOID?: string;  // OID of parent repeating group if nested
    isAnswersDataStable?: boolean;
    // Loading state for draft save
    savingDraft?: boolean;
    // Image base URL for loading images from external server
    imageBaseUrl?: string;
    summarizedGroupsRef?: React.MutableRefObject<{ [groupOID: string]: boolean }>;
    suppressDeletionConfirmRef?: React.MutableRefObject<boolean>;
    onSummarizeGroupClear?: (controlItemOID: string, repeatingGroupOID: string, groupItemOIDs: string[]) => void;
}

const SESSION_PATCH_DELAY_MS = 600;
export const resolveDerivedItemValue = (
    isDeleted: boolean,
    dataType: string | undefined,
    rawValue: any
): string | null => {
    if (isDeleted) {
        return (dataType || '').toLowerCase() === 'integer' ? '0' : null;
    }

    if (rawValue === undefined || rawValue === null) {
        return '';
    }

    return String(rawValue);
};


const QuestionRendererCard_v2Component: React.FC<Props> = ({ OID, formDefs, itemGroupDefs, itemDefs, codeListDefs, conditionDefs, measureDef, methodDefs,
    OnItemGrpDataChange, show, showRepeat, showGroupQus, itemGroupDatas, validate, mappings, tradeData, readonly = false,
    currentQuestionIndex = 0, onQuestionChange, onQuestionComplete, navigateToMainOID, repeatingChildren, stepId, allQuestionsData,
    getCurrentQuestionIndex, getRepeatingInstanceCount, updateRepeatingInstanceCount, getMethodDefRepeatCount, multiInstanceParser, dependencyParser: dependencyParserProp, activeStep, totalSteps, handleBack, handleNext,
    handleComplete, canProceed, parentAnswersData,
    parentInstanceSuffix, parentRepeatingGroupOID, isAnswersDataStable = true, savingDraft = false, imageBaseUrl = '', summarizedGroupsRef, suppressDeletionConfirmRef, onSummarizeGroupClear }) => {

    const [itemData, setItemData] = useState<ItemData[]>([]);
    const [itemGroupData, setItemGroupData] = useState<ItemGroupData[]>([]);
    const [itemGroupDatases, setItemGroupDatas] = useState<ItemGroupData[]>([]);
    const repeatCount = {};

    // New state for question pagination
    const [questionsInGroup, setQuestionsInGroup] = useState<any[]>([]);

    const [currentQuestionData, setCurrentQuestionData] = useState<any>(null);

    const [activeMethodDefTab, setActiveMethodDefTab] = useState<{ [key: string]: number }>({});

    const [activeRepeatingTabs, setActiveRepeatingTabs] = useState<{ [groupOID: string]: number }>({});

    const [summarizedGroups, setSummarizedGroups] = useState<{ [groupOID: string]: boolean }>({});

    const [summarizeConfirmDialog, setSummarizeConfirmDialog] = useState<{
        open: boolean;
        groupOID: string;
        newValue: boolean;
        repeatingChild: any;
    } | null>(null);

    React.useEffect(() => {
        if (summarizedGroupsRef) {
            summarizedGroupsRef.current = summarizedGroups;
        }
    }, [summarizedGroups, summarizedGroupsRef]);

    const [answersData, setAnswersDataState] = useState<any>({});
    const [repeatingInstanceData, setRepeatingInstanceData] = useState<any>({});

    const [prevItemCount, setPrevItemCount] = useState<number>(0);

    const [isCleanupInProgress, setIsCleanupInProgress] = useState<boolean>(false);

    const [recentlySelectedItems, setRecentlySelectedItems] = React.useState<{ [key: string]: any }>({});

    // Root-caused 2026-09-08 (architectural fix, after 6+ point patches each surfaced a
    // NEW unguarded writer to `itemData` in a different place): `itemData` had no single
    // owner. At least 7 independent code paths call setItemData(...) — the itemGroupDatas
    // restore effect, the parentAnswersData sync effect, the direct-edit path inside
    // OnItemDataChange, cleanupLocalUIState, resetItemData, the multi-instance merge
    // logic, and a dialog-driven group-clear handler — each with its own ad-hoc (or
    // absent) idea of whether it's safe to overwrite an item the user just edited. Every
    // point patch fixed the ONE writer it targeted and left the others untouched, which
    // is why testing kept finding a new revert path.
    //
    // Fix: a SINGLE, authoritative gate. The three writers implicated in this bug (restore
    // effect, parentAnswersData sync, direct edit) now route every candidate value through
    // resolveProtectedItemValue() below instead of writing to itemData directly for that
    // item — making it structurally impossible for one of them to bypass protection by
    // forgetting a check, the way scattered per-effect flags did. Value-based, not
    // timing-based (see this session's own history of fixed-duration guards proving too
    // short for this question's real round-trip): protection is released only once an
    // external source's OWN candidate value actually matches what the user set, not after
    // an arbitrary delay.
    const protectedItemValuesRef = React.useRef<Record<string, any>>({});

    const itemValueKey = React.useCallback((item: any): string => String(item?.Value ?? ''), []);

    /** Call when the user genuinely, deliberately edits an item (see OnItemDataChange's own
     *  `meta.isUserEdit` doc). Marks it protected until an external source's OWN candidate
     *  value matches what was just set. */
    const markItemUserEdited = React.useCallback((itemOID: string, item: any) => {
        protectedItemValuesRef.current[itemOID] = item;
    }, []);

    /**
     * The single decision point for "should this externally-sourced candidate value for
     * `itemOID` actually be applied?" If the item isn't protected, the candidate is
     * accepted immediately. If it IS protected, the candidate is accepted (and protection
     * released) only once its value matches the protected one — anything else is a stale
     * echo and is discarded, however long it takes to arrive.
     */
    const resolveProtectedItemValue = React.useCallback((itemOID: string, candidate: any): any => {
        const protectedItem = protectedItemValuesRef.current[itemOID];
        if (!protectedItem) return candidate;
        if (itemValueKey(candidate) === itemValueKey(protectedItem)) {
            delete protectedItemValuesRef.current[itemOID];
            return candidate;
        }
        return protectedItem;
    }, [itemValueKey]);

    const lastSentDataRef = React.useRef<{ [key: string]: { value: any, timestamp: number } }>({});

    // Track last synced derived field values to prevent redundant session storage updates
    const lastSyncedDerivedFieldsRef = React.useRef<{ [key: string]: any }>({});
    const lastSyncedCurrentBidDataRef = React.useRef<string>('');
    const lastAppliedMethodDependencyHashRef = React.useRef<string>('');
    const derivedAuditRef = React.useRef<Record<string, { value: any; isDeleted: boolean; repeatKey: number; itemGroupOID?: string }>>({});
    const derivedItemOIDsRef = React.useRef<Set<string>>(new Set());
    const lastKnownItemRefsWithMethodRef = React.useRef<any[]>([]);

    const lastUserEditedOIDRef = React.useRef<string | undefined>(undefined);

    const [userIsActivelySelecting, setUserIsActivelySelecting] = React.useState(false);

    const [isMultiInstanceOperation, setIsMultiInstanceOperation] = React.useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const { accessToken } = useOidcAccessToken();

    // State for question image zoom dialog
    const [zoomedQuestionImage, setZoomedQuestionImage] = useState<{ url: string, label: string } | null>(null);

    // Root ref used to toggle readonly behavior on all internal inputs
    const rootRef = React.useRef<HTMLDivElement | null>(null);

    React.useEffect(() => {
        if (!rootRef.current) return;
        // Target form controls and clickable elements only — leave container pointer events so scrolling works
        const interactiveSelectors = 'input, textarea, select, button, [onclick], [role="button"], a, .MuiButton-root, .MuiCardActionArea-root';

        const applyReadonlyToElement = (el: Element) => {
            try {
                const e = el as HTMLElement;
                // Keep navigation buttons interactive and visually normal
                const skipNavIds = ['create-bid-v2-button-next-question', 'create-bid-v2-button-previous-question', 'create-bid-v2-button-continue', 'create-bid-v2-button-complete'];
                if (e.id && skipNavIds.includes(e.id)) {
                    if ('disabled' in e) (e as HTMLButtonElement | HTMLInputElement).disabled = false;
                    e.style.pointerEvents = '';
                    e.style.cursor = '';
                    return;
                }

                // Disable form controls
                if (e.tagName === 'INPUT' || e.tagName === 'TEXTAREA' || e.tagName === 'SELECT' || e.tagName === 'BUTTON') {
                    (e as HTMLInputElement).disabled = readonly as boolean;
                }

                // Prevent pointer events on interactive elements (but not the whole container) so scrolling still works
                e.style.pointerEvents = readonly ? 'none' : '';
                e.style.cursor = readonly ? 'default' : '';
            } catch (err) {
                // no-op
            }
        };

        const applyToAll = () => {
            const nodes = rootRef.current!.querySelectorAll(interactiveSelectors);

            nodes.forEach((n: Element) => applyReadonlyToElement(n));
        };

        // Initial pass
        applyToAll();

        // If readonly is true, also observe DOM changes and apply readonly to any dynamically added inputs
        let observer: MutationObserver | null = null;
        if (readonly) {
            observer = new MutationObserver((mutations) => {
                mutations.forEach(m => {
                    m.addedNodes.forEach((node) => {
                        if (!(node instanceof Element)) return;

                        try {
                            const added = node as Element;

                            // If the added node itself matches, apply
                            if (added.matches && added.matches(interactiveSelectors)) {
                                applyReadonlyToElement(added);
                            }

                            // Also apply to any children inside the added subtree
                            const inner = added.querySelectorAll(interactiveSelectors);
                            (Array.from(inner) as Element[]).forEach((innerEl) => applyReadonlyToElement(innerEl));
                        } catch (e) {
                            // ignore
                        }
                    });
                });
            });

            observer.observe(rootRef.current, { childList: true, subtree: true });
        }

        return () => {
            if (observer) observer.disconnect();
        };

        // Note: visual opacity is applied only to scrollable content areas to avoid muting navigation buttons

    }, [readonly]);

    const answersDataRef = React.useRef<any>({});

    const shownConditionalQuestionsRef = React.useRef<Set<string>>(new Set());

    const setAnswersData = React.useCallback((newData: any) => {
        if (typeof newData === 'function') {
            setAnswersDataState((prev: any) => {
                const computed = newData(prev);
                answersDataRef.current = computed;
                return computed;
            });
        } else {
            answersDataRef.current = newData;
            setAnswersDataState(newData);
        }
    }, []);

    const getGroupSummarizeInstances = React.useCallback((itemGroupOID: string): boolean => {
        if (!formDefs) return false;
        for (const formDef of formDefs) {
            const igRef = formDef.ItemGroupRef?.find((ref) => ref.ItemGroupOID === itemGroupOID);
            if (igRef && igRef.SummarizeInstances === 'Yes') return true;
        }
        return false;
    }, [formDefs]);

    const auditDerivedChanges = React.useCallback((
        itemRefsWithMethod: any[],
        sourceData: Record<string, any>,
        stage: string
    ) => {
        const trackedOids = itemRefsWithMethod.map((r: any) => r.ItemOID);
        derivedItemOIDsRef.current = new Set(trackedOids);

        const prev = derivedAuditRef.current;
        const next: Record<string, { value: any; isDeleted: boolean; repeatKey: number; itemGroupOID?: string }> = {};

        trackedOids.forEach((oid: string) => {
            const raw = sourceData?.[oid];
            if (raw === undefined) return;

            const asObject = raw && typeof raw === 'object';
            next[oid] = {
                value: asObject && 'value' in raw ? raw.value : raw,
                isDeleted: asObject && raw.isDeleted === true,
                repeatKey: asObject && typeof raw.repeatKey === 'number' ? raw.repeatKey : 0,
                itemGroupOID: asObject ? raw.itemGroupOID : undefined
            };
        });

        const added: string[] = [];
        const removed: string[] = [];
        const changed: string[] = [];

        Object.keys(next).forEach((oid) => {
            if (!prev[oid]) {
                added.push(oid);
                return;
            }

            const prevValue = prev[oid];
            const nextValue = next[oid];
            if (
                prevValue.value !== nextValue.value ||
                prevValue.isDeleted !== nextValue.isDeleted ||
                prevValue.repeatKey !== nextValue.repeatKey ||
                prevValue.itemGroupOID !== nextValue.itemGroupOID
            ) {
                changed.push(oid);
            }
        });

        Object.keys(prev).forEach((oid) => {
            if (!next[oid]) {
                removed.push(oid);
            }
        });


        derivedAuditRef.current = next;
    }, []);

    const showStatusBatchRef = React.useRef<any[]>([]);
    const showStatusTimerRef = React.useRef<NodeJS.Timeout | null>(null);
    const showStatusFrameRef = React.useRef<number | null>(null);

    const templateVersion = {
        MethodDef: methodDefs || [],
        FormDef: formDefs || [],
        ItemGroupDef: itemGroupDefs || [],
        ItemDef: itemDefs || [],
        ConditionDef: conditionDefs || []
    };
    const dependencyParser_local = React.useMemo(() => {
        if (!itemGroupDefs || !Array.isArray(itemGroupDefs) || itemGroupDefs.length === 0) {
            return null;
        }
        return new DependencyGraphParser(templateVersion);
    }, [methodDefs, formDefs, itemGroupDefs, itemDefs, conditionDefs]);
    const dependencyParser = dependencyParserProp || dependencyParser_local; // Use parent parser if available
    const dependencyGraph = React.useMemo(() => {
        if (!dependencyParser) return new Map();
        return dependencyParser.buildGraph();
    }, [dependencyParser]);

    const multiInstanceParser_local = React.useMemo(() => {
        if (!itemGroupDefs || !Array.isArray(itemGroupDefs) || itemGroupDefs.length === 0) {
            return null;
        }
        return new MultiInstanceParser(templateVersion, dependencyGraph);
    }, [methodDefs, formDefs, itemGroupDefs, dependencyGraph]);
    const multiInstanceParser_final = multiInstanceParser || multiInstanceParser_local;

    const multiInstanceConfigs = React.useMemo(() => {
        if (!multiInstanceParser_final) {
            return new Map();
        }
        const configs = multiInstanceParser_final.parseAll();

        return configs;
    }, [multiInstanceParser_final]);

    const isItemInRepeatingInstance = React.useCallback((itemOID: string): boolean => {
        if (!multiInstanceConfigs) return false;
        for (const config of Array.from(multiInstanceConfigs.values()) as any[]) {
            const repeatingGroup = itemGroupDefs.find(g => g.OID === config.repeatingGroupOID);
            if (repeatingGroup) {
                const isInGroup = repeatingGroup.ItemRef?.some((ref: any) => ref.ItemOID === itemOID);
                if (isInGroup) return true;
            }
        }
        return false;
    }, [multiInstanceConfigs, itemGroupDefs]);

    const hasRepeatCountValue = React.useCallback((repeatCountQuestionOID: string, currentAnswers: any): boolean => {
        const answer = currentAnswers[repeatCountQuestionOID];
        // RepeatCount uses integer values (1, 2, 3, etc.)
        return answer?.value !== undefined && answer?.value !== null && answer?.value !== '' && Number(answer?.value) > 0;
    }, []);

    // ========================================
    // AUTO-CALCULATE DERIVED FIELDS (M.7 JavaScript expressions)
    // ========================================

    // Extract field dependencies from MethodDefs to only recalculate when relevant fields change
    const methodDependencyHash = React.useMemo(() => {
        if (!methodDefs || !answersData) return '';

        const dependentFields = new Set<string>();

        // Find all ItemOID references in MethodDef expressions
        methodDefs.forEach(methodDef => {
            const expression = methodDef.FormalExpression?.text;
            if (expression) {
                // Extract all I.xxx references
                const matches = expression.match(/I\.\d+/g);
                if (matches) {
                    matches.forEach(itemOID => dependentFields.add(itemOID));
                }
            }
        });

        // Create hash of only the dependent field values
        const values: Record<string, any> = {};
        dependentFields.forEach(itemOID => {
            const fieldData = answersData[itemOID];
            if (fieldData && typeof fieldData === 'object' && 'value' in fieldData) {
                // For fields with a non-empty value string, use it directly
                if (fieldData.value !== '' && fieldData.value !== null && fieldData.value !== undefined) {
                    values[itemOID] = fieldData.value;
                } else if (fieldData.CodeList && Array.isArray(fieldData.CodeList)) {
                    // For multiselect/singleselect fields: derive value from enabled CodeList items
                    const enabledCodes: string[] = [];
                    for (const cl of fieldData.CodeList) {
                        if (cl.CodeListItem && Array.isArray(cl.CodeListItem)) {
                            cl.CodeListItem.forEach((item: any) => {
                                if (item.IsEnabled) enabledCodes.push(item.CodedValue);
                            });
                        }
                    }
                    values[itemOID] = enabledCodes.join(',');
                } else {
                    values[itemOID] = fieldData.value;
                }
            } else {
                values[itemOID] = fieldData;
            }

            Object.keys(answersData).forEach(key => {
                if (key.startsWith(`${itemOID}_`) && !values[key]) {
                    const suffixedData = answersData[key];
                    values[key] = suffixedData && typeof suffixedData === 'object' && 'value' in suffixedData
                        ? suffixedData.value
                        : suffixedData;
                }
            });
        });

        return JSON.stringify(values);
    }, [answersData, methodDefs]);

    React.useEffect(() => {

        if (!answersData || Object.keys(answersData).length === 0) {
            return;
        }

        if (!methodDefs || methodDefs.length === 0) {
            return;
        }

        if (!itemGroupDefs || itemGroupDefs.length === 0) {
            return;
        }

        // Only recompute when a method-def dependency actually changes.
        // This prevents the derived-field writeback from re-entering the same effect.
        if (lastAppliedMethodDependencyHashRef.current === methodDependencyHash) {
            return;
        }
        lastAppliedMethodDependencyHashRef.current = methodDependencyHash;

        // OIDs available within THIS card's groups. A derived field whose method
        // references an operand OUTSIDE these groups is "cross-group": the card's
        // local answersData cannot resolve those operands (they live on other steps),
        // so computing it here yields a WRONG value (operands read as 0) that clobbers
        // the correct value produced by the global recompute effect in Requirements_v3.
        // Such fields are owned exclusively by that global effect — skip them here.
        const localItemOIDs = new Set<string>();
        itemGroupDefs.forEach((g: any) => (g.ItemRef || []).forEach((r: any) => localItemOIDs.add(r.ItemOID)));

        // Find all ItemRefs with MethodOID across all ItemGroups
        let itemRefsWithMethod: any[] = [];
        itemGroupDefs.forEach((itemGroupDef: any) => {
            if (!itemGroupDef.ItemRef) return;

            const isRepeating = itemGroupDef.Repeating === 'Yes';

            itemGroupDef.ItemRef.forEach((itemRef: any) => {
                if (!itemRef.MethodOID) return;

                // Skip RepeatCount-style MethodDefs — they are UI control, not calculations
                const md = methodDefs?.find(m => m.OID === itemRef.MethodOID);
                if (md?.FormalExpression?.text?.includes('RepeatCount')) return;

                // Skip cross-group derived fields (non-repeating) — owned by the global
                // recompute effect, which sees the full answersData. Computing them here
                // with partial card-local data produces a wrong value that overwrites it.
                if (!isRepeating) {
                    const operandOIDs = (md?.FormalExpression?.text || '').match(/I\.\d+/g) || [];
                    const isCrossGroup = operandOIDs.some((oid: string) => !localItemOIDs.has(oid));
                    if (isCrossGroup) {
                        return;
                    }
                }

                if (isRepeating) {
                    // Find how many instances are active by scanning answersData for suffixed keys
                    // belonging to any non-derived sibling item in this group
                    const siblingOIDs = itemGroupDef.ItemRef
                        .filter((r: any) => !r.MethodOID)
                        .map((r: any) => r.ItemOID);

                    const activeRepeatKeys = new Set<number>();
                    const latestAnswers = answersDataRef.current || {};
                    Object.keys(latestAnswers).forEach(key => {
                        const parts = key.split('_');
                        if (parts.length === 2 && siblingOIDs.includes(parts[0])) {
                            const rk = parseInt(parts[1]);
                            if (!isNaN(rk) && rk > 0) activeRepeatKeys.add(rk);
                        }
                    });

                    // Also check itemGroupDatas for persisted instances
                    itemGroupDatas?.forEach((igd: any) => {
                        if (igd.ItemGroupOID === itemGroupDef.OID && igd.ItemGroupRepeatKey > 0) {
                            activeRepeatKeys.add(igd.ItemGroupRepeatKey);
                        }
                    });

                    activeRepeatKeys.forEach(repeatKey => {
                        itemRefsWithMethod.push({
                            ...itemRef,
                            ItemGroupOID: itemGroupDef.OID,
                            ItemGroupRepeatKey: repeatKey  // instance-specific
                        });
                    });
                } else {
                    itemRefsWithMethod.push({
                        ...itemRef,
                        ItemGroupOID: itemGroupDef.OID,
                        ItemGroupRepeatKey: 0
                    });
                }
            });
        });
        // Autosave/reload can temporarily provide itemGroupDefs without MethodOID refs.
        // Reuse last known derived refs to keep derived recalculation stable across those transient snapshots.
        if (itemRefsWithMethod.length > 0) {
            lastKnownItemRefsWithMethodRef.current = itemRefsWithMethod;
        } else if (lastKnownItemRefsWithMethodRef.current.length > 0) {
            itemRefsWithMethod = lastKnownItemRefsWithMethodRef.current;
        }


        if (itemRefsWithMethod.length === 0) {
            return;
        }

        // Calculate derived fields
        const wasteCalcPairs = buildWasteCalcPairs(methodDefs, itemGroupDefs);
        const updatedData = calculateDerivedFields(
            itemRefsWithMethod,
            methodDefs,
            answersData,
            lastUserEditedOIDRef.current,
            wasteCalcPairs
        );
        lastUserEditedOIDRef.current = undefined;

        auditDerivedChanges(itemRefsWithMethod, updatedData, 'after-calculateDerivedFields');

        const itemDataTypeByOID = (itemDefs || []).reduce((acc: Record<string, string>, itemDef: ItemDef) => {
            acc[itemDef.OID] = itemDef.DataType || '';
            return acc;
        }, {});

        // MetaKey is defined on the ItemDef, not on the ItemRef passed to the calculator,
        // so the derived snapshot below must re-attach it from the template. Without this,
        // syncing a derived field into itemGroupData replaces the entry with a MetaKey-less
        // object (see handleItemGroupData) and the MetaKey "magically" disappears.
        const itemMetaKeyByOID = (itemDefs || []).reduce((acc: Record<string, string>, itemDef: ItemDef) => {
            if (itemDef?.MetaKey) acc[itemDef.OID] = itemDef.MetaKey;
            return acc;
        }, {} as Record<string, string>);

        // Prepare derived fields for session storage sync
        const derivedItemData = itemRefsWithMethod.map(itemRef => {
            const repeatKey: number = typeof itemRef.ItemGroupRepeatKey === 'number'
                ? itemRef.ItemGroupRepeatKey : 0;
            // Use suffixed key for repeating instances
            const lookupKey = repeatKey > 0
                ? `${itemRef.ItemOID}_${repeatKey}`
                : itemRef.ItemOID;

            const fieldData = (updatedData ?? answersData ?? {})[lookupKey];
            const isDeleted = fieldData?.isDeleted === true;
            const dataType = itemDataTypeByOID[itemRef.ItemOID];
            const value = resolveDerivedItemValue(isDeleted, dataType, fieldData?.value);
            const metaKey = fieldData?.MetaKey || itemMetaKeyByOID[itemRef.ItemOID];

            return {
                ItemOID: itemRef.ItemOID,        // base OID (for handleItemGroupData routing)
                Value: value,
                CodeList: [],
                ItemGroupRepeatKey: repeatKey,   // correct instance index
                ItemGroupOID: fieldData?.itemGroupOID || itemRef.ItemGroupOID,
                isDeleted,
                // Only attach MetaKey when known so an empty string never overwrites an
                // existing MetaKey during the merge in handleItemGroupData.
                ...(metaKey ? { MetaKey: metaKey } : {})
            };
        });
        // Log all createBid_requirements session storage entries after calculation
        try {
            const sessionSnapshot: Record<string, any> = {};
            for (let i = 0; i < sessionStorage.length; i++) {
                const key = sessionStorage.key(i);
                if (key && key.startsWith('createBid_requirements')) {
                    try {
                        sessionSnapshot[key] = JSON.parse(sessionStorage.getItem(key) || 'null');
                    } catch {
                        sessionSnapshot[key] = sessionStorage.getItem(key);
                    }
                }
            }
        } catch (e) {
            console.warn('Failed to read sessionStorage:', e);
        }

        const patchSessionAnswersData = (items: typeof derivedItemData, sourceData: Record<string, any>) => {
            try {
                for (let i = 0; i < sessionStorage.length; i++) {
                    const key = sessionStorage.key(i);
                    if (!key || !key.startsWith('createBid_requirements')) continue;
                    const raw = sessionStorage.getItem(key);
                    if (!raw) continue;
                    const entry = JSON.parse(raw);
                    if (!entry?.answersData) continue;
                    let patched = false;
                    items.forEach(item => {
                        const inMemory = sourceData[item.ItemOID];
                        const sessionVal = entry.answersData[item.ItemOID];
                        const newVal = item.Value;
                        const newIsDeleted = item.isDeleted === true;
                        if (sessionVal && typeof sessionVal === 'object' && 'value' in sessionVal) {
                            const valueChanged = sessionVal.value !== newVal;
                            const deletedChanged = (sessionVal.isDeleted === true) !== newIsDeleted;
                            if (valueChanged || deletedChanged) {
                                entry.answersData[item.ItemOID] = {
                                    ...sessionVal,
                                    value: newVal,
                                    isDeleted: newIsDeleted
                                };
                                patched = true;
                            }
                        } else if (inMemory && typeof inMemory === 'object' && 'value' in inMemory) {
                            // Field exists in memory but not yet in session — write it
                            entry.answersData[item.ItemOID] = {
                                ...inMemory,
                                value: newVal,
                                isDeleted: newIsDeleted
                            };
                            patched = true;
                        }
                    });
                    if (patched) {
                        sessionStorage.setItem(key, JSON.stringify(entry));
                    }
                }
            } catch (e) {
                console.warn('Failed to patch sessionStorage answersData:', e);
            }
        };

        // Create deduplication key for derived fields
        const derivedDataKey = JSON.stringify(derivedItemData.map(d => ({ oid: d.ItemOID, value: d.Value, isDeleted: d.isDeleted })));

        // Whether we've already pushed this exact derived snapshot to sessionStorage.
        // NOTE: this gate is used ONLY to skip the redundant sessionStorage patch — it
        // must NOT block the in-memory commit below. Previously it early-returned before
        // setAnswersData, so when a derived result returned to a previously-seen snapshot
        // (e.g. edit a value then change it back) the recomputed value was never committed
        // and the displayed/stored value went stale. The methodDependencyHash guard above
        // already prevents recompute loops, so committing here is safe.
        const alreadySyncedThisSnapshot = !!lastSyncedDerivedFieldsRef.current[derivedDataKey];

        // AFTER (single fire with staleness guard):
        const scheduleSessionPatch = (items: typeof derivedItemData, sourceData: Record<string, any>, label: string) => {
            setTimeout(() => {
                // Staleness guard: skip if sessionStorage already has the correct derived values
                // (avoids redundant JSON serialisation on every render cycle)
                const alreadyCurrent = items.every(item => {
                    for (let i = 0; i < sessionStorage.length; i++) {
                        const key = sessionStorage.key(i);
                        if (!key?.startsWith('createBid_requirements')) continue;
                        try {
                            const entry = JSON.parse(sessionStorage.getItem(key) || 'null');
                            const storedAnswer = entry?.answersData?.[item.ItemOID];
                            if (storedAnswer && typeof storedAnswer === 'object' && 'value' in storedAnswer) {
                                if (storedAnswer.value !== item.Value) return false; // mismatch — patch needed
                            }
                        } catch { /* continue */ }
                    }
                    return true; // this item is already correct
                });

                if (!alreadyCurrent) {
                    patchSessionAnswersData(items, sourceData);
                }
            }, SESSION_PATCH_DELAY_MS);
        };

        // Only update if there are changes
        if (updatedData !== answersData) {
            // ALWAYS commit the recomputed values to in-memory state — independent of the
            // sessionStorage dedup. This is the fix for the stale-calc symptom.
            setAnswersData(updatedData);

            // Sync calculated fields to itemGroupData for session storage persistence
            if (derivedItemData.length > 0) {
                handleItemGroupData(derivedItemData);
                if (!alreadySyncedThisSnapshot) {
                    lastSyncedDerivedFieldsRef.current[derivedDataKey] = Date.now();
                    scheduleSessionPatch(derivedItemData, updatedData, 'new values');
                }
            }
        } else {
            const hasValues = derivedItemData.some(d => d.Value !== '' || d.isDeleted === true);
            const needsSync = hasValues && !alreadySyncedThisSnapshot;

            if (derivedItemData.length > 0 && needsSync) {
                lastSyncedDerivedFieldsRef.current[derivedDataKey] = Date.now();
                handleItemGroupData(derivedItemData);
                scheduleSessionPatch(derivedItemData, answersData, 'first sync');
            } else if (!needsSync && alreadySyncedThisSnapshot) {
                // Always patch session — Requirements_v2 may have re-saved its stale copy.
                scheduleSessionPatch(derivedItemData, answersData, 'already synced, re-patch');
            }
        }
    }, [methodDependencyHash, answersData, methodDefs, itemGroupDefs, setAnswersData]);

    const CLEANUP_DEBUG = false; // Set to false to disable all cleanup debug logs
    const debugCleanup = (message: string, data?: any) => {
        if (CLEANUP_DEBUG) {
        }
    };

    // UI CLEANUP: Function to clean up local UI state when cleanup occurs
    const cleanupLocalUIState = React.useCallback((dependentsToClean: string[]) => {
        debugCleanup(`CLEANING LOCAL UI STATE for: ${dependentsToClean.join(', ')}`);

        // Extract base OIDs from dependentsToClean (for both I.2 and I.2_1 formats)
        const baseOIDsToClean = new Set<string>();
        const fullKeysToClean = new Set<string>(dependentsToClean);

        dependentsToClean.forEach(key => {
            // Extract base OID (I.2 from I.2_1)
            const baseOID = key.split('_')[0];
            baseOIDsToClean.add(baseOID);
        });


        // Clean up itemData
        setItemData(prevItemData => {

            const cleanedItemData = prevItemData.filter(item => {
                // Build the full key for this item
                const itemFullKey = item.ItemGroupRepeatKey
                    ? `${item.ItemOID}_${item.ItemGroupRepeatKey}`
                    : item.ItemOID;

                // Check if either the full key or base OID should be cleaned
                const shouldClean = fullKeysToClean.has(itemFullKey) || baseOIDsToClean.has(item.ItemOID);

                if (shouldClean) {
                    debugCleanup(`Removing item: ${itemFullKey} (ItemOID: ${item.ItemOID}, RepeatKey: ${item.ItemGroupRepeatKey})`);
                }

                return !shouldClean;
            });

            debugCleanup(`ItemData cleaned: ${prevItemData.length} → ${cleanedItemData.length} items`);
            return cleanedItemData;
        });

        // Clean up answersData
        setAnswersData(prevAnswersData => {

            const cleanedAnswersData = { ...prevAnswersData };
            const removedDerivedKeys: string[] = [];
            dependentsToClean.forEach(key => {
                if (derivedItemOIDsRef.current.has(key) && cleanedAnswersData[key] !== undefined) {
                    removedDerivedKeys.push(key);
                }
                delete cleanedAnswersData[key];

                // If it's a base OID (I.2), also clean all instance variations (I.2_1, I.2_2, etc.)
                if (!key.includes('_')) {
                    Object.keys(cleanedAnswersData).forEach(answerKey => {
                        if (answerKey.startsWith(`${key}_`)) {
                            debugCleanup(`Removing answer variation: ${answerKey}`);
                            if (derivedItemOIDsRef.current.has(answerKey) && cleanedAnswersData[answerKey] !== undefined) {
                                removedDerivedKeys.push(answerKey);
                            }
                            delete cleanedAnswersData[answerKey];
                        }
                    });
                }
            });

            debugCleanup(`AnswersData cleaned: ${Object.keys(prevAnswersData).length} → ${Object.keys(cleanedAnswersData).length} items`);
            return cleanedAnswersData;
        });
    }, []);

    //  UI SYNC: Sync local UI state with backend state after cleanup
    const syncUIStateWithBackend = React.useCallback(() => {
        const hasActiveInstances = multiInstanceConfigs ? Array.from(multiInstanceConfigs.values() as IterableIterator<any>).some((config: any) => {
            return hasRepeatCountValue(config.repeatCountQuestionOID, answersData);
        }) : false;

        if (hasActiveInstances) {
            debugCleanup(`SKIPPING ALL CLEANUP: Active repeating instances detected`);
            return;
        }

        if (isMultiInstanceOperation) {
            return;
        }

        if (userIsActivelySelecting) {
            return;
        }

        debugCleanup(`SYNCING UI STATE WITH BACKEND`);

        // Get all item OIDs that should exist according to backend
        const backendItemOIDs = new Set<string>();
        itemGroupDatas?.forEach(group => {
            group.ItemData?.forEach(item => {
                if (item.ItemOID && item.Value !== undefined && item.Value !== null && item.Value !== '') {
                    backendItemOIDs.add(item.ItemOID);
                }
            });
        });

        debugCleanup(`Backend has items: [${Array.from(backendItemOIDs).join(', ')}]`);
        debugCleanup(`Recently selected items: [${Object.keys(recentlySelectedItems).join(', ')}]`);
        debugCleanup(`User actively selecting: ${userIsActivelySelecting}`);

        // Clean up itemData - remove items not in backend
        setItemData(prevItemData => {
            const syncedItemData = prevItemData.filter(item => {
                if (recentlySelectedItems[item.ItemOID]) {
                    debugCleanup(`PRESERVING recently selected item: ${item.ItemOID}`);
                    return true; // Keep recently selected items
                }
                if (item.Value !== undefined && item.Value !== null && item.Value !== '') {
                    debugCleanup(`PROTECTING item with value: ${item.ItemOID}=${item.Value}`);
                    return true;
                }
                if (isItemInRepeatingInstance(item.ItemOID)) {
                    // Check if any RepeatCount controller has a value
                    const hasActiveInstance = Array.from(multiInstanceConfigs.values() as IterableIterator<any>).some((config: any) => {
                        // Check if this item is in this config's repeating group
                        const repeatingGroup = itemGroupDefs.find(g => g.OID === config.repeatingGroupOID);
                        const isInThisGroup = repeatingGroup?.ItemRef?.some((ref: any) => ref.ItemOID === item.ItemOID);

                        if (!isInThisGroup) return false;

                        // Check if the RepeatCount controller has a value
                        return hasRepeatCountValue(config.repeatCountQuestionOID, answersData);
                    });

                    if (hasActiveInstance) {
                        debugCleanup(`PROTECTED repeating instance item: ${item.ItemOID}`);
                        return true;
                    }
                }

                return backendItemOIDs.has(item.ItemOID);
            });

            if (syncedItemData.length !== prevItemData.length) {
                const removedItems = prevItemData.filter(item => !syncedItemData.includes(item));
                debugCleanup(`REMOVED from itemData: [${removedItems.map(i => i.ItemOID).join(', ')}]`);
            }
            return syncedItemData;
        });

        // Clean up answersData - remove items not in backend
        setAnswersData(prevAnswersData => {
            const syncedAnswersData = { ...prevAnswersData };
            Object.keys(syncedAnswersData).forEach(key => {
                const baseOID = key.split('_')[0]; // Handle multi-instance keys like I.2_1

                // �️ REPEATING INSTANCE PROTECTION: Keep items in any repeating instance
                if (isItemInRepeatingInstance(baseOID)) {
                    // Check if any RepeatCount controller has a value
                    const hasActiveInstance = Array.from(multiInstanceConfigs.values() as IterableIterator<any>).some((config: any) => {
                        // Check if this item is in this config's repeating group
                        const repeatingGroup = itemGroupDefs.find(g => g.OID === config.repeatingGroupOID);
                        const isInThisGroup = repeatingGroup?.ItemRef?.some((ref: any) => ref.ItemOID === baseOID);

                        if (!isInThisGroup) return false;

                        // Check if the RepeatCount controller has a value
                        return hasRepeatCountValue(config.repeatCountQuestionOID, prevAnswersData);
                    });

                    if (hasActiveInstance) {
                        debugCleanup(`PROTECTED repeating instance answer: ${key}`);
                        return; // Don't remove this answer
                    }
                }

                if (!backendItemOIDs.has(baseOID)) {
                    delete syncedAnswersData[key];
                }
            });

            if (Object.keys(syncedAnswersData).length !== Object.keys(prevAnswersData).length) {
                const removedKeys = Object.keys(prevAnswersData).filter(key => !syncedAnswersData[key]);
                debugCleanup(`REMOVED from answersData: [${removedKeys.join(', ')}]`);
            }

            return syncedAnswersData;
        });
    }, [itemGroupDatas, recentlySelectedItems, isMultiInstanceOperation, userIsActivelySelecting]);

    const sendFilteredDataToRequirements = React.useCallback((itGrp: any) => {
        const latestAnswers = answersDataRef.current || {};
        const getAnswerForItem = (item: any) => {
            if (!item?.ItemOID) return undefined;
            const repeatKey = item?.ItemGroupRepeatKey;
            if (repeatKey !== undefined && repeatKey !== null && repeatKey !== 0) {
                const suffixedKey = `${item.ItemOID}_${repeatKey}`;
                if (latestAnswers[suffixedKey]) {
                    return latestAnswers[suffixedKey];
                }
            }
            return latestAnswers[item.ItemOID];
        };

        // Get items that should exist according to current backend state
        const validItemOIDs = new Set<string>();
        itemGroupDatas?.forEach(group => {
            group.ItemData?.forEach(item => {
                if (item.ItemOID && item.Value !== undefined && item.Value !== null && item.Value !== '') {
                    validItemOIDs.add(item.ItemOID);
                }
            });
        });
        const filteredItemGroup = itGrp.ItemGroup.map((group: any) => {
            const filteredItems = group.ItemData.filter((item: any) => {
                // Always allow the item if it's in the current backend data
                if (validItemOIDs.has(item.ItemOID)) {
                    return true;
                }

                const isDerivedItem = derivedItemOIDsRef.current.has(item.ItemOID);
                if (isDerivedItem) {
                    return true;
                }

                if (isCleanupInProgress) {
                    debugCleanup(`BLOCKED sending cleaned item: ${item.ItemOID}="${item.Value}"`);
                    return false;
                }

                return true; // Allow new items when not in cleanup
            }).map((item: any) => {
                const answer = getAnswerForItem(item);
                // Behavioral intent: Only true/true-string values trigger deletion logic; false values are ignored
                const answerHasDeletedFlag = answer && typeof answer === 'object' &&
                    isDeletedFlag(answer.isDeleted);
                const itemHasDeletedFlag = isDeletedFlag(item.isDeleted);

                // Preserve explicit item flag; otherwise hydrate from latest answersData.
                if (itemHasDeletedFlag) {
                    return item;
                }
                if (answerHasDeletedFlag) {
                    return {
                        ...item,
                        isDeleted: isDeletedFlag(answer.isDeleted)
                    };
                }
                return item;
            });

            const summarizeFlag = summarizedGroups[group.ItemGroupOID] === true ? true : undefined;

            const result: any = {
                ...group,
                ItemData: filteredItems
            };
            if (summarizeFlag) {
                result.SummarizeMode = true;
            } else {
                delete result.SummarizeMode;
            }
            return result;
        }).filter((group: any) => group.ItemData.length > 0);

        const filteredItGrp = {
            ...itGrp,
            ItemGroup: filteredItemGroup
        };

        const originalCount = itGrp.ItemGroup.reduce((total: number, group: any) => total + group.ItemData.length, 0);
        const filteredCount = filteredItemGroup.reduce((total: number, group: any) => total + group.ItemData.length, 0);

        if (originalCount !== filteredCount) {
            debugCleanup(`FILTERED SEND: ${originalCount} → ${filteredCount} items sent to Requirements_v2`);
        }

        OnItemGrpDataChange(filteredItGrp);
    }, [itemGroupDatas, isCleanupInProgress, summarizedGroups]);

    useEffect(() => {
        return () => {
            // Clear any pending batches on unmount
            if (showStatusTimerRef.current) {
                clearTimeout(showStatusTimerRef.current);
            }
            if (showStatusFrameRef.current) {
                cancelAnimationFrame(showStatusFrameRef.current);
            }
            showStatusBatchRef.current = [];
        };
    }, []);

    useEffect(() => {

        setItemGroupDatas(itemGroupDatas);

        const currentItemCount = itemGroupDatas?.reduce((total, group) => total + (group.ItemData?.length || 0), 0) || 0;

        // CLEANUP COORDINATION: Skip restoration if cleanup is in progress
        if (isCleanupInProgress) {
            debugCleanup(`RESTORE SKIPPED - Cleanup is in progress, avoiding restoration conflicts`);
            return;
        }

        // NOTE (2026-09-08): a second "count decreased => treat as cleanup, skip restore"
        // heuristic used to live here, duplicating isCleanupInProgress above. It compared
        // the current count against the historical PEAK (prevItemCount is ratcheted down
        // on every skip), so once itemGroupDatas arrived in a naturally-shrinking sequence
        // (e.g. during initial hydration) it would skip forever and itemData would never
        // get populated at all — root cause of "Asset/Trade questions render with no
        // answers" (2026-09-08). isCleanupInProgress is the single, explicit signal for
        // "cleanup is in flight, don't restore" — removed the redundant duplicate.
        setPrevItemCount(currentItemCount);

        if (itemGroupDatas && itemGroupDatas.length > 0) {
            const restoredItemData: any[] = [];

            itemGroupDatas.forEach((itemGroup: any) => {
                if (itemGroup.ItemData && Array.isArray(itemGroup.ItemData)) {
                    itemGroup.ItemData.forEach((item: any) => {
                        if (item.ItemOID && item.Value !== undefined && item.Value !== null && item.Value !== '') {
                            // See resolveProtectedItemValue's own doc (architectural fix,
                            // 2026-09-08): this effect unconditionally rebuilt itemData from
                            // itemGroupDatas on every reference change — the single decision
                            // point below is what stops it from overwriting a value the user
                            // just genuinely edited.
                            const candidate = {
                                ItemOID: item.ItemOID,
                                Value: item.Value,
                                ItemGroupRepeatKey: item.ItemGroupRepeatKey || 0,
                                CodeList: item.CodeList || null
                            };
                            restoredItemData.push(resolveProtectedItemValue(item.ItemOID, candidate));
                        }
                    });
                }
            });

            if (restoredItemData.length > 0) {
                debugCleanup(`RESTORING ${restoredItemData.length} items to UI`,
                    restoredItemData.map(item => `${item.ItemOID}="${item.Value}"`).slice(0, 5).join(', ')
                );
                setItemData(restoredItemData);
            }
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [itemGroupDatas])


    useEffect(() => {
        if (!itemGroupDatas || itemGroupDatas.length === 0) return;
        if (isCleanupInProgress) return; // Don't sync during cleanup

        const codeListAnswers: Record<string, any> = {};

        itemGroupDatas.forEach((itemGroup: any) => {
            if (itemGroup.ItemData && Array.isArray(itemGroup.ItemData)) {
                itemGroup.ItemData.forEach((item: any) => {
                    // Sync items that have CodeList (multiselect child questions)
                    if (item.ItemOID && item.CodeList && item.CodeList.length > 0) {
                        const answerKey = item.ItemGroupRepeatKey > 0
                            ? `${item.ItemOID}_${item.ItemGroupRepeatKey}`
                            : item.ItemOID;

                        // Update existing answers when CodeList selection changes (critical for derived fields).
                        const existingAnswer = answersData[answerKey];
                        const existingValue = existingAnswer?.value ?? '';
                        const nextValue = item.Value || '';
                        const existingCodeList = JSON.stringify(existingAnswer?.CodeList || []);
                        const nextCodeList = JSON.stringify(item.CodeList || []);
                        const shouldSync = !existingAnswer || existingValue !== nextValue || existingCodeList !== nextCodeList;

                        if (shouldSync) {
                            codeListAnswers[answerKey] = {
                                ...(existingAnswer || {}),
                                value: nextValue,
                                dataType: existingAnswer?.dataType || 'singleselect',
                                CodeList: item.CodeList,
                                itemOID: existingAnswer?.itemOID || item.ItemOID,
                                itemGroupOID: existingAnswer?.itemGroupOID || itemGroup.ItemGroupOID,
                                repeatKey: existingAnswer?.repeatKey ?? item.ItemGroupRepeatKey ?? 0,
                                timestamp: Date.now()
                            };

                        }
                    }
                });
            }
        });

        if (Object.keys(codeListAnswers).length > 0) {

            setAnswersData(prev => ({
                ...prev,
                ...codeListAnswers
            }));
        }
    }, [itemGroupDatas, isCleanupInProgress, answersData]);

    const answersDataSyncedRef = React.useRef(false);
    useEffect(() => {
        //  TASK 1.4: Only sync once to prevent cascade re-renders
        if (answersDataSyncedRef.current) return;

        if (itemData && itemData.length > 0 && Object.keys(answersData).length === 0) {


            const newAnswersData: any = {};
            itemData.forEach((item: any) => {
                if (item.ItemOID && item.Value !== undefined && item.Value !== null && item.Value !== '') {

                    let answerKey = item.ItemOID;
                    let inferredParentKey = item.ParentItemGroupRepeatKey;
                    if (!inferredParentKey && item.ItemGroupRepeatKey > 0) {
                        // Check if this item belongs to a nested repeating group
                        const isNestedGroup = repeatingChildren?.some(rc =>
                            rc.itemGroupDef.OID === item.ItemGroupOID && rc.isNested
                        );

                        if (isNestedGroup) {
                            // This is nested but missing ParentItemGroupRepeatKey - infer it
                            inferredParentKey = item.ItemGroupRepeatKey;

                        }
                    }

                    // Check for nested multi-instance: ParentItemGroupRepeatKey + ItemGroupRepeatKey
                    if (inferredParentKey && inferredParentKey > 0) {
                        // Nested instance: I.24_1_2 (parent instance 1, nested instance 2)
                        answerKey = `${item.ItemOID}_${inferredParentKey}_${item.ItemGroupRepeatKey || 0}`;

                    } else if (item.ItemGroupRepeatKey && item.ItemGroupRepeatKey > 0) {
                        // Regular multi-instance: I.21_1
                        answerKey = `${item.ItemOID}_${item.ItemGroupRepeatKey}`;

                    }


                    // If value is empty, skip it (it will be removed by cleanup logic below)
                    if (item.Value === '' || item.Value === null || item.Value === undefined) {
                        // Don't add to newAnswersData, but check if it exists in old answersData and should be removed
                        if (answersData[answerKey]) {
                        }
                        return; // Skip this item
                    }

                    newAnswersData[answerKey] = {
                        value: item.Value,
                        dataType: item.CodeList ? 'singleselect' : 'string'
                    };
                }
            });

            const keysToDelete: string[] = [];
            Object.keys(answersData).forEach(existingKey => {
                // Check if this key existed before but is not in newAnswersData (meaning it was cleared)
                if (!newAnswersData[existingKey]) {
                    // Check if any item in itemData has this OID but with empty value
                    const itemOID = existingKey.split('_')[0];
                    const clearedItem = itemData.find((item: any) => {
                        const suffix = item.ItemGroupRepeatKey > 0 ? `_${item.ItemGroupRepeatKey}` : '';
                        const key = `${item.ItemOID}${suffix}`;
                        return key === existingKey && (item.Value === '' || item.Value === null || item.Value === undefined);
                    });

                    if (clearedItem) {
                        keysToDelete.push(existingKey);
                    }
                }
            });

            if (Object.keys(newAnswersData).length > 0 || keysToDelete.length > 0) {
                const keysToRemove: string[] = [];
                Object.keys(newAnswersData).forEach(key => {
                    const parts = key.split('_');
                    if (parts.length === 2) { // Double-level key like I.24_1
                        // Check if a triple-level version exists (I.24_1_*)
                        const tripleLevelExists = Object.keys(newAnswersData).some(k => {
                            const kParts = k.split('_');
                            return kParts.length === 3 && kParts[0] === parts[0] && kParts[1] === parts[1];
                        });

                        if (tripleLevelExists) {
                            keysToRemove.push(key);
                        }
                    }
                });

                // Update answersData: merge new data and remove cleared/duplicate keys
                setAnswersData(prev => {
                    const updated = { ...prev, ...newAnswersData };

                    // Remove duplicates
                    keysToRemove.forEach(key => delete updated[key]);

                    // Remove cleared values
                    keysToDelete.forEach(key => delete updated[key]);



                    return updated;
                });
                debugCleanup(`UI Sync: ${Object.keys(newAnswersData).length} answers synced, ${keysToDelete.length} cleared`);
                answersDataSyncedRef.current = true;
            }
        }
    }, [itemData]) //  Removed answersData from deps (was causing re-runs)

    useEffect(() => {
        answersDataRef.current = answersData;
    }, [answersData]);

    const lastParentAnswersDataRef = React.useRef<any>(null);
    useEffect(() => {
        if (!parentAnswersData) return; // No parent data, skip

        const parentDataString = JSON.stringify(parentAnswersData);
        const lastParentDataString = lastParentAnswersDataRef.current ? JSON.stringify(lastParentAnswersDataRef.current) : null;

        if (parentDataString === lastParentDataString) {
            return; // No change, skip update
        }

        // Save old parent data BEFORE updating ref
        const oldParentData = lastParentAnswersDataRef.current;
        lastParentAnswersDataRef.current = parentAnswersData;

        // Merge parent data into local state
        setAnswersData(prev => {

            const merged = { ...prev, ...parentAnswersData };

            // Remove malformed entries (missing itemGroupOID)
            // These are legacy entries from sessionStorage that were never properly synced
            Object.keys(merged).forEach(key => {
                const entry = merged[key];
                const keyParts = key.split('_');

                // If this is a 2-part or 3-part key (instance-specific) but missing itemGroupOID
                if (keyParts.length >= 2 && !entry.itemGroupOID) {
                    delete merged[key];
                }
            });

            //nDetect deletions from parent (keys in old parent but NOT in new parent)
            if (oldParentData) {
                const deletedKeys: string[] = [];
                Object.keys(oldParentData).forEach(key => {
                    if (!parentAnswersData[key]) {
                        delete merged[key];
                        deletedKeys.push(key);
                    }
                });

                if (deletedKeys.length > 0) {
                    const deletedInstanceKeys = new Set<number>();
                    deletedKeys.forEach(key => {
                        const parts = key.split('_');
                        if (parts.length === 2) { // 2-part key like I.35_1
                            const instanceKey = parseInt(parts[1]);
                            if (!isNaN(instanceKey)) {
                                deletedInstanceKeys.add(instanceKey);
                            }
                        }
                    });

                    if (deletedInstanceKeys.size > 0) {
                        Object.keys(merged).forEach(key => {
                            const parts = key.split('_');
                            if (parts.length === 3) { // 3-part key like I.24_1_1
                                const parentInstanceKey = parseInt(parts[1]);
                                if (!isNaN(parentInstanceKey) && deletedInstanceKeys.has(parentInstanceKey)) {
                                    delete merged[key];
                                }
                            }
                        });
                    }

                    setItemData(prevItemData => {
                        return prevItemData.filter((item: any) => {
                            const itemKey = item.ItemGroupRepeatKey && item.ItemGroupRepeatKey > 0
                                ? `${item.ItemOID}_${item.ItemGroupRepeatKey}`
                                : item.ItemOID;

                            // Remove if this key was deleted from parent
                            if (deletedKeys.includes(itemKey)) {
                                return false;
                            }

                            // Remove if this is a nested item whose parent instance was deleted
                            if (item.ParentItemGroupRepeatKey && deletedInstanceKeys.has(item.ParentItemGroupRepeatKey)) {
                                return false;
                            }


                            // This catches items like I.28_1 that might have malformed answersData structure
                            if (item.ItemGroupRepeatKey && item.ItemGroupRepeatKey > 0) {
                                // Check if ANY deleted key has the same instance number
                                const instanceKey = item.ItemGroupRepeatKey;
                                const hasDeletedInstance = deletedKeys.some(key => {
                                    const parts = key.split('_');
                                    if (parts.length >= 2) {
                                        const deletedInstanceKey = parseInt(parts[1]);
                                        return !isNaN(deletedInstanceKey) && deletedInstanceKey === instanceKey;
                                    }
                                    return false;
                                });

                                if (hasDeletedInstance) {
                                    return false;
                                }
                            }

                            return true;
                        });
                    });

                    setItemGroupDatas(prevItemGroupDatas => {
                        return prevItemGroupDatas.map((group: any) => {
                            if (!group.ItemData || !Array.isArray(group.ItemData)) {
                                return group;
                            }

                            // Check if this entire ItemGroup instance was deleted
                            const groupInstanceKey = group.ItemGroupRepeatKey && group.ItemGroupRepeatKey > 0
                                ? group.ItemGroupRepeatKey
                                : null;

                            // If the entire ItemGroup instance was deleted, mark all items for removal
                            let removeEntireGroup = false;
                            if (groupInstanceKey && deletedInstanceKeys.has(groupInstanceKey)) {
                                removeEntireGroup = true;
                            }

                            // Filter out ItemData entries that belong to deleted instances
                            const filteredItemData = group.ItemData.filter((item: any) => {
                                // If the entire group instance was deleted, remove all items
                                if (removeEntireGroup) {
                                    const itemKey = `${item.ItemOID}_${groupInstanceKey}`;
                                    return false;
                                }

                                const itemKey = item.ItemGroupRepeatKey && item.ItemGroupRepeatKey > 0
                                    ? `${item.ItemOID}_${item.ItemGroupRepeatKey}`
                                    : item.ItemOID;

                                // Remove if this key was deleted from parent
                                if (deletedKeys.includes(itemKey)) {
                                    return false;
                                }

                                // Remove if this item belongs to a deleted instance
                                if (item.ItemGroupRepeatKey && item.ItemGroupRepeatKey > 0) {
                                    const instanceKey = item.ItemGroupRepeatKey;
                                    const hasDeletedInstance = deletedKeys.some(key => {
                                        const parts = key.split('_');
                                        if (parts.length >= 2) {
                                            const deletedInstanceKey = parseInt(parts[1]);
                                            return !isNaN(deletedInstanceKey) && deletedInstanceKey === instanceKey;
                                        }
                                        return false;
                                    });

                                    if (hasDeletedInstance) {
                                        return false;
                                    }
                                }

                                return true;
                            });

                            return {
                                ...group,
                                ItemData: filteredItemData
                            };
                        }).filter((group: any) => {
                            // Remove ItemGroup if it has no ItemData left
                            return group.ItemData && group.ItemData.length > 0;
                        });
                    });
                }
            }

            const keysToRemove: string[] = [];
            Object.keys(merged).forEach(key => {
                const parts = key.split('_');
                if (parts.length === 2) {
                    const tripleLevelExists = Object.keys(merged).some(k => {
                        const kParts = k.split('_');
                        return kParts.length === 3 && kParts[0] === parts[0] && kParts[1] === parts[1];
                    });

                    if (tripleLevelExists) {
                        keysToRemove.push(key);
                    }
                }
            });

            // Remove duplicate keys
            keysToRemove.forEach(key => delete merged[key]);
            // Log what changed
            const newKeys = Object.keys(parentAnswersData).filter(k => !prev[k] && !keysToRemove.includes(k));
            const updatedKeys = Object.keys(parentAnswersData).filter(k =>
                prev[k] && JSON.stringify(prev[k]) !== JSON.stringify(parentAnswersData[k])
            );

            if (newKeys.length > 0 || updatedKeys.length > 0 || keysToRemove.length > 0) {
            }

            return merged;
        });

        setItemData(prevItemData => {
            const updatedItemData = [...prevItemData];
            let hasChanges = false;

            // Update existing items with values from parentAnswersData
            updatedItemData.forEach((item, index) => {
                const answerKey = item.ItemGroupRepeatKey && item.ItemGroupRepeatKey > 0
                    ? `${item.ItemOID}_${item.ItemGroupRepeatKey}`
                    : item.ItemOID;
                const parentAnswer = parentAnswersData[answerKey] || parentAnswersData[item.ItemOID];

                // See resolveProtectedItemValue's own doc (architectural fix, 2026-09-08):
                // this effect is a SEPARATE sync path from the itemGroupDatas restore effect
                // above (different trigger — parentAnswersData, a prop from
                // Requirements_v2.tsx) and was one of the writers that motivated
                // consolidating protection into a single decision point rather than each
                // writer carrying its own copy of the check.
                if (parentAnswer && parentAnswer.value !== undefined && parentAnswer.value !== item.Value) {
                    const candidate = { ...item, Value: parentAnswer.value };
                    const resolved = resolveProtectedItemValue(item.ItemOID, candidate);
                    if (resolved !== item) {
                        updatedItemData[index] = resolved;
                        hasChanges = true;
                    }
                }
            });

            return hasChanges ? updatedItemData : prevItemData;
        });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [parentAnswersData]); // Sync whenever parent data changes

    // Extract questions from ItemGroup for pagination
    useEffect(() => {
        if (itemGroupDefs && itemGroupDefs.length > 0) {
            const currentItemGroup = itemGroupDefs[0]; // We're showing one ItemGroup at a time
            if (currentItemGroup && currentItemGroup.ItemRef && Array.isArray(currentItemGroup.ItemRef)) {
                // Get all questions (ItemRefs) for this ItemGroup
                const allQuestions = currentItemGroup.ItemRef.map((itemRef: any) => {
                    const itemDef = itemDefs.find(item => item.OID === itemRef.ItemOID);
                    return {
                        itemRef,
                        itemDef,
                        isConditional: !!itemRef.CollectionExceptionConditionOID
                    };
                }).filter(q => q.itemDef); // Only include questions that have ItemDef

                const currentStepQuestionOIDs = (itemGroupDefs[0]?.ItemRef || []).map((ref: any) => ref.ItemOID);

                const controllingQuestions = allQuestions.filter(question => {
                    // Check if this question is a controlling question for RepeatCount
                    const isControllingQuestion = repeatingChildren?.some(rc =>
                        rc.controlItemOID === question.itemDef?.OID
                    );

                    if (!isControllingQuestion) {
                        return false;
                    }

                    const isInCurrentStep = currentStepQuestionOIDs.includes(question.itemDef?.OID);

                    return !isInCurrentStep;
                });

                const allVisibleQuestions = allQuestions.filter(question => {
                    // Exclude conditional questions
                    if (question.isConditional) {
                        return false;
                    }

                    // Exclude derived/calculated fields (ItemRef with MethodOID) – only hide if explicitly marked as not visible
                    if (question.itemRef?.MethodOID) {
                        if (question.itemRef?.IsVisible === 'No') {
                            return false;
                        }
                        return true;
                    }

                    // Exclude controlling questions (they render inline with MethodDef)
                    const isControlling = controllingQuestions.some(cq =>
                        cq.itemDef?.OID === question.itemDef?.OID
                    );

                    return !isControlling;
                });

                // Separate for debugging
                const mainQuestions = allVisibleQuestions.filter(q => !q.isConditional);
                const conditionalQuestions = allQuestions.filter(q => q.isConditional);

                setQuestionsInGroup(allVisibleQuestions); // Include controlling questions!

                // Set current question data from all visible questions (not just main questions)
                if (allVisibleQuestions[currentQuestionIndex]) {
                    setCurrentQuestionData(allVisibleQuestions[currentQuestionIndex]);
                } else if (allVisibleQuestions.length > 0) {
                    // Fallback to first question if index is out of bounds
                    setCurrentQuestionData(allVisibleQuestions[0]);
                } else {
                    setCurrentQuestionData(null);
                }
            }
        }
    }, [itemGroupDefs, itemDefs, currentQuestionIndex])

    // Sidebar "jump to question": resolve the requested main-question OID against THIS
    // card's own visible-question list (questionsInGroup) and switch to that page. This
    // is the single source of truth for the index, so direct selection always lands on
    // the right question — unlike a sidebar-computed index, which can drift from the
    // card's filtering of controlling / derived questions. Fires once per timestamp and
    // never interferes with Next/Back (which change currentQuestionIndex directly).
    const lastNavTsRef = React.useRef<number | null>(null);
    useEffect(() => {
        if (!navigateToMainOID || navigateToMainOID.stepId !== stepId) return;
        if (lastNavTsRef.current === navigateToMainOID.timestamp) return;
        if (!questionsInGroup || questionsInGroup.length === 0) return; // wait until the list is ready
        const oidOf = (q: any) => q?.itemDef?.OID || q?.itemRef?.ItemOID;
        let targetIndex = questionsInGroup.findIndex((q: any) => oidOf(q) === navigateToMainOID.oid);
        if (targetIndex < 0) {
            // The target is not one of the card's pageable questions — e.g. a derived
            // (MethodOID) field or a controlling question, which the sidebar lists but the
            // card renders inline rather than as its own page. Land on the nearest PRECEDING
            // visible question in ItemRef order so the click still navigates to where that
            // field is shown, instead of doing nothing.
            const refOIDs: string[] = (itemGroupDefs?.[0]?.ItemRef || []).map((r: any) => r.ItemOID);
            const pos = refOIDs.indexOf(navigateToMainOID.oid);
            if (pos < 0) return; // not resolvable yet; let a later list update retry
            for (let p = pos; p >= 0 && targetIndex < 0; p--) {
                targetIndex = questionsInGroup.findIndex((q: any) => oidOf(q) === refOIDs[p]);
            }
            if (targetIndex < 0) targetIndex = 0; // last resort: first question of the step
        }
        lastNavTsRef.current = navigateToMainOID.timestamp;
        if (targetIndex !== currentQuestionIndex && onQuestionChange) {
            onQuestionChange(targetIndex);
        }
    }, [navigateToMainOID, questionsInGroup, stepId, currentQuestionIndex, onQuestionChange, itemGroupDefs]);

    const conditionalQuestionsToShow = React.useMemo(() => {
        const questionsToShow: any[] = [];

        if (currentQuestionData && itemGroupDefs && itemGroupDefs.length > 0) {
            const currentItemGroup = itemGroupDefs[0];
            if (currentItemGroup && currentItemGroup.ItemRef && Array.isArray(currentItemGroup.ItemRef)) {
                // Find conditional questions that depend on current question and evaluate them
                currentItemGroup.ItemRef.forEach((itemRef: any) => {
                    if (itemRef.CollectionExceptionConditionOID) {
                        const conditionDef = conditionDefs?.find(cd => cd.OID === itemRef.CollectionExceptionConditionOID);
                        if (conditionDef) {
                            const expressionText = conditionDef.FormalExpression?.text;
                            if (!expressionText) return;
                            const referencedOIDs = Array.from(expressionText.matchAll(/I\.\d+/g)).map(m => m[0]);

                            if (!referencedOIDs.includes(currentQuestionData.itemDef?.OID ?? '')) return;
                       
                            const formData: Record<string, any> = {};
                            itemGroupDatas?.forEach((igd) =>
                                igd?.ItemData?.forEach((id: any) => {
                                    if (id?.ItemOID) formData[id.ItemOID] = id;
                                })
                            );
                            if (parentAnswersData) Object.assign(formData, parentAnswersData);

                            let shouldShow = false;
                            try {
                                const result = executeMethodExpression(expressionText, formData);
                                shouldShow = !result;
                            } catch {
                                shouldShow = false;
                            }

                            if (shouldShow) {
                                questionsToShow.push({
                                    itemRef,
                                    groupOID: currentItemGroup.OID
                                });
                            }
                        }
                    }
                });
            }
        }

        return questionsToShow;
    }, [currentQuestionData, itemGroupDatas, itemGroupDefs, conditionDefs, parentAnswersData]);

    useEffect(() => {
        conditionalQuestionsToShow.forEach(({ itemRef, groupOID }) => {
            const item = {
                "id": itemRef.ItemOID,
                "indexes": 0,
                "status": true,
                "GroupOID": groupOID,
                "TradeOID": OID,
                "ItemGrpIndex": 0
            };
            show(item);
        });
    }, [conditionalQuestionsToShow, OID, show]);

    function handleNestedSuffixSync(controlItemOID: string, newValue: any, parentRepeatKey?: number) {

        // Find all nested groups controlled by this question
        const nestedGroups = repeatingChildren?.filter(rc =>
            rc.controlItemOID === controlItemOID && rc.isNested
        );

        if (!nestedGroups || nestedGroups.length === 0) {
            return;
        }

        const newCount = parseInt(newValue) || 0;
        const parentSuffix = parentRepeatKey ? `_${parentRepeatKey}` : '';

        nestedGroups.forEach(nestedGroup => {
            // Get all questions in the nested group
            const nestedQuestions = nestedGroup.itemGroupDef?.ItemRef || [];


            // Find all existing answers for this nested group with this parent suffix
            const existingNestedAnswers = Object.keys(answersData).filter(key => {
                return nestedQuestions.some(q => {
                    // Match pattern: QuestionOID + ParentSuffix + _NestedIndex
                    const basePattern = `${q.ItemOID}${parentSuffix}_`;
                    return key.startsWith(basePattern);
                });
            });


            // Remove answers for instances beyond the new count
            const updatedAnswers = { ...answersData };
            let deletedCount = 0;

            existingNestedAnswers.forEach(answerKey => {
                // Extract the nested instance index from the key
                const match = answerKey.match(/_(\d+)$/);
                if (match) {
                    const nestedIndex = parseInt(match[1]);
                    if (nestedIndex > newCount) {
                        delete updatedAnswers[answerKey];
                        deletedCount++;
                    }
                }
            });

            if (deletedCount > 0) {
                setAnswersData(updatedAnswers);


                const updatedItemGrpData = (itemGroupDatas.length > 0 ? [...itemGroupDatas] : [...itemGroupData]).filter((group: any) => {
                    // Keep groups that are NOT nested instances of this group beyond the new count
                    if (group.ItemGroupOID === nestedGroup.itemGroupDef.OID &&
                        group.ParentItemGroupRepeatKey === parentRepeatKey) {
                        // This is a nested instance - check if it should be removed
                        const shouldKeep = group.ItemGroupRepeatKey <= newCount;
                        if (!shouldKeep) {
                        }
                        return shouldKeep;
                    }
                    return true; // Keep all non-nested groups
                });

                // Update ItemGroupData state
                if (itemGroupDatas.length > 0) {
                    setItemGroupDatas(updatedItemGrpData);
                } else {
                    setItemGroupData(updatedItemGrpData);
                }

            }
        });
    }

    //To set ItemData
    // `meta.isUserEdit`: see MultiChoice_v2.tsx's own doc on its call site — set ONLY by a
    // genuine, deliberate user click, never inferred from the mere fact this was called.
    function OnItemDataChange(item: any, meta?: { isUserEdit?: boolean }) {
        const itemOID = item?.ItemOID;

        lastUserEditedOIDRef.current = itemOID;

        // Mark protected as EARLY as possible — before any of this function's own
        // cascading effects (cleanup, parent notification) can fire — so there is no
        // window where an external sync could race ahead of this. See
        // resolveProtectedItemValue's own doc for the full mechanism.
        if (meta?.isUserEdit && itemOID) {
            markItemUserEdited(itemOID, item);
        }

        if (item?.ItemGroupRepeatKey && item.ItemGroupRepeatKey > 0 && !item?.ItemGroupOID) {
            const itemKey = `${itemOID}_${item.ItemGroupRepeatKey}`;
            return; // Don't process this malformed entry
        }

        if (!item?.ItemGroupOID) {
            // Find which ItemGroup this ItemOID belongs to
            const parentItemGroup = itemGroupDefs?.find((igd: any) =>
                igd?.ItemRef?.some((ir: any) => ir.ItemOID === itemOID)
            );

            if (parentItemGroup) {
                item.ItemGroupOID = parentItemGroup.OID;
            } else {
            }
        }

        const isNestedControlQuestion = repeatingChildren?.some(rc =>
            rc.controlItemOID === itemOID && rc.isNested
        );

        if (isNestedControlQuestion) {
            // Handle nested suffix synchronization
            handleNestedSuffixSync(itemOID, item?.Value, item?.ItemGroupRepeatKey);
        }

        const isMultiInstanceItem = isItemInRepeatingInstance(itemOID);
        if (isMultiInstanceItem) {
            setIsMultiInstanceOperation(true);
            // Longer protection for multi-instance items
            setTimeout(() => {
                setIsMultiInstanceOperation(false);
            }, 3000);
        }

        debugCleanup(`Answer changed: ${itemOID} = "${item?.Value}"`);

        setUserIsActivelySelecting(true);
        const userInteractionTimer = setTimeout(() => {
            setUserIsActivelySelecting(false);
        }, isMultiInstanceItem ? 1000 : 200); // Faster response for immediate selection changes

        setTimeout(() => {
            clearTimeout(userInteractionTimer);
            setUserIsActivelySelecting(false);
        }, isMultiInstanceItem ? 2000 : 500); // Reduced safety net timeout

        // `recentlySelectedItems` below is now used ONLY by syncUIStateWithBackend's own
        // cleanup-protection ("don't delete an item I just touched") — the deselect-revert
        // bug this session spent many rounds on is fixed by the SEPARATE, value-based
        // protectedItemValuesRef mechanism above (see resolveProtectedItemValue's own
        // doc), which does not depend on a timer at all. Kept at its original duration —
        // this timer only controls how long cleanup treats a touched item as "still
        // relevant enough not to prune," not whether an edit can be silently reverted.
        const preservationTime = isMultiInstanceItem ? 5000 : 2000;
        if (meta?.isUserEdit) {
            setRecentlySelectedItems(prev => ({
                ...prev,
                [itemOID]: {
                    item: item,
                    timestamp: Date.now(),
                    isMultiInstance: isMultiInstanceItem
                }
            }));
        }

        // Clear the recent selection after a delay (only scheduled when one was actually set above).
        if (meta?.isUserEdit) {
            setTimeout(() => {
                setRecentlySelectedItems(prev => {
                    const updated = { ...prev };
                    delete updated[itemOID];
                    return updated;
                });
            }, preservationTime);
        }

        const oldAnswer = answersData[itemOID];
        const oldValue = oldAnswer?.value || oldAnswer;

        const shouldCleanup = oldValue !== item?.Value;

        if (shouldCleanup) {
            setIsCleanupInProgress(true);
            let questionsToClean: string[] = [];

            if (dependencyParser) {
                const currentAnswers: Record<string, any> = {};
                Object.keys(answersData).forEach(key => {
                    currentAnswers[key] = answersData[key]; // Keep full answer object
                });

                currentAnswers[itemOID] = {
                    ...(answersData[itemOID] || {}),
                    CodeList: item?.CodeList, // Use the new CodeList with updated IsEnabled flags
                    value: item?.Value?.toString() || ''
                };

                // Determine if this is an instance question or base question
                const isInstanceQuestion = item?.ItemGroupRepeatKey && item.ItemGroupRepeatKey > 0;

                if (isInstanceQuestion) {
                    // For instance questions, use instance-aware cleanup
                    const instanceSuffix = `_${item.ItemGroupRepeatKey}`;
                    questionsToClean = dependencyParser.getCleanupTargetsWithInstance(
                        itemOID,
                        instanceSuffix,
                        item?.Value?.toString() || '',
                        currentAnswers
                    );
                } else {
                    // For base questions, use base cleanup (which we'll enhance to handle all instances)

                    questionsToClean = dependencyParser.getCleanupTargets(
                        itemOID,
                        item?.Value?.toString() || '',
                        currentAnswers
                    );
                }

            }

            // For base questions, include ALL instance variations of dependent questions
            // For instance questions, the parser already returned the specific instance keys
            const allKeysToClean: string[] = [];
            (questionsToClean ?? []).forEach(questionId => {
                allKeysToClean.push(questionId);
                // add all instance variations
                if (!questionId.includes('_')) {
                    Object.keys(answersData).forEach(key => {
                        if (key.startsWith(`${questionId}_`)) {
                            allKeysToClean.push(key);
                        }
                    });
                }
            });

            const hadCleanupItems = allKeysToClean.length > 0;
            if (hadCleanupItems) {
                cleanupLocalUIState(allKeysToClean);
            }

            // Clear the flag after cleanup completes
            setTimeout(() => {
                setIsCleanupInProgress(false);

                if (hadCleanupItems && !userIsActivelySelecting && !isMultiInstanceOperation) {
                    syncUIStateWithBackend();
                }
            }, isMultiInstanceItem ? 200 : 100); // Slightly longer for multi-instance
        }

        // DIRECT ITEM DATA UPDATE: Handle multi-instance items with minimal interference
        if (itemData.length !== 0 && item !== undefined && item !== null) {
            const index = itemData.findIndex((obj) => obj.ItemOID === itemOID && obj.ItemGroupRepeatKey == item.ItemGroupRepeatKey);

            const updatedTextData = [...itemData];
            if (index !== -1) {
                updatedTextData[index] = item;
                if (isMultiInstanceItem) {
                }
            }
            else {
                updatedTextData.push(item);
                if (isMultiInstanceItem) {
                }
            }
            setItemData(updatedTextData);
            handleItemGroupData(updatedTextData);
        } else {
            setItemData([...itemData, item]);
            handleItemGroupData([item]);
            if (isMultiInstanceItem) {
            }
        }

        const answerKey = item?.ItemGroupRepeatKey && item.ItemGroupRepeatKey > 0
            ? `${itemOID}_${item.ItemGroupRepeatKey}`
            : itemOID;

        const isValueEmpty = item?.Value === '' || item?.Value === null || item?.Value === undefined;
        const isRangeCheckFailed = !!item?.rangeCheckFailed;

        if (isValueEmpty && !isRangeCheckFailed) {
            setAnswersData((prev: any) => {
                const updated = { ...prev };
                delete updated[answerKey];
                return updated;
            });
        } else {
            setAnswersData((prev: any) => ({
                ...prev,
                [answerKey]: {
                    ...(prev[answerKey] || {}),
                    value: item.Value,
                    dataType: item?.CodeList ? 'singleselect' : (prev[answerKey]?.dataType || 'string'),
                    itemOID: itemOID,
                    itemGroupOID: item?.ItemGroupOID || prev[answerKey]?.itemGroupOID,
                    repeatKey: item?.ItemGroupRepeatKey ?? prev[answerKey]?.repeatKey ?? 0,
                    CodeList: item?.CodeList || prev[answerKey]?.CodeList,
                    timestamp: Date.now(),
                    rangeCheckFailed: isRangeCheckFailed
                }
            }));
        }
    }

    // HELPER FUNCTION: Find all dependent questions recursively
    function findAllDependentQuestions(parentQuestionOID: string): string[] {
        const dependents: string[] = [];
        const visited = new Set<string>();

        function findDependentsRecursive(currentParentOID: string, level: number = 0) {
            if (visited.has(currentParentOID) || level > 10) return; // Prevent infinite recursion
            visited.add(currentParentOID);


            // Search through all item groups for conditional questions
            itemGroupDefs?.forEach((igd) => {
                igd?.ItemRef?.forEach((ir: any) => {
                    if (ir.CollectionExceptionConditionOID) {
                        // Find the condition definition
                        const conditionDef = conditionDefs?.find(cd => cd.OID === ir.CollectionExceptionConditionOID);
                        if (conditionDef) {
                            const expressionText = conditionDef.FormalExpression?.text;
                            if (expressionText) {
                                let conditionParentOID = '';

                                // Handle different condition formats
                                if (expressionText.includes('||')) {
                                    // Multiple conditions - check all parts
                                    const conditions = expressionText.split('||');
                                    for (const condition of conditions) {
                                        const extractedOID = condition.split("==")[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                        if (extractedOID === currentParentOID) {
                                            conditionParentOID = extractedOID;
                                            break;
                                        }
                                    }
                                } else {
                                    // Single condition
                                    conditionParentOID = expressionText.split("==")[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                }

                                if (conditionParentOID === currentParentOID) {
                                    const dependentOID = ir.ItemOID;
                                    if (!dependents.includes(dependentOID)) {
                                        dependents.push(dependentOID);

                                        // RECURSIVE: Find dependents of this dependent (multi-level cleanup)
                                        findDependentsRecursive(dependentOID, level + 1);
                                    }
                                }
                            }
                        }
                    }
                });
            });
        }

        findDependentsRecursive(parentQuestionOID);

        return dependents;
    }

    let itemGroupDataArray: ItemGroupData[] = [];

    function handleNestedODMConversion(itemData: any) {
        // Process nested instance items
        const nestedInstanceItems = itemData.filter((item: any) => {
            const isNested = repeatingChildren?.some(rc =>
                rc.itemGroupDef.OID === item.ItemGroupOID && rc.isNested
            );
            return isNested && item?.ItemGroupOID && item?.ItemGroupRepeatKey >= 0;
        });

        const updatedItemGrpData = itemGroupDatas.length > 0 ? [...itemGroupDatas] : [...itemGroupData];

        nestedInstanceItems.forEach((nestedItem: any) => {
            const index = updatedItemGrpData.findIndex((obj: any) =>
                obj.ItemGroupOID === nestedItem.ItemGroupOID &&
                obj.ItemGroupRepeatKey === nestedItem.ItemGroupRepeatKey &&
                obj.ParentItemGroupRepeatKey === nestedItem.ParentItemGroupRepeatKey
            );

            if (index !== -1) {
                // Update existing ItemGroupData
                const itd = [...updatedItemGrpData[index].ItemData];
                const ind = itd.findIndex((obj: any) => obj.ItemOID === nestedItem.ItemOID);

                if (ind !== -1) {
                    itd[ind] = nestedItem;
                } else {
                    itd.push(nestedItem);
                }

                updatedItemGrpData[index] = {
                    ItemData: itd,
                    ItemGroupOID: nestedItem.ItemGroupOID,
                    ItemGroupRepeatKey: nestedItem.ItemGroupRepeatKey,
                    ParentItemGroupRepeatKey: nestedItem.ParentItemGroupRepeatKey
                };
            } else {
                updatedItemGrpData.push({
                    ItemData: [nestedItem],
                    ItemGroupOID: nestedItem.ItemGroupOID,
                    ItemGroupRepeatKey: nestedItem.ItemGroupRepeatKey,
                    ParentItemGroupRepeatKey: nestedItem.ParentItemGroupRepeatKey
                });
            }
        });

        const cleanedItemGrpData = updatedItemGrpData.filter((group: any) => {
            // If this is a nested group (has ParentItemGroupRepeatKey), keep it
            if (group.ParentItemGroupRepeatKey !== undefined) {
                return true;
            }

            // Check if this is a group that SHOULD have ParentItemGroupRepeatKey
            const isNestedGroup = repeatingChildren?.some(rc =>
                rc.itemGroupDef.OID === group.ItemGroupOID && rc.isNested
            );

            // If it's a nested group but doesn't have ParentItemGroupRepeatKey, it's an orphan - remove it
            if (isNestedGroup && group.ItemGroupRepeatKey > 0) {
                return false;
            }

            return true; // Keep non-nested groups
        });

        // Update state
        if (itemGroupDatas.length > 0) {
            setItemGroupDatas(cleanedItemGrpData);
        } else {
            setItemGroupData(cleanedItemGrpData);
        }

        // Prepare ODM structure
        let itGrp = {
            "ItemGroup": updatedItemGrpData,
            "TradeOID": OID
        };

        // Send to Requirements_v2
        sendFilteredDataToRequirements(itGrp);
    }

    //To set ItemGroupData
    const handleItemGroupData = (itemData: any) => {

        const incomingDerivedItems = (itemData || []).filter((item: any) => derivedItemOIDsRef.current.has(item?.ItemOID));


        let repeatCount = {};

        const hasNestedInstanceData = itemData.find(item => {
            if (!item?.ItemGroupOID || item?.ItemGroupRepeatKey === undefined) return false;

            // Check if this ItemGroupOID belongs to a nested repeating group
            const isNested = repeatingChildren?.some(rc =>
                rc.itemGroupDef.OID === item.ItemGroupOID && rc.isNested
            );

            return isNested;
        });

        if (hasNestedInstanceData) {

            // For nested instances, we need to handle ODM conversion specially
            handleNestedODMConversion(itemData);
            return; // Early return after handling nested data
        }

        if (itemData.find(item => item?.ItemGroupOID && item?.ItemGroupRepeatKey >= 0)) {
            const td = itemData.find(item => item?.ItemGroupOID && item?.ItemGroupRepeatKey >= 0);

            const multiInstanceItems = itemData.filter(item => item?.ItemGroupOID && item?.ItemGroupRepeatKey >= 0);


            // Find existing ItemGroupData with same OID and RepeatKey
            const updatedItemGrpData = itemGroupDatas.length > 0 ? [...itemGroupDatas] : [...itemGroupData];


            multiInstanceItems.forEach(td => {

                const index = updatedItemGrpData.findIndex((obj) => {
                    const basicMatch = obj.ItemGroupOID === td.ItemGroupOID && obj.ItemGroupRepeatKey === td.ItemGroupRepeatKey;

                    // For nested instances, also match ParentItemGroupRepeatKey
                    if (td.ParentItemGroupRepeatKey !== undefined) {
                        return basicMatch && obj.ParentItemGroupRepeatKey === td.ParentItemGroupRepeatKey;
                    }

                    // For non-nested, just basic match
                    return basicMatch;
                });

                if (index !== -1) {
                    // Update existing ItemGroupData
                    const itd = [...updatedItemGrpData[index].ItemData];
                    const ind = itd.findIndex((obj) => obj.ItemOID === td.ItemOID);
                    if (ind !== -1) {
                        // Merge rather than replace so fields the incoming snapshot omits
                        // (notably MetaKey on derived items) are preserved instead of dropped.
                        itd[ind] = { ...itd[ind], ...td };
                    } else {
                        itd.push(td);
                    }

                    const updatedGroup: any = {
                        ItemData: itd,
                        ItemGroupOID: td.ItemGroupOID,
                        ItemGroupRepeatKey: td.ItemGroupRepeatKey
                    };

                    // Add ParentItemGroupRepeatKey if present (nested instance)
                    if (td.ParentItemGroupRepeatKey !== undefined) {
                        updatedGroup.ParentItemGroupRepeatKey = td.ParentItemGroupRepeatKey;
                    }

                    if (td.SummarizeMode === true) {
                        updatedGroup.SummarizeMode = true;
                    }

                    updatedItemGrpData[index] = updatedGroup;
                } else {
                    // Create new ItemGroupData entry
                    const newGroup: any = {
                        ItemData: [td],
                        ItemGroupOID: td.ItemGroupOID,
                        ItemGroupRepeatKey: td.ItemGroupRepeatKey
                    };

                    // Add ParentItemGroupRepeatKey if present (nested instance)
                    if (td.ParentItemGroupRepeatKey !== undefined) {
                        newGroup.ParentItemGroupRepeatKey = td.ParentItemGroupRepeatKey;
                    }

                    if (td.SummarizeMode === true) {
                        newGroup.SummarizeMode = true;
                    }

                    updatedItemGrpData.push(newGroup);

                }
            });

            if (itemGroupDatas.length > 0) {
                setItemGroupDatas(updatedItemGrpData);
            } else {
                setItemGroupData(updatedItemGrpData);
            }

            let itGrp = {
                "ItemGroup": updatedItemGrpData,
                "TradeOID": OID
            };


            // Log nested instances specifically
            const nestedGroups = itGrp.ItemGroup.filter(g => g.ParentItemGroupRepeatKey !== undefined);
            if (nestedGroups.length > 0) {
            } else {
            }

            const dataKey = JSON.stringify(itGrp.ItemGroup.map((g: any) => ({
                oid: g.ItemGroupOID,
                repeatKey: g.ItemGroupRepeatKey,
                parentRepeatKey: g.ParentItemGroupRepeatKey, // Include in dedup check
                items: g.ItemData.map((i: any) => ({
                    oid: i.ItemOID,
                    value: i.Value,
                    // Include CodeList enabled status to detect checkbox changes
                    codeListEnabled: i.CodeList?.map((cl: any) =>
                        cl.CodeListItem?.map((cli: any) => cli.IsEnabled).join(',')
                    ).join('|') || ''
                }))
            })));

            const now = Date.now();
            const lastSent = lastSentDataRef.current[dataKey];

            if (lastSent && now - lastSent.timestamp < 100) {
                return;
            }

            lastSentDataRef.current[dataKey] = { value: itGrp, timestamp: now };

            sendFilteredDataToRequirements(itGrp);
            return; // Early return to skip the complex loop below
        }

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
                                    // if (itemGroupData[i].ItemGroupRepeatKey !== i) {

                                    const index = itemGroupData.findIndex((obj) => obj.ItemGroupOID === igd.OID && obj.ItemGroupRepeatKey == repeatIndexValue);
                                    const updatedItemGrpData = [...itemGroupData];
                                    if (index !== -1) {
                                        const itd = [...updatedItemGrpData[index].ItemData];
                                        const ind = itd.findIndex((obj) => obj.ItemOID === td.ItemOID && obj.ItemGroupRepeatKey == td.ItemGroupRepeatKey);
                                        if (ind !== -1) {
                                            itd[ind] = td;
                                        } else {
                                            itd.push(td)
                                        }
                                        updatedItemGrpData[index] = { ItemData: itd, ItemGroupOID: updatedItemGrpData[index].ItemGroupOID, ItemGroupRepeatKey: repeatIndexValue };
                                    }
                                    else {
                                        if (td.ItemGroupRepeatKey === repeatIndexValue) {
                                            updatedItemGrpData.push({ ItemData: [td], ItemGroupOID: igd.OID, ItemGroupRepeatKey: repeatIndexValue });
                                        }
                                    }
                                    setItemGroupData(updatedItemGrpData);
                                    let itGrp = {
                                        "ItemGroup": updatedItemGrpData,
                                        "TradeOID": OID
                                    }
                                    sendFilteredDataToRequirements(itGrp);
                                } else {
                                    const repeatKey = td.ItemGroupRepeatKey || 0;
                                    itemGroupDataArray.push({ ItemData: [td], ItemGroupOID: igd.OID, ItemGroupRepeatKey: repeatKey });
                                    setItemGroupData(itemGroupDataArray);
                                    let itGrp = {
                                        "ItemGroup": itemGroupDataArray,
                                        "TradeOID": OID
                                    }
                                    sendFilteredDataToRequirements(itGrp);
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
                                    const index = itemGroupDatases.findIndex((obj) => obj.ItemGroupOID === igd.OID && obj.ItemGroupRepeatKey === td.ItemGroupRepeatKey);
                                    if (index !== -1) {
                                        const itd = [...updatedItemGrpData[index].ItemData];
                                        const ind = itd.findIndex((obj) => obj.ItemOID === td.ItemOID && obj.ItemGroupRepeatKey === td.ItemGroupRepeatKey);
                                        if (ind !== -1) {
                                            itd[ind] = td;
                                        } else {
                                            itd.push(td)
                                        }
                                        updatedItemGrpData[index] = { ItemData: itd, ItemGroupOID: updatedItemGrpData[index].ItemGroupOID, ItemGroupRepeatKey: td.ItemGroupRepeatKey };
                                    }
                                    else {
                                        updatedItemGrpData.push({ ItemData: [td], ItemGroupOID: igd.OID, ItemGroupRepeatKey: td.ItemGroupRepeatKey });
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

                debugCleanup(`Sending data to Requirements_v2`, {
                    totalItems: updatedItemGrpData.reduce((total, group) => total + group.ItemData.length, 0),
                    items: updatedItemGrpData.flatMap(group =>
                        group.ItemData.map(item => `${item.ItemOID}="${item.Value}"`).slice(0, 3)
                    ).join(', ')
                });

                sendFilteredDataToRequirements(itGrp);
            }
        }


    }

    const syncCurrentBidDataSession = React.useCallback(() => {
        if (typeof window === 'undefined') return;

        const pathname = window.location.pathname;
        if (pathname !== '/soldproduction/projectinformation' && pathname !== '/createChangeOrder') {
            return;
        }

        const storedBidDataString = sessionStorage.getItem('currentBidData');
        if (!storedBidDataString) return;

        const sourceItemGroups = itemGroupDatas.length > 0 ? itemGroupDatas : itemGroupData;
        if (!sourceItemGroups || sourceItemGroups.length === 0) return;

        const derivedItems: any[] = [];
        itemGroupDefs.forEach((itemGroupDef: any) => {
            if (!itemGroupDef.ItemRef) return;

            const isRepeating = itemGroupDef.Repeating === 'Yes';

            itemGroupDef.ItemRef.forEach((itemRef: any) => {
                if (!itemRef.MethodOID) return;

                const md = methodDefs?.find((m: any) => m.OID === itemRef.MethodOID);
                if (md?.FormalExpression?.text?.includes('RepeatCount')) return;

                const repeatKeys = new Set<number>();
                if (isRepeating) {
                    sourceItemGroups.forEach((group: any) => {
                        if (group.ItemGroupOID === itemGroupDef.OID) {
                            repeatKeys.add(group.ItemGroupRepeatKey ?? 0);
                        }
                    });
                    if (repeatKeys.size === 0) {
                        repeatKeys.add(0);
                    }
                } else {
                    repeatKeys.add(0);
                }

                repeatKeys.forEach(repeatKey => {
                    const group = sourceItemGroups.find((candidate: any) =>
                        candidate.ItemGroupOID === itemGroupDef.OID && (candidate.ItemGroupRepeatKey ?? 0) === repeatKey
                    );
                    const itemData = group?.ItemData?.find((candidate: any) => candidate.ItemOID === itemRef.ItemOID);
                    if (!itemData) return;

                    derivedItems.push({
                        ItemOID: itemRef.ItemOID,
                        Value: itemData.Value,
                        ItemGroupRepeatKey: repeatKey,
                        isDeleted: isDeletedFlag(itemData.isDeleted)
                    });
                });
            });
        });

        if (derivedItems.length === 0) return;

        try {
            const bidData = JSON.parse(storedBidDataString);
            if (!bidData || !Array.isArray(bidData.answerData)) return;

            const syncSignature = JSON.stringify(derivedItems.map((item: any) => ({
                oid: item.ItemOID,
                repeatKey: item.ItemGroupRepeatKey ?? 0,
                value: item.Value,
                isDeleted: item.isDeleted === true
            })));

            if (lastSyncedCurrentBidDataRef.current === syncSignature) {
                return;
            }

            let hasChanges = false;
            const updatedAnswerData = bidData.answerData.map((answer: any) => {
                let parsedAnswersData: any;

                try {
                    parsedAnswersData = JSON.parse(answer?.answersData || 'null');
                } catch {
                    return answer;
                }

                const itemGroupData = parsedAnswersData?.EventData?.[0]?.FormData?.[0]?.ItemGroupData;
                if (!Array.isArray(itemGroupData)) {
                    return answer;
                }

                let answerChanged = false;
                const nextItemGroupData = itemGroupData.map((group: any) => {
                    let groupChanged = false;
                    const nextItemData = (group.ItemData || []).map((item: any) => {
                        const repeatKey = item.ItemGroupRepeatKey ?? group.ItemGroupRepeatKey ?? 0;
                        const match = derivedItems.find((derivedItem: any) =>
                            derivedItem.ItemOID === item.ItemOID && (derivedItem.ItemGroupRepeatKey ?? 0) === repeatKey
                        );

                        if (!match) {
                            return item;
                        }

                        const nextValue = match.Value;
                        const nextIsDeleted = match.isDeleted === true;
                        const currentValue = item.Value;
                        const currentIsDeleted = item.isDeleted === true;

                        if (currentValue === nextValue && currentIsDeleted === nextIsDeleted) {
                            return item;
                        }

                        groupChanged = true;
                        answerChanged = true;
                        return {
                            ...item,
                            Value: nextValue,
                            isDeleted: nextIsDeleted
                        };
                    });

                    return groupChanged ? { ...group, ItemData: nextItemData } : group;
                });

                if (!answerChanged) {
                    return answer;
                }

                hasChanges = true;
                return {
                    ...answer,
                    answersData: JSON.stringify({
                        ...parsedAnswersData,
                        EventData: [{
                            ...parsedAnswersData.EventData?.[0],
                            FormData: [{
                                ...parsedAnswersData.EventData?.[0]?.FormData?.[0],
                                ItemGroupData: nextItemGroupData
                            }]
                        }]
                    }),
                    updatedDate: new Date().toISOString()
                };
            });

            const nextBidData = {
                ...bidData,
                answerData: updatedAnswerData
            };

            if (!hasChanges) {
                lastSyncedCurrentBidDataRef.current = syncSignature;
                return;
            }

            lastSyncedCurrentBidDataRef.current = syncSignature;
            sessionStorage.setItem('currentBidData', JSON.stringify(nextBidData));
        } catch (error) {
            // Keep UI flow resilient if the stored snapshot is malformed.
        }
    }, [itemGroupData, itemGroupDatas, itemGroupDefs, methodDefs]);

    React.useEffect(() => {
        if (!methodDependencyHash) {
            return;
        }

        syncCurrentBidDataSession();
    }, [methodDependencyHash, syncCurrentBidDataSession]);

    function showGroup(status: any, cOID: any, OID: any, itemDataValue: any, IsRepeat: boolean) {
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
                                //if (res.OID === "M.1") {
                                const expressionText = res?.FormalExpression?.text;
                                if (expressionText && expressionText.includes(':')) {
                                    const [itemOID, type] = expressionText
                                        .replace(/[!()]/g, '')
                                        .split(':')
                                        .map(str => str.trim());

                                    let itemGrpDef = itemGroupDefs.find((obj) => obj.OID === ir.ItemGroupOID);
                                    let itemIndex = itemGrpDef?.ItemRef.findIndex((obj) => obj.ItemOID === itemOID);
                                    //to check whether item of methodOID is present in the removed itemGroup...
                                    if (itemIndex !== -1 && itemIndex !== undefined || null) {
                                        //if Yes...
                                        let itemGrpRef = fd?.ItemGroupRef?.find((x) => x.MethodOID === res.OID);
                                        if (itemGrpRef !== null || undefined) {
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

    const batchedShow = React.useCallback((item: any) => {
        // Add to batch queue
        showStatusBatchRef.current.push(item);

        // Clear existing timer/frame
        if (showStatusTimerRef.current) {
            clearTimeout(showStatusTimerRef.current);
        }
        if (showStatusFrameRef.current) {
            cancelAnimationFrame(showStatusFrameRef.current);
        }

        showStatusFrameRef.current = requestAnimationFrame(() => {
            // Deduplicate items by id (keep latest)
            const itemMap = new Map();
            showStatusBatchRef.current.forEach(batchItem => {
                const key = `${batchItem.id}_${batchItem.ItemGrpIndex || 0}`;
                itemMap.set(key, batchItem);
            });

            const uniqueItems = Array.from(itemMap.values());

            React.startTransition(() => {
                // Batch all show calls into a single state update
                uniqueItems.forEach(batchItem => {
                    show(batchItem);
                });
            });

            // Clear batch
            showStatusBatchRef.current = [];
            showStatusFrameRef.current = null;
        });
    }, [show]);

    //To show question based on condition
    function showQuestion(status: any, itemOID: any, OID: any, itemDataValue: any, IsRepeat: boolean, itemGrpIndex: number) {

        if (status) {
            itemGroupDefs?.map((igd) => {
                igd?.ItemRef?.map((ir, index) => {
                    showGroup(status, itemOID, OID, itemDataValue, IsRepeat);
                    if (ir.CollectionExceptionConditionOID === itemOID) {
                        let item = {
                            "id": ir.ItemOID,
                            "indexes": index,
                            "status": status,
                            "GroupOID": igd.OID,
                            "TradeOID": OID,
                            "ItemGrpIndex": itemGrpIndex
                        }
                        batchedShow(item); //  Use batched version
                    }
                })
            })

        } else {

            itemGroupDefs?.map((igd) => {
                igd?.ItemRef?.map((ir, index) => {
                    //no need to be inside itemRef for loop (showGroup)
                    showGroup(status, itemOID, OID, itemDataValue, IsRepeat);
                    if (ir.CollectionExceptionConditionOID === itemOID) {
                        let item = {
                            "id": ir.ItemOID,
                            "indexes": index,
                            "status": status,
                            "GroupOID": igd.OID,
                            "TradeOID": OID,
                            "ItemGrpIndex": itemGrpIndex
                        }
                        batchedShow(item); //  Use batched version
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
                                            batchedShow(item); //  Use batched version
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
                                                                batchedShow(item); //  Use batched version
                                                                resetItemData(item)
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
                                                                                    batchedShow(item); //  Use batched version
                                                                                    resetItemData(item)
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
        setItemData(prevItemData => {
            return prevItemData.map((i: any) => {
                if (i.ItemOID === item.id && i.ItemGroupRepeatKey === item.ItemGrpIndex) {
                    return {
                        ...i,
                        Value: '', // Clear the value so question starts fresh
                        CodeList: i.CodeList?.map((c: any) => ({
                            ...c,
                            CodeListItem: c.CodeListItem.map((cli: any) => ({
                                ...cli,
                                IsEnabled: false
                            }))
                        })) || []
                    };
                }
                return i;
            });
        });
    }

    //For repeat item
    function handleRepeat(itemGroup: any, indexes: any) {

        if (itemGroup !== null && itemGroup !== undefined) {

            // for (let i = 0; i < itemGroupDefs.length; i++) {
            //     if (itemGroupData[i].ItemGroupOID === itemGroup.OID) {
            //         itemGroupData[i].ItemGroupRepeatKey = itemGroupData[i].ItemGroupRepeatKey + 1;           
            //     }
            // }
            //itemGroupDefs.splice(indexes, 0, itemGroup);
            showRepeat(itemGroupDefs);
        }

    }

    //For delete item in Repeat
    function handleDelete(event: any, itemGroup: any, index: number) {
        if (itemGroup == null) return;

        const updatedItemGroups = itemGroupDefs.filter((_, i) => i !== index);
        showRepeat(updatedItemGroups);
    }

    // NEW: Unified RepeatCount logic with multi-path support
    // RENAMED to _local to avoid conflict with prop
    const getMethodDefRepeatCount_local = (methodOID: string): number => {
        if (getMethodDefRepeatCount) {
            return getMethodDefRepeatCount(methodOID);
        }

        const methodDef = methodDefs?.find((md) => md.OID === methodOID);
        if (!methodDef) {
            return 0;
        }

        const expressionText = methodDef?.FormalExpression?.text;
        if (!expressionText || !expressionText.includes('RepeatCount')) {
            return 0;
        }

        // Extract the controlling question ID
        const [mdItemOID, type] = expressionText
            .replace(/[!()]/g, '')
            .split(':')
            .map(str => str.trim());

        if (type !== 'RepeatCount') {
            return 0;
        }

        if (answersDataRef.current[mdItemOID]?.value !== undefined && answersDataRef.current[mdItemOID]?.value !== null && answersDataRef.current[mdItemOID]?.value !== '') {
            const answerValue = answersDataRef.current[mdItemOID].value;
            if (!isNaN(parseInt(answerValue))) {
                const countFromAnswers = parseInt(answerValue);
                return countFromAnswers;
            }
        }

        const controllerQuestionId = getControllerForQuestion(mdItemOID);

        const context: MultiInstanceContext = {
            questionId: mdItemOID,
            controllerQuestionId: controllerQuestionId,
            controllerValue: answersData[controllerQuestionId],
            parentPath: getParentPath(mdItemOID),
            methodDefs: methodDefs || [],
            itemGroupData: itemGroupDatas || [],
            answersData: answersData
        };


        const result = shouldRenderMultiInstance(context);

        if (!result.shouldRender && result.instanceCount === 0) {

            let itemGrpData = itemGroupDatas?.find((igd) => igd?.ItemData?.find((id) => id?.ItemOID === mdItemOID));
            if (itemGrpData) {
                let iData = itemGrpData?.ItemData?.find((id) => id?.ItemOID === mdItemOID);
                if (iData && iData.Value && !isNaN(parseInt(iData.Value))) {
                    const fallbackCount = parseInt(iData.Value);
                    return fallbackCount;
                }
            }
        }

        return result.shouldRender ? result.instanceCount : 0;
    };

    // Helper: Get controller question for a given question
    const getControllerForQuestion = (questionId: string): string => {
        // Default: return the question itself
        return questionId;
    };

    // Helper: Get parent path for a question (for debugging)
    const getParentPath = (questionId: string): string[] => {
        // This should be populated based on actual hierarchy
        // For now, return basic path
        return [questionId];
    };

    // Handle tab change for MethodDef
    const handleMethodDefTabChange = (itemGroupOID: string, newValue: number) => {
        setActiveMethodDefTab(prev => ({
            ...prev,
            [itemGroupOID]: newValue
        }));
    };

    // Get the question text for tab naming (DYNAMIC - supports all languages and formats)
    const getMethodDefQuestionText = (methodOID: string): string => {
        const methodDef = methodDefs?.find((md) => md.OID === methodOID);

        if (!methodDef) {
            return 'Item';
        }

        const expressionText = methodDef?.FormalExpression?.text;
        if (!expressionText || !expressionText.includes(':')) {
            return 'Item';
        }

        // Extract control item OID
        const [mdItemOID] = expressionText
            .replace(/[!()]/g, '')
            .split(':')
            .map(str => str.trim());

        const itemDef = itemDefs?.find((item) => item.OID === mdItemOID);
        if (!itemDef) {
            return 'Item';
        }

        if (itemDef.Name && itemDef.Name.trim() !== '') {
            // Remove common question prefixes/suffixes for cleaner labels
            let cleanName = itemDef.Name
                .replace(/^(number of|count of|how many|cantidad de|anzahl|combien de|quantos)/i, '')
                .replace(/\?+$/g, '')
                .trim(); // NOSONAR: Safe regex — anchored prefix + suffix cleanup on controlled UI strings.

            // Capitalize first letter
            if (cleanName.length > 0) {
                cleanName = cleanName.charAt(0).toUpperCase() + cleanName.slice(1);
                return cleanName;
            }
        }

        if (itemDef.Category && itemDef.Category.trim() !== '') {
            return itemDef.Category;
        }

        if (itemDef.MetaKey && itemDef.MetaKey.includes('.')) {
            const parts = itemDef.MetaKey.split('.');
            const lastPart = parts[parts.length - 1];
            if (lastPart && lastPart.trim() !== '') {
                return lastPart
                    .replace(/([A-Z])/g, ' $1') // Add spaces before capitals
                    .trim()
                    .split(' ')
                    .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
                    .join(' ');
            }
        }

        const questionText = itemDef.Question?.TranslatedText?.text || '';

        const patterns = [
            /how many (\w+)/i,           // English: "How many vents"
            /number of (\w+)/i,          // English: "Number of vents"
            /cuántos (\w+)/i,            // Spanish: "Cuántos ventiladores"
            /cantidad de (\w+)/i,        // Spanish: "Cantidad de ventiladores"
            /wie viele (\w+)/i,          // German: "Wie viele Lüftungsöffnungen"
            /anzahl (\w+)/i,             // German: "Anzahl Lüftungsöffnungen"
            /combien de (\w+)/i,         // French: "Combien de ventilateurs"
            /quantos (\w+)/i,            // Portuguese: "Quantos ventiladores"
            /(\w+)\s*\?$/i,              // Generic: "Vents?" (last word before ?)
            /enter\s+(\w+)\s+count/i,   // English: "Enter vent count"
            /(\w+)\s+count\s*\??$/i,    // Generic: "Vent count?" or "Vent count"
        ];  // NOSONAR: Safe regex — anchored prefix + suffix cleanup on controlled UI strings.

        for (const pattern of patterns) {
            const match = questionText.match(pattern);
            if (match && match[1] && match[1].length > 1) { // At least 2 chars to avoid single letters
                // Capitalize first letter
                return match[1].charAt(0).toUpperCase() + match[1].slice(1);
            }
        }

        // If all else fails, return generic "Item"
        return 'Item';
    };

    // Navigation functions for question pagination
    const handlePreviousQuestion = () => {
        if (currentQuestionIndex > 0 && onQuestionChange) {
            onQuestionChange(currentQuestionIndex - 1);
        } else if (currentQuestionIndex === 0 && handleBack) {
            // If on first question of step, go to previous step
            handleBack();
        }
    };

    const handleNextQuestion = async () => {
        // When on Bid Details page during Contract Approval and this card is a trade (not asset A.1), skip autosave and validation — just navigate
        const isBidDetailsPage = isBidDetailsRoute();
        const isContractApproval = typeof window !== 'undefined' ? sessionStorage.getItem('currentStatus') === 'Contract Approval' : false;
        const isTradeCard = (OID && OID !== 'A.1');

        if (isBidDetailsPage && isContractApproval && isTradeCard) {
            if (currentQuestionIndex < questionsInGroup.length - 1 && onQuestionChange) {
                onQuestionChange(currentQuestionIndex + 1);
            } else if (currentQuestionIndex === questionsInGroup.length - 1) {
                if (activeStep === (totalSteps || 1) - 1) {
                    if (handleComplete) handleComplete();
                } else {
                    if (handleNext) handleNext();
                }
            }
            return;
        }

        // Autosave + version (AB#15695 answer-row versioning) BEFORE navigation, on every
        // Next Question click that actually changes the trade's answers -- not gated to the
        // Bid Details page or to the last question of the group anymore. hasAnswerDataChanged
        // below is what keeps this a no-op when nothing changed, so "click Next Question" and
        // "click Next Question with no edits" behave differently without needing a page/step
        // gate here. The backend (AnswersService.updateAnswerTemplateData) bumps answers.version
        // on every successful save it receives, so any call that reaches autoSaveBid below with
        // real changes versions the row.
        //
        // isTradeCard-gated: this block rebuilds ODM data from THIS card's own
        // itemGroupDatas/answersDataRef and stamps it onto every answerData entry whose tradeOid
        // is NOT "A.1" (see updatedAnswerData below) -- i.e. it is trade-question autosave only.
        // Asset Information (OID === "A.1") has its own direct versioning call
        // (CreateBidWizard_v3's saveAssetToDatabase -> updateTemplatesData on Continue). Without
        // this guard, clicking Continue on the Asset card ran this block too, stamping the
        // asset's in-progress answers onto the trade row and versioning/duplicating it.
        //
        // !isBidDetailsPage: reverted per explicit request -- on Bid Details, Next Question must
        // be pure navigation with no autosave/versioning; versioning happens ONLY on the
        // page-level Update button. The Create Bid WIZARD's own Next Question autosave (this
        // same block, when isBidDetailsPage is false) is unaffected by this revert.
        if (isTradeCard && !isBidDetailsPage) {
            setIsSaving(true);

            // Force React to render the loading state
            await new Promise(resolve => setTimeout(resolve, 0));

            try {
                const storedBidDataString = sessionStorage.getItem('currentBidData');

                if (storedBidDataString) {
                    const fullBidData = JSON.parse(storedBidDataString);
                    const latestAnswers = answersDataRef.current || {};
                    const getAnswerForItem = (item: any) => {
                        if (!item?.ItemOID) return undefined;
                        const repeatKey = item?.ItemGroupRepeatKey;
                        if (repeatKey !== undefined && repeatKey !== null && repeatKey !== 0) {
                            const suffixedKey = `${item.ItemOID}_${repeatKey}`;
                            if (latestAnswers[suffixedKey]) {
                                return latestAnswers[suffixedKey];
                            }
                        }
                        return latestAnswers[item.ItemOID];
                    };

                    // Build fresh ODM data from current trade questions (similar to Requirements_v2)
                    // Use itemGroupDatas which contains the current answers
                    const freshItemGroupData = (itemGroupDatas || []).map((group: any) => ({
                        ...group,
                        ItemData: (group.ItemData || []).map((item: any) => {
                            const answer = getAnswerForItem(item);
                            // Behavioral intent: Only true/true-string values trigger deletion logic; false values are ignored
                            const answerHasDeletedFlag = answer && typeof answer === 'object' &&
                                isDeletedFlag(answer.isDeleted);
                            const itemHasDeletedFlag = isDeletedFlag(item.isDeleted);

                            if (itemHasDeletedFlag) {
                                return item;
                            }
                            if (answerHasDeletedFlag) {
                                return {
                                    ...item,
                                    isDeleted: isDeletedFlag(answer.isDeleted)
                                };
                            }
                            return item;
                        })
                    }));

                    const freshOdmData = {
                        EventData: [{
                            FormData: [{
                                ItemGroupData: freshItemGroupData,
                                FormOID: "F.1",
                                TransactionType: "Insert"
                            }],
                            EventOID: "E.1"
                        }],
                        OID: OID,
                        Key: "123",
                        MetaDataVersionOID: fullBidData?.tradeTemplateVersion || "MDV.1"
                    };

                    const freshTradeOdmString = JSON.stringify(freshOdmData);

                    // Update the trade questions entry in answerData (any entry that's NOT asset questions "A.1")
                    const updatedAnswerData = fullBidData.answerData?.map((answer: any) => {
                        // Update trade entry - matches empty tradeOid or trade-specific OIDs (anything except "A.1")
                        if (answer.tradeOid !== "A.1") {
                            return {
                                ...answer,
                                answersData: freshTradeOdmString,
                                updatedDate: new Date().toISOString()
                            };
                        }
                        // Keep asset data as-is
                        return answer;
                    }) || [];

                    // `status` stripped: fullBidData is the page-open snapshot, so its status
                    // is stale after any in-page status change. An autosave saves answers.
                    const payload = stripSnapshotStatus({
                        ...fullBidData,
                        answerData: updatedAnswerData
                    });

                    // 🔧 Restore answerData from session storage if empty
                    await restoreAnswerData(payload);

                    const { autoSaveBid } = await import('../../../services/BidService');
                    const ensureVersions = async (answers: any[]) => {
                        if (!Array.isArray(answers)) return;
                        for (let i = 0; i < answers.length; i++) {
                            const a = answers[i];
                            if (a && (a.questionTemplateVersion === null || a.questionTemplateVersion === undefined)) {
                                try {
                                    const res = await getTemplatesByTradeName(a.tradeOid || a.tradeOid, 'publish', accessToken);
                                    if (res?.data && res.data.version !== undefined) {
                                        a.questionTemplateVersion = res.data.version;
                                    }
                                } catch (e) { }
                            }
                        }
                    };

                    // Enrich versions first so the snapshot comparison is apples-to-apples
                    await ensureVersions(payload.answerData);

                    const existingBidId = sessionStorage.getItem('currentBidId');
                    if (existingBidId && !payload.id) {
                        payload.id = parseInt(existingBidId, 10);
                    }

                    // Skip the API call (and the rest of this block) if answerData hasn't
                    // changed since the last successful save (checked AFTER ensureVersions so
                    // snapshot and current payload are comparable). This must NOT `return` out
                    // of handleNextQuestion itself -- this block now runs on every Next
                    // Question click (not just the last question of a soldproduction group),
                    // so an early return here would silently swallow navigation on every click
                    // where nothing changed, which is the common case.
                    if (hasAnswerDataChanged(payload, 'QuestionRendererCard')) {
                        const response = await autoSaveBid(payload, accessToken);
                        // Record baseline only on a successful save
                        if (response?.status === 200 || response?.status === 201) {
                            recordLastSavedAnswerData(payload);
                        }
                        // Minimum delay to ensure the "Saving..." loader is visible -- only
                        // when a real save happened; an unconditional delay here would slow
                        // down every Next Question click even when nothing was saved.
                        await new Promise(resolve => setTimeout(resolve, 500));
                    }
                }
            } catch (error: any) {
                console.error('error:', error?.message);
            } finally {
                setIsSaving(false);
            }
        }

        // Continue with normal navigation
        if (currentQuestionIndex < questionsInGroup.length - 1 && onQuestionChange) {
            onQuestionChange(currentQuestionIndex + 1);
        } else if (currentQuestionIndex === questionsInGroup.length - 1) {
            // If on last question of step
            if (activeStep === (totalSteps || 1) - 1) {
                // If on last step, complete
                if (handleComplete) handleComplete();
            } else {
                // Go to next step
                if (handleNext) handleNext();
            }
        }
    };

    const isLastQuestionOfStep = currentQuestionIndex === questionsInGroup.length - 1;
    const isLastStep = activeStep === (totalSteps || 1) - 1;

    // Helper function to check if two OIDs match (handles different formats)
    const doOIDsMatch = (oid1: string, oid2: string): boolean => {
        if (!oid1 || !oid2) return false;

        // Direct match
        if (oid1 === oid2) return true;

        // Try different format combinations
        const cleanOid1 = oid1.replace(/^I\./, '');
        const cleanOid2 = oid2.replace(/^I\./, '');

        // Match without I. prefix
        if (cleanOid1 === cleanOid2) return true;

        // Match with I. prefix added
        if (`I.${cleanOid1}` === oid2 || oid1 === `I.${cleanOid2}`) return true;

        return false;
    };

    // Helper function to evaluate condition expressions
    const evaluateConditionExpression = (expressionText: string, itgrpDt: any[]): boolean => {
        if (!expressionText) return false;

        try {
            // Handle OR conditions (||)
            if (expressionText.includes('||')) {
                const orParts = expressionText.split('||');
                return orParts.some(part => {
                    const trimmedPart = part.trim();
                    return evaluateSingleCondition(trimmedPart, itgrpDt);
                });
            }

            // Handle single condition
            return evaluateSingleCondition(expressionText, itgrpDt);
        } catch (error) {
            // console.error('Error evaluating condition expression:', error);
            return false;
        }
    };

    // Helper function to evaluate a single condition
    const evaluateSingleCondition = (conditionText: string, itgrpDt: any[]): boolean => {
        if (!conditionText.includes('==')) return false;

        const [itemOIDPart, expectedValue] = conditionText.split('==');
        const itemOID = itemOIDPart.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
        const condValue = expectedValue.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');

        // Find the item data for this condition
        const mainQuestionData = itgrpDt?.map((igd) =>
            igd?.ItemData?.find((id: any) => id?.ItemOID === itemOID)
        ).filter(Boolean);

        if (!mainQuestionData || mainQuestionData.length === 0) {
            return false;
        }

        const mainData = mainQuestionData[0];

        // Check CodeList items (for select type questions)
        if (mainData?.CodeList && mainData.CodeList.length > 0) {
            return mainData.CodeList.some((codeList: any) =>
                codeList.CodeListItem?.some((item: any) =>
                    item.CodedValue === condValue && item.IsEnabled
                )
            );
        }

        // Check direct value match
        return mainData?.Value === condValue;
    };

    const handleQuestionCompleted = (questionData: any) => {
        onQuestionComplete?.(questionData);
    };

    // Render single question for pagination mode
    const renderSingleQuestion = (itemGroup: any, questionData: any, itgrpDt: any, indexes: number) => {
        const { itemRef: ir, itemDef: item } = questionData;
        const index = 0; // Since we're showing one question at a time

        let measureUnit = measureDef?.MeasurementUnit?.find((mu) => mu?.OID === item?.MeasurementOid);
        let itemData;
        if (itemGroup?.Repeating === 'Yes') {
            // For repeating groups, filter by current repeat instance
            const currentRepeatKey = repeatCount[itemGroup.OID] || 1;
            itemData = itgrpDt?.map((igd) =>
                igd?.ItemData?.find((id) =>
                    id?.ItemOID === item?.OID &&
                    id?.ItemGroupRepeatKey === currentRepeatKey
                )
            ).filter(Boolean);
        } else {
            // For non-repeating groups, filter normally
            itemData = itgrpDt?.map((igd) => igd?.ItemData?.find((id) => id?.ItemOID === item?.OID)).filter(Boolean);
        }

        // This handles the case where itgrpDt is stale but parentAnswersData has fresh values (e.g., after Cancel)
        if ((!itemData || itemData.length === 0 || !itemData[0]?.Value) && parentAnswersData) {
            const answerKey = item?.OID;
            const parentAnswer = parentAnswersData[answerKey];
            if (parentAnswer && parentAnswer.value !== undefined) {
                // Build itemData from parentAnswersData
                itemData = [{
                    ItemOID: item?.OID,
                    Value: parentAnswer.value,
                    DataType: parentAnswer.dataType || item?.DataType,
                    MetaKey: item?.MetaKey || '',
                    ItemGroupRepeatKey: 0,
                    CodeList: parentAnswer.CodeList || []
                }];
            }
        }

        if ((!itemData || itemData.length === 0 || !itemData[0]?.Value) && ir.MethodOID && ir.IsVisible === 'Yes') {
            const answerKey = item?.OID;
            const localAnswer = answersData[answerKey];
            const localValue = localAnswer && typeof localAnswer === 'object' ? localAnswer.value : localAnswer;
            if (localValue !== undefined && localValue !== null && localValue !== '') {
                itemData = [{
                    ItemOID: item?.OID,
                    Value: String(localValue),
                    DataType: item?.DataType || 'float',
                    MetaKey: item?.MetaKey || '',
                    ItemGroupRepeatKey: 0,
                    CodeList: []
                }];
            }
        }

        let itdt = validate?.find((v) => v?.OID === item?.OID);

        // Filter codeList for this question
        let codeList: any;
        if (itemGroup?.Repeating === "Yes") {
            codeList = codeListDefs?.filter((codeList) => codeList?.OID === item?.CodeListRef?.CodeListOID && codeList?.ItemGroupRepeatKey === repeatCount[itemGroup.OID]);
        } else {
            codeList = codeListDefs?.filter((codeList) => codeList?.OID === item?.CodeListRef?.CodeListOID);
        }
        // Get current repeat key for repeating groups - needed for instance-specific visibility checks
        const currentRepeatKey = (itemGroup?.Repeating === 'Yes') ? (repeatCount[itemGroup.OID] || 1) : undefined;
        
        const findParentData = (parentOID: string) => {
            const localParentAnswer = answersData?.[parentOID];
            const parentAnswer = parentAnswersData?.[parentOID];
            const mergedParentAnswer = { ...localParentAnswer, ...parentAnswer };

            const hasEnabledCodeListItem = Array.isArray(mergedParentAnswer.CodeList) && mergedParentAnswer.CodeList.length > 0 &&
                mergedParentAnswer.CodeList.some((cl: any) => cl?.CodeListItem?.some((c: any) => c?.IsEnabled));
            const hasValue = mergedParentAnswer.value !== undefined && mergedParentAnswer.value !== null && mergedParentAnswer.value !== '';

            if (hasValue || hasEnabledCodeListItem) {
                return {
                    ItemOID: parentOID,
                    Value: mergedParentAnswer.value,
                    CodeList: mergedParentAnswer.CodeList || [],
                    ItemGroupRepeatKey: mergedParentAnswer.repeatKey || 0
                };
            }

            if (localParentAnswer !== undefined || parentAnswer !== undefined) {
                return null;
            }

            let parentFromItgrpDt = itgrpDt?.map((igd: any) => igd?.ItemData?.find((id: any) =>
                id?.ItemOID === parentOID &&
                (currentRepeatKey === undefined || id?.ItemGroupRepeatKey === currentRepeatKey)
            )).filter(Boolean)?.[0];

            if (parentFromItgrpDt) {
                return parentFromItgrpDt;
            }

            return null;
        };
        let showItem = true;
        const isItemReadOnly = readonly || ir?.ReadOnly === 'Yes';
        if (ir.MethodOID) {
            if (ir.IsVisible === 'No') {
                showItem = false;
                return null;
            }
        }

        if (ir.CollectionExceptionConditionOID !== null && ir.CollectionExceptionConditionOID !== undefined) {
            let itemConDef = conditionDefs?.find((cd) => cd.OID === ir.CollectionExceptionConditionOID);
            if (itemConDef) {
                let cdf = itemConDef?.FormalExpression?.text.includes("||");

                if (cdf === true) {
                    let cdd = itemConDef?.FormalExpression?.text.split("||");
                    for (let i = 0; i < cdd.length; i++) {
                        let cdnItemOID = cdd[i].split("==")[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                        let cdnValue = cdd[i].split("==")[1]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                        // For repeating groups, filter by ItemGroupRepeatKey to check same-instance controller
                        let iData = findParentData(cdnItemOID);
                        if (iData) {
                            // iData found via helper
                            // itemDataArr is an array, so loop through it
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
                                    showItem = true;
                                    break;
                                } else {
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
                    let iData = findParentData(cdnItemOID);
                    if (iData) {
                        // iData found via helper
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

        // Find matching mapping for this item
        let matchedMapping: any = null;
        if (Array.isArray(mappings) && mappings.length > 0) {
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

            if (matchedItemDefs.length > 0) {
                matchedMapping = matchedItemDefs[0];
            }
        }

        let condition = conditionDefs?.filter((cd) => {
            let cdn = cd.FormalExpression.text.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{ }! ]/g, '');
            if (cdn === item?.OID)
                return cdn
        });

        if (!itemGroupDefs || !Array.isArray(itemGroupDefs) || itemGroupDefs.length === 0 ||
            !itemDefs || !Array.isArray(itemDefs) || itemDefs.length === 0 ||
            !itemGroupDefs[0]?.ItemRef || !Array.isArray(itemGroupDefs[0].ItemRef)) {
            return (
                <Box display="flex" justifyContent="center" p={4}>
                    <Typography>Loading question data...</Typography>
                </Box>
            );
        }
        // Add this helper just before the return statement in QuestionRendererCard_v2Component
        const FreeText = (
            itemDef: any,
            measureUnit: any,
            itemDatas: any[],
            error: any,
            isReadOnly: boolean,
            mapping?: any,
            tradeDataProp?: any
        ) => {
            // Extract current value from itemDatas (same way FreeText did internally)
            const existingItem = itemDatas?.find((d: any) => d?.ItemOID === itemDef?.OID);
            const currentValue = existingItem?.Value ?? '';

            const handleChange = (itemOID: string, value: string) => {
                // Reconstruct ItemData shape that OnItemDataChange expects
                if (existingItem) {
                    OnItemDataChange({ ...existingItem, Value: value });
                } else {
                    OnItemDataChange({
                        ItemOID: itemOID,
                        Value: value,
                        CodeList: [],
                        MetaKey: itemDef?.MetaKey ?? '',
                        ItemGroupRepeatKey: 0,
                    });
                }
            };

            return (
                <FreeText_v2
                    itemDef={itemDef}
                    value={currentValue}
                    onChange={handleChange}
                    measureDef={measureUnit}
                    error={error}
                    readonly={isReadOnly}
                />
            );
        };
        return (
            <Box key={item?.OID} sx={{ mb: 3 }}>
                {/* Vertical layout: Image, then question text, then input */}

                {/* 1. Question Image - Above question text (centered) */}
                {showItem && (() => {
                    const thumbnailSrc = getImageUrl(item?.ThumbnailUrl, imageBaseUrl);
                    const fullImageSrc = getImageUrl(item?.ImageUrl, imageBaseUrl);

                    if (thumbnailSrc || fullImageSrc) {
                        return (
                            <Box sx={{ mb: 2, display: 'flex', justifyContent: 'center' }}>
                                <Box className="question-image-container">
                                    <img
                                        src={thumbnailSrc || fullImageSrc || ''}
                                        alt={item?.Question?.TranslatedText?.text || 'Question image'}
                                        className="question-image"
                                        onError={(e) => {
                                            console.error(`[QuestionRenderer] Failed to load question image for ${item?.OID}:`, thumbnailSrc);
                                            e.currentTarget.parentElement!.style.display = 'none';
                                        }}
                                    />
                                    <Box
                                        className="question-image-zoom-btn"
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            setZoomedQuestionImage({
                                                url: (fullImageSrc || thumbnailSrc) as string,
                                                label: item?.Question?.TranslatedText?.text || 'Question image'
                                            });
                                        }}
                                    />
                                </Box>
                            </Box>
                        );
                    }
                    return null;
                })()}


                {showItem && (() => {
                    const typographySx = { mt: 0, mb: 2, fontSize: '1.1rem', fontWeight: 'medium' };

                    return ir.Mandatory === 'Yes' && item !== undefined
                        ? <Typography sx={typographySx}>{item?.Question?.TranslatedText?.text} <span style={{ color: 'red' }}>*</span></Typography>
                        : <Typography sx={typographySx}>{item?.Question?.TranslatedText?.text}</Typography>
                })()}


                {item?.DataType === 'text' && item !== undefined && showItem && FreeText(item, measureUnit, itemData, itdt, isItemReadOnly, matchedMapping, tradeData)}
                {item?.DataType === 'string' && item !== undefined && showItem && FreeText(item, measureUnit, itemData, itdt, isItemReadOnly, matchedMapping, tradeData)}
                {item?.DataType === 'integer' && item !== undefined && showItem && <IntegerText OID={OID} itemDef={item} measureDef={measureUnit} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} showStatus={showQuestion}
                    isRepeatItemGrp={itemGroup?.Repeating} itgrpInd={repeatCount[itemGroup.OID]} itemGrp={itemGroup} callRepeat={handleRepeat} methodDefs={methodDefs} mapping={matchedMapping} tradeData={tradeData} readonly={isItemReadOnly} />}
                {item?.DataType === 'feetinches' && item !== undefined && showItem && (<FeetInchesInput_v2 OID={OID} itemDef={item} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} isRepeatItemGrp={itemGroup?.Repeating} itgrpInd={repeatCount[itemGroup.OID]} readonly={isItemReadOnly} />)}
                {item?.DataType === 'float' && item !== undefined && showItem && <FloatText itemDef={item} measureDef={measureUnit} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} readonly={isItemReadOnly} mapping={matchedMapping} tradeData={tradeData} />}
                {item?.DataType === 'multilinetext' && item !== undefined && showItem && <MultilineText itemDef={item} measureDef={measureUnit} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} readonly={isItemReadOnly} />}
                {item?.DataType === 'singleselect' && item !== undefined && showItem && <SingleSelect_v2 OID={OID} itemDef={item} conditionDef={condition} codeList={codeList} readonly={isItemReadOnly}
                    onItemchange={OnItemDataChange} showStatus={showQuestion} itemDatas={itemData} error={itdt} index={index} isRepeatItemGrp={itemGroup?.Repeating} itgrpInd={repeatCount[itemGroup.OID]} methodDefs={methodDefs} imageBaseUrl={imageBaseUrl} />}
                {item?.DataType === 'multiselect' && item !== undefined && showItem && <MultiChoice_v2 OID={OID} itemDef={item} conditionDef={condition} codeList={codeList} readonly={isItemReadOnly}
                    onItemchange={OnItemDataChange} showStatus={showQuestion} itemDatas={itemData} error={itdt} index={index} isRepeatItemGrp={itemGroup?.Repeating} itgrpInd={repeatCount[itemGroup.OID]} methodDefs={methodDefs} itemRefMethodOID={ir?.MethodOID} imageBaseUrl={imageBaseUrl} />}
                {item?.DataType === 'date' && item !== undefined && showItem && <DateSelector itemDef={item} conditionDef={condition} codeList={codeList} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} readonly={isItemReadOnly} />}
                {item?.DataType === 'datetime' && item !== undefined && showItem && <DateTime itemDef={item} conditionDef={condition} codeList={codeList} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} readonly={isItemReadOnly} />}
                {item?.DataType === 'combobox' && item !== undefined && showItem && <Combo itemDef={item} conditionDef={condition} codeList={codeList} onItemchange={OnItemDataChange} showStatus={showQuestion} itemDatas={itemData} error={itdt} />}
                {item?.DataType === 'boolean' && item !== undefined && showItem && <RadioType_v2 OID={OID} itemDef={item} conditionDef={condition} codeList={codeList} onItemchange={OnItemDataChange} readonly={isItemReadOnly}
                    showStatus={showQuestion} itemDatas={itemData} error={itdt} index={index} isRepeatItemGrp={itemGroup?.Repeating} itgrpInd={repeatCount[itemGroup.OID]} methodDefs={methodDefs} />}
            </Box>
        );
    };

    // Build dependency tree for conditional questions to handle nested dependencies
    const buildConditionalDependencyTree = (mainQuestionOID: string, itemGroup: any): Set<string> => {
        const dependentQuestions = new Set<string>();
        const visited = new Set<string>();

        const findDependents = (questionOID: string) => {
            if (visited.has(questionOID)) return;
            visited.add(questionOID);

            // Find all conditional questions that depend on this question
            itemGroup?.ItemRef?.forEach((itemRef: any) => {
                if (itemRef.CollectionExceptionConditionOID) {
                    const conditionDef = conditionDefs?.find(cd => cd.OID === itemRef.CollectionExceptionConditionOID);
                    if (conditionDef) {
                        const conditionText = conditionDef.FormalExpression?.text || '';
                        let dependsOnCurrentQuestion = false;

                        // Handle OR conditions
                        if (conditionText.includes('||')) {
                            const orParts = conditionText.split('||');
                            dependsOnCurrentQuestion = orParts.some(part => {
                                const itemOID = part.split('==')[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                return itemOID === questionOID;
                            });
                        } else {
                            // Handle simple conditions
                            const conditionItemOID = conditionText.split('==')[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                            dependsOnCurrentQuestion = conditionItemOID === questionOID;
                        }

                        if (dependsOnCurrentQuestion) {
                            dependentQuestions.add(itemRef.ItemOID);
                            // Recursively find questions that depend on this conditional question
                            findDependents(itemRef.ItemOID);
                        }
                    }
                }
            });
        };

        // Start with the main question
        findDependents(mainQuestionOID);

        return dependentQuestions;
    };

    const renderQuestionWithConditionals = (itemGroup: any, questionData: any, itgrpDt: any, indexes: number) => {
        const questionsToRender: React.ReactElement[] = [];

        // Always render the main question first (unless it's a derived field with MethodOID)
        const mainQuestionElement = renderSingleQuestion(itemGroup, questionData, itgrpDt, indexes);
        if (mainQuestionElement) {
            questionsToRender.push(mainQuestionElement);
        }

        // Build dependency tree for current main question (includes nested dependencies)
        let allowedConditionalQuestions: Set<string> = buildConditionalDependencyTree(questionData.itemDef?.OID, itemGroup);


        if (itemGroup?.ItemRef && Array.isArray(itemGroup.ItemRef)) {
            itemGroup.ItemRef.forEach((itemRef: any, index: number) => {
                // Skip the main question (already rendered above)
                if (itemRef.ItemOID === questionData.itemDef?.OID) {
                    return;
                }

                // Only render conditional questions
                if (itemRef.CollectionExceptionConditionOID) {
                    const conditionalItemDef = itemDefs.find(item => item.OID === itemRef.ItemOID);
                    if (conditionalItemDef) {
                        // Only render if this conditional question is in the dependency tree
                        const shouldRenderConditional = allowedConditionalQuestions.has(itemRef.ItemOID);

                        if (shouldRenderConditional) {
                            const conditionDef = conditionDefs?.find(cd => cd.OID === itemRef.CollectionExceptionConditionOID);
                            let conditionSatisfied = false; // Default to false for safety

                            if (conditionDef && parentAnswersData) {
                                const conditionText = conditionDef.FormalExpression?.text || '';
                                // Parent (Requirements_v3) is authoritative — its value wins over stale card-local state
                                const currentAnswersForCondition = { ...answersData, ...parentAnswersData };

                                // Handle OR conditions (||)
                                if (conditionText.includes('||')) {
                                    const orParts = conditionText.split('||');
                                    for (const part of orParts) {
                                        const partParts = part.split('==');
                                        const parentOID = partParts[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                        const requiredValue = partParts[1]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');


                                        const parentData = currentAnswersForCondition[parentOID];
                                        if (parentData) {
                                            if (parentData.CodeList && parentData.CodeList.length > 0) {
                                                const enabledItem = parentData.CodeList[0].CodeListItem?.find((c: any) =>
                                                    c.CodedValue === requiredValue && c.IsEnabled
                                                );
                                                if (enabledItem) {
                                                    conditionSatisfied = true;
                                                    break; // One satisfied clause is enough for OR
                                                }
                                            } else if (parentData.value === requiredValue) {
                                                conditionSatisfied = true;
                                                break;
                                            }
                                        }
                                    }
                                } else {
                                    // Simple condition (no OR)
                                    const parentOID = conditionText.split('==')[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                    const requiredValue = conditionText.split('==')[1]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');

                                    const parentData = currentAnswersForCondition[parentOID];

                                    if (parentData) {
                                        if (parentData.CodeList && parentData.CodeList.length > 0) {
                                            const enabledItem = parentData.CodeList[0].CodeListItem?.find((c: any) =>
                                                c.CodedValue === requiredValue && c.IsEnabled
                                            );

                                            conditionSatisfied = !!enabledItem;
                                        } else if (parentData.value === requiredValue) {
                                            conditionSatisfied = true;
                                        } else {
                                            conditionSatisfied = false;
                                        }
                                    } else {
                                        // Parent not in answersData means it was cleaned up - don't show child
                                        conditionSatisfied = false;
                                    }
                                }
                            }

                            if (conditionSatisfied) {
                                const conditionalQuestionData = {
                                    itemRef,
                                    itemDef: conditionalItemDef,
                                    isConditional: true
                                };

                                questionsToRender.push(
                                    <Box key={`conditional-${itemRef.ItemOID}`}>
                                        {(itemRef as any)._subGroupLabel && (
                                            <Box sx={{ mb: 1.5, mt: 1, px: 1.5, py: 0.75, backgroundColor: '#eef2f7', borderLeft: '3px solid #1a365d', borderRadius: '0 4px 4px 0' }}>
                                                <Typography variant="caption" sx={{ fontWeight: 700, color: '#1a365d', textTransform: 'uppercase', letterSpacing: '0.6px' }}>
                                                    {(itemRef as any)._subGroupLabel}
                                                </Typography>
                                            </Box>
                                        )}
                                        {renderSingleQuestion(itemGroup, conditionalQuestionData, itgrpDt, index)}
                                    </Box>
                                );
                            }
                        } else {
                        }
                    }
                }
            });
        }

        return (
            <Box>
                {questionsToRender}
            </Box>
        );
    };
    const formatNestedItemDatas = (
        nestedAnswerKey: string,
        nestedItemRef: any,
        nestedItemDef: any,
        nestedInstanceIndex: number,
        parentInstanceIndex: number
    ): ItemData[] => {
        const currentAnswer = answersData[nestedAnswerKey];

        // Return empty array if no answer exists yet
        if (!currentAnswer || currentAnswer.value === undefined) {
            return [];
        }

        // Convert to ItemData format
        const itemData: ItemData = {
            ItemOID: nestedItemRef.ItemOID,
            Value: currentAnswer.value,
            ItemGroupRepeatKey: nestedInstanceIndex,
            ParentItemGroupRepeatKey: parentInstanceIndex,
            MetaKey: nestedItemDef?.MetaKey || '',
            CodeList: currentAnswer.CodeList || [],
            isDeleted: (currentAnswer.isDeleted === true || currentAnswer.isDeleted === 'true') ? true : undefined
        };

        return [itemData];
    };

    const handleNestedItemChange = (
        itemData: ItemData,
        nestedAnswerKey: string,
        nestedDataType: string,
        nestedChild: any,
        instanceIndex: number
    ) => {

        setAnswersData(prev => {
            const updated = {
                ...prev,
                [nestedAnswerKey]: {
                    value: itemData.Value,
                    dataType: nestedDataType,
                    CodeList: itemData.CodeList
                }
            };

            const parts = nestedAnswerKey.split('_');
            if (parts.length === 3) { // Triple-level key like I.24_1_1
                const doubleLevelKey = `${parts[0]}_${parts[1]}`; // I.24_1
                if (updated[doubleLevelKey]) {
                    delete updated[doubleLevelKey];
                }
            }

            return updated;
        });

        const enhancedData = {
            ...itemData,
            ItemGroupOID: nestedChild.itemGroupDef.OID,
            ParentItemGroupRepeatKey: instanceIndex
        };

        try {
            OnItemDataChange(enhancedData);
        } catch (error) {
            // console.error('[NESTED_WRAPPER] OnItemDataChange error:', error);
        }
    };

    /**
     * Format answersData to component format for PARENT multi-instance
     */
    const formatParentItemDatas = (
        answerKey: string,
        itemRef: any,
        itemDefinition: any,
        instanceIndex: number
    ): ItemData[] => {
        const currentAnswer = answersData[answerKey];

        // Also check itemData as backup (existing logic)
        if (!currentAnswer) {
            const itemDataEntry = itemData.find(item =>
                item.ItemOID === itemRef.ItemOID &&
                item.ItemGroupRepeatKey === instanceIndex
            );
            if (itemDataEntry && itemDataEntry.Value) {
                const itemData_result: ItemData = {
                    ItemOID: itemRef.ItemOID,
                    Value: itemDataEntry.Value,
                    ItemGroupRepeatKey: instanceIndex,
                    MetaKey: itemDefinition?.MetaKey || '',
                    CodeList: itemDataEntry.CodeList || [],
                    isDeleted: isDeletedFlag((itemDataEntry as any).isDeleted) ? true : undefined
                };
                return [itemData_result];
            }
            return [];
        }

        const itemData_result: ItemData = {
            ItemOID: itemRef.ItemOID,
            Value: currentAnswer.value,
            ItemGroupRepeatKey: instanceIndex,
            MetaKey: itemDefinition?.MetaKey || '',
            CodeList: currentAnswer.CodeList || [],
            isDeleted: (currentAnswer.isDeleted === true || currentAnswer.isDeleted === 'true') ? true : undefined
        };


        return [itemData_result];
    };

    const handleParentItemChange = (
        itemData: ItemData,
        answerKey: string,
        dataType: string,
        repeatingChild: any,
        instanceIndex: number
    ) => {

        // Step 1: Update answersData for UI reactivity
        setAnswersData(prev => ({
            ...prev,
            [answerKey]: {
                value: itemData.Value,
                dataType: dataType,
                CodeList: itemData.CodeList
            }
        }));

        const enhancedData: any = {
            ...itemData,
            ItemGroupOID: repeatingChild.itemGroupDef.OID,
            SummarizeMode: summarizedGroups[repeatingChild.itemGroupDef.OID] === true ? true : undefined
        };

        OnItemDataChange(enhancedData);

        // ── AUTO-CALCULATE: if this field is the trigger for any derived field, calculate now ──
        repeatingChild.itemGroupDef.ItemRef?.forEach((ir: any) => {
            if (!ir.MethodOID) return;
            const md = methodDefs?.find((m: any) => m.OID === ir.MethodOID);
            if (!md?.FormalExpression?.text) return;
            if (md.FormalExpression.text.includes('RepeatCount')) return;

            // Find input refs and which one is last in group order
            const inputRefs: string[] = md.FormalExpression.text.match(/I\.\d+/g) || [];
            const groupInputOIDs: string[] = repeatingChild.itemGroupDef.ItemRef
                .filter((r: any) => !r.MethodOID)
                .map((r: any) => r.ItemOID);

            let lastIndex = -1;
            let triggerOID = '';
            inputRefs.forEach((ref: string) => {
                const idx = groupInputOIDs.indexOf(ref);
                if (idx > lastIndex) { lastIndex = idx; triggerOID = ref; }
            });

            // Only auto-calculate when the changed field IS the trigger field
            if (!inputRefs.includes(itemData.ItemOID)) return;

            // Build resolved data using the just-updated value (not yet in answersDataRef)
            const latestAnswers = { ...answersDataRef.current, [answerKey]: { value: itemData.Value, dataType } };
            const resolvedData: Record<string, any> = { ...latestAnswers };
            inputRefs.forEach((ref: string) => {
                const suffixedKey = `${ref}_${instanceIndex}`;
                if (latestAnswers[suffixedKey] !== undefined) {
                    resolvedData[ref] = latestAnswers[suffixedKey];
                }
            });

            let result: any;

            try {
                result = executeMethodExpression(
                    md.FormalExpression.text,
                    resolvedData
                );
            } catch (e) {
                console.error('[AUTO_CALC] eval error:', e);
                return;
            }

            if (result === null || result === undefined) return;

            const outputKey = `${ir.ItemOID}_${instanceIndex}`;
            const resultStr = String(result);

            setAnswersData((prev: any) => ({
                ...prev,
                [outputKey]: {
                    ...(prev[outputKey] || {}),
                    value: resultStr,
                    dataType: 'integer',
                    itemOID: ir.ItemOID,
                    itemGroupOID: repeatingChild.itemGroupDef.OID,
                    repeatKey: instanceIndex,
                    isDeleted: false
                }
            }));

            OnItemDataChange({
                ItemOID: ir.ItemOID,
                Value: resultStr,
                ItemGroupRepeatKey: instanceIndex,
                ItemGroupOID: repeatingChild.itemGroupDef.OID,
                CodeList: [],
                isDeleted: false
            });
        });
        // Step 3: Update repeating instance data (preserve existing logic even if unused)
        const instanceKey = `${repeatingChild.itemGroupDef.OID}_${instanceIndex}`;
        setRepeatingInstanceData(prev => {
            const updated = { ...prev };
            if (!updated[instanceKey]) {
                updated[instanceKey] = {
                    ItemGroupOID: repeatingChild.itemGroupDef.OID,
                    ItemGroupRepeatKey: instanceIndex,
                    ItemData: []
                };
            }
            const existingItemIndex = updated[instanceKey].ItemData.findIndex((item: any) => item.ItemOID === itemData.ItemOID);
            const itemDataEntry = {
                ItemOID: itemData.ItemOID,
                Value: itemData.Value,
                ItemGroupRepeatKey: instanceIndex
            };
            if (existingItemIndex >= 0) {
                updated[instanceKey].ItemData[existingItemIndex] = itemDataEntry;
            } else {
                updated[instanceKey].ItemData.push(itemDataEntry);
            }
            return updated;
        });
    };
    // ────────────────────────────────────────────────────────────────────────────

    // PHASE 2: Helper function to render nested tabs for a specific control question
    const renderNestedTabsForQuestion = (controlQuestionOID: string, instanceIndex: number, repeatingChild: any) => {
        if (!repeatingChildren || !getRepeatingInstanceCount) return null;

        // Find nested children controlled by this question
        const nestedForThisQuestion = repeatingChildren.filter(nestedChild =>
            nestedChild.isNested &&
            nestedChild.controlItemOID === controlQuestionOID &&
            nestedChild.parentRepeatingGroupOID === repeatingChild.itemGroupDef.OID
        );

        if (nestedForThisQuestion.length === 0) return null;

        return nestedForThisQuestion.map((nestedChild, idx) => {
            // Build compound suffix for nested group
            const compoundSuffix = parentInstanceSuffix
                ? `${parentInstanceSuffix}_${instanceIndex}`  // e.g., "_1_1"
                : `_${instanceIndex}`;  // e.g., "_1"


            let nestedInstanceCount = 0;
            const nestedAnswerKey = compoundSuffix ? `${nestedChild.controlItemOID}${compoundSuffix}` : nestedChild.controlItemOID;
            const localAnswer = answersDataRef.current[nestedAnswerKey];

            if (localAnswer?.value !== undefined && localAnswer?.value !== null && localAnswer?.value !== '') {
                const localValue = parseInt(localAnswer.value);
                if (!isNaN(localValue)) {
                    nestedInstanceCount = Math.max(0, Math.min(localValue, 20)); // Cap at 20 instances
                }
            }

            // Fallback to prop function if local state doesn't have the answer yet
            if (nestedInstanceCount === 0 && getRepeatingInstanceCount) {
                nestedInstanceCount = getRepeatingInstanceCount(
                    nestedChild.controlItemOID,
                    compoundSuffix
                );
            }

            if (nestedInstanceCount === 0) return null;

            const nestedTabInstances = Array.from({ length: nestedInstanceCount }, (_, index) => index + 1);
            const activeNestedTab = activeRepeatingTabs[nestedChild.itemGroupDef.OID] || 0;

            return (
                <Box key={`nested-${nestedChild.itemGroupDef.OID}-${idx}`} sx={{
                    mt: 2,
                    mb: 3,
                    pl: 2,
                    borderLeft: '3px solid #2196f3',
                    overflow: 'visible' // Ensure container doesn't clip borders
                }}>
                    {/* Unified Nested Container with consistent border */}
                    <Box sx={{
                        border: '1px solid #90caf9',
                        borderRadius: '4px',
                        overflow: 'visible', // Allow borders to show
                        backgroundColor: '#fff'
                    }}>
                        {/* Nested Group Header */}
                        <Box sx={{
                            backgroundColor: '#e3f2fd',
                            p: 1.5,
                            borderRadius: '4px 4px 0 0',
                            borderBottom: '1px solid #90caf9'
                        }}>
                            <Typography variant="subtitle2" sx={{ fontSize: '0.85rem', fontWeight: 600, color: '#1976d2' }}>
                                ↳ {nestedChild.label}
                            </Typography>
                        </Box>

                        {/* Nested Tabs */}
                        <Box sx={{
                            width: '100%',
                            overflow: 'visible' // Allow borders to show
                        }}>
                            <Tabs
                                value={activeNestedTab}
                                onChange={(event, newValue) => {
                                    setActiveRepeatingTabs(prev => ({
                                        ...prev,
                                        [nestedChild.itemGroupDef.OID]: newValue
                                    }));
                                }}
                                variant="scrollable"
                                scrollButtons="auto"
                                allowScrollButtonsMobile
                                sx={{
                                    backgroundColor: '#fff',
                                    borderBottom: '1px solid #90caf9',
                                    '& .MuiTab-root': {
                                        fontSize: '0.75rem',
                                        minHeight: '32px',
                                        py: 0.5,
                                        minWidth: '80px',
                                        maxWidth: '100px',
                                        color: '#1976d2'
                                    }
                                }}
                            >
                                {nestedTabInstances.map((nestedInstanceIndex) => (
                                    <Tab
                                        key={nestedInstanceIndex}
                                        label={`${nestedChild.label} ${nestedInstanceIndex}`}
                                        value={nestedInstanceIndex - 1}
                                        sx={{
                                            textTransform: 'none',
                                            fontSize: '0.75rem'
                                        }}
                                    />
                                ))}
                            </Tabs>
                        </Box>

                        {/* Nested Tab Content */}
                        <Box sx={{
                            borderRadius: '0 0 4px 4px',
                            overflow: 'visible' // Allow content to breathe
                        }}>
                            {nestedTabInstances.map((nestedInstanceIndex) => (
                                <Box
                                    key={nestedInstanceIndex}
                                    hidden={activeNestedTab !== nestedInstanceIndex - 1}
                                    sx={{ display: activeNestedTab === nestedInstanceIndex - 1 ? 'block' : 'none', p: 2 }}
                                >
                                    {/*PHASE 3: Render actual questions in nested tabs */}
                                    {nestedChild.itemGroupDef.ItemRef && nestedChild.itemGroupDef.ItemRef.map((nestedItemRef: any) => {
                                        const nestedItemDef = itemDefs.find(item => item.OID === nestedItemRef.ItemOID);
                                        const nestedQuestionText = nestedItemDef?.Question?.TranslatedText?.text;
                                        const nestedDataType = nestedItemDef?.DataType;

                                        // Build nested answer key with compound suffix
                                        const nestedAnswerKey = `${nestedItemRef.ItemOID}${compoundSuffix}_${nestedInstanceIndex}`;

                                        // Skip RepeatCount questions in nested tabs (if any)
                                        const isNestedRepeatCountQuestion = repeatingChildren?.some(rc =>
                                            rc.controlItemOID === nestedItemRef.ItemOID &&
                                            !rc.isNested
                                        );

                                        if (isNestedRepeatCountQuestion) {
                                            return null;
                                        }

                                        // HIDE DERIVED FIELDS: Skip questions with MethodOID (calculated fields like M.7)
                                        if (nestedItemRef.MethodOID) {
                                            return null;
                                        }

                                        if (!nestedQuestionText) return null;

                                        // Get current answer for this nested question
                                        let nestedCurrentAnswer = answersData[nestedAnswerKey];

                                        // Prepare image component
                                        const thumbnailSrc = getImageUrl(nestedItemDef?.ThumbnailUrl, imageBaseUrl);
                                        const fullImageSrc = getImageUrl(nestedItemDef?.ImageUrl, imageBaseUrl);
                                        const hasImage = !!(thumbnailSrc || fullImageSrc);

                                        return (
                                            <Box key={nestedItemRef.ItemOID} sx={{ mb: 2 }}>
                                                {/* Main Layout: Left (Question + Input) | Right (Image) */}
                                                <Box sx={{ display: 'flex', gap: 2, alignItems: 'flex-start' }}>
                                                    {/* Left Column: Question Text + Input */}
                                                    <Box sx={{ flex: 1, minWidth: 0 }}>
                                                        {/* Question Text */}
                                                        <Typography variant="subtitle2" sx={{ fontSize: '0.95rem', fontWeight: 500, color: '#1a365d', mb: 1.5 }}>
                                                            {nestedQuestionText}{nestedItemRef.Mandatory === 'Yes' && <span style={{ color: 'red' }}> *</span>}
                                                        </Typography>

                                                        <Box>

                                                            {nestedDataType === 'feetinches' && (() => {
                                                                const nestedItemDatas = formatNestedItemDatas(
                                                                    nestedAnswerKey,
                                                                    nestedItemRef,
                                                                    nestedItemDef,
                                                                    nestedInstanceIndex,
                                                                    instanceIndex
                                                                );
                                                                return (
                                                                    <FeetInchesInput_v2
                                                                        OID={OID}
                                                                        itemDef={nestedItemDef}
                                                                        onItemchange={(itemData: any) =>
                                                                            handleNestedItemChange(
                                                                                itemData,
                                                                                nestedAnswerKey,
                                                                                nestedDataType,
                                                                                nestedChild,
                                                                                instanceIndex
                                                                            )
                                                                        }
                                                                        itemDatas={nestedItemDatas}
                                                                        error={undefined}
                                                                        isRepeatItemGrp="Yes"
                                                                        itgrpInd={nestedInstanceIndex}
                                                                        readonly={false}
                                                                    />
                                                                );
                                                            })()}
                                                            {nestedDataType === 'integer' && (() => {
                                                                const nestedItemDatas = formatNestedItemDatas(
                                                                    nestedAnswerKey,
                                                                    nestedItemRef,
                                                                    nestedItemDef,
                                                                    nestedInstanceIndex,
                                                                    instanceIndex
                                                                );


                                                                return (
                                                                    <IntegerText
                                                                        OID={OID}
                                                                        itemDef={nestedItemDef}
                                                                        measureDef={measureDef}
                                                                        onItemchange={(itemData: ItemData) => {
                                                                            handleNestedItemChange(
                                                                                itemData,
                                                                                nestedAnswerKey,
                                                                                nestedDataType,
                                                                                nestedChild,
                                                                                instanceIndex
                                                                            );
                                                                        }}
                                                                        itemDatas={nestedItemDatas}
                                                                        error={undefined}
                                                                        isRepeatItemGrp="Yes"
                                                                        itgrpInd={nestedInstanceIndex}
                                                                        itemGrp={nestedChild.itemGroupDef}
                                                                        showStatus={() => { }} // No nested repeat control
                                                                        methodDefs={methodDefs}
                                                                        mapping={undefined}
                                                                        tradeData={undefined}
                                                                        readonly={false}
                                                                    />
                                                                );
                                                            })()}

                                                            {nestedDataType === 'float' && (() => {
                                                                const nestedItemDatas = formatNestedItemDatas(
                                                                    nestedAnswerKey,
                                                                    nestedItemRef,
                                                                    nestedItemDef,
                                                                    nestedInstanceIndex,
                                                                    instanceIndex
                                                                );

                                                                return (
                                                                    <FloatText
                                                                        itemDef={nestedItemDef}
                                                                        measureDef={measureDef}
                                                                        onItemchange={(itemData: ItemData) => {
                                                                            handleNestedItemChange(
                                                                                itemData,
                                                                                nestedAnswerKey,
                                                                                nestedDataType,
                                                                                nestedChild,
                                                                                instanceIndex
                                                                            );
                                                                        }}
                                                                        itemDatas={nestedItemDatas}
                                                                        error={undefined}
                                                                    />
                                                                );
                                                            })()}
                                                            {nestedDataType === 'string' && (() => {
                                                                const nestedItemDatas = formatNestedItemDatas(
                                                                    nestedAnswerKey,
                                                                    nestedItemRef,
                                                                    nestedItemDef,
                                                                    nestedInstanceIndex,
                                                                    instanceIndex);
                                                                const existingItem = nestedItemDatas?.[0];
                                                                return (
                                                                    <FreeText_v2
                                                                        itemDef={nestedItemDef}
                                                                        value={existingItem?.Value ?? ''}
                                                                        onChange={(itemOID, value) =>
                                                                            handleNestedItemChange(
                                                                                { ...(existingItem ?? { ItemOID: itemOID, CodeList: [], MetaKey: nestedItemDef?.MetaKey ?? '', ItemGroupRepeatKey: nestedInstanceIndex }), Value: value },
                                                                                nestedAnswerKey, nestedDataType, nestedChild, instanceIndex
                                                                            )
                                                                        }
                                                                        measureDef={measureDef}
                                                                        error={undefined}
                                                                        readonly={readonly}
                                                                    />
                                                                );
                                                            })()}

                                                            {nestedDataType === 'text' && (() => {
                                                                const nestedItemDatas = formatNestedItemDatas(
                                                                    nestedAnswerKey,
                                                                    nestedItemRef,
                                                                    nestedItemDef,
                                                                    nestedInstanceIndex,
                                                                    instanceIndex
                                                                );
                                                                const existingItem = nestedItemDatas?.[0];
                                                                return (
                                                                    <FreeText_v2
                                                                        itemDef={nestedItemDef}
                                                                        value={existingItem?.Value ?? ''}
                                                                        onChange={(itemOID, value) =>
                                                                            handleNestedItemChange(
                                                                                { ...(existingItem ?? { ItemOID: itemOID, CodeList: [], MetaKey: nestedItemDef?.MetaKey ?? '', ItemGroupRepeatKey: nestedInstanceIndex }), Value: value },
                                                                                nestedAnswerKey, nestedDataType, nestedChild, instanceIndex
                                                                            )
                                                                        }
                                                                        measureDef={measureDef}
                                                                        error={undefined}
                                                                        readonly={readonly}
                                                                    />
                                                                );
                                                            })()}

                                                            {nestedDataType === 'singleselect' && (() => {
                                                                const nestedItemDatas = formatNestedItemDatas(
                                                                    nestedAnswerKey,
                                                                    nestedItemRef,
                                                                    nestedItemDef,
                                                                    nestedInstanceIndex,
                                                                    instanceIndex
                                                                );

                                                                const conditionDef = conditionDefs?.filter((cd) => {
                                                                    if (!cd?.FormalExpression?.text) return false;
                                                                    let cdn = cd.FormalExpression.text.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{ }! ]/g, '');
                                                                    return cdn === nestedItemDef?.OID;
                                                                }) || [];


                                                                let codeList = codeListDefs?.filter((cl) =>
                                                                    cl?.OID === nestedItemDef?.CodeListRef?.CodeListOID &&
                                                                    cl?.ItemGroupRepeatKey === nestedInstanceIndex
                                                                );

                                                                // If no match with ItemGroupRepeatKey, try without it
                                                                if (!codeList || codeList.length === 0) {
                                                                    codeList = codeListDefs?.filter((cl) =>
                                                                        cl?.OID === nestedItemDef?.CodeListRef?.CodeListOID
                                                                    );
                                                                }

                                                                return (
                                                                    <SingleSelect_v2
                                                                        OID={OID}
                                                                        itemDef={nestedItemDef}
                                                                        conditionDef={conditionDef}
                                                                        codeList={codeList}
                                                                        onItemchange={(itemData: ItemData) => {
                                                                            handleNestedItemChange(
                                                                                itemData,
                                                                                nestedAnswerKey,
                                                                                nestedDataType,
                                                                                nestedChild,
                                                                                instanceIndex
                                                                            );
                                                                        }}
                                                                        showStatus={() => { }}
                                                                        itemDatas={nestedItemDatas}
                                                                        error={undefined}
                                                                        index={0}
                                                                        isRepeatItemGrp="Yes"
                                                                        itgrpInd={nestedInstanceIndex}
                                                                        methodDefs={methodDefs}
                                                                        readonly={readonly}
                                                                        imageBaseUrl={imageBaseUrl}
                                                                    />
                                                                );
                                                            })()}

                                                            {nestedDataType === 'multiselect' && (() => {
                                                                const nestedItemDatas = formatNestedItemDatas(
                                                                    nestedAnswerKey,
                                                                    nestedItemRef,
                                                                    nestedItemDef,
                                                                    nestedInstanceIndex,
                                                                    instanceIndex
                                                                );

                                                                const conditionDef = conditionDefs?.filter((cd) => {
                                                                    if (!cd?.FormalExpression?.text) return false;
                                                                    let cdn = cd.FormalExpression.text.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{ }! ]/g, '');
                                                                    return cdn === nestedItemDef?.OID;
                                                                }) || [];

                                                                // Find the codeList for this nested item
                                                                let codeList = codeListDefs?.filter((cl) =>
                                                                    cl?.OID === nestedItemDef?.CodeListRef?.CodeListOID &&
                                                                    cl?.ItemGroupRepeatKey === nestedInstanceIndex
                                                                );

                                                                // If no match with ItemGroupRepeatKey, try without it
                                                                if (!codeList || codeList.length === 0) {
                                                                    codeList = codeListDefs?.filter((cl) =>
                                                                        cl?.OID === nestedItemDef?.CodeListRef?.CodeListOID
                                                                    );
                                                                }

                                                                return (
                                                                    <MultiChoice_v2
                                                                        OID={OID}
                                                                        itemDef={nestedItemDef}
                                                                        conditionDef={conditionDef}
                                                                        codeList={codeList}
                                                                        onItemchange={(itemData: ItemData) => {
                                                                            handleNestedItemChange(
                                                                                itemData,
                                                                                nestedAnswerKey,
                                                                                nestedDataType,
                                                                                nestedChild,
                                                                                instanceIndex
                                                                            );
                                                                        }}
                                                                        showStatus={() => { }}
                                                                        itemDatas={nestedItemDatas}
                                                                        error={undefined}
                                                                        index={0}
                                                                        isRepeatItemGrp="Yes"
                                                                        itgrpInd={nestedInstanceIndex}
                                                                        methodDefs={methodDefs}
                                                                        readonly={readonly}
                                                                        imageBaseUrl={imageBaseUrl}
                                                                    />
                                                                );
                                                            })()}

                                                            {nestedDataType === 'boolean' && (() => {
                                                                const nestedItemDatas = formatNestedItemDatas(
                                                                    nestedAnswerKey,
                                                                    nestedItemRef,
                                                                    nestedItemDef,
                                                                    nestedInstanceIndex,
                                                                    instanceIndex
                                                                );

                                                                const conditionDef = conditionDefs?.filter((cd) => {
                                                                    if (!cd?.FormalExpression?.text) return false;
                                                                    let cdn = cd.FormalExpression.text.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{ }! ]/g, '');
                                                                    return cdn === nestedItemDef?.OID;
                                                                }) || [];

                                                                // Find the codeList for this nested item
                                                                let codeList = codeListDefs?.filter((cl) =>
                                                                    cl?.OID === nestedItemDef?.CodeListRef?.CodeListOID &&
                                                                    cl?.ItemGroupRepeatKey === nestedInstanceIndex
                                                                );

                                                                // If no match with ItemGroupRepeatKey, try without it
                                                                if (!codeList || codeList.length === 0) {
                                                                    codeList = codeListDefs?.filter((cl) =>
                                                                        cl?.OID === nestedItemDef?.CodeListRef?.CodeListOID
                                                                    );
                                                                }

                                                                return (
                                                                    <RadioType_v2
                                                                        OID={OID}
                                                                        itemDef={nestedItemDef}
                                                                        conditionDef={conditionDef}
                                                                        codeList={codeList}
                                                                        onItemchange={(itemData: ItemData) => {
                                                                            handleNestedItemChange(
                                                                                itemData,
                                                                                nestedAnswerKey,
                                                                                nestedDataType,
                                                                                nestedChild,
                                                                                instanceIndex
                                                                            );
                                                                        }}
                                                                        showStatus={() => { }}
                                                                        itemDatas={nestedItemDatas}
                                                                        error={undefined}
                                                                        index={0}
                                                                        isRepeatItemGrp="Yes"
                                                                        itgrpInd={nestedInstanceIndex}
                                                                        methodDefs={methodDefs}
                                                                    />
                                                                );
                                                            })()}

                                                            {nestedDataType === 'date' && (() => {
                                                                const nestedItemDatas = formatNestedItemDatas(
                                                                    nestedAnswerKey,
                                                                    nestedItemRef,
                                                                    nestedItemDef,
                                                                    nestedInstanceIndex,
                                                                    instanceIndex
                                                                );

                                                                const conditionDef = conditionDefs?.filter((cd) => {
                                                                    if (!cd?.FormalExpression?.text) return false;
                                                                    let cdn = cd.FormalExpression.text.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{ }! ]/g, '');
                                                                    return cdn === nestedItemDef?.OID;
                                                                }) || [];

                                                                // Find the codeList for this nested item
                                                                let codeList = codeListDefs?.filter((cl) =>
                                                                    cl?.OID === nestedItemDef?.CodeListRef?.CodeListOID &&
                                                                    cl?.ItemGroupRepeatKey === nestedInstanceIndex
                                                                );

                                                                // If no match with ItemGroupRepeatKey, try without it
                                                                if (!codeList || codeList.length === 0) {
                                                                    codeList = codeListDefs?.filter((cl) =>
                                                                        cl?.OID === nestedItemDef?.CodeListRef?.CodeListOID
                                                                    );
                                                                }

                                                                return (
                                                                    <DateSelector
                                                                        itemDef={nestedItemDef}
                                                                        conditionDef={conditionDef}
                                                                        codeList={codeList}
                                                                        onItemchange={(itemData: ItemData) => {
                                                                            handleNestedItemChange(
                                                                                itemData,
                                                                                nestedAnswerKey,
                                                                                nestedDataType,
                                                                                nestedChild,
                                                                                instanceIndex
                                                                            );
                                                                        }}
                                                                        itemDatas={nestedItemDatas}
                                                                        error={undefined}
                                                                    />
                                                                );
                                                            })()}

                                                            {nestedDataType === 'datetime' && (() => {
                                                                const nestedItemDatas = formatNestedItemDatas(
                                                                    nestedAnswerKey,
                                                                    nestedItemRef,
                                                                    nestedItemDef,
                                                                    nestedInstanceIndex,
                                                                    instanceIndex
                                                                );

                                                                const conditionDef = conditionDefs?.filter((cd) => {
                                                                    if (!cd?.FormalExpression?.text) return false;
                                                                    let cdn = cd.FormalExpression.text.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{ }! ]/g, '');
                                                                    return cdn === nestedItemDef?.OID;
                                                                }) || [];

                                                                // Find the codeList for this nested item
                                                                let codeList = codeListDefs?.filter((cl) =>
                                                                    cl?.OID === nestedItemDef?.CodeListRef?.CodeListOID &&
                                                                    cl?.ItemGroupRepeatKey === nestedInstanceIndex
                                                                );

                                                                // If no match with ItemGroupRepeatKey, try without it
                                                                if (!codeList || codeList.length === 0) {
                                                                    codeList = codeListDefs?.filter((cl) =>
                                                                        cl?.OID === nestedItemDef?.CodeListRef?.CodeListOID
                                                                    );
                                                                }

                                                                return (
                                                                    <DateTime
                                                                        itemDef={nestedItemDef}
                                                                        conditionDef={conditionDef}
                                                                        codeList={codeList}
                                                                        onItemchange={(itemData: ItemData) => {
                                                                            handleNestedItemChange(
                                                                                itemData,
                                                                                nestedAnswerKey,
                                                                                nestedDataType,
                                                                                nestedChild,
                                                                                instanceIndex
                                                                            );
                                                                        }}
                                                                        itemDatas={nestedItemDatas}
                                                                        error={undefined}
                                                                    />
                                                                );
                                                            })()}

                                                            {nestedDataType === 'combobox' && (() => {
                                                                const nestedItemDatas = formatNestedItemDatas(
                                                                    nestedAnswerKey,
                                                                    nestedItemRef,
                                                                    nestedItemDef,
                                                                    nestedInstanceIndex,
                                                                    instanceIndex
                                                                );

                                                                const conditionDef = conditionDefs?.filter((cd) => {
                                                                    if (!cd?.FormalExpression?.text) return false;
                                                                    let cdn = cd.FormalExpression.text.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{ }! ]/g, '');
                                                                    return cdn === nestedItemDef?.OID;
                                                                }) || [];

                                                                // Find the codeList for this nested item
                                                                let codeList = codeListDefs?.filter((cl) =>
                                                                    cl?.OID === nestedItemDef?.CodeListRef?.CodeListOID &&
                                                                    cl?.ItemGroupRepeatKey === nestedInstanceIndex
                                                                );

                                                                // If no match with ItemGroupRepeatKey, try without it
                                                                if (!codeList || codeList.length === 0) {
                                                                    codeList = codeListDefs?.filter((cl) =>
                                                                        cl?.OID === nestedItemDef?.CodeListRef?.CodeListOID
                                                                    );
                                                                }

                                                                return (
                                                                    <Combo
                                                                        itemDef={nestedItemDef}
                                                                        conditionDef={conditionDef}
                                                                        codeList={codeList}
                                                                        onItemchange={(itemData: ItemData) => {
                                                                            handleNestedItemChange(
                                                                                itemData,
                                                                                nestedAnswerKey,
                                                                                nestedDataType,
                                                                                nestedChild,
                                                                                instanceIndex
                                                                            );
                                                                        }}
                                                                        showStatus={() => { }}
                                                                        itemDatas={nestedItemDatas}
                                                                        error={undefined}
                                                                    />
                                                                );
                                                            })()}
                                                        </Box>
                                                    </Box>

                                                    {/* Right Column: Image (if available) */}
                                                    {hasImage && (
                                                        <Box sx={{ flex: '0 0 auto' }}>
                                                            <Box className="question-image-container">
                                                                <img
                                                                    src={thumbnailSrc || fullImageSrc || ''}
                                                                    alt={nestedQuestionText || 'Question image'}
                                                                    className="question-image-multi-instance"
                                                                    onError={(e) => {
                                                                        console.error(`[QuestionRenderer NESTED] Failed to load question image for ${nestedItemDef?.OID}:`, thumbnailSrc);
                                                                        e.currentTarget.parentElement!.style.display = 'none';
                                                                    }}
                                                                />
                                                                <Box
                                                                    className="question-image-zoom-btn"
                                                                    onClick={(e) => {
                                                                        e.stopPropagation();
                                                                        setZoomedQuestionImage({
                                                                            url: (fullImageSrc || thumbnailSrc) as string,
                                                                            label: nestedQuestionText || 'Question image'
                                                                        });
                                                                    }}
                                                                />
                                                            </Box>
                                                        </Box>
                                                    )}
                                                </Box>
                                            </Box>
                                        );
                                    })}

                                    {(!nestedChild.itemGroupDef.ItemRef || nestedChild.itemGroupDef.ItemRef.length === 0) && (
                                        <Typography variant="body2" color="textSecondary" sx={{ textAlign: 'center', py: 2 }}>
                                            No questions defined for this nested group.
                                        </Typography>
                                    )}
                                </Box>
                            ))}
                        </Box>
                    </Box> {/* Close unified nested container */}
                </Box>
            );
        });
    };

    // Render repeating group tabs seamlessly (as part of main question card)
    const renderRepeatingGroupTabsSeamless = (repeatingChild: any, stepId: string) => {

        if (!getRepeatingInstanceCount || !repeatingChild.controlItemOID) return null;
        const instanceCount = getRepeatingInstanceCount(
            repeatingChild.controlItemOID,
            parentInstanceSuffix  // Pass parent suffix for nested groups
        );

        if (instanceCount > 0) {
            const controlItemDef = itemDefs?.find((d: any) => d.OID === repeatingChild.controlItemOID);
            if (controlItemDef?.RangeCheck && Array.isArray(controlItemDef.RangeCheck)) {
                const localAnswerKey = parentInstanceSuffix
                    ? `${repeatingChild.controlItemOID}${parentInstanceSuffix}`
                    : repeatingChild.controlItemOID;
                const localAnswer = answersDataRef.current[localAnswerKey];
                const localValue = localAnswer?.value ?? localAnswer;
                if (localValue !== undefined && localValue !== null && localValue !== '') {
                    const rangeResult = validateRangeCheck(localValue, controlItemDef.RangeCheck);
                    if (!rangeResult.isValid) {
                        return null;
                    }
                }
            }
        }

        const groupOID = repeatingChild.itemGroupDef.OID;
        const activeTab = activeRepeatingTabs[groupOID] || 0;

        const hasSummarizeFlag = getGroupSummarizeInstances(groupOID);
        const isSummarized = hasSummarizeFlag && (summarizedGroups[groupOID] === true);

        const visibleInstanceCount = isSummarized ? 1 : instanceCount;

        if (instanceCount === 0) {
            const _cDef2 = itemDefs?.find((d: any) => d.OID === repeatingChild.controlItemOID);
            if (_cDef2?.RangeCheck && Array.isArray(_cDef2.RangeCheck)) {
                const _key2 = parentInstanceSuffix
                    ? `${repeatingChild.controlItemOID}${parentInstanceSuffix}`
                    : repeatingChild.controlItemOID;
                const _val2 = answersDataRef.current[_key2]?.value ?? '';
                if (_val2 !== '' && !validateRangeCheck(_val2, _cDef2.RangeCheck).isValid) {
                    return null;
                }
            }
            return (
                <Box sx={{
                    p: 2,
                    backgroundColor: '#f8f9fa',
                    borderRadius: '4px',
                    border: '1px dashed #dee2e6',
                    textAlign: 'center'
                }}>
                    <Typography variant="body2" color="textSecondary">
                        Enter a value in the control field above to create instances of "{repeatingChild.label}"
                    </Typography>
                </Box>
            );
        }

        const tabInstances = Array.from({ length: visibleInstanceCount }, (_, index) => index + 1);

        return (
            <Box sx={{ maxWidth: '100%', width: '100%', overflow: 'visible' }}>
                {/* Tab Header - seamless with main question */}
                <Box sx={{
                    backgroundColor: '#f8f9fa',
                    p: 2,
                    borderRadius: '4px 4px 0 0',
                    border: '1px solid #e0e0e0',
                    borderBottom: 'none',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    flexWrap: 'wrap',
                    gap: 1
                }}>
                    <Typography variant="subtitle1" sx={{ fontSize: '0.95rem', fontWeight: 600, color: '#495057' }}>
                        {repeatingChild.label}
                    </Typography>

                    {hasSummarizeFlag && (
                        <FormControlLabel
                            control={
                                <Switch
                                    size="small"
                                    checked={summarizedGroups[groupOID] === true}
                                    onChange={(e) => {
                                        const newChecked = e.target.checked;
                                        const groupItemOIDs = new Set(
                                            (repeatingChild.itemGroupDef.ItemRef || []).map((ref: any) => ref.ItemOID as string)
                                        );
                                        if (repeatingChild.controlItemOID) {
                                            groupItemOIDs.add(repeatingChild.controlItemOID as string);
                                        }
                                        const hasExistingData = Object.keys(answersData).some(key => {
                                            const matchesGroup = Array.from(groupItemOIDs).some((oid) =>
                                                key === (oid as string) || key.startsWith(`${oid as string}_`)
                                            );
                                            if (!matchesGroup) return false;
                                            const ans = answersData[key];
                                            if (!ans) return false;
                                            const hasValue = ans.value !== undefined && ans.value !== null && ans.value !== '';
                                            const hasCodeListSelection = Array.isArray(ans.CodeList) &&
                                                ans.CodeList.length > 0 &&
                                                ans.CodeList[0]?.CodeListItem?.some((cli: any) => cli.IsEnabled === true);
                                            return hasValue || hasCodeListSelection;
                                        });
                                        if (hasExistingData) {
                                            setSummarizeConfirmDialog({
                                                open: true,
                                                groupOID,
                                                newValue: newChecked,
                                                repeatingChild
                                            });
                                        } else {
                                            setSummarizedGroups(prev => ({ ...prev, [groupOID]: newChecked }));
                                            setActiveRepeatingTabs(prev => ({ ...prev, [groupOID]: 0 }));
                                        }
                                    }}
                                    color="primary"
                                />
                            }
                            label={
                                <Typography variant="body2" sx={{ fontSize: '0.8rem', color: '#6c757d' }}>
                                    Summarize
                                </Typography>
                            }
                            sx={{ m: 0 }}
                        />
                    )}
                </Box>

                {/* Tabs - Responsive width container */}
                <Box sx={{
                    width: '100%', // Full width to match parent
                    overflow: 'visible'
                }}>
                    <Tabs
                        value={Math.min(activeTab, visibleInstanceCount - 1)}
                        onChange={(event, newValue) => {
                            setActiveRepeatingTabs(prev => ({
                                ...prev,
                                [groupOID]: newValue
                            }));
                        }}
                        variant="scrollable"
                        scrollButtons="auto"
                        allowScrollButtonsMobile
                        sx={{
                            backgroundColor: '#fff',
                            width: '100%', // Full width to match container
                            '& .MuiTabs-flexContainer': {
                                width: '100%' // Force flex container to expand
                            },
                            '& .MuiTab-root': {
                                fontSize: '0.8rem',
                                minHeight: '40px',
                                py: 1,
                                minWidth: '100px', // Fixed minimum width
                                maxWidth: '120px'  // Fixed maximum width
                            },
                            '& .MuiTabs-scrollButtons': {
                                '&.Mui-disabled': {
                                    opacity: 0.3
                                }
                            }
                        }}
                    >
                        {tabInstances.map((instanceIndex) => (
                            <Tab
                                key={instanceIndex}
                                label={isSummarized ? `${repeatingChild.label} Summarize` : `${repeatingChild.label} ${instanceIndex}`}
                                value={instanceIndex - 1}
                                sx={{
                                    textTransform: 'none',
                                    fontSize: '0.8rem',
                                    minWidth: '100px',
                                    maxWidth: '120px',
                                    px: 2
                                }}
                            />
                        ))}
                    </Tabs>
                </Box>

                {/* Tab Content - seamless with main card */}
                <Box sx={{
                    backgroundColor: '#fff',
                    border: '1px solid #e0e0e0',
                    borderTop: 'none',
                    borderRadius: '0 0 4px 4px',
                    position: 'relative',
                    '&::after': {
                        content: '""',
                        position: 'absolute',
                        right: 0,
                        top: 0,
                        bottom: 0,
                        width: '1px',
                        backgroundColor: '#e0e0e0',
                        pointerEvents: 'none',
                        zIndex: 1
                    }
                }}>
                    {tabInstances.map((instanceIndex) => (
                        <Box
                            key={instanceIndex}
                            hidden={activeTab !== instanceIndex - 1}
                            sx={{ display: activeTab === instanceIndex - 1 ? 'block' : 'none', p: 3 }}
                        >
                            {/* Custom Question Rendering for Repeating Instances */}
                            {repeatingChild.itemGroupDef.ItemRef && repeatingChild.itemGroupDef.ItemRef.map((itemRef: any, questionIndex: number) => {

                                const itemDefinition = itemDefs.find(item => item.OID === itemRef.ItemOID);
                                const questionText = itemDefinition?.Question?.TranslatedText?.text || '';
                                const dataType = itemDefinition?.DataType;
                                const answerKey = `${itemRef.ItemOID}_${instanceIndex}`;
                                const currentParentGroupOID = repeatingChild.itemGroupDef.OID; // The group we're currently rendering inside

                                const stripSuffix = (oid: string) => oid?.replace(/_\d+$/g, '') || oid;
                                const currentParentBase = stripSuffix(currentParentGroupOID);

                                const isRepeatCountQuestion = repeatingChildren?.some(rc => {
                                    // Must control this specific question
                                    if (rc.controlItemOID !== itemRef.ItemOID) return false;

                                    // For top-level rendering (no parent instance suffix)
                                    if (!parentInstanceSuffix) {
                                        return !rc.isNested; // Only direct children
                                    }

                                    // For nested rendering (inside an instance)
                                    if (rc.isNested) {
                                        const rcParentBase = stripSuffix(rc.parentRepeatingGroupOID);
                                        return currentParentBase === rcParentBase;
                                    }

                                    return false;
                                });

                                if (isRepeatCountQuestion) {
                                    return null; // Don't render RepeatCount questions inside instances
                                }

                                // HIDE DERIVED FIELDS: Skip questions with MethodOID (calculated fields like M.7)
                                // DERIVED FIELDS: Render as read-only display (not editable)
                                if (itemRef.MethodOID) {
                                    const md = methodDefs?.find((m: any) => m.OID === itemRef.MethodOID);
                                    if (!md?.FormalExpression?.text || md.FormalExpression.text.includes('RepeatCount')) {
                                        return null; // Skip UI-control methods entirely
                                    }

                                    const outputKey = `${itemRef.ItemOID}_${instanceIndex}`;
                                    const outputData = answersData[outputKey];
                                    const outputVal = outputData && typeof outputData === 'object'
                                        ? outputData.value
                                        : outputData;

                                    const outputItemDef = itemDefs?.find((d: any) => d.OID === itemRef.ItemOID);
                                    const questionText = outputItemDef?.Question?.TranslatedText?.text || '';
                                    const measureUnit = measureDef?.MeasurementUnit?.find(
                                        (mu: any) => mu?.OID === outputItemDef?.MeasurementOid
                                    );

                                    const translatedText = measureUnit?.Symbol?.TranslatedText;

                                    const unitSymbol =
                                        Array.isArray(translatedText)
                                            ? translatedText[0]?.text || ''
                                            : translatedText?.text || '';
                                    if (!itemDefinition) return null;
                                    const handleChange = (key: string, value: string) => {
                                        setAnswersData((prev: any) => ({
                                            ...prev,
                                            [key]: value
                                        }));
                                    };

                                    return (
                                        <Box
                                            key={`${itemRef.ItemOID}_derived_${instanceIndex}`}
                                            sx={{
                                                mb: 3,
                                                p: 0,
                                            }}
                                        >
                                            <Typography
                                                variant="h6"
                                                sx={{
                                                    fontSize: '1.1rem',
                                                    fontWeight: 500,
                                                    color: '#1a365d',
                                                    mb: 1
                                                }}
                                            >
                                                {questionText}
                                            </Typography>

                                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                                <TextField id="question-renderer-card-v2-textfield-textfield"
                                                    variant="outlined"
                                                    size="small"
                                                    sx={{
                                                        m: 1,
                                                        '& .MuiInputBase-root': {
                                                            backgroundColor: '#f5f5f5',
                                                            cursor: 'not-allowed'
                                                        }
                                                    }}
                                                    value={outputVal || ''}
                                                    InputProps={{
                                                        readOnly: itemRef.ReadOnly === 'Yes' || !!itemRef.MethodOID,
                                                    }}
                                                    fullWidth
                                                />

                                                {unitSymbol && (
                                                    <Typography sx={{ textAlign: 'center' }}>
                                                        {unitSymbol}
                                                    </Typography>
                                                )}
                                            </Box>
                                        </Box>
                                    );
                                }

                                const showOn = itemRef.ShowOn;
                                if (isSummarized && showOn === 'Normal') return null;
                                if (!isSummarized && showOn === 'Summarize') return null;

                                const hasCondition = itemRef.CollectionExceptionConditionOID &&
                                    itemRef.CollectionExceptionConditionOID !== "NONE";

                                if (hasCondition && dependencyParser) {
                                    const instanceSuffix = `_${instanceIndex}`;
                                    const questionKey = `${itemRef.ItemOID}${instanceSuffix}`;
                                    // Merge parentAnswersData and local answersData
                                    // Local answersData has newer values (like I.38_1 when user just clicked Yes)
                                    // parentAnswersData may not be updated yet due to prop propagation delay
                                    const currentAnswersData = { ...parentAnswersData, ...answersData };
                                    const parentQuestions = dependencyParser.getParentQuestions(itemRef.ItemOID);

                                    let hasParentAnswer = true;
                                    if (parentQuestions && parentQuestions.length > 0) {
                                        const parentKey = `${parentQuestions[0]}${instanceSuffix}`;
                                        // Check for actual non-empty value, not just object existence
                                        // {value: ''} should NOT be considered as "has answer"
                                        const parentData = currentAnswersData[parentKey];

                                        // For singleselect questions, the selection is stored in
                                        // CodeList.CodeListItem.IsEnabled flag, NOT in the Value field
                                        // (Value is always '' for singleselect with CodeList)
                                        let hasValueAnswer = !!(parentData && parentData.value !== undefined && parentData.value !== null && parentData.value !== '');

                                        // Check CodeList for IsEnabled selection (used by SingleSelect_v2)
                                        let hasCodeListSelection = false;
                                        if (parentData?.CodeList && Array.isArray(parentData.CodeList)) {
                                            for (const cl of parentData.CodeList) {
                                                if (cl?.CodeListItem && Array.isArray(cl.CodeListItem)) {
                                                    const enabledItem = cl.CodeListItem.find((item: any) => item?.IsEnabled === true);
                                                    if (enabledItem) {
                                                        hasCodeListSelection = true;
                                                        break;
                                                    }
                                                }
                                            }
                                        }

                                        hasParentAnswer = hasValueAnswer || hasCodeListSelection;

                                        if (!hasParentAnswer) {
                                            // Parent has no valid answer - hide conditional child
                                            // Also clear from tracking to ensure fresh state
                                            shownConditionalQuestionsRef.current.delete(questionKey);
                                            return null;
                                        }
                                    }

                                    if (hasParentAnswer) {
                                        const shouldRenderQuestion = dependencyParser.evaluateConditionWithInstance(
                                            itemRef.ItemOID,
                                            currentAnswersData,
                                            instanceSuffix
                                        );


                                        if (shouldRenderQuestion) {
                                            // Track that this question has been shown
                                            shownConditionalQuestionsRef.current.add(questionKey);
                                        } else {
                                            // Remove from tracking when hidden
                                            shownConditionalQuestionsRef.current.delete(questionKey);
                                            return null;
                                        }
                                    }
                                }

                                // STABLE SELECTION: Check both answersData and itemData for current value
                                let currentAnswer = answersData[answerKey];

                                // If not in answersData, check itemData as backup
                                if (!currentAnswer) {
                                    const itemDataEntry = itemData.find(item =>
                                        item.ItemOID === itemRef.ItemOID &&
                                        item.ItemGroupRepeatKey === instanceIndex
                                    );
                                    if (itemDataEntry && itemDataEntry.Value) {
                                        currentAnswer = {
                                            value: itemDataEntry.Value,
                                            dataType: dataType
                                        };
                                    }
                                }

                                if (!itemDefinition) return null;

                                // Prepare image component
                                const thumbnailSrc = getImageUrl(itemDefinition?.ThumbnailUrl, imageBaseUrl);
                                const fullImageSrc = getImageUrl(itemDefinition?.ImageUrl, imageBaseUrl);
                                const hasImage = !!(thumbnailSrc || fullImageSrc);

                                return (
                                    <Box key={`${itemRef.ItemOID}_summarize_${summarizedGroups[groupOID] ? '1' : '0'}`} sx={{ mb: 3 }}>
                                        <Box sx={{ display: 'flex', gap: 3, alignItems: 'flex-start' }}>
                                            <Box sx={{ flex: 1, minWidth: 0 }}>
                                                {questionText && (
                                                    <Typography variant="h6" sx={{ fontSize: '1.1rem', fontWeight: 500, color: '#1a365d', mb: 2 }}>
                                                        {questionText}{itemRef.Mandatory === 'Yes' && <span style={{ color: 'red' }}> *</span>}
                                                    </Typography>
                                                )}

                                                {/* Answer Input */}
                                                <Box>
                                                    {dataType === 'singleselect' && (() => {
                                                        const parentItemDatas = formatParentItemDatas(answerKey, itemRef, itemDefinition, instanceIndex);

                                                        // Get condition definition
                                                        const conditionDef = conditionDefs?.filter((cd) => {
                                                            if (!cd?.FormalExpression?.text) return false;
                                                            let cdn = cd.FormalExpression.text.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{ }! ]/g, '');
                                                            return cdn === itemDefinition?.OID;
                                                        }) || [];

                                                        // Find the codeList for this item
                                                        // First try with ItemGroupRepeatKey, then fallback to just OID match
                                                        let codeList = codeListDefs?.filter((cl) =>
                                                            cl?.OID === itemDefinition?.CodeListRef?.CodeListOID &&
                                                            cl?.ItemGroupRepeatKey === instanceIndex
                                                        );

                                                        // If no match with ItemGroupRepeatKey, try without it
                                                        if (!codeList || codeList.length === 0) {
                                                            codeList = codeListDefs?.filter((cl) =>
                                                                cl?.OID === itemDefinition?.CodeListRef?.CodeListOID
                                                            );
                                                        }

                                                        return (
                                                            <SingleSelect_v2
                                                                OID={OID}
                                                                itemDef={itemDefinition}
                                                                onItemchange={(itemData) => handleParentItemChange(itemData, answerKey, dataType, repeatingChild, instanceIndex)}
                                                                itemDatas={parentItemDatas}
                                                                error={undefined}
                                                                showStatus={() => { }}
                                                                methodDefs={methodDefs}
                                                                codeList={codeList}
                                                                conditionDef={conditionDef}
                                                                isRepeatItemGrp="Yes"
                                                                itgrpInd={instanceIndex}
                                                                itemGrp={repeatingChild.itemGroupDef}
                                                                mapping={undefined}
                                                                tradeData={undefined}
                                                                readonly={readonly}
                                                                imageBaseUrl={imageBaseUrl}
                                                            />
                                                        );
                                                    })()}

                                                    {dataType === 'multiselect' && (() => {
                                                        const parentItemDatas = formatParentItemDatas(answerKey, itemRef, itemDefinition, instanceIndex);

                                                        // Get condition definition
                                                        const conditionDef = conditionDefs?.filter((cd) => {
                                                            if (!cd?.FormalExpression?.text) return false;
                                                            let cdn = cd.FormalExpression.text.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{ }! ]/g, '');
                                                            return cdn === itemDefinition?.OID;
                                                        }) || [];

                                                        // Find the codeList for this item
                                                        let codeList = codeListDefs?.filter((cl) =>
                                                            cl?.OID === itemDefinition?.CodeListRef?.CodeListOID &&
                                                            cl?.ItemGroupRepeatKey === instanceIndex
                                                        );

                                                        // If no match with ItemGroupRepeatKey, try without it
                                                        if (!codeList || codeList.length === 0) {
                                                            codeList = codeListDefs?.filter((cl) =>
                                                                cl?.OID === itemDefinition?.CodeListRef?.CodeListOID
                                                            );
                                                        }

                                                        return (
                                                            <MultiChoice_v2
                                                                OID={OID}
                                                                itemDef={itemDefinition}
                                                                onItemchange={(itemData) => handleParentItemChange(itemData, answerKey, dataType, repeatingChild, instanceIndex)}
                                                                itemDatas={parentItemDatas}
                                                                error={undefined}
                                                                showStatus={() => { }}
                                                                methodDefs={methodDefs}
                                                                codeList={codeList}
                                                                conditionDef={conditionDef}
                                                                isRepeatItemGrp="Yes"
                                                                itgrpInd={instanceIndex}
                                                                readonly={readonly}
                                                                imageBaseUrl={imageBaseUrl}
                                                                index={0}
                                                            />
                                                        );
                                                    })()}

                                                    {dataType === 'boolean' && (() => {
                                                        const parentItemDatas = formatParentItemDatas(answerKey, itemRef, itemDefinition, instanceIndex);

                                                        const conditionDef = conditionDefs?.filter((cd) => {
                                                            if (!cd?.FormalExpression?.text) return false;
                                                            let cdn = cd.FormalExpression.text.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{ }! ]/g, '');
                                                            return cdn === itemDefinition?.OID;
                                                        }) || [];

                                                        let codeList = codeListDefs?.filter((cl) =>
                                                            cl?.OID === itemDefinition?.CodeListRef?.CodeListOID &&
                                                            cl?.ItemGroupRepeatKey === instanceIndex
                                                        );

                                                        if (!codeList || codeList.length === 0) {
                                                            codeList = codeListDefs?.filter((cl) =>
                                                                cl?.OID === itemDefinition?.CodeListRef?.CodeListOID
                                                            );
                                                        }

                                                        return (
                                                            <RadioType_v2
                                                                OID={OID}
                                                                itemDef={itemDefinition}
                                                                conditionDef={conditionDef}
                                                                codeList={codeList}
                                                                onItemchange={(itemData) => handleParentItemChange(itemData, answerKey, dataType, repeatingChild, instanceIndex)}
                                                                showStatus={() => { }}
                                                                itemDatas={parentItemDatas}
                                                                error={undefined}
                                                                index={0}
                                                                isRepeatItemGrp="Yes"
                                                                itgrpInd={instanceIndex}
                                                                methodDefs={methodDefs}
                                                                readonly={readonly}
                                                            />
                                                        );
                                                    })()}

                                                    {dataType === 'feetinches' && (() => {
                                                        const parentItemDatas = formatParentItemDatas(
                                                            answerKey, itemRef, itemDefinition, instanceIndex
                                                        );
                                                        return (
                                                            <FeetInchesInput_v2
                                                                OID={OID}
                                                                itemDef={itemDefinition}
                                                                onItemchange={(itemData: any) =>
                                                                    handleParentItemChange(itemData, answerKey, dataType, repeatingChild, instanceIndex)
                                                                }
                                                                itemDatas={parentItemDatas}
                                                                error={undefined}
                                                                isRepeatItemGrp="Yes"
                                                                itgrpInd={instanceIndex}
                                                                readonly={readonly}
                                                            />
                                                        );
                                                    })()}
                                                    {dataType === 'integer' && (() => {
                                                        const parentItemDatas = formatParentItemDatas(answerKey, itemRef, itemDefinition, instanceIndex);
                                                        const measureUnit = measureDef; // Use existing measureDef prop

                                                        return (
                                                            <IntegerText
                                                                OID={OID}
                                                                itemDef={itemDefinition}
                                                                measureDef={measureUnit}
                                                                onItemchange={(itemData) => handleParentItemChange(itemData, answerKey, dataType, repeatingChild, instanceIndex)}
                                                                itemDatas={parentItemDatas}
                                                                error={undefined}
                                                                isRepeatItemGrp="Yes"
                                                                itgrpInd={instanceIndex}
                                                                itemGrp={repeatingChild.itemGroupDef}
                                                                showStatus={() => { }}
                                                                methodDefs={methodDefs}
                                                                mapping={undefined}
                                                                tradeData={undefined}
                                                            />
                                                        );
                                                    })()}

                                                    {dataType === 'float' && (() => {
                                                        const parentItemDatas = formatParentItemDatas(answerKey, itemRef, itemDefinition, instanceIndex);
                                                        const measureUnit = measureDef; // Use existing measureDef prop

                                                        return (
                                                            <FloatText
                                                                itemDef={itemDefinition}
                                                                measureDef={measureUnit}
                                                                onItemchange={(itemData) => handleParentItemChange(itemData, answerKey, dataType, repeatingChild, instanceIndex)}
                                                                itemDatas={parentItemDatas}
                                                                error={undefined}
                                                            />
                                                        );
                                                    })()}

                                                    {dataType === 'text' && (() => {
                                                        const parentItemDatas = formatParentItemDatas(answerKey, itemRef, itemDefinition, instanceIndex);
                                                        const existingItem = parentItemDatas?.[0];
                                                        return (
                                                            <FreeText_v2
                                                                itemDef={itemDefinition}
                                                                value={existingItem?.Value ?? ''}
                                                                onChange={(itemOID, value) =>
                                                                    handleParentItemChange(
                                                                        { ...(existingItem ?? { ItemOID: itemOID, CodeList: [], MetaKey: itemDefinition?.MetaKey ?? '', ItemGroupRepeatKey: instanceIndex }), Value: value },
                                                                        answerKey, dataType, repeatingChild, instanceIndex
                                                                    )
                                                                }
                                                                measureDef={measureDef}
                                                                error={undefined}
                                                                readonly={readonly}
                                                            />
                                                        );
                                                    })()}

                                                    {dataType === 'string' && (() => {
                                                        const parentItemDatas = formatParentItemDatas(answerKey, itemRef, itemDefinition, instanceIndex);
                                                        const existingItem = parentItemDatas?.[0];
                                                        return (
                                                            <FreeText_v2
                                                                itemDef={itemDefinition}
                                                                value={existingItem?.Value ?? ''}
                                                                onChange={(itemOID, value) =>
                                                                    handleParentItemChange(
                                                                        { ...(existingItem ?? { ItemOID: itemOID, CodeList: [], MetaKey: itemDefinition?.MetaKey ?? '', ItemGroupRepeatKey: instanceIndex }), Value: value },
                                                                        answerKey, dataType, repeatingChild, instanceIndex
                                                                    )
                                                                }
                                                                measureDef={measureDef}
                                                                error={undefined}
                                                                readonly={readonly}
                                                            />
                                                        );
                                                    })()}

                                                    {dataType === 'multilinetext' && (() => {
                                                        const parentItemDatas = formatParentItemDatas(answerKey, itemRef, itemDefinition, instanceIndex);
                                                        return (
                                                            <MultilineText
                                                                itemDef={itemDefinition}
                                                                onItemchange={(itemData) => handleParentItemChange(itemData, answerKey, dataType, repeatingChild, instanceIndex)}
                                                                itemDatas={parentItemDatas}
                                                                error={undefined}
                                                                readonly={readonly}
                                                            />
                                                        );
                                                    })()}

                                                    {dataType === 'combobox' && (() => {
                                                        const parentItemDatas = formatParentItemDatas(answerKey, itemRef, itemDefinition, instanceIndex);
                                                        return (
                                                            <Combo
                                                                itemDef={itemDefinition}
                                                                conditionDef={[]}
                                                                codeList={[]}
                                                                onItemchange={(itemData) => handleParentItemChange(itemData, answerKey, dataType, repeatingChild, instanceIndex)}
                                                                showStatus={() => {}}
                                                                itemDatas={parentItemDatas}
                                                                error={undefined}
                                                            />
                                                        );
                                                    })()}

                                                    {dataType === 'date' && (() => {
                                                        const parentItemDatas = formatParentItemDatas(answerKey, itemRef, itemDefinition, instanceIndex);
                                                        return (
                                                            <DateSelector
                                                                itemDef={itemDefinition}
                                                                conditionDef={[]}
                                                                codeList={[]}
                                                                onItemchange={(itemData) => handleParentItemChange(itemData, answerKey, dataType, repeatingChild, instanceIndex)}
                                                                itemDatas={parentItemDatas}
                                                                error={undefined}
                                                            />
                                                        );
                                                    })()}

                                                    {dataType === 'datetime' && (() => {
                                                        const parentItemDatas = formatParentItemDatas(answerKey, itemRef, itemDefinition, instanceIndex);
                                                        return (
                                                            <DateTime
                                                                itemDef={itemDefinition}
                                                                conditionDef={[]}
                                                                codeList={[]}
                                                                onItemchange={(itemData) => handleParentItemChange(itemData, answerKey, dataType, repeatingChild, instanceIndex)}
                                                                itemDatas={parentItemDatas}
                                                                error={undefined}
                                                            />
                                                        );
                                                    })()}
                                                </Box>
                                            </Box>

                                            {/* Right Column: Image (if available) */}
                                            {hasImage && (
                                                <Box sx={{ flex: '0 0 auto' }}>
                                                    <Box className="question-image-container">
                                                        <img
                                                            src={thumbnailSrc || fullImageSrc || ''}
                                                            alt={questionText || 'Question image'}
                                                            className="question-image-multi-instance"
                                                            onError={(e) => {
                                                                console.error(`[QuestionRenderer MULTI-INSTANCE] Failed to load question image for ${itemDefinition?.OID}:`, thumbnailSrc);
                                                                e.currentTarget.parentElement!.style.display = 'none';
                                                            }}
                                                        />
                                                        <Box
                                                            className="question-image-zoom-btn"
                                                            onClick={(e) => {
                                                                e.stopPropagation();
                                                                setZoomedQuestionImage({
                                                                    url: (fullImageSrc || thumbnailSrc) as string,
                                                                    label: questionText || 'Question image'
                                                                });
                                                            }}
                                                        />
                                                    </Box>
                                                </Box>
                                            )}
                                        </Box>

                                        {renderNestedTabsForQuestion(itemRef.ItemOID, instanceIndex, repeatingChild)}
                                    </Box>
                                );
                            })}

                            {(!repeatingChild.itemGroupDef.ItemRef || repeatingChild.itemGroupDef.ItemRef.length === 0) && (
                                <Typography variant="body2" color="textSecondary" sx={{ textAlign: 'center', py: 4 }}>
                                    No questions defined for this repeating group.
                                </Typography>
                            )}
                        </Box>
                    ))}
                </Box>
            </Box>
        );
    };

    // Render MethodDef ItemGroup with tabs
    const renderMethodDefItemGroup = (itemGroup: any, iGrpReferen: any, itgrpDt: any, indexes: number) => {
        const methodOID = iGrpReferen.MethodOID;

        const rawRepeatCount = getMethodDefRepeatCount_local(methodOID);

        const effectiveRepeatCount = (() => {
            if (rawRepeatCount <= 0) return 0;
            const md = methodDefs?.find((m) => m.OID === methodOID);
            if (!md?.FormalExpression?.text?.includes(':')) return rawRepeatCount;
            const [controlItemOID] = md.FormalExpression.text
                .replace(/[!()]/g, '').split(':').map((s: string) => s.trim());
            const controlItemDef = itemDefs?.find((d) => d.OID === controlItemOID);
            if (!controlItemDef?.RangeCheck || !Array.isArray(controlItemDef.RangeCheck)) return rawRepeatCount;
            const currentValue = answersDataRef.current[controlItemOID]?.value ?? '';
            const validation = validateRangeCheck(currentValue, controlItemDef.RangeCheck);
            return validation.isValid ? rawRepeatCount : 0;
        })();

        const repeatCount = effectiveRepeatCount;
        const itemName = getMethodDefQuestionText(methodOID);

        const methodDef = methodDefs?.find((md) => md.OID === methodOID);
        let controllingQuestion: any = null;
        let controllingQuestionItemRef: any = null;
        let controllingQuestionData: any = null;

        if (methodDef) {
            const expressionText = methodDef?.FormalExpression?.text;
            if (expressionText && expressionText.includes(':')) {
                const [controlItemOID] = expressionText
                    .replace(/[!()]/g, '')
                    .split(':')
                    .map(str => str.trim());

                // Find the controlling question ItemDef
                controllingQuestion = itemDefs?.find((item) => item.OID === controlItemOID);

                // Find the ItemRef for this controlling question
                controllingQuestionItemRef = itemGroup?.ItemRef?.find((ir: any) => ir.ItemOID === controlItemOID);


                const dataSourceToCheck = parentAnswersData || answersData;


                const answerKey = Object.keys(dataSourceToCheck).find(key => {
                    if (key === controlItemOID) return true; // Exact match
                    if (key.startsWith(controlItemOID + '_')) return true; // With suffix
                    return false;
                });

                if (answerKey && dataSourceToCheck[answerKey]) {
                    controllingQuestionData = {
                        ItemOID: controlItemOID,
                        Value: dataSourceToCheck[answerKey].value,
                        DataType: dataSourceToCheck[answerKey].dataType
                    };
                } else {
                    // Fall back to itemGroupDatas (persisted state)
                    for (const igd of (itemGroupDatas || [])) {
                        const foundItem = igd?.ItemData?.find((id: any) => id?.ItemOID === controlItemOID);
                        if (foundItem) {
                            controllingQuestionData = foundItem;
                            break;
                        }
                    }
                    if (!controllingQuestionData) {
                    }
                }


            }
        }

        const isControllingValueOutOfRange = (() => {
            if (!methodDef?.FormalExpression?.text?.includes(':')) return false;
            const [cOID] = methodDef.FormalExpression.text
                .replace(/[!()]/g, '').split(':').map((s: string) => s.trim());
            const cDef = itemDefs?.find((d: any) => d.OID === cOID);
            if (!cDef?.RangeCheck || !Array.isArray(cDef.RangeCheck)) return false;
            const cVal = answersDataRef.current[cOID]?.value ?? '';
            return cVal !== '' && !validateRangeCheck(cVal, cDef.RangeCheck).isValid;
        })();
        const showOnlyControllingQuestion = repeatCount <= 0 && !isControllingValueOutOfRange;

        // Initialize active tab if not set
        if (activeMethodDefTab[itemGroup.OID] === undefined) {
            setActiveMethodDefTab(prev => ({
                ...prev,
                [itemGroup.OID]: 0
            }));
        }

        const currentTab = activeMethodDefTab[itemGroup.OID] || 0;

        const handleRepeatCountChange = (newItemData: any) => {

            // Call the parent's OnItemDataChange
            OnItemDataChange(newItemData);

            // ALSO update answersData directly for immediate reactivity
            if (newItemData && newItemData[0]?.ItemData?.[0]) {
                const itemData = newItemData[0].ItemData[0];
                const newValue = parseInt(itemData.Value) || 0;

                setAnswersData((prev: any) => {
                    const updated = {
                        ...prev,
                        [itemData.ItemOID]: {
                            value: itemData.Value,
                            dataType: itemData.DataType || 'integer'
                        }
                    };
                    return updated;
                });

                if (updateRepeatingInstanceCount && itemGroup?.OID) {
                    updateRepeatingInstanceCount(
                        itemData.ItemOID,
                        newValue,
                        itemGroup.OID,
                        parentInstanceSuffix
                    );
                } else {
                }
            }
        };

        return (
            <Card key={itemGroup.OID} className='POCard' variant="outlined" sx={{ p: 3, height: '480px', display: 'flex', flexDirection: 'column' }}>
                {controllingQuestion && (
                    <Box sx={{ mb: 3, pb: 2, borderBottom: repeatCount > 0 ? '1px solid #e0e0e0' : 'none' }}>
                        {/* Question Title */}
                        {controllingQuestionItemRef?.Mandatory === 'Yes' ? (
                            <Typography sx={{ mb: 2, fontSize: '1.1rem', fontWeight: 'medium' }}>
                                {controllingQuestion.Question?.TranslatedText?.text} <span style={{ color: 'red' }}>*</span>
                            </Typography>
                        ) : (
                            <Typography sx={{ mb: 2, fontSize: '1.1rem', fontWeight: 'medium' }}>
                                {controllingQuestion.Question?.TranslatedText?.text}
                            </Typography>
                        )}

                        {/* Question Image (if available) - Below question text */}
                        {(() => {
                            const thumbnailSrc = getImageUrl(controllingQuestion?.ThumbnailUrl, imageBaseUrl);
                            const fullImageSrc = getImageUrl(controllingQuestion?.ImageUrl, imageBaseUrl);

                            if (thumbnailSrc || fullImageSrc) {
                                return (
                                    <Box className="question-image-wrapper">
                                        <Box className="question-image-container">
                                            <img
                                                src={thumbnailSrc || fullImageSrc || ''}
                                                alt={controllingQuestion?.Question?.TranslatedText?.text || 'Question image'}
                                                className="question-image"
                                                onError={(e) => {
                                                    console.error(`[QuestionRenderer] Failed to load question image for ${controllingQuestion?.OID}:`, thumbnailSrc);
                                                    e.currentTarget.parentElement!.style.display = 'none';
                                                }}
                                            />
                                            <Box
                                                className="question-image-zoom-btn"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    setZoomedQuestionImage({
                                                        url: (fullImageSrc || thumbnailSrc) as string,
                                                        label: controllingQuestion?.Question?.TranslatedText?.text || 'Question image'
                                                    });
                                                }}
                                            />
                                        </Box>
                                    </Box>
                                );
                            }
                            return null;
                        })()}

                        {/* Render the controlling question input */}
                        {controllingQuestion.DataType === 'integer' && (
                            <IntegerText
                                key={`${controllingQuestion.OID}-${controllingQuestionData?.Value || ''}`}
                                OID={OID}
                                itemDef={controllingQuestion}
                                measureDef={measureDef?.MeasurementUnit?.find((mu) => mu?.OID === controllingQuestion?.MeasurementOid)}
                                onItemchange={handleRepeatCountChange}
                                value={controllingQuestionData?.Value || ''}
                                itemDatas={controllingQuestionData ? [controllingQuestionData] : []}
                                error={validate?.find((v) => v?.OID === controllingQuestion?.OID)}
                                showStatus={showQuestion}
                                isRepeatItemGrp={itemGroup?.Repeating}
                                itgrpInd={1}
                                itemGrp={itemGroup}
                                callRepeat={handleRepeat}
                                methodDefs={methodDefs}
                                mapping={null}
                                tradeData={tradeData}
                            />
                        )}
                    </Box>
                )}

                {/* Show message if no count entered yet */}
                {showOnlyControllingQuestion && (
                    <Box sx={{
                        flex: 1,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        textAlign: 'center',
                        color: '#666'
                    }}>
                        <Typography variant="body1">
                            Enter a number above to add {itemName} details
                        </Typography>
                    </Box>
                )}

                {/* Tabs for RepeatCount instances */}
                {repeatCount > 0 && (
                    <>
                        <Box sx={{ mb: 2, flexShrink: 0 }}>
                            <Box sx={{
                                display: 'flex',
                                gap: 1,
                                p: 1,
                                backgroundColor: '#f5f5f5',
                                borderRadius: '25px',
                                width: 'fit-content',
                                overflowX: 'auto',
                                '&::-webkit-scrollbar': {
                                    height: '4px',
                                },
                                '&::-webkit-scrollbar-track': {
                                    background: 'transparent',
                                },
                                '&::-webkit-scrollbar-thumb': {
                                    background: '#c1c1c1',
                                    borderRadius: '2px',
                                },
                            }}>
                                {Array.from({ length: repeatCount }, (_, index) => (
                                    <Box
                                        key={index}
                                        onClick={() => handleMethodDefTabChange(itemGroup.OID, index)}
                                        sx={{
                                            px: 3,
                                            py: 1,
                                            borderRadius: '20px',
                                            cursor: 'pointer',
                                            fontSize: '0.875rem',
                                            fontWeight: 500,
                                            whiteSpace: 'nowrap',
                                            transition: 'all 0.2s ease-in-out',
                                            backgroundColor: currentTab === index ? '#1976d2' : 'transparent',
                                            color: currentTab === index ? 'white' : '#666',
                                            '&:hover': {
                                                backgroundColor: currentTab === index ? '#1565c0' : '#e0e0e0',
                                            },
                                        }}
                                        role="tab"
                                        aria-selected={currentTab === index}
                                        id={`${itemGroup.OID}-tab-${index}`}
                                        aria-controls={`${itemGroup.OID}-tabpanel-${index}`}
                                    >
                                        {`${itemName.charAt(0).toUpperCase() + itemName.slice(1)} ${index + 1}`}
                                    </Box>
                                ))}
                            </Box>
                        </Box>

                        {/* Tab Content */}
                        <Box sx={{
                            flex: 1,
                            overflowY: 'auto',
                            overflowX: 'hidden',
                            pr: 2,
                            mb: 2,
                            minHeight: 0,
                            '&::-webkit-scrollbar': {
                                width: '8px',
                            },
                            '&::-webkit-scrollbar-track': {
                                background: '#f1f1f1',
                                borderRadius: '4px',
                            },
                            '&::-webkit-scrollbar-thumb': {
                                background: '#c1c1c1',
                                borderRadius: '4px',
                            },
                            '&::-webkit-scrollbar-thumb:hover': {
                                background: '#a8a8a8',
                            },
                        }}>
                            {Array.from({ length: repeatCount }, (_, tabIndex) => (
                                <Box
                                    key={tabIndex}
                                    role="tabpanel"
                                    hidden={currentTab !== tabIndex}
                                    id={`${itemGroup.OID}-tabpanel-${tabIndex}`}
                                    aria-labelledby={`${itemGroup.OID}-tab-${tabIndex}`}
                                    sx={{ height: '100%' }}
                                >
                                    {currentTab === tabIndex && (
                                        <Box>
                                            {(() => {
                                                return null;
                                            })()}
                                            {itemGroup?.ItemRef?.map((ir: any, index: number) => {

                                                if (controllingQuestion && ir.ItemOID === controllingQuestion.OID) {
                                                    return null;
                                                }

                                                if (ir.MethodOID) {
                                                    if (ir.IsVisible === 'Yes') {
                                                        // allow through to render normally
                                                    } else {
                                                        return null;
                                                    }
                                                }

                                                // NEW: Check conditional visibility with instance context
                                                const instanceSuffix = `_${tabIndex + 1}`;

                                                const hasCondition = ir.CollectionExceptionConditionOID && ir.CollectionExceptionConditionOID !== "NONE";


                                                // Only evaluate condition if question has one
                                                if (hasCondition && dependencyParser) {
                                                    if (!isAnswersDataStable) {
                                                        return null;
                                                    }

                                                    // Use parentAnswersData if available (has suffix keys), otherwise fall back to local answersData
                                                    const dataForEvaluation = { ...(parentAnswersData || {}), ...answersData };

                                                    const shouldRenderQuestion = dependencyParser.evaluateConditionWithInstance(
                                                        ir.ItemOID,
                                                        dataForEvaluation,
                                                        instanceSuffix
                                                    );

                                                    if (!shouldRenderQuestion) {
                                                        return null; // Skip rendering this question
                                                    }

                                                } else if (hasCondition && !dependencyParser) {
                                                }

                                                let item: any;

                                                // Create a modified item with RepeatKey for this tab instance
                                                const baseItem = itemDefs?.find((id: any) => id?.OID === ir?.ItemOID);
                                                if (baseItem) {
                                                    item = {
                                                        ...baseItem,
                                                        ItemGroupRepeatKey: tabIndex + 1 // 1-based indexing for tabs
                                                    };
                                                }

                                                let measureUnit = measureDef?.MeasurementUnit?.find((mu) => mu?.OID === item?.MeasurementOid);

                                                // Get item data for this specific tab instance
                                                let itemData = itgrpDt?.map((igd: any) =>
                                                    igd?.ItemData?.find((id: any) =>
                                                        id?.ItemOID === item?.OID &&
                                                        id?.ItemGroupRepeatKey === (tabIndex + 1)
                                                    )
                                                ).filter(Boolean);

                                                let itdt = validate?.find((v) => v?.OID === item?.OID);

                                                let codeList: any = codeListDefs?.filter((codeList) =>
                                                    codeList?.OID === item?.CodeListRef?.CodeListOID &&
                                                    codeList?.ItemGroupRepeatKey === (tabIndex + 1)
                                                );

                                                // Find matching mapping for this item
                                                let matchedMapping: any = null;
                                                if (Array.isArray(mappings) && mappings.length > 0) {
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

                                                    if (matchedItemDefs.length > 0) {
                                                        matchedMapping = matchedItemDefs[0];
                                                    }
                                                }

                                                let condition = conditionDefs?.filter((cd) => {
                                                    let cdn = cd.FormalExpression.text.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{ }! ]/g, '');
                                                    if (cdn === item?.OID)
                                                        return cdn
                                                });

                                                // Apply conditional logic for this item
                                                let showItem = true;
                                                if (ir.CollectionExceptionConditionOID !== null && ir.CollectionExceptionConditionOID !== undefined) {
                                                    let itemConDef = conditionDefs?.find((cd) => cd.OID === ir.CollectionExceptionConditionOID);
                                                    if (itemConDef) {
                                                    }
                                                }

                                                return showItem ? (
                                                    <Box key={`${item?.OID}-${tabIndex}`} sx={{ mb: 2 }}>
                                                        {/* Question Title */}
                                                        {(() => {
                                                            const isBidDetailsPage = isBidDetailsRoute();
                                                            const typographySx = { mt: 2, mb: 2, fontSize: '1.1rem', fontWeight: 'medium' };

                                                            return ir.Mandatory === 'Yes' && item !== undefined
                                                                ? <Typography sx={typographySx}>{item?.Question?.TranslatedText?.text} <span style={{ color: 'red' }}>*</span></Typography>
                                                                : <Typography sx={typographySx}>{item?.Question?.TranslatedText?.text}</Typography>
                                                        })()}

                                                        {/* Question Image (if available) - Below question text */}
                                                        {(() => {
                                                            const thumbnailSrc = getImageUrl(item?.ThumbnailUrl, imageBaseUrl);
                                                            const fullImageSrc = getImageUrl(item?.ImageUrl, imageBaseUrl);

                                                            if (thumbnailSrc || fullImageSrc) {
                                                                return (
                                                                    <Box className="question-image-wrapper">
                                                                        <Box className="question-image-container">
                                                                            <img
                                                                                src={thumbnailSrc || fullImageSrc || ''}
                                                                                alt={item?.Question?.TranslatedText?.text || 'Question image'}
                                                                                className="question-image"
                                                                                onError={(e) => {
                                                                                    console.error(`[QuestionRenderer] Failed to load question image for ${item?.OID}:`, thumbnailSrc);
                                                                                    e.currentTarget.parentElement!.style.display = 'none';
                                                                                }}
                                                                            />
                                                                            <Box
                                                                                className="question-image-zoom-btn"
                                                                                onClick={(e) => {
                                                                                    e.stopPropagation();
                                                                                    setZoomedQuestionImage({
                                                                                        url: (fullImageSrc || thumbnailSrc) as string,
                                                                                        label: item?.Question?.TranslatedText?.text || 'Question image'
                                                                                    });
                                                                                }}
                                                                            />
                                                                        </Box>
                                                                    </Box>
                                                                );
                                                            }
                                                            return null;
                                                        })()}

                                                        {/* Question Input Components */}
                                                        {item?.DataType === 'text' && item !== undefined && <FreeText itemDef={item} measureDef={measureUnit} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} readonly={readonly} />}
                                                        {item?.DataType === 'string' && item !== undefined && <FreeText itemDef={item} measureDef={measureUnit} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} readonly={readonly} />}
                                                        {item?.DataType === 'integer' && item !== undefined && <IntegerText OID={OID} itemDef={item} measureDef={measureUnit} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} showStatus={showQuestion}
                                                            isRepeatItemGrp={itemGroup?.Repeating} itgrpInd={tabIndex + 1} itemGrp={itemGroup} callRepeat={handleRepeat} methodDefs={methodDefs} mapping={matchedMapping} tradeData={tradeData} readonly={readonly} />}
                                                        {item?.DataType === 'measure_ft_in' && item !== undefined && (<FeetInchesInput_v2 OID={OID} itemDef={item} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} isRepeatItemGrp={itemGroup?.Repeating} itgrpInd={tabIndex + 1} readonly={readonly} />)}
                                                        {item?.DataType === 'float' && item !== undefined && <FloatText itemDef={item} measureDef={measureUnit} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} readonly={readonly} />}
                                                        {item?.DataType === 'multilinetext' && item !== undefined && <MultilineText itemDef={item} measureDef={measureUnit} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} readonly={readonly} />}
                                                        {item?.DataType === 'singleselect' && item !== undefined && <SingleSelect_v2 OID={OID} itemDef={item} conditionDef={condition} codeList={codeList}
                                                            onItemchange={OnItemDataChange} showStatus={showQuestion} itemDatas={itemData} error={itdt} index={index} isRepeatItemGrp={itemGroup?.Repeating} itgrpInd={tabIndex + 1} methodDefs={methodDefs} readonly={readonly} imageBaseUrl={imageBaseUrl} />}
                                                        {item?.DataType === 'multiselect' && item !== undefined && <MultiChoice_v2 OID={OID} itemDef={item} conditionDef={condition} codeList={codeList}
                                                            onItemchange={OnItemDataChange} showStatus={showQuestion} itemDatas={itemData} error={itdt} index={index} isRepeatItemGrp={itemGroup?.Repeating} itgrpInd={tabIndex + 1} methodDefs={methodDefs} readonly={readonly} imageBaseUrl={imageBaseUrl} />}
                                                        {item?.DataType === 'date' && item !== undefined && <DateSelector itemDef={item} conditionDef={condition} codeList={codeList} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} />}
                                                        {item?.DataType === 'datetime' && item !== undefined && <DateTime itemDef={item} conditionDef={condition} codeList={codeList} onItemchange={OnItemDataChange} itemDatas={itemData} error={itdt} />}
                                                        {item?.DataType === 'combobox' && item !== undefined && <Combo itemDef={item} conditionDef={condition} codeList={codeList} onItemchange={OnItemDataChange} showStatus={showQuestion} itemDatas={itemData} error={itdt} />}
                                                        {item?.DataType === 'boolean' && item !== undefined && <RadioType_v2 OID={OID} itemDef={item} conditionDef={condition} codeList={codeList} onItemchange={OnItemDataChange}
                                                            showStatus={showQuestion} itemDatas={itemData} error={itdt} index={index} isRepeatItemGrp={itemGroup?.Repeating} itgrpInd={tabIndex + 1} methodDefs={methodDefs} readonly={readonly} />}
                                                    </Box>
                                                ) : null;
                                            })}
                                        </Box>
                                    )}
                                </Box>
                            ))}
                        </Box>
                    </>
                )}
            </Card>
        );
    };
    const [showNextButton, setShowNextButton] = React.useState(true);
    const scrollableQuestionAreaRef = React.useRef<HTMLDivElement>(null);

    React.useEffect(() => {
        const scrollable = scrollableQuestionAreaRef.current;
        if (!scrollable) {
            setShowNextButton(true);
            return;
        }

        // Enable Next button only when scrolled to (or near) bottom
        const checkShowButton = () => {
            const threshold = 40; // px, larger for easier access
            const isScrollable = scrollable.scrollHeight > scrollable.clientHeight + 1;
            if (!isScrollable) {
                setShowNextButton(true);
                return;
            }
            const atBottom = scrollable.scrollHeight - scrollable.scrollTop - scrollable.clientHeight <= threshold;
            setShowNextButton(atBottom);
        };

        let debounceTimer: ReturnType<typeof setTimeout> | null = null;
        const handleScroll = () => {
            if (debounceTimer) clearTimeout(debounceTimer);
            debounceTimer = setTimeout(checkShowButton, 40);
        };

        scrollable.addEventListener('scroll', handleScroll, { passive: true });
        window.addEventListener('resize', checkShowButton);
        checkShowButton();

        return () => {
            scrollable.removeEventListener('scroll', handleScroll);
            window.removeEventListener('resize', checkShowButton);
            if (debounceTimer) clearTimeout(debounceTimer);
        };
    }, [currentQuestionData, questionsInGroup]);

    return (
        <Box ref={rootRef} sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
            {/* Question Pagination Mode - Always On */}
            {currentQuestionData ? (
                <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                    {(() => {
                        return null;
                    })()}
                    {itemGroupDefs.map((itemGroup, indexes) => {
                        let showItemGroup = true;

                        // Apply ItemGroup conditional logic (same as original)
                        let iGrpReferen = formDefs[0]?.ItemGroupRef?.find(x => x.ItemGroupOID === itemGroup.OID);


                        if (iGrpReferen !== null && iGrpReferen !== undefined && iGrpReferen.CollectionExceptionConditionOID !== null && iGrpReferen.CollectionExceptionConditionOID !== undefined) {
                            let itemGrpConDef = conditionDefs?.find((cd) => cd.OID === iGrpReferen?.CollectionExceptionConditionOID);

                            if (itemGrpConDef) {
                                let conditionText = itemGrpConDef.FormalExpression.text;

                                let cdnItemOID = conditionText.split("==")[0].replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                                let cdnValue = conditionText.split("==")[1].replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');


                                let itemGrpData = itemGroupDatas?.find((igd) => igd?.ItemData?.find((id) => id?.ItemOID === cdnItemOID));

                                if (itemGrpData !== null && itemGrpData !== undefined) {
                                    let iData = itemGrpData?.ItemData?.find((id) => id?.ItemOID === cdnItemOID);
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
                                    // This handles the case where itemGroupDatas is stale but parentAnswersData has the fresh values
                                    const parentData = parentAnswersData?.[cdnItemOID];
                                    const parentValue = parentData?.value;

                                    if (parentValue !== undefined) {
                                        // Use parentAnswersData for visibility determination
                                        showItemGroup = parentValue === cdnValue;
                                    } else {
                                        showItemGroup = false;
                                    }
                                }
                            } else {
                            }
                        } else {
                        }


                        if (!repeatCount[itemGroup.OID]) {
                            repeatCount[itemGroup.OID] = 1;
                        } else {
                            repeatCount[itemGroup.OID]++;
                        }

                        let itgrpDt: any;
                        if (itemGroup?.Repeating === 'Yes') {
                            itgrpDt = itemGroupDatas?.filter((igd) => igd.ItemGroupOID === itemGroup.OID && igd.ItemGroupRepeatKey === repeatCount[itemGroup.OID]);
                        } else {
                            itgrpDt = itemGroupDatas?.filter((igd) => igd.ItemGroupOID === itemGroup.OID);
                        }

                        const isRepeatingGroupWithMethod = !!(iGrpReferen?.MethodOID && itemGroup.Repeating === 'Yes');

                        if (showItemGroup && isRepeatingGroupWithMethod) {
                            return renderMethodDefItemGroup(itemGroup, iGrpReferen, itgrpDt, indexes);
                        } else {
                            if (iGrpReferen?.MethodOID && itemGroup.Repeating !== 'Yes') {
                            }
                        }

                        return showItemGroup ? (
                            <Card key={itemGroup.OID} className='POCard' variant="outlined" sx={{
                                display: 'flex',
                                flexDirection: 'column',
                                height: 'calc(100vh - 350px)', // LAYOUT FIX: Further reduced height for button visibility
                                maxHeight: 'calc(100vh - 370px)', // LAYOUT FIX: Constrain height
                                overflow: 'hidden' // LAYOUT FIX: Prevent card scrollbar
                            }}>
                                {/* Scrollable Question Content - LAYOUT FIX: Only this area scrolls */}
                                <Box sx={{
                                    flex: 1,
                                    overflowY: 'auto',
                                    overflowX: 'hidden',
                                    px: 1, // LAYOUT FIX: Add horizontal padding inside scrollable area
                                    pt: 1, // LAYOUT FIX: Add top padding
                                    minHeight: 0, // LAYOUT FIX: Important for flex child with overflow
                                    '&::-webkit-scrollbar': {
                                        width: '8px',
                                    },
                                    '&::-webkit-scrollbar-track': {
                                        background: '#f1f1f1',
                                        borderRadius: '4px',
                                    },
                                    '&::-webkit-scrollbar-thumb': {
                                        background: '#c1c1c1',
                                        borderRadius: '4px',
                                    },
                                    '&::-webkit-scrollbar-thumb:hover': {
                                        background: '#a8a8a8',
                                    },
                                }}
                                    ref={scrollableQuestionAreaRef}>
                                    {/* Sub-group section label — shown when first question of a sub-group is active */}
                                    {indexes === 0 && (currentQuestionData?.itemRef as any)?._subGroupLabel && (
                                        <Box sx={{ mb: 1.5, px: 1.5, py: 0.75, backgroundColor: '#eef2f7', borderLeft: '3px solid #1a365d', borderRadius: '0 4px 4px 0' }}>
                                            <Typography variant="caption" sx={{ fontWeight: 700, color: '#1a365d', textTransform: 'uppercase', letterSpacing: '0.6px' }}>
                                                {(currentQuestionData.itemRef as any)._subGroupLabel}
                                            </Typography>
                                        </Box>
                                    )}
                                    {/* Single Question Rendering with Conditionals */}
                                    {renderQuestionWithConditionals(itemGroup, currentQuestionData, itgrpDt, indexes)}

                                    {/* Render Instance Questions with Tabs - CONTEXTUAL INLINE RENDERING */}
                                    {repeatingChildren && getRepeatingInstanceCount && currentQuestionData && (() => {
                                        // Get current question OID
                                        const currentQuestionOID = currentQuestionData?.itemDef?.OID;

                                        const dependentConditionalQuestions = buildConditionalDependencyTree(currentQuestionOID, itemGroupDefs[0]);
                                        const contextualInstances = repeatingChildren.filter(repeatingChild => {
                                            const instanceCount = getRepeatingInstanceCount(repeatingChild.controlItemOID);
                                            if (instanceCount === 0 && !repeatingChild.isNested) return false; // Allow nested even if count is 0

                                            const isDirectlyControlled = repeatingChild.controlItemOID === currentQuestionOID;
                                            const isContextuallyRelated = dependentConditionalQuestions.has(repeatingChild.controlItemOID);
                                            const isNestedChild = repeatingChild.isNested === true;

                                            return isDirectlyControlled || isContextuallyRelated || isNestedChild;
                                        });

                                        // Get controlling question info for display
                                        const instancesWithContext = contextualInstances.map(repeatingChild => {
                                            const controllingQuestion = itemDefs.find(item => item.OID === repeatingChild.controlItemOID);
                                            return {
                                                ...repeatingChild,
                                                controllingQuestionText: controllingQuestion?.Question?.TranslatedText?.text || 'Unknown',
                                                instanceCount: getRepeatingInstanceCount(repeatingChild.controlItemOID),
                                                relationship: repeatingChild.controlItemOID === currentQuestionOID ? 'direct' : 'contextual'
                                            };
                                        });

                                        if (instancesWithContext.length === 0) return null;


                                        return (
                                            <Box sx={{ mt: 3 }}>
                                                {instancesWithContext.map((repeatingChild) => {
                                                    const instanceCount = getRepeatingInstanceCount(repeatingChild.controlItemOID);

                                                    if (instanceCount > 0) {
                                                        return (
                                                            <Box key={repeatingChild.itemGroupDef.OID} sx={{ mb: 2 }}>
                                                                {/* Message removed as requested */}
                                                                {renderRepeatingGroupTabsSeamless(repeatingChild, stepId || '')}
                                                            </Box>
                                                        );
                                                    }
                                                    return null;
                                                })}
                                            </Box>
                                        );
                                    })()}
                                </Box>

                                {handleBack && handleNext && (
                                    <Box sx={{
                                        flexShrink: 0,
                                        borderTop: '1px solid #e0e0e0',
                                        backgroundColor: '#fff',
                                        padding: '12px 24px',
                                        display: 'flex',
                                        justifyContent: 'space-between',
                                        alignItems: 'center',
                                        pointerEvents: 'auto',
                                    }}>
                                        <Button
                                            id='create-bid-v2-button-previous-question'
                                            onClick={handlePreviousQuestion}
                                            disabled={currentQuestionIndex === 0 && activeStep === 0}
                                            variant="outlined"
                                            sx={{
                                                textTransform: 'none',
                                                minWidth: '100px'
                                            }}
                                        >
                                            Back
                                        </Button>

                                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                                            <Typography variant="body2" color="text.secondary">
                                                Question {currentQuestionIndex + 1} of {questionsInGroup.length}
                                            </Typography>

                                            <Button
                                                id='create-bid-v2-button-next-question'
                                                onClick={handleNextQuestion}
                                                disabled={isSaving || !showNextButton} // Disable during save or before scrolling to bottom
                                                variant="contained"
                                                startIcon={isSaving ? <CircularProgress size={20} sx={{ color: 'white' }} /> : null}
                                                sx={{
                                                    backgroundColor: '#1a365d',
                                                    '&:hover': { backgroundColor: '#2d4a6b' },
                                                    textTransform: 'none',
                                                    minWidth: '100px',
                                                    '&:disabled': {
                                                        backgroundColor: '#e0e0e0',
                                                        color: '#999'
                                                    }
                                                }}
                                            >
                                                {isSaving ? (window.location.pathname === '/soldproduction/projectinformation' ? 'Saving...' : 'Saving Draft...') :
                                                    isLastQuestionOfStep && isLastStep ? "Complete" :
                                                        isLastQuestionOfStep ? "Next Step" : "Next Question"}
                                            </Button>
                                        </Box>
                                    </Box>
                                )}

                            </Card>
                        ) : null;
                    })}
                </Box>
            ) : (
                /* Loading State - Show when no question data is available */
                <Box sx={{
                    height: '100%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexDirection: 'column'
                }}>
                    <Typography variant="h6" color="text.secondary" sx={{ mb: 2 }}>
                        Loading Questions...
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                        Please wait while the questions are being prepared.
                    </Typography>
                </Box>
            )}


            {/* Summarize toggle confirmation dialog — single combined warning,
                suppresses the parent DeletionConfirmDialog for nested count changes */}
            <Dialog
                open={summarizeConfirmDialog?.open === true}
                onClose={() => setSummarizeConfirmDialog(null)}
                maxWidth="xs"
                fullWidth
            >
                <DialogContent sx={{ p: 3 }}>
                    <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
                        Clear existing data and summarize?
                    </Typography>
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                        Summarizing will replace the current facet information with a new data set.
                        All existing data will be permanently deleted and must be re‑entered if needed.
                        This action cannot be undone.
                    </Typography>
                    <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1 }}>
                        <Button id="question-renderer-card-v2-button-cancel"
                            variant="outlined"
                            size="small"
                            onClick={() => setSummarizeConfirmDialog(null)}
                            sx={{ textTransform: 'none' }}
                        >
                            Cancel
                        </Button>
                        <Button id="question-renderer-card-v2-button-ok"
                            variant="contained"
                            size="small"
                            color="error"
                            sx={{ textTransform: 'none' }}
                            onClick={() => {
                                if (!summarizeConfirmDialog) return;
                                const { groupOID, newValue, repeatingChild: rc } = summarizeConfirmDialog;
                                setSummarizeConfirmDialog(null);

                                setSummarizedGroups(prev => ({ ...prev, [groupOID]: newValue }));
                                setActiveRepeatingTabs(prev => ({ ...prev, [groupOID]: 0 }));
                                const groupItemOIDs = (rc.itemGroupDef.ItemRef || [])
                                    .map((ref: any) => ref.ItemOID as string);

                                if (onSummarizeGroupClear && rc.controlItemOID) {
                                    onSummarizeGroupClear(rc.controlItemOID, groupOID, groupItemOIDs);
                                }

                                const allOIDs = new Set([rc.controlItemOID, ...groupItemOIDs].filter(Boolean));
                                setItemData((prev: ItemData[]) =>
                                    prev.filter((item: ItemData) => !allOIDs.has(item.ItemOID))
                                );
                            }}
                        >
                            Ok
                        </Button>
                    </Box>
                </DialogContent>
            </Dialog>

            {/* Zoom Dialog for question images */}
            <ImageZoomDialog
                open={!!zoomedQuestionImage}
                onClose={() => setZoomedQuestionImage(null)}
                imageSrc={zoomedQuestionImage?.url || null}
                imageAlt={zoomedQuestionImage?.label || ''}
            />
        </Box>
    );
};

const QuestionRendererCard = React.memo(QuestionRendererCard_v2Component, (prevProps, nextProps) => {
    return (
        prevProps.OID === nextProps.OID &&
        prevProps.itemGroupDatas === nextProps.itemGroupDatas &&
        prevProps.currentQuestionIndex === nextProps.currentQuestionIndex &&
        prevProps.activeStep === nextProps.activeStep &&
        prevProps.parentAnswersData === nextProps.parentAnswersData &&
        prevProps.stepId === nextProps.stepId &&
        // Sidebar "jump to question": a new target (changed timestamp) must re-render so
        // the navigation effect runs — otherwise direct selection silently does nothing.
        prevProps.navigateToMainOID?.timestamp === nextProps.navigateToMainOID?.timestamp
    );
});

export default QuestionRendererCard;
