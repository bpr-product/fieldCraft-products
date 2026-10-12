/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2024
* @author      : 
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Requirements page with pagination stepper, multi-instance support, and universal parsers
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 18839  | 2026-04-30 | Karthika S | Add Summarize toggle confirmation dialog to prevent data loss
* 23795  | 2026-07-06 | Karthika S | Fix Multi Choice re-select after deselect being dropped — allow incoming items with an explicit CodeList selection past the empty-ref/template guard
* 30581  | 2026-09-11 | Mohamed Riyaz A | Bid Details is decided by isBidDetailsRoute(location.pathname) on both routes, so the wizard's draft autosave never runs there; the snapshot autosave drops the page-open status
*/

// ============================================================================
// REACT & CORE LIBRARIES
// ============================================================================
import React, {
  useState,
  useEffect,
  useCallback,
  useRef,
  forwardRef,
  useImperativeHandle,
  useContext,
  useMemo
} from 'react';

// ============================================================================
// MATERIAL-UI COMPONENTS
// ============================================================================
import {
  Box,
  Typography,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions
} from '@mui/material';
import WarningAmberRoundedIcon from '@mui/icons-material/WarningAmberRounded';

// ============================================================================
// EXTERNAL LIBRARIES & AUTH
// ============================================================================
import { useOidcAccessToken } from '@axa-fr/react-oidc';
import { TenantContext, useHandleUnauthorized } from '../../../hooks/UserSession';

// ============================================================================
// SERVICES & UTILITIES
// ============================================================================
import { getTemplatesByTradeName } from '../../../services/TradeTemplateService';
import { autoSaveBid } from '../../../services/BidService'; // 🆕 AUTOSAVE: Import createBid and autoSaveBid APIs
import {
  saveRequirementsState,
  loadRequirementsState,
  debounce,
  SaveGate,
  hasAnswerDataChanged,
  recordLastSavedAnswerData
} from '../../../utils/createBidStateManager';
import { convertItemGroupDataToODM, validateODMStructure, isDeletedFlag } from './utils/odmConversionUtils';
import { getActiveMetaDataVersionOID, resolveMergedTemplateVersion } from '../../../utils/sdmMergeUtils';

// ============================================================================
// TYPE DEFINITIONS & INTERFACES
// ============================================================================
import {
  CodeList,
  ItemDef,
  FormData,
  ItemGroupDef,
  SDM,
  BuildingData,
  ItemGroupData,
  FormDef,
  EventDef,
  TemplateVersion,
  BasicDefinitions,
  Mappings,
  MethodDef,
  ConditionDef
} from '../../../interface/SdmInterfaces';

import {
  RequirementsProps as RequirementsPropsType,
  StepData as StepDataType,
  RepeatingChildGroup as RepeatingChildGroupType,
  RepeatingGroupMapping as RepeatingGroupMappingType,
  BpeData
} from './types';
import { QAResponse } from '../../../services/BallParkEstimateService';
import { detectServicesFromAnswers, servicesInAnswerText } from './utils/detectServicesFromAnswers';

// ============================================================================
// REQUIREMENTS COMPONENTS
// ============================================================================

import { DeletionConfirmDialog } from './components/DeletionConfirmDialog';
import { RequirementsStepperHeader } from './components/RequirementsStepperHeader';
import { MainContentArea } from './components/MainContentArea';
import { SidebarSection } from './components/SidebarSection';


// ============================================================================
// UNIVERSAL PARSERS (Replace hardcoded config)
// ============================================================================
import {
  TemplateMetadataParser,
  QuestionMetadataParser,
  DependencyGraphParser,
  MultiInstanceParser
} from './parsers';

// ============================================================================
// UTILITY FUNCTIONS - Repeating Groups & Multi-Instance
// ============================================================================
import {
  buildRepeatingGroupMappings,
  getRepeatingInstanceCount as getRepeatingInstanceCountUtil,
  handleCountChange as handleCountChangeUtil,
  updateRepeatingInstanceCount as updateRepeatingInstanceCountUtil,
} from './utils/repeatingGroupUtils';

// ============================================================================
// UTILITY FUNCTIONS - Conditional Logic
// ============================================================================
import {
  evaluateConditionalQuestionVisibility as evaluateConditionalQuestionVisibilityUtil,
  convertAnswersDataToItemGroupData as convertAnswersDataToItemGroupDataUtil,
  computeReferenceBidSkipOIDs,
} from './utils/conditionalLogicUtils';

// ============================================================================
// UTILITY FUNCTIONS - Hierarchy & Requirements
// ============================================================================
import {
  findParentQuestion as findParentQuestionUtil,
  organizeQuestionsHierarchically as organizeQuestionsHierarchicallyUtil,
  HierarchyUtilsState
} from './utils/requirementsUtils';

// ============================================================================
// UTILITY FUNCTIONS - Dependent Answer Cleanup
// ============================================================================
import {
  clearDependentAnswers as clearDependentAnswersUtil,
  analyzeDependentQuestions as analyzeDependentQuestionsUtil,
  updateItemGroupDataAfterCleanup as updateItemGroupDataAfterCleanupUtil,
  DependentCleanupConfig
} from './utils/dependentAnswerCleanup';
import {
  isCollectionExcludedByFormalExpression,
  getItemOIDsHiddenAfterBranchChange
} from './utils/collectionExceptionUtils';
import { useLocation } from 'react-router-dom';
import { getRequest } from '../../../services/MeasurementService';
import { getAssetUidByAsset } from '../../../services/AssetService';
import { loadSelectTradeState, ensureCompletePayload } from '../../../utils/createBidStateManager';
import { calculateDerivedFields, buildWasteCalcPairs } from '../../../utils/MethodDefCalculator';
import { getCatalogueSasUrls } from '../../../services/CatalogueService';
import { CatalogueImageContext } from '../../../context/CatalogueImageContext';
import { FEATURE_FLAGS } from '../../../constants/FeatureFlag';
import { roofLensExportToMetaKeyValues, roofLensExportToPerFacetMetaKeyValues } from '../../asset/rooflens/measurementMetaKeys';
import { RoofLensExportData } from '../../asset/rooflens/exportRoofLens';
import { resolveMeasurementValue } from './measurementSource';
import { buildPerFacetAnswers, resolveCodedMeasurement, MAX_FACET_INSTANCES } from './perFacetAutofill';
import { isBidDetailsRoute, stripSnapshotStatus } from '../../../utils/bidDetailsRoute';

// ============================================================================
// CONFIGURATION & HELPERS
// ============================================================================


type RequirementsProps = RequirementsPropsType;
type StepData = StepDataType;
type RepeatingChildGroup = RepeatingChildGroupType;
type RepeatingGroupMapping = RepeatingGroupMappingType;



const Requirements = forwardRef<any, RequirementsProps>(({
  data,
  onDataChange,
  onSave,
  tradeType,
  odmData,
  odmVersion,
  assetVersion,
  odmProjectData,
  odmProjectVersion,
  tradeVersion,
  createBidFormRef,
  questionRendererRef,
  userdata,
  // 🆕 AUTOSAVE STATUS CALLBACKS: Update parent AutosaveStatus component
  onAutosaveStart,
  onAutosaveSuccess,
  onAutosaveError,
  summarizedGroupsRef,
  onLoaded,
  onBpeData,
}, ref) => {

  const AUTO_FILL_DEBOUNCE_MS = 50;

  // ============================================================================
  // NAVIGATION & STEPPER STATE
  // ============================================================================
  const [activeStep, setActiveStep] = useState(0);
  const [steps, setSteps] = useState<StepData[]>([]);
  const [completed, setCompleted] = useState<{ [k: number]: boolean }>({});
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState<{ [stepId: string]: number }>({});
  const [questionCompletionStatus, setQuestionCompletionStatus] = useState<{ [stepId: string]: { [questionIndex: number]: boolean } }>({});
  const [navigationTarget, setNavigationTarget] = useState<{ stepId: string; questionId: string; timestamp: number } | null>(null);
  // Sidebar "jump to question": the resolved MAIN-question OID for the active card to
  // page to (the card maps OID→index against its own list, avoiding index drift).
  const [navigateToMainOID, setNavigateToMainOID] = useState<{ stepId: string; oid: string; timestamp: number } | null>(null);

  // ============================================================================
  // TEMPLATE & API DATA STATE
  // ============================================================================
  const [loading, setLoading] = useState(true);
  const [templateData, setTemplateData] = useState<any>(null);
  const [template, setTemplate] = useState<TemplateVersion>();
  const [response, setResponse] = useState<SDM | any>();
  const [templateVersion, setTemplateVersion] = useState<string>(''); // Store API version

  // Template Metadata
  const [formDef, setFormDef] = useState<FormDef[]>([]);
  const [eventDef, setEventDef] = useState<EventDef[]>([]);
  const [itemGroup, setItemGroup] = useState<ItemGroupDef[]>([]);
  const [itemDef, setItemDef] = useState<ItemDef[]>([]);
  const [mappings, setMappings] = useState<Mappings[]>([]);
  const [codeList, setCodeList] = useState<CodeList[]>([]);
  const [methodDef, setMethodDef] = useState<MethodDef[]>([]);
  const [conditionDef, setConditionDef] = useState<ConditionDef[]>([]);
  const [measureUnit, setMeasureUnit] = useState<BasicDefinitions | any>();
  const [imageBaseUrl, setImageBaseUrl] = useState<string>('');
  const [catalogueSasMap, setCatalogueSasMap] = useState<Record<string, string>>({});


  // ============================================================================
  // ANSWER DATA & USER INPUT STATE
  // ============================================================================
  const [answersData, setAnswersData] = useState<{ [questionId: string]: any }>({});
  const [itemGroupData, setItemGroupData] = useState<ItemGroupData[]>([]);
  const [tradeData, setTradeData] = useState<any[]>([]);
  const [isAnswersDataStable, setIsAnswersDataStable] = useState<boolean>(false);
  const [assetOdmSnapshot, setAssetOdmSnapshot] = useState<string>('');

  // ============================================================================
  // MULTI-INSTANCE & REPEATING GROUPS STATE
  // ============================================================================
  const [repeatingGroupMappings, setRepeatingGroupMappings] = useState<RepeatingGroupMapping[]>([]);
  const [repeatingInstanceData, setRepeatingInstanceData] = useState<{ [groupOID_instanceIndex: string]: any }>({});
  const [activeRepeatingTabs, setActiveRepeatingTabs] = useState<{ [groupOID: string]: number }>({});
  const [repeatingInstanceCounts, setRepeatingInstanceCounts] = useState<{ [controlItemOID: string]: number }>({});

  // ============================================================================
  // CONDITIONAL QUESTIONS & SIDEBAR STATE
  // ============================================================================
  const [allQuestionsData, setAllQuestionsData] = useState<{ [stepId: string]: any[] }>({});
  const [conditionalQuestionsData, setConditionalQuestionsData] = useState<{ [parentQuestionId: string]: any[] }>({});
  const [conditionalQuestionVisibility, setConditionalQuestionVisibility] = useState<{ [conditionOID: string]: boolean }>({});
  const [methodDefTabsData, setMethodDefTabsData] = useState<{ [questionId: string]: { [tabIndex: number]: any } }>({});
  const [expandedAccordions, setExpandedAccordions] = useState<{ [stepId: string]: boolean }>({});

  // ============================================================================
  // VALIDATION STATE
  // ============================================================================
  const [validationErrors, setValidationErrors] = useState<{ [questionId: string]: string }>({});
  const [mandatoryQuestions, setMandatoryQuestions] = useState<{ [questionId: string]: boolean }>({});
  const [attemptedNext, setAttemptedNext] = useState(false);

  // ============================================================================
  // UI & ERROR STATE
  // ============================================================================
  const [error, setError] = useState(false);
  // Incrementing this key forces React to unmount and remount the component tree
  const [restoreKey, setRestoreKey] = useState(0);

  // ============================================================================
  // 🆕 AUTOSAVE STATE
  // ============================================================================
  const [savingDraft, setSavingDraft] = useState<boolean>(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [openValidationWarning, setOpenValidationWarning] = useState(false);
  const [validationWarningMessage, setValidationWarningMessage] = useState('');
  const [validationWarningTitle, setValidationWarningTitle] = useState('');

  // Track recently cleaned items to prevent them from being re-added by sync
  const recentlyCleanedItemsRef = useRef<Set<string>>(new Set());
  // Debounce timer for clearing the ref - resets on each new event
  const cleanupClearTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const location = useLocation();

  /** Stepper / sidebar / main content — excludes item groups when CollectionExceptionCondition evaluates true (SDM). */
  const visibleSteps = React.useMemo(() => {
    return steps.filter(step => {
      if (step.id === 'summary') return true;
      if (!step.collectionExceptionConditionOID) return true;
      const condDef = conditionDef?.find((cd: any) => cd.OID === step.collectionExceptionConditionOID);
      const expr = condDef?.FormalExpression?.text;
      return !isCollectionExcludedByFormalExpression(expr, answersData);
    });
  }, [steps, conditionDef, answersData]);

  React.useEffect(() => {
    setActiveStep(prev => {
      if (visibleSteps.length === 0) return 0;
      return prev >= visibleSteps.length ? visibleSteps.length - 1 : prev;
    });
  }, [visibleSteps]);

  React.useEffect(() => {
    // Only run migration once AND only if answersData is not empty
    if (hasMigratedAnswersDataRef.current || Object.keys(answersData).length === 0) {
      return;
    }

    const needsMigration = Object.values(answersData).some(
      (answer: any) => !answer.originalItemData && answer.itemOID
    );

    if (needsMigration && itemDef.length > 0 && itemGroup.length > 0) {

      hasMigratedAnswersDataRef.current = true; // Mark migration as done

      setAnswersData(prev => {
        const migrated: any = {};

        Object.entries(prev).forEach(([key, answer]: [string, any]) => {

          if (answer.originalItemData) {
            migrated[key] = answer;
            return;
          }

          // Parse key to get ItemOID and repeatKey
          const parts = key.split('_');
          const itemOID = answer.itemOID || parts[0];
          const repeatKey = answer.itemGroupRepeatKey || (parts.length > 1 ? parseInt(parts[parts.length - 1]) : 0);

          // Get itemGroupOID from itemGroup (find which group contains this ItemOID)
          let itemGroupOID = answer.itemGroupOID;
          if (!itemGroupOID && itemGroup.length > 0) {
            const groupDef = itemGroup.find((g: any) =>
              g.ItemRef?.some((ref: any) => ref.ItemOID === itemOID)
            );
            itemGroupOID = groupDef?.OID || null;
          }
          if (!answer.itemGroupOID && itemGroup.length > 0) {
            const groupDef = itemGroup.find((g: any) =>
              g.ItemRef && g.ItemRef.some((ref: any) => ref.ItemOID === itemOID)
            );
            if (groupDef) {
              itemGroupOID = groupDef.OID;
            }
          }

          // Get dataType from itemDef
          const itemDefEntry = itemDef.find((def: any) => def.OID === itemOID);
          const dataType = itemDefEntry?.DataType || answer.dataType || 'singleselect';

          // Determine ParentItemGroupRepeatKey from key pattern or template metadata
          let parentKey: number | undefined = undefined;
          if (parts.length === 3) {
            // Triple-level key: I.24_1_1 means parentKey=1
            parentKey = parseInt(parts[1]);
          } else if (parts.length === 2) {
            // Check if this group is nested using template metadata
            const mapping = repeatingGroupMappings.find(m => m.repeatingGroupOID === itemGroupOID);
            if (mapping?.isNested) {
              // For nested groups, default to parent instance 0 during migration
              // (actual parent will be determined from context during runtime)
              parentKey = 0;
            }
          }

          migrated[key] = {
            value: answer.value,
            itemGroupOID: itemGroupOID,
            repeatKey: repeatKey,
            timestamp: answer.timestamp || Date.now(),
            dataType: dataType,
            ...(Object.prototype.hasOwnProperty.call(answer, 'isDeleted')
              ? { isDeleted: isDeletedFlag(answer.isDeleted) }
              : {}),
            // PRESERVE MetaKey for deleted items so it's available during rebuild
            ...(answer.MetaKey ? { MetaKey: answer.MetaKey } : {}),
            originalItemData: {
              ItemOID: itemOID,
              Value: answer.value,
              ItemGroupRepeatKey: repeatKey,
              ...(parentKey !== undefined ? { ParentItemGroupRepeatKey: parentKey } : {})
            },
            // Preserve CodeList if present
            ...(answer.CodeList ? { CodeList: answer.CodeList } : {})
          };
        });

        return migrated;
      });
    }
  }, [answersData, itemDef, itemGroup]);

  const hasInitializedFromProps = useRef(false);

  useEffect(() => {
    // Only run once when component mounts with data.
    // Wait for itemDef to be loaded so DataType values are resolved correctly.
    if (hasInitializedFromProps.current || !data) return;
    if (itemDef.length === 0) return;

    // Check multiple possible data structures
    let requirementsData = null;
    let dataSource = '';

    // Option 1: data.answerData array (used by ShoppingCart_v2)
    if (data.answerData && Array.isArray(data.answerData)) {
      // Find requirements entry (tradeOid !== "A.1")
      const requirementsEntry = data.answerData.find((entry: any) => entry.tradeOid !== "A.1" && entry.answersData !== "" && data !== null);
      if (requirementsEntry?.answersData) {
        requirementsData = requirementsEntry.answersData;
        dataSource = 'data.answerData array';
      }
    }

    // Option 2: data.requirements or data.requirementAnswers
    if (!requirementsData) {
      requirementsData = data.requirements || data.requirementAnswers;
      dataSource = data.requirements ? 'data.requirements' : 'data.requirementAnswers';
    }


    if (requirementsData) {
      try {


        // Parse if string (data might be JSON stringified)
        const parsedData = typeof requirementsData === 'string'
          ? JSON.parse(requirementsData)
          : requirementsData;


        // Skip if empty array
        if (Array.isArray(parsedData) && parsedData.length === 0) {
          return;
        }

        // Check multiple possible SDM structures
        let itemGroups: any[] | null = null;

        // Try: ClinicalData.ItemGroupData (standard SDM)
        if (parsedData?.ClinicalData?.ItemGroupData) {
          itemGroups = parsedData.ClinicalData.ItemGroupData;
        }
        // Try: EventData[0].FormData[0].ItemGroupData (alternative SDM format)
        else if (parsedData?.EventData?.[0]?.FormData?.[0]?.ItemGroupData) {
          itemGroups = parsedData.EventData[0].FormData[0].ItemGroupData;
        }

        if (itemGroups && Array.isArray(itemGroups)) {
          // Convert ItemGroupData to answersData format
          const answers: { [key: string]: any } = {};

          // FIX BUG #3: Flatten nested ItemGroupData structure
          // This ensures ALL nested instances are processed, not just top-level
          const flattenItemGroups = (groups: any[], inheritedParentKey?: number): any[] => {
            const flattened: any[] = [];

            groups.forEach(group => {
              // Add current group (without nested ItemGroupData)
              const { ItemGroupData: nestedGroups, ...groupWithoutNested } = group;

              // Preserve ParentItemGroupRepeatKey from parent OR inherit from recursion
              const groupParentKey = group.ParentItemGroupRepeatKey ?? inheritedParentKey;
              if (groupParentKey !== undefined) {
                groupWithoutNested.ParentItemGroupRepeatKey = groupParentKey;
              }

              flattened.push(groupWithoutNested);

              // Recursively flatten nested groups, passing current group's RepeatKey as parent
              if (nestedGroups && Array.isArray(nestedGroups)) {
                const currentRepeatKey = group.ItemGroupRepeatKey ?? 0;
                const nestedFlattened = flattenItemGroups(nestedGroups, currentRepeatKey);
                flattened.push(...nestedFlattened);
              }
            });

            return flattened;
          };

          // Flatten the ItemGroupData structure
          const flatItemGroups = flattenItemGroups(itemGroups);

          // Process ALL flattened groups
          flatItemGroups.forEach((group: any) => {
            if (group.ItemData && Array.isArray(group.ItemData)) {
              group.ItemData.forEach((item: any) => {
                let key = item.ItemOID;
                const parentKey = item.ParentItemGroupRepeatKey ?? group.ParentItemGroupRepeatKey;
                let effectiveParentKey = parentKey;

                // Check if this group is nested using template metadata
                if (effectiveParentKey === undefined) {
                  const mapping = repeatingGroupMappings.find(m => m.repeatingGroupOID === group.ItemGroupOID);
                  if (mapping?.isNested && mapping?.parentRepeatingGroupOID) {
                    const parentGroup = flatItemGroups.find(g =>
                      g.ItemGroupOID === mapping.parentRepeatingGroupOID &&
                      g.ItemGroupRepeatKey === (item.ItemGroupRepeatKey || group.ItemGroupRepeatKey)
                    );
                    if (parentGroup) {
                      effectiveParentKey = parentGroup.ItemGroupRepeatKey;
                    }
                    // If no parent found, leave effectiveParentKey undefined to avoid orphan keys
                  }
                }

                // Build suffix key
                if (effectiveParentKey !== undefined && effectiveParentKey > 0) {
                  // Nested instance: I.38_1_2 (parent instance 1, nested instance 2)
                  key = `${item.ItemOID}_${effectiveParentKey}_${item.ItemGroupRepeatKey || 0}`;
                } else if (item.ItemGroupRepeatKey && item.ItemGroupRepeatKey > 0) {
                  // Regular instance: I.35_1
                  key = `${item.ItemOID}_${item.ItemGroupRepeatKey}`;
                }

                // Get DataType from itemDef for this item
                const itemDefEntry = itemDef.find((def: any) => def.OID === item.ItemOID);
                const dataType = itemDefEntry?.DataType || 'singleselect';

                answers[key] = {
                  value: item.Value,
                  itemGroupOID: group.ItemGroupOID,
                  repeatKey: item.ItemGroupRepeatKey || 0,
                  timestamp: Date.now(),
                  dataType: dataType,
                  ...(Object.prototype.hasOwnProperty.call(item, 'isDeleted')
                    ? { isDeleted: item.isDeleted === true || item.isDeleted === 'true' }
                    : {}),
                  // PRESERVE MetaKey for deleted items so it's available during rebuild
                  ...(item.MetaKey ? { MetaKey: item.MetaKey } : {}),
                  // Store original structure for compatibility
                  originalItemData: {
                    ItemOID: item.ItemOID,
                    Value: item.Value,
                    ItemGroupRepeatKey: item.ItemGroupRepeatKey || 0,
                    ...(effectiveParentKey !== undefined ? { ParentItemGroupRepeatKey: effectiveParentKey } : {})
                  },
                  // Preserve CodeList structure if present
                  ...(item.CodeList && item.CodeList.length > 0 ? { CodeList: item.CodeList } : {})
                };
              });
            }
          });

          setAnswersData(answers);

          // Also populate itemGroupData from the flattened structure
          setItemGroupData(flatItemGroups);

          hasInitializedFromProps.current = true;

        }
      } catch (error) {
        // console.error(' Error parsing requirements data from props:', error);
      }
    }
  }, [data, itemDef]); // itemDef added: re-run once template loads so DataType is resolved correctly
  const [errorCode, setErrorCode] = useState<any>(undefined);
  const [errorReason, setErrorReason] = useState<string>('');

  // ============================================================================
  // UNIVERSAL TEMPLATE PARSERS (Replace hardcoded config)
  // ============================================================================
  const templateMetadataParserRef = useRef<TemplateMetadataParser | null>(null);
  const questionMetadataParserRef = useRef<QuestionMetadataParser | null>(null);
  const dependencyGraphParserRef = useRef<DependencyGraphParser | null>(null);
  const multiInstanceParserRef = useRef<MultiInstanceParser | null>(null);

  // ============================================================================
  // REF FLAGS & GUARDS (For lifecycle control & race condition prevention)
  // ============================================================================
  const previousTradeTypeRef = useRef<string | undefined>(undefined);
  const hasRestoredRef = useRef(false);
  const hasMigratedAnswersDataRef = useRef(false); // Track if migration has been done
  const processedRepeatingGroupsRef = useRef<Set<string>>(new Set());
  const isApplyingSuffixSyncRef = useRef<boolean>(false);
  const isProcessingCleanupRef = useRef<boolean>(false);
  const isMergingItemGroupDataRef = useRef<boolean>(false); // NEW: Guard for merge operations
  const answersDataRef = useRef<any>(answersData);
  const assetCrossLinkAppliedRef = useRef(false);
  // Tracks the last-seen asset ODM source signature so the AutoFillRef cross-link
  // can be re-applied when the asset answers change mid-session (see the effect
  // defined after applyAutoFillForVisibleQuestions).
  const lastAutoFillSourceSigRef = useRef<string | null>(null);

  // ============================================================================
  // CHANGE TRACKING: Track last saved data to avoid redundant API calls
  // ============================================================================
  const lastSavedDataRef = useRef<any>(null);

  // ============================================================================
  // VALIDATION: Check mandatory fields before saving
  // ============================================================================
  const validateMandatoryFields = (payload: any) => {
    const errors: string[] = [];

    if (!payload.customerUid || payload.customerUid === '') {
      errors.push('Customer');
    }
    if (!payload.assetUid || payload.assetUid === '') {
      errors.push('Asset');
    }
    if (!payload.trade && !payload.tradeOid) {
      errors.push('Trade');
    }
    if (!payload.jobStartDate || payload.jobStartDate === null) {
      errors.push('Start Date');
    }

    return {
      isValid: errors.length === 0,
      missingFields: errors
    };
  };




  // ============================================================================
  // HOOKS & EXTERNAL INTEGRATIONS
  // ============================================================================
  const { accessToken, accessTokenPayload } = useOidcAccessToken(); // 🆕 AUTOSAVE: Added accessTokenPayload
  const { handleUnauthorized } = useHandleUnauthorized();
  const { tenantSessionDetails } = useContext(TenantContext);

  // ============================================================================
  // AUTO-FILL: Listen for applyBidAnswers events (from Recommendation Panel + EagleView)
  // ============================================================================
  useEffect(() => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { captureSnapshot } = require('../suggestionRevokeStore');

    const handleApplyBidAnswers = (event: Event) => {
      const customEvent = event as CustomEvent<Record<string, string> & { __bidId?: string; __isRevoke?: boolean; __sourceIsSynthetic?: boolean }>;
      const appliedAnswers = customEvent.detail;
      if (!appliedAnswers || typeof appliedAnswers !== 'object') return;

      const { __bidId, __isRevoke, __sourceIsSynthetic, ...answersOnly } = appliedAnswers as any;
      const bidIdForSnapshot = typeof __bidId === 'string' ? __bidId : '';
      if (bidIdForSnapshot && !__isRevoke) {
        const prevValues: Record<string, string> = {};
        for (const oid of Object.keys(answersOnly)) {
          const existing = (answersData as any)?.[oid];
          if (existing) {
            prevValues[oid] = String(existing.value ?? existing.Value ?? '');
          } else {
            prevValues[oid] = '';
          }
        }
        captureSnapshot(bidIdForSnapshot, prevValues);
      }

      if (__isRevoke) {
        skipNextCountCheckRef.current = true;
      }

      // Helper: rebuild the existing ODM CodeList by flipping IsEnabled
      // on whichever CodeListItem(s) match the incoming CodedValue(s).
      //
      // Incoming `value` formats from the rec backend (both PG real bids
      // AND synthetic Mongo bids store CodedValues, NOT user-visible
      // labels):
      //   single-select   →  "1"           — a single CodedValue
      //   multi-select    →  "1,2,3"       — comma-separated CodedValues
      //                      "1, 2, 3"     — whitespace tolerated
      //
      // Canonical ODM shape consumed by the renderer + countQuestions:
      //   { CodeList: [{ CodeListItem: [{ CodedValue, IsEnabled }, …] }],
      //     value: <coded value string> }
      //
      // Without this rebuild, writing `CodeList: ["1"]` (a string array)
      // means the renderer can't find a matching CodeListItem, the
      // chip stays blank, and countQuestions counts the row as
      // unanswered → "0 / 21" on Finalize Bid for selected real bids.
      const applyCodedValuesToCodeList = (existingCodeList: any, codedValueString: string) => {
        const selectedSet = new Set(
          String(codedValueString).split(',').map((s) => s.trim()).filter(Boolean),
        );
        if (!Array.isArray(existingCodeList) || existingCodeList.length === 0) {
          return existingCodeList;
        }
        return existingCodeList.map((cl: any) => {
          if (!cl || typeof cl !== 'object') return cl;
          const items = Array.isArray(cl.CodeListItem) ? cl.CodeListItem : [];
          return {
            ...cl,
            CodeListItem: items.map((item: any) => ({
              ...item,
              IsEnabled: selectedSet.has(String(item?.CodedValue ?? '')),
            })),
          };
        });
      };

      // Build a fresh answersData entry for an OID that exists in the loaded
      // template but isn't in answersData yet (the common case when the user
      // selects a recommendation before answering anything). Resolves the
      // question's DataType + ItemGroup + CodeList from the template so the
      // entry counts correctly, renders in the form, AND rebuilds on save via
      // convertToODMFormat. Returns null when the OID is NOT in the template
      // (genuine template drift) — caller then skips it.
      const buildEntryForTemplateOid = (oid: string, valueStr: string): any | null => {
        const itemDefEntry = itemDef.find((d: any) => d.OID === oid);
        if (!itemDefEntry) return null; // not in template → skip (drift)

        let itemGroupOID = '';
        for (const ig of itemGroup) {
          if (ig?.ItemRef?.some((ref: any) => ref.ItemOID === oid)) {
            itemGroupOID = ig.OID;
            break;
          }
        }

        const dataType = itemDefEntry.DataType || 'text';
        const entry: any = {
          value: valueStr,
          itemGroupOID,
          repeatKey: 0,
          timestamp: Date.now(),
          dataType,
          originalItemData: { ItemOID: oid, Value: valueStr, ItemGroupRepeatKey: 0 },
        };

        if (dataType === 'singleselect' || dataType === 'multiselect') {
          const codeListOID = itemDefEntry.CodeListRef?.CodeListOID;
          const codeListDef = codeListOID
            ? codeList.find((cl: any) => cl.OID === codeListOID)
            : null;
          if (codeListDef?.CodeListItem) {
            const selectedSet = new Set(
              valueStr.split(',').map((s) => s.trim()).filter(Boolean),
            );
            entry.CodeList = [{
              OID: codeListOID,
              Name: codeListOID,
              DataType: codeListDef.DataType,
              CodeListItem: codeListDef.CodeListItem.map((it: any) => ({
                ...it,
                IsEnabled: selectedSet.has(String(it.CodedValue ?? '')),
              })),
              ItemGroupRepeatKey: 0,
            }];
            entry.Value = '';
          } else {
            entry.Value = valueStr;
          }
        } else {
          entry.Value = valueStr;
        }
        return entry;
      };

      // ── Structure guard: selecting a reference bid must NOT introduce questions ──
      // A selected/suggested bid may only FILL answers for questions that are ALREADY
      // visible, OR that this SAME bid's own answers legitimately make visible (normal
      // Q&A branching, e.g. roof material → roof pitch). It must never reveal or expand
      // an optional add-on group that the bid's own data does NOT actually turn on
      // (e.g. "Roof Vents", "Pipes", "Skylights"). The bid can carry such OIDs — often
      // because it was authored under a different template version where the same OID
      // was a flat question (OID drift).
      //
      // computeReferenceBidSkipOIDs itself is unchanged — it always evaluates visibility
      // against whatever answersData it's handed. The fix lives HERE, in what we hand it:
      // instead of the raw pre-apply answersData (which can never reflect this bid's own
      // driver answers, so any question gated by one always looked "hidden" and got
      // skipped — including its driver, out of caution), we simulate "what the answers
      // would look like if this bid's own answers were already applied" and evaluate
      // against THAT. A question the bid's own data genuinely makes visible loads
      // correctly; an add-on group the bid's own data does not turn on stays correctly
      // hidden — because the simulation reflects the real bid, not a guess.
      const simulatedAnswersData: Record<string, any> = { ...(answersDataRef.current || {}) };
      if (!__isRevoke) {
        for (const [itemOid, value] of Object.entries(answersOnly)) {
          const isEmptyPreview = !value && value !== '0';
          if (isEmptyPreview) continue;
          const existingPreview = simulatedAnswersData[itemOid];
          if (existingPreview && typeof existingPreview === 'object') {
            const isSelectTypePreview = !!(
              existingPreview.dataType === 'singleselect'
              || existingPreview.dataType === 'multiselect'
              || (Array.isArray(existingPreview.CodeList)
                  && existingPreview.CodeList.length > 0
                  && typeof existingPreview.CodeList[0] === 'object'
                  && existingPreview.CodeList[0] !== null
                  && Array.isArray(existingPreview.CodeList[0].CodeListItem))
            );
            simulatedAnswersData[itemOid] = isSelectTypePreview
              ? { ...existingPreview, CodeList: applyCodedValuesToCodeList(existingPreview.CodeList, String(value)), value: String(value) }
              : { ...existingPreview, Value: String(value), value: String(value) };
          } else {
            const builtPreview = buildEntryForTemplateOid(itemOid, String(value));
            if (builtPreview) simulatedAnswersData[itemOid] = builtPreview;
          }
        }
      }
      // Read CURRENT template from refs (the handler's closure can be stale).
      // computeReferenceBidSkipOIDs returns the OIDs a selected bid must NOT load: any
      // conditional question that stays hidden even after simulating this bid's own
      // answers + the parent/driver OID(s) of any group that still stays hidden.
      // (Pure + unit-tested in conditionalLogicUtils — unchanged by this fix.)
      // AB#30521 frontend parity fix: the property's own real per-facet MEASUREMENT group
      // control OID(s), resolved by MetaKey from the current trade template — same two aliases
      // the Java FACET_COUNT_METAKEY_ALIASES uses. Exempted from the sourceIsSynthetic
      // force-skip below (see computeReferenceBidSkipOIDs' own param doc for why).
      const facetCountControlOids = new Set<string>(
        (itemDef || [])
          .filter((d: any) => {
            const mk = String(d?.MetaKey || '').toLowerCase();
            return mk === 'facetcount' || mk === 'totalrooffacets';
          })
          .map((d: any) => d.OID)
          .filter(Boolean),
      );
      const skipOIDs = computeReferenceBidSkipOIDs(
        itemGroupRef.current || [],
        conditionDefRef.current || [],
        itemGroupDataRef.current || [],
        __isRevoke ? (answersDataRef.current || {}) : simulatedAnswersData,
        formDefRef.current || [],
        methodDefRef.current || [],
        !__isRevoke, // simulatedAnswersData is a post-apply snapshot — see param doc
        !__isRevoke && __sourceIsSynthetic === true, // never force-skip on a revoke pass
        facetCountControlOids,
      ) || new Set<string>();

      setAnswersData(prev => {
        const updated = { ...prev };
        for (const [itemOid, value] of Object.entries(answersOnly)) {
          // Structure guard (see above): never load a currently-hidden conditional
          // question, nor the parent/driver whose value would reveal one. Applies to
          // both apply AND revoke (we never set these, so there is nothing to clear).
          if (skipOIDs.has(itemOid)) {
            continue;
          }
          // Asset-derived questions (their value flows in from the Asset
          // Information section via AutoFillRef cross-link) must NEVER be
          // touched by a recommended/reference bid — neither apply NOR revoke.
          // Detected structurally so it holds across template versions/trades:
          //   - the template ItemDef carries an AutoFillRef (asset source), or
          //   - the live entry was already flagged _crossLinkedFromAsset.
          const defForOid: any = Array.isArray(itemDef)
            ? itemDef.find((d: any) => d?.OID === itemOid)
            : null;
          const isAssetDerived =
            updated[itemOid]?._crossLinkedFromAsset === true
            || !!(defForOid?.AutoFillRef?.TradeOID && defForOid?.AutoFillRef?.ItemOID);
          if (isAssetDerived) continue;

          const isEmpty = !value && value !== '0';
          if (isEmpty && !__isRevoke) continue;

          const existing = updated[itemOid];
          // Select-type detection: dataType marker first, CodeList shape as
          // fallback (entries created by some code paths lack dataType, and
          // missing the marker must not stop the CodeList from rebuilding).
          const isSelectType = !!existing && (
            existing.dataType === 'singleselect'
            || existing.dataType === 'multiselect'
            || (Array.isArray(existing.CodeList)
                && existing.CodeList.length > 0
                && typeof existing.CodeList[0] === 'object'
                && existing.CodeList[0] !== null
                && Array.isArray(existing.CodeList[0].CodeListItem))
          );
          if (isEmpty && __isRevoke) {
            if (existing) {
              if (isSelectType) {
                // Revoke clears all IsEnabled flags while keeping the
                // CodeList structure intact (so the chips re-render
                // empty rather than disappearing entirely).
                const clearedCodeList = applyCodedValuesToCodeList(existing.CodeList, '');
                updated[itemOid] = { ...existing, CodeList: clearedCodeList, value: '' };
              } else {
                updated[itemOid] = { ...existing, Value: '', value: '' };
              }
            } else {
              delete updated[itemOid];
            }
            continue;
          }

          if (existing) {
            if (isSelectType) {
              // Rebuild the ODM CodeList so the matching CodeListItem(s)
              // have IsEnabled=true. Works for both:
              //   - "1"      → single value selected
              //   - "1,2,3"  → multi-select (each split on comma)
              const newCodeList = applyCodedValuesToCodeList(existing.CodeList, String(value));
              updated[itemOid] = {
                ...existing,
                CodeList: newCodeList,
                value: String(value),
              };
            } else {
              updated[itemOid] = { ...existing, Value: String(value), value: String(value) };
            }
          } else {
            // OID not in answersData yet. Build a proper entry from the
            // template (resolves DataType + CodeList). Returns null only when
            // the OID isn't in the template at all (genuine drift) — skip then.
            const built = buildEntryForTemplateOid(itemOid, String(value));
            if (built) {
              updated[itemOid] = built;
            }
          }
        }
        return updated;
      });

      // Mirror the same answers into itemGroupData — the ODM structure
      // renderSingleQuestion checks FIRST (see Bug A). Without this, a field whose
      // ODM entry is already non-empty from a previous apply keeps showing that
      // stale value forever, no matter what answersData/parentAnswersData say.
      setItemGroupData(prev => {
        const updated = prev.map(g => ({ ...g, ItemData: [...(g.ItemData || [])] }));
        for (const [itemOid, value] of Object.entries(answersOnly)) {
          if (skipOIDs.has(itemOid)) continue;
          const defForOid: any = Array.isArray(itemDef)
            ? itemDef.find((d: any) => d?.OID === itemOid)
            : null;
          const isAssetDerived = !!(defForOid?.AutoFillRef?.TradeOID && defForOid?.AutoFillRef?.ItemOID);
          if (isAssetDerived) continue;

          let groupOID = '';
          for (const ig of itemGroup) {
            if (ig?.ItemRef?.some((ref: any) => ref.ItemOID === itemOid)) { groupOID = ig.OID; break; }
          }
          if (!groupOID) continue;

          const isEmpty = !value && value !== '0';
          const newValue = isEmpty && __isRevoke ? '' : String(value);

          let gIdx = updated.findIndex((g: any) => g.ItemGroupOID === groupOID && g.ItemGroupRepeatKey === 0);
          if (gIdx === -1) {
            updated.push({ ItemGroupOID: groupOID, ItemGroupRepeatKey: 0, ItemData: [] } as any);
            gIdx = updated.length - 1;
          }
          const group: any = updated[gIdx];
          const iIdx = group.ItemData.findIndex((i: any) => i.ItemOID === itemOid);
          const existingCodeList = iIdx >= 0 ? group.ItemData[iIdx].CodeList : undefined;
          // Select-type questions render from CodeList.IsEnabled, not raw Value.
          // A field with NO prior itemGroupData entry (never answered before) has
          // no existingCodeList to rebuild from — without building one fresh here
          // (mirroring buildEntryForTemplateOid's own CodeListRef resolution), this
          // entry would carry Value only, itemGroupDatas would still "find" it
          // (blocking the correctly-built answersData/buildEntryForTemplateOid
          // fallback from ever being consulted per Bug A's priority order), and the
          // question would render as unanswered despite answersData being correct.
          let codeListForItem = existingCodeList
            ? applyCodedValuesToCodeList(existingCodeList, newValue)
            : undefined;
          if (!codeListForItem) {
            const codeListOID = defForOid?.CodeListRef?.CodeListOID;
            const codeListDef = codeListOID
              ? codeList.find((cl: any) => cl.OID === codeListOID)
              : null;
            if (codeListDef?.CodeListItem) {
              const selectedSet = new Set(
                newValue.split(',').map((s: string) => s.trim()).filter(Boolean),
              );
              codeListForItem = [{
                OID: codeListOID,
                Name: codeListOID,
                DataType: codeListDef.DataType,
                CodeListItem: codeListDef.CodeListItem.map((it: any) => ({
                  ...it,
                  IsEnabled: selectedSet.has(String(it.CodedValue ?? '')),
                })),
                ItemGroupRepeatKey: 0,
              }];
            }
          }
          const newItem: any = {
            ItemOID: itemOid,
            Value: newValue,
            ItemGroupRepeatKey: 0,
            ...(codeListForItem ? { CodeList: codeListForItem } : {}),
          };
          if (iIdx >= 0) group.ItemData[iIdx] = newItem; else group.ItemData.push(newItem);
        }
        return updated;
      });

      // Remount the question content on EVERY apply (not just revoke) so the
      // renderer re-initializes its inputs from the freshly-applied answersData
      // / itemGroupData above. QuestionRendererCard_v2 keeps its own local
      // itemData/answersData state (including a one-time sync ref) that does
      // NOT re-sync from new props after its first population — so without a
      // remount here, a second Suggest/Select on a DIFFERENT project would
      // keep showing the FIRST project's stale values (or, on revoke, the
      // field would still SHOW the suggested value while the count goes grey).
      // Batched with the setAnswersData/setItemGroupData above, so the remount
      // reads the just-applied (or just-cleared) data, not stale state.
      setRestoreKey((k) => k + 1);

      // onDataChange is notified by the dedicated effect (lines ~2201-2228)
      // after the state update settles — no stale closure call needed here.
    };

    window.addEventListener('applyBidAnswers', handleApplyBidAnswers);
    return () => window.removeEventListener('applyBidAnswers', handleApplyBidAnswers);
  }, [answersData, onDataChange, itemDef, itemGroup, codeList]);

  // ==================== CLEANUP LOGIC - REFACTORED ====================

  const checkForCleanupTrigger = (data: any): string[] => {
    // Parser must be initialized before cleanup can work
    if (!dependencyGraphParserRef.current) {
      return [];
    }

    const targetsToClean: string[] = [];

    const shouldSkipCleanupForMultiselect = (itemOID: string, incomingData: any): boolean => {
      const currentAnswer = answersData[itemOID];
      const isMultiselect = currentAnswer?.dataType === 'multiselect';

      if (!isMultiselect || !currentAnswer?.CodeList) {
        return false; // Not multiselect, don't skip
      }

      // Get new CodeList from incoming data
      let newCodeList = null;
      if (incomingData.ItemGroup && Array.isArray(incomingData.ItemGroup)) {
        for (const group of incomingData.ItemGroup) {
          const itemData = group.ItemData?.find((item: any) => item.ItemOID === itemOID);
          if (itemData?.CodeList !== null && itemData?.CodeList !== undefined) {
            newCodeList = itemData.CodeList;
            break;
          }
        }
      } else if (incomingData.CodeList !== null && incomingData.CodeList !== undefined && incomingData.ItemOID === itemOID) {
        newCodeList = incomingData.CodeList;
      }

      if (!newCodeList) {
        return false; // No new CodeList, don't skip
      }

      // Compare selected items
      const oldSelected = (currentAnswer.CodeList !== null && currentAnswer.CodeList !== undefined)
        ? (currentAnswer.CodeList as any)[0]?.CodeListItem?.filter((item: any) => item !== null && item !== undefined && item.IsEnabled).map((item: any) => item.CodedValue).sort() || []
        : [];
      const newSelected = (newCodeList !== null && newCodeList !== undefined)
        ? (newCodeList as any)[0]?.CodeListItem?.filter((item: any) => item !== null && item !== undefined && item.IsEnabled).map((item: any) => item.CodedValue).sort() || []
        : [];

      const noChange = JSON.stringify(oldSelected) === JSON.stringify(newSelected);
      return noChange;
    };

    // Check ItemGroup array format
    if (data.ItemGroup && Array.isArray(data.ItemGroup)) {
      for (const group of data.ItemGroup) {
        if (group.ItemData) {
          for (const item of group.ItemData) {
            // Determine the proper answer key (respect instance suffix)
            const answerKey = (item.ItemGroupRepeatKey && item.ItemGroupRepeatKey > 0)
              ? `${item.ItemOID}_${item.ItemGroupRepeatKey}`
              : item.ItemOID;
            const existingAnswer = answersData[answerKey] || answersData[item.ItemOID];
            const incomingValue = item.Value;

            if (existingAnswer === undefined && incomingValue !== undefined && incomingValue !== null && incomingValue !== '') {
              continue;
            }
            if (shouldSkipCleanupForMultiselect(item.ItemOID, data)) {
              continue;
            }

            const hasCodeList = item.CodeList !== null && item.CodeList !== undefined && item.CodeList.length > 0;
            if (hasCodeList && existingAnswer?.CodeList) {
              const existingEnabled = existingAnswer.CodeList?.[0]?.CodeListItem
                ?.filter((c: any) => c.IsEnabled).map((c: any) => c.CodedValue).sort() || [];
              const incomingEnabled = item.CodeList?.[0]?.CodeListItem
                ?.filter((c: any) => c.IsEnabled).map((c: any) => c.CodedValue).sort() || [];

              if (JSON.stringify(existingEnabled) === JSON.stringify(incomingEnabled)) {
                continue;
              }
            } else {
              // If existing answer exists and value is unchanged, skip cleanup
              if (existingAnswer !== undefined) {
                const existingVal = existingAnswer?.value ?? existingAnswer?.Value;
                // For multiselect we already handled via shouldSkipCleanupForMultiselect
                if (existingVal !== undefined && existingVal === incomingValue) {
                  continue;
                }
              }
            }

            // FIX: Build updated answersData with the NEW CodeList so condition evaluation
            // uses the updated state (after the option was unchecked)
            const updatedAnswersForCleanup = {
              ...answersData,
              [item.ItemOID]: {
                ...existingAnswer,
                CodeList: item.CodeList, // Use the new CodeList with updated IsEnabled flags
                value: item.Value
              }
            };
            const targets = dependencyGraphParserRef.current.getCleanupTargets(
              item.ItemOID,
              item.Value,
              updatedAnswersForCleanup
            );
            targetsToClean.push(...targets);
          }
        }
      }
    }
    // Check direct item change format
    if (data.ItemOID) {
      const answerKey = (data.ItemGroupRepeatKey && data.ItemGroupRepeatKey > 0)
        ? `${data.ItemOID}_${data.ItemGroupRepeatKey}`
        : data.ItemOID;

      const existingAnswer = answersData[answerKey] || answersData[data.ItemOID];
      const incomingValue = data.Value;

      // If this is a first-time entry (no existing answer) and we have a value, skip cleanup
      if (existingAnswer === undefined && incomingValue !== undefined && incomingValue !== null && incomingValue !== '') {
        // first-time entry - no cleanup needed
        return Array.from(new Set(targetsToClean));
      }

      if (!shouldSkipCleanupForMultiselect(data.ItemOID, data)) {
        // If existing answer exists and value is unchanged, skip cleanup
        if (existingAnswer !== undefined) {
          const existingVal = existingAnswer?.value ?? existingAnswer?.Value;
          if (existingVal !== undefined && existingVal === incomingValue) {
            return Array.from(new Set(targetsToClean));
          }
        }
        // FIX: Build updated answersData with the NEW CodeList so condition evaluation
        // uses the updated state (after the option was unchecked)
        const updatedAnswersForCleanup = {
          ...answersData,
          [data.ItemOID]: {
            ...existingAnswer,
            CodeList: data.CodeList, // Use the new CodeList with updated IsEnabled flags
            value: data.Value
          }
        };
        // USE PARSER: Get cleanup targets from dependency graph
        const targets = dependencyGraphParserRef.current.getCleanupTargets(
          data.ItemOID,
          data.Value,
          updatedAnswersForCleanup
        );
        targetsToClean.push(...targets);
      }
    }

    const result = Array.from(new Set(targetsToClean));
    return result; // Remove duplicates
  };

  const getRepeatingInstanceCount = (controlItemOID: string, parentSuffix?: string): number => {
    return getRepeatingInstanceCountUtil(controlItemOID, answersData, parentSuffix);
  };

  // Helper function to handle count changes with confirmation for deletions
  const handleCountChange = (controlItemOID: string, newCount: number, oldCount: number, repeatingGroupOID: string) => {
    const state = {
      repeatingInstanceCounts,
      repeatingInstanceData,
      itemGroupData,
      itemGroup,
      itemDef,
      answersData,
      previousAnswersData: secondPreviousAnswersDataRef.current
    };

    const setters = {
      setRepeatingInstanceCounts,
      setRepeatingInstanceData,
      setItemGroupData,
      setAnswersData,
      setDeletionConfirmation
    };

    if (suppressDeletionConfirmRef.current && newCount < oldCount) {
      updateRepeatingInstanceCountUtil(controlItemOID, newCount, repeatingGroupOID, setters, state);
      return;
    }

    handleCountChangeUtil(
      controlItemOID,
      newCount,
      oldCount,
      repeatingGroupOID,
      setters,
      state
    );
  };

  // Helper function to update repeating instance count and manage instance data
  // (wrapper for utility function)
  const updateRepeatingInstanceCount = (controlItemOID: string, newCount: number, repeatingGroupOID: string) => {
    const oldCount = repeatingInstanceCounts[controlItemOID] || 0;

    // When deleting instances, add all affected answer keys to recentlyCleanedItemsRef
    // This prevents the sync effect from re-adding them
    if (newCount < oldCount) {
      // Find all answer keys that belong to instances being deleted
      const instancesToDelete = Array.from({ length: oldCount - newCount }, (_, i) => oldCount - i);

      Object.keys(answersData).forEach(questionId => {
        const answer = answersData[questionId];

        // Check if this is a direct child of the repeating group being deleted
        if (answer.itemGroupOID === repeatingGroupOID && answer.repeatKey > newCount) {
          recentlyCleanedItemsRef.current.add(questionId);
        }

        // Check if this is a nested child whose parent is being deleted
        if (answer.originalItemData?.ParentItemGroupRepeatKey !== undefined) {
          const parentRepeatKey = answer.originalItemData.ParentItemGroupRepeatKey;
          if (instancesToDelete.includes(parentRepeatKey)) {
            recentlyCleanedItemsRef.current.add(questionId);
          }
        }
      });

      // Clear the ref after debounce to allow re-adding if user creates new instances
      if (cleanupClearTimeoutRef.current) {
        clearTimeout(cleanupClearTimeoutRef.current);
      }
      cleanupClearTimeoutRef.current = setTimeout(() => {
        recentlyCleanedItemsRef.current.clear();
      }, 2000); // 2 seconds to allow all effects to settle
    }

    const state = {
      repeatingInstanceCounts,
      repeatingInstanceData,
      itemGroupData,
      itemGroup,
      itemDef,
      answersData
    };

    const setters = {
      setRepeatingInstanceCounts,
      setRepeatingInstanceData,
      setItemGroupData,
      setAnswersData,
      setDeletionConfirmation
    };

    updateRepeatingInstanceCountUtil(
      controlItemOID,
      newCount,
      repeatingGroupOID,
      setters,
      state
    );
  };

  const onSummarizeGroupClear = useCallback((
    controlItemOID: string,
    repeatingGroupOID: string,
    groupItemOIDs: string[]
  ) => {
    const allOIDs = new Set([controlItemOID, ...groupItemOIDs]);

    setAnswersData((prev: any) => {
      const next = { ...prev };
      let changed = false;
      Object.keys(next).forEach(key => {
        const base = key.split('_')[0];
        if (allOIDs.has(key) || allOIDs.has(base)) {
          delete next[key];
          recentlyCleanedItemsRef.current.add(key);
          changed = true;
        }
      });
      return changed ? next : prev;
    });
    setRepeatingInstanceCounts((prev: any) => {
      if (prev[controlItemOID] === undefined || prev[controlItemOID] === 0) return prev;
      return { ...prev, [controlItemOID]: 0 };
    });

    setItemGroupData((prev: ItemGroupData[]) =>
      prev.filter((g: any) => g.ItemGroupOID !== repeatingGroupOID)
    );

    if (cleanupClearTimeoutRef.current) clearTimeout(cleanupClearTimeoutRef.current);
    cleanupClearTimeoutRef.current = setTimeout(() => {
      recentlyCleanedItemsRef.current.clear();
    }, 3000);

    const customerUid = data?.customerUid;
    const assetUid = data?.assetUid;
    const tradeOid = tradeType;
    if (customerUid && assetUid && tradeOid) {
      setTimeout(() => {
        const stateToFlush = {
          answersData: answersDataRef.current,
          activeStep,
          completed,
          itemGroupData: [],
          repeatingInstanceData,
          currentQuestionIndex,
          tradeType
        };
        saveRequirementsState(stateToFlush, customerUid, assetUid, tradeOid, data?.jobStartDate);
      }, 0);
    }
  }, [activeStep, completed, currentQuestionIndex, data, repeatingInstanceData, tradeType]);

  // State for deletion confirmation dialog
  const [deletionConfirmation, setDeletionConfirmation] = useState<{
    show: boolean;
    controlItemOID?: string;
    newCount?: number;
    oldCount?: number;
    repeatingGroupOID?: string;
    instancesToDelete?: number[];
    // For two-phase deletion - store pending deletion info
    keysToDelete?: string[];
    originalValue?: string;
    originalAnswersData?: { [key: string]: any };
  }>({ show: false });

  const skipNextCountCheckRef = useRef(false);

  const suppressDeletionConfirmRef = useRef(false);

  const previousAnswersDataRef = useRef<{ [key: string]: any }>({});
  const secondPreviousAnswersDataRef = useRef<{ [key: string]: any }>({});
  useEffect(() => {
    if (!deletionConfirmation.show && Object.keys(answersData).length > 0) {
      secondPreviousAnswersDataRef.current = { ...previousAnswersDataRef.current };
      previousAnswersDataRef.current = { ...answersData };
    }
  });

  const sourceOdmCacheRef = useRef<{ [tradeOID: string]: any[] }>({});

  const getSourceItemGroups = useCallback((tradeOID: string): any[] => {
    if (sourceOdmCacheRef.current[tradeOID]) return sourceOdmCacheRef.current[tradeOID];

    let odmString: string | null = null;
    if (data?.answerData && Array.isArray(data.answerData)) {
      const entry = data.answerData.find((e: any) => e.tradeOid === tradeOID);
      if (entry?.answersData) {
        odmString = typeof entry.answersData === 'string'
          ? entry.answersData
          : JSON.stringify(entry.answersData);
      }
    }
    if (!odmString && tradeOID === 'A.1' && odmData?.current) {
      odmString = odmData.current;
    }

    if (!odmString) {
      sourceOdmCacheRef.current[tradeOID] = [];
      return [];
    }

    try {
      const parsed = JSON.parse(odmString);
      const groups: any[] =
        parsed?.EventData?.[0]?.FormData?.[0]?.ItemGroupData ||
        parsed?.ClinicalData?.ItemGroupData ||
        [];
      sourceOdmCacheRef.current[tradeOID] = groups;
      return groups;
    } catch {
      sourceOdmCacheRef.current[tradeOID] = [];
      return [];
    }
  }, [data, odmData]);

  useEffect(() => {
    sourceOdmCacheRef.current = {};
  }, [data, odmData]);

  useEffect(() => {

    const fetchTemplateData = async () => {
      if (!tradeType) {
        setLoading(false);
        onLoaded?.();
        return;
      }

      try {
        setLoading(true);
        const hasTemplateChanged = previousTradeTypeRef.current !== undefined && previousTradeTypeRef.current !== tradeType;

        // Read measurementType from session storage since data prop may not have it yet
        const savedTradeState = loadSelectTradeState(data?.customerUid, data?.assetUid, null);
        const measurementTypeFromSession = savedTradeState?.measurementType;
        const shouldSkipEagleView = measurementTypeFromSession === 'no' || data?.measurementType === 'no' || data?.measurementData === 'no';



        let fetchedTradeData: any = null;
        // RoofLens MetaKey -> value map (flag-gated). Empty unless the flag is on
        // and the asset carries a parseable RoofLens measurement export.
        let roofLensMeta: Record<string, string | number> = {};
        // RoofLens PER-FACET MetaKey maps (flag-gated). One entry per facet, keyed by
        // the real measurement vocabulary (area/Pitch/LengthRidge/…). Drives the
        // GENERIC per-facet repeating-group autofill below — matched to the template
        // by ItemDef.MetaKey, never by OID, so it works for ANY trade whose template
        // tags per-facet questions with a measurement MetaKey.
        let roofLensPerFacet: Array<Record<string, string | number>> = [];
        if (!shouldSkipEagleView) {
          const assetresponse = await getAssetUidByAsset(data?.assetUid, accessToken);
          const res = await getRequest(assetresponse?.data.id, accessToken);
          fetchedTradeData = res?.data;
          setTradeData(fetchedTradeData || {});

          // Flatten the asset's saved RoofLens measurement (if any) into MetaKey
          // values so the pre-population loop can prefer it over EagleView. Only
          // when the RoofLens measurement source is the active source.
          if (FEATURE_FLAGS.rooflens_measurement_source_enable) {
            const measurementData = assetresponse?.data?.measurementData;
            if (typeof measurementData === 'string' && measurementData.trim() !== '') {
              try {
                const parsedRoofLens = JSON.parse(measurementData) as RoofLensExportData;
                roofLensMeta = roofLensExportToMetaKeyValues(parsedRoofLens);
                roofLensPerFacet = roofLensExportToPerFacetMetaKeyValues(parsedRoofLens);
              } catch (e) {
                console.warn('Failed to parse RoofLens measurementData; falling back to EagleView', e);
              }
            }
          }

        } else {
          // User selected "no" - clear any existing tradeData
          setTradeData([]);

        }


        if (hasTemplateChanged) {
          // Clear all answer data
          setAnswersData({});

          // Clear ItemGroup data
          setItemGroupData([]);

          // Clear repeating instance data
          setRepeatingInstanceData({});
          setRepeatingInstanceCounts({});
          setActiveRepeatingTabs({});

          // Clear question navigation state
          setCurrentQuestionIndex({});
          setQuestionCompletionStatus({});

          // Clear conditional question state
          setConditionalQuestionVisibility({});
          setConditionalQuestionsData({});

          // Clear validation state
          setValidationErrors({});
          setAttemptedNext(false);

          // Reset active step to 0
          setActiveStep(0);
          setCompleted({});

          // Clear sidebar data
          setAllQuestionsData({});
          setMethodDefTabsData({});
          setExpandedAccordions({});
          assetCrossLinkAppliedRef.current = false;
        }

        // Update previous trade type ref
        previousTradeTypeRef.current = tradeType;

        const response = await getTemplatesByTradeName(tradeType, 'publish', accessToken);
        handleUnauthorized(response);

        if (response?.data?.questionTemplateData) {
          const templateJson = JSON.parse(response.data.questionTemplateData);
          setTemplateData(templateJson);
          setResponse(templateJson);

          // Store the template version from API response
          if (response?.data?.version) {
            setTemplateVersion(response.data.version);
          }

          const templateVersion = resolveMergedTemplateVersion(
            templateJson?.SDM?.Building?.TemplateVersion
          );
          const itemGroupDefs = templateVersion?.ItemGroupDef || [];
          // Set QuestionRenderer data
          setTemplate(templateVersion);
          setFormDef(templateVersion?.FormDef || []);
          setEventDef(templateVersion?.EventDef || []);
          setItemGroup(itemGroupDefs);
          setItemDef(templateVersion?.ItemDef || []);
          setMappings(templateVersion?.Mappings || []);
          setCodeList(templateVersion?.CodeList || []);
          setMeasureUnit(templateJson?.SDM?.Building?.BasicDefinition);
          setMethodDef(templateVersion?.MethodDef || []);
          setConditionDef(templateVersion?.ConditionDef || []);

          // Extract imageBaseUrl from template Images config
          const templateImages = templateVersion?.Images;
          if (templateImages && templateImages.length > 0 && templateImages[0].baseUrl) {
            const baseUrl = templateImages[0].baseUrl;
            setImageBaseUrl(baseUrl);
                        // Collect all unique filenames from CodeListItems and fetch fresh SAS URLs
            const codeLists = templateVersion?.CodeList ?? [];
            const filenames = new Set<string>();
            for (const cl of codeLists) {
              for (const item of (cl.CodeListItem ?? [])) {
                if (item.ImageUrl) filenames.add(item.ImageUrl);
                if (item.ThumbnailUrl) filenames.add(item.ThumbnailUrl);
              }
            }
            if (filenames.size > 0) {
              getCatalogueSasUrls(Array.from(filenames), accessToken)
                .then(map => setCatalogueSasMap(map))
                .catch(() => {});
            }
          }


          // Initialize universal parsers
          templateMetadataParserRef.current = new TemplateMetadataParser(templateJson);
          questionMetadataParserRef.current = new QuestionMetadataParser(templateVersion);
          dependencyGraphParserRef.current = new DependencyGraphParser(templateVersion);

          // Build dependency graph first, then initialize multi-instance parser
          const dependencyGraph = dependencyGraphParserRef.current.buildGraph();
          multiInstanceParserRef.current = new MultiInstanceParser(templateVersion, dependencyGraph);

          // Build repeating group mappings
          const mappings = buildRepeatingGroupMappings(
            itemGroupDefs,
            templateVersion?.FormDef || [],
            templateVersion?.MethodDef || []
          );
          setRepeatingGroupMappings(mappings);

          // Get set of repeating group OIDs that have valid mappings
          // Get set of repeating group OIDs that have valid mappings
          const mappedRepeatingOIDs = new Set(mappings.map(m => m.repeatingGroupOID));

          // Build map: ItemGroupOID → CollectionExceptionConditionOID from FormDef
          const formItemGroupConditionMap = new Map<string, string>();
          (templateVersion?.FormDef || []).forEach((fd: any) => {
            (fd.ItemGroupRef || []).forEach((igRef: any) => {
              if (igRef.CollectionExceptionConditionOID) {
                formItemGroupConditionMap.set(
                  igRef.ItemGroupOID,
                  igRef.CollectionExceptionConditionOID
                );
              }
            });
          });

          const subGroupsByParent = new Map<string, any[]>();
          itemGroupDefs.forEach((ig: any) => {
            if (ig._naomiParentOID) {
              if (!subGroupsByParent.has(ig._naomiParentOID)) subGroupsByParent.set(ig._naomiParentOID, []);
              subGroupsByParent.get(ig._naomiParentOID)!.push(ig);
            }
          });
          const collectSubGroupItems = (sgOID: string): any[] => {
            const sg = itemGroupDefs.find((ig: any) => ig.OID === sgOID);
            if (!sg) return [];
            if ((sg as any).Repeating === 'Yes') return [];
            const sgCondition = formItemGroupConditionMap.get(sgOID);
            const sgLabel: string = (sg as any).Description?.TranslatedText?.text || (sg as any).Name || '';
            const items: any[] = [];
            ((sg as any).ItemRef || []).forEach((itemRef: any, itemIndex: number) => {
              items.push({
                ...(sgCondition ? { ...itemRef, CollectionExceptionConditionOID: sgCondition } : { ...itemRef }),
                ...(itemIndex === 0 && sgLabel ? { _subGroupLabel: sgLabel } : {})
              });
            });
            (subGroupsByParent.get(sgOID) || []).forEach((childSg: any) => {
              items.push(...collectSubGroupItems(childSg.OID));
            });
            return items;
          };

          // Build steps from ItemGroups, excluding mapped repeating groups
          const dynamicSteps: StepData[] = itemGroupDefs
            .filter(itemGroup => {
              if ((itemGroup as any)._naomiParentOID) return false;
              if (itemGroup.Repeating !== 'Yes') return true;

              // Include repeating groups that don't have valid mappings (fallback to regular steps)
              return !mappedRepeatingOIDs.has(itemGroup.OID);
            })
            .map((itemGroup: any, index: number) => {
              // This allows nested rendering to find children of children    
              const repeatingChildren: RepeatingChildGroup[] = mappings
                .map(mapping => {
                  const childGroupDef = itemGroupDefs.find(ig => ig.OID === mapping.repeatingGroupOID);
                  return {
                    itemGroupDef: childGroupDef!,
                    controlItemOID: mapping.controlItemOID,
                    label: mapping.label,
                    methodOID: mapping.methodOID,
                    // NEW: Pass nested info to repeating children
                    isNested: mapping.isNested,
                    parentRepeatingGroupOID: mapping.isNested ? mapping.parentGroupOID : undefined
                  };
                });

              const directSubGroups = subGroupsByParent.get(itemGroup.OID) || [];
              let mergedItemRefs = [...(itemGroup.ItemRef || [])];
              directSubGroups.forEach((sg: any) => {
                mergedItemRefs.push(...collectSubGroupItems(sg.OID));
              });
              const mergedItemGroupDef = directSubGroups.length > 0
                ? { ...itemGroup, ItemRef: mergedItemRefs }
                : itemGroup;

              return {
                id: itemGroup.OID || `step${index + 1}`,
                title: getStepTitle(itemGroup, index),
                description: getStepDescription(itemGroup, index),
                completed: false,
                itemGroupDef: mergedItemGroupDef,
                repeatingChildren,
                collectionExceptionConditionOID: formItemGroupConditionMap.get(itemGroup.OID) || null
              };
            });

          // Add Summary step at the end
          dynamicSteps.push({
            id: 'summary',
            title: 'Summary',
            description: 'Review all requirements and finalize',
            completed: false,
            collectionExceptionConditionOID: undefined
          });

          setSteps(dynamicSteps);

          const mergedItemGroupDefs = dynamicSteps
            .filter((s: StepData) => s.itemGroupDef)
            .map((s: StepData) => s.itemGroupDef!);
          if (mergedItemGroupDefs.length > 0) {
            const mergedOIDs = new Set(mergedItemGroupDefs.map((g: any) => g.OID));
            const excludedRepeatingGroupDefs = itemGroupDefs.filter((g: any) => !mergedOIDs.has(g.OID));
            dependencyGraphParserRef.current = new DependencyGraphParser({
              ...templateVersion,
              ItemGroupDef: [...mergedItemGroupDefs, ...excludedRepeatingGroupDefs]
            });
          }

          const templateItemDefs = templateVersion?.ItemDef || [];


          const rooflensFlagOn = FEATURE_FLAGS.rooflens_measurement_source_enable;
          const hasRoofLensMeta = Object.keys(roofLensMeta).length > 0;
          if (templateItemDefs.length > 0 && (fetchedTradeData || hasRoofLensMeta)) {
            const prePopulatedAnswers: { [questionId: string]: any } = {};

            templateItemDefs.forEach((itemDef: any) => {
              // Use MetaKey to look up value in tradeData (this is how IntegerText does it)
              const metaKey = itemDef?.MetaKey;
              if (metaKey && itemDef?.OID) {
                // RoofLens wins over EagleView when the flag is on; otherwise this
                // resolves exactly to today's EagleView root / TotalMeasurements lookup.
                const resolved = resolveMeasurementValue(
                  metaKey,
                  roofLensMeta,
                  fetchedTradeData,
                  rooflensFlagOn
                );
                const rawValue = resolved?.value;
                const isRoofLensSource = resolved?.source === 'rooflens';

                if (rawValue !== undefined && rawValue !== null) {
                  // A TOP-LEVEL coded (select) measurement question — e.g. a
                  // predominant-pitch dropdown — must be stored in coded CodeList
                  // shape, not as a raw "6/12". Resolve generically from the
                  // CodeList's own labels (no hardcoded CodeList ids). Gated to
                  // RoofLens-sourced values so EagleView behavior is unchanged.
                  let codedTopLevel: { value: string; codeList: any[] } | null = null;
                  if (isRoofLensSource && itemDef.CodeListRef?.CodeListOID) {
                    codedTopLevel = resolveCodedMeasurement(
                      itemDef,
                      rawValue,
                      templateVersion?.CodeList || [],
                      0
                    );
                  }

                  // Parse numeric values (like "34741 sq. ft" -> 34741). RoofLens
                  // values may already be numbers, so normalize to string first.
                  const parsed = parseFloat(String(rawValue));
                  let valueToStore: string | null = null;

                  if (codedTopLevel) {
                    valueToStore = codedTopLevel.value;
                  } else if (!isNaN(parsed)) {
                    valueToStore = parsed.toString();
                  } else if (typeof rawValue === 'string' && rawValue.trim() !== '') {
                    // For non-numeric string values
                    valueToStore = rawValue;
                  }

                  // This ensures EagleView values are included in submission payload
                  if (valueToStore !== null) {
                    // Find the itemGroup that contains this itemDef
                    const templateItemGroups = templateVersion?.ItemGroupDef || [];
                    let itemGroupOID: string | null = null;
                    for (const group of templateItemGroups) {
                      if (group.ItemRef?.some((ref: any) => ref.ItemOID === itemDef.OID)) {
                        itemGroupOID = group.OID;
                        break;
                      }
                    }

                    // Only pre-populate if we found the correct ItemGroup
                    if (itemGroupOID) {


                      const entry: any = {
                        value: valueToStore,
                        itemOID: itemDef.OID,
                        itemGroupOID: itemGroupOID,
                        repeatKey: 0,
                        dataType: itemDef.DataType || 'integer',
                        codeListItem: null,
                        isNull: false,
                        // Keep EagleView tagging for EagleView-sourced values; tag
                        // RoofLens-sourced values distinctly so downstream can tell.
                        isEagleViewPrePopulated: !isRoofLensSource,
                        isRoofLensPrePopulated: isRoofLensSource
                      };
                      if (codedTopLevel) {
                        entry.CodeList = codedTopLevel.codeList;
                      }
                      prePopulatedAnswers[itemDef.OID] = entry;
                    }
                  }
                }
              }
            });


            // Merge with existing answersData (don't overwrite if already exists)
            if (Object.keys(prePopulatedAnswers).length > 0) {
              setAnswersData((prev: { [questionId: string]: any }) => {
                const merged = { ...prePopulatedAnswers };
                // Preserve any existing answers (user-entered takes priority)
                Object.keys(prev).forEach(key => {
                  if (prev[key] !== undefined && prev[key] !== '') {
                    merged[key] = prev[key];
                  }
                });
                return merged;
              });
            }
          }

          // ── GENERIC per-facet repeating-group autofill (MetaKey-matched) ────────
          // Trade-agnostic: for EVERY repeating ItemGroupDef, fill its member
          // questions whose ItemDef.MetaKey is in the per-facet measurement
          // vocabulary, one instance per RoofLens facet. WHERE to write is read
          // dynamically from the template (ItemDef.OID, its repeating group OID,
          // the control item OID from repeatingGroupMappings) — no hardcoded OIDs.
          // A group with no measurement-tagged member is skipped. Manual edits win.
          if (rooflensFlagOn && roofLensPerFacet.length > 0) {
            const perFacetItemDefs: any[] = templateVersion?.ItemDef || [];
            const perFacetItemGroupDefs: any[] = templateVersion?.ItemGroupDef || [];
            const perFacetCodeLists: any[] = templateVersion?.CodeList || [];
            const itemDefsByOid: Record<string, any> = {};
            perFacetItemDefs.forEach((d: any) => { if (d?.OID) itemDefsByOid[d.OID] = d; });

            if (roofLensPerFacet.length > MAX_FACET_INSTANCES) {
              console.warn(
                `[RoofLens autofill] ${roofLensPerFacet.length} facets exceed the ` +
                `${MAX_FACET_INSTANCES}-instance UI cap; extra facets were not materialized.`
              );
            }

            // Accumulate patches across all measurement repeating groups.
            const allAnswers: { [answerKey: string]: any } = {};
            const allItemGroupRows: any[] = [];
            const controlCounts: { [controlItemOID: string]: number } = {};
            const detectedGroups: Array<{ repeatingGroupOID: string; metaKeys: string[]; controlItemOID: string | null }> = [];
            const matchedMetaKeys = new Set<string>();

            perFacetItemGroupDefs
              .filter((ig: any) => ig?.Repeating === 'Yes')
              .forEach((repeatingGroupDef: any) => {
                const built = buildPerFacetAnswers(
                  roofLensPerFacet,
                  repeatingGroupDef,
                  itemDefsByOid,
                  perFacetCodeLists
                );
                if (!built) return; // no measurement-tagged member — generic skip

                built.matchedMetaKeys.forEach((k) => matchedMetaKeys.add(k));
                Object.assign(allAnswers, built.answers);
                allItemGroupRows.push(...built.itemGroupData);

                // Resolve the control item that drives this group's instance count
                // dynamically from the repeating-group mappings (parsed from the
                // MethodDef FormalExpression). Set its answer to the clamped count
                // and seed repeatingInstanceCounts so the count-sync effect sees
                // stored == current and leaves the pre-built instances in place.
                const mapping = mappings.find((m) => m.repeatingGroupOID === repeatingGroupDef.OID);
                const controlItemOID = mapping?.controlItemOID ?? null;
                if (controlItemOID) {
                  let controlGroupOID: string | null = null;
                  for (const g of perFacetItemGroupDefs) {
                    if (g?.ItemRef?.some((ref: any) => ref.ItemOID === controlItemOID)) {
                      controlGroupOID = g.OID;
                      break;
                    }
                  }
                  const controlDef = itemDefsByOid[controlItemOID];
                  allAnswers[controlItemOID] = {
                    value: String(built.clampedCount),
                    itemOID: controlItemOID,
                    itemGroupOID: controlGroupOID,
                    repeatKey: 0,
                    dataType: controlDef?.DataType || 'integer',
                    codeListItem: null,
                    isNull: false,
                    isEagleViewPrePopulated: false,
                    isRoofLensPrePopulated: true,
                  };
                  controlCounts[controlItemOID] = built.clampedCount;
                } else {
                  // Per-facet answers were built but the group's repeat-count control
                  // item couldn't be resolved (unparseable/absent MethodDef expression),
                  // so the instances won't auto-surface until the user bumps the count.
                  console.warn(
                    `[RoofLens autofill] repeating group ${repeatingGroupDef.OID} has no ` +
                    `resolvable control item; ${built.clampedCount} facet(s) filled but not auto-shown.`
                  );
                }

                detectedGroups.push({
                  repeatingGroupOID: repeatingGroupDef.OID,
                  metaKeys: built.matchedMetaKeys,
                  controlItemOID,
                });
              });

            // Flag-gated diagnostic: what we detected, and any source measurement
            // MetaKey that matched NO template question (surfaces template gaps).
            const sourceMetaKeys = new Set<string>();
            roofLensPerFacet.forEach((row) => Object.keys(row).forEach((k) => sourceMetaKeys.add(k)));
            const unmatchedMetaKeys = Array.from(sourceMetaKeys).filter((k) => !matchedMetaKeys.has(k));
            console.debug('[RoofLens autofill] per-facet', {
              detectedGroups,
              perFacetCount: roofLensPerFacet.length,
              clampedCount: Math.min(roofLensPerFacet.length, MAX_FACET_INSTANCES),
              unmatchedMetaKeys,
            });

            // Decide, from the current answers, which keys we may write — a genuine
            // user edit (untagged, non-empty) always wins; empty or previously
            // auto-filled slots are (re)filled. Use the SAME set for answersData,
            // itemGroupData AND the instance count so the three can never diverge —
            // divergence would let the answersData⇄itemGroupData sync effect revert a
            // refreshed value, or leave stored count != control answer (spurious delete
            // dialog). See the parallel cross-link fix elsewhere in this file.
            const writableAnswerKeys = new Set<string>();
            Object.keys(allAnswers).forEach((key) => {
              const existing = (answersData as any)?.[key];
              const isEmpty =
                !existing ||
                existing.value === '' ||
                existing.value === null ||
                existing.value === undefined;
              const wasAutoFilled =
                existing?.isRoofLensPrePopulated === true ||
                existing?.isEagleViewPrePopulated === true;
              if (isEmpty || wasAutoFilled) writableAnswerKeys.add(key);
            });

            if (writableAnswerKeys.size > 0) {
              setAnswersData((prev: { [questionId: string]: any }) => {
                const merged = { ...prev };
                writableAnswerKeys.forEach((key) => { merged[key] = allAnswers[key]; });
                return merged;
              });

              // itemGroupData: write unconditionally for exactly the items whose answer
              // we wrote (mirrors the answersData decision) so a refresh can't be reverted.
              if (allItemGroupRows.length > 0) {
                setItemGroupData((prev: ItemGroupData[]) => {
                  const updated = prev.map((g: any) => ({ ...g, ItemData: [...(g.ItemData || [])] }));
                  allItemGroupRows.forEach((newRow) => {
                    const rk = newRow.ItemGroupRepeatKey ?? 0;
                    const writableItems = (newRow.ItemData || []).filter((it: any) => {
                      const key = rk > 0 ? `${it.ItemOID}_${rk}` : it.ItemOID;
                      return writableAnswerKeys.has(key);
                    });
                    if (writableItems.length === 0) return;
                    const idx = updated.findIndex(
                      (g: any) =>
                        g.ItemGroupOID === newRow.ItemGroupOID &&
                        (g.ItemGroupRepeatKey ?? 0) === rk
                    );
                    if (idx === -1) {
                      updated.push({ ...newRow, ItemData: writableItems } as any);
                      return;
                    }
                    const group: any = updated[idx];
                    writableItems.forEach((newItem: any) => {
                      const existIdx = group.ItemData.findIndex((i: any) => i.ItemOID === newItem.ItemOID);
                      if (existIdx === -1) group.ItemData.push(newItem);
                      else group.ItemData[existIdx] = newItem; // answer already gated → overwrite
                    });
                    updated[idx] = group;
                  });
                  return updated;
                });
              }
            }

            // Instance count: keep in lockstep with the control answer. If the control
            // answer was (re)written this pass, match its count; otherwise seed only when
            // unset so a user-set count is never clobbered.
            if (Object.keys(controlCounts).length > 0) {
              setRepeatingInstanceCounts((prev: { [controlItemOID: string]: number }) => {
                const merged = { ...prev };
                Object.entries(controlCounts).forEach(([oid, count]) => {
                  if (writableAnswerKeys.has(oid)) merged[oid] = count;
                  else if (!merged[oid]) merged[oid] = count;
                });
                return merged;
              });
            }
          }

          if (!assetCrossLinkAppliedRef.current) {
            try {
              const currentItemDefs: any[] = templateVersion?.ItemDef || [];
              const currentItemGroups: any[] = templateVersion?.ItemGroupDef || [];
              const autoFillItems = currentItemDefs.filter(
                (def: any) => def.AutoFillRef?.TradeOID && def.AutoFillRef?.ItemOID
              );

              if (autoFillItems.length > 0) {
                const crossLinkPatch: { [key: string]: any } = {};

                autoFillItems.forEach((def: any) => {
                  const { TradeOID, ItemOID, Index } = def.AutoFillRef;
                  const sourceIndex = Index ?? 0;

                  const sourceGroups = getSourceItemGroups(TradeOID);
                  const valuesByIndex: string[] = [];
                  sourceGroups.forEach((group: any) => {
                    (group.ItemData || []).forEach((item: any) => {
                      if (item.ItemOID === ItemOID && item.Value) {
                        const idx = item.ItemGroupRepeatKey ?? 0;
                        valuesByIndex[idx] = item.Value;
                      }
                    });
                  });
                  const compactValues = valuesByIndex.filter(Boolean);
                  const resolvedValue = compactValues[sourceIndex];

                  if (!resolvedValue) return;
                  let targetGroupOID = '';
                  for (const ig of currentItemGroups) {
                    if (ig.ItemRef?.some((ref: any) => ref.ItemOID === def.OID)) {
                      targetGroupOID = ig.OID;
                      break;
                    }
                  }
                  if (!targetGroupOID) return;

                  crossLinkPatch[def.OID] = {
                    value: resolvedValue,
                    itemGroupOID: targetGroupOID,
                    repeatKey: 0,
                    timestamp: Date.now(),
                    dataType: def.DataType || 'text',
                    originalItemData: {
                      ItemOID: def.OID,
                      Value: resolvedValue,
                      ItemGroupRepeatKey: 0,
                    },
                    _crossLinkedFromAsset: true,
                  };
                });

                if (Object.keys(crossLinkPatch).length > 0) {
                  assetCrossLinkAppliedRef.current = true;
                  setAnswersData((prev: { [questionId: string]: any }) => {
                    const merged = { ...prev };
                    Object.entries(crossLinkPatch).forEach(([oid, entry]) => {
                      const existing = prev[oid];
                      const isEmpty =
                        !existing ||
                        existing.value === '' ||
                        existing.value === null ||
                        existing.value === undefined;
                      const wasCrossLinked = existing?._crossLinkedFromAsset === true;
                      if (isEmpty || wasCrossLinked) {
                        merged[oid] = entry;
                      }
                    });
                    return merged;
                  });
                  const patchesByGroup: { [groupOID: string]: any[] } = {};
                  Object.values(crossLinkPatch).forEach((entry: any) => {
                    const gOID = entry.itemGroupOID;
                    if (!patchesByGroup[gOID]) patchesByGroup[gOID] = [];
                    patchesByGroup[gOID].push({
                      ItemOID: entry.originalItemData.ItemOID,
                      Value: entry.value,
                      ItemGroupRepeatKey: 0,
                    });
                  });
                  setItemGroupData((prev: ItemGroupData[]) => {
                    const updated = [...prev];

                    Object.entries(patchesByGroup).forEach(([groupOID, newItems]) => {
                      const igIdx = updated.findIndex(
                        (g: any) => g.ItemGroupOID === groupOID
                      );

                      if (igIdx === -1) {
                        updated.push({
                          ItemGroupOID: groupOID,
                          ItemGroupRepeatKey: 0,
                          ItemData: newItems,
                        } as any);
                      } else {
                        const group: any = {
                          ...updated[igIdx],
                          ItemData: [...((updated[igIdx] as any).ItemData || [])],
                        };
                        newItems.forEach((newItem: any) => {
                          const existIdx = group.ItemData.findIndex(
                            (i: any) => i.ItemOID === newItem.ItemOID
                          );
                          const existingVal =
                            existIdx !== -1 ? group.ItemData[existIdx]?.Value : undefined;
                          const groupIsEmpty =
                            !existingVal || existingVal === '' || existingVal === null;
                          if (groupIsEmpty) {
                            if (existIdx === -1) group.ItemData.push(newItem);
                            else group.ItemData[existIdx] = newItem;
                          }
                        });
                        updated[igIdx] = group;
                      }
                    });

                    return updated;
                  });
                }
              }
            } catch (error: any) {
              console.warn('[AutoFill] Cross-link patching failed:', error);
            }
          }
        }
      } catch (error: any) {
        setError(true);
        setErrorCode(error?.response?.status);
        setErrorReason(error?.response?.data?.message || 'Failed to load template. Please try again or contact support.');
        setSteps([]);
      } finally {
        setLoading(false);
        onLoaded?.();
      }
    };

    fetchTemplateData();
    // Re-run when the customer/asset context changes too (not just trade) so stale
    // EagleView/asset data isn't used if the customer or asset switches. Use the
    // specific primitive fields (not the whole `data` object) to avoid render loops.
  }, [accessToken, tradeType, data?.customerUid, data?.assetUid]); // eslint-disable-line react-hooks/exhaustive-deps

  // Extract questions from all steps for sidebar
  useEffect(() => {
    if (steps.length > 0 && itemDef.length > 0) {
      const questionsMap: { [stepId: string]: any[] } = {};

      steps.forEach((step) => {
        if (step.itemGroupDef && step.itemGroupDef.ItemRef) {
          const questions = step.itemGroupDef.ItemRef.map((itemRef: any) => {
            const itemDefinition = itemDef.find(item => item.OID === itemRef.ItemOID);
            return {
              id: itemRef.ItemOID,
              itemRef,
              itemDef: itemDefinition,
              isConditional: !!itemRef.CollectionExceptionConditionOID,
              conditionOID: itemRef.CollectionExceptionConditionOID,
              question: itemDefinition?.Question?.TranslatedText?.text || `Question ${itemRef.ItemOID}`,
              dataType: itemDefinition?.DataType,
              stepId: step.id,
              stepTitle: step.title
            };
          }).filter(q => q.itemDef);

          questionsMap[step.id] = questions;
        }
      });

      setAllQuestionsData(questionsMap);
    }
  }, [steps, itemDef]);

  // Extract mandatory questions from ItemRefs
  useEffect(() => {
    if (steps.length > 0 && itemGroup.length > 0) {
      const mandatoryMap: { [questionId: string]: boolean } = {};

      steps.forEach((step) => {
        if (step.itemGroupDef && step.itemGroupDef.ItemRef) {
          step.itemGroupDef.ItemRef.forEach((itemRef: any) => {
            if (itemRef.Mandatory === 'Yes') {
              mandatoryMap[itemRef.ItemOID] = true;
            }
          });
        }

        // Also check repeating children
        if (step.repeatingChildren) {
          step.repeatingChildren.forEach(repeatingChild => {
            if (repeatingChild.itemGroupDef && repeatingChild.itemGroupDef.ItemRef) {
              repeatingChild.itemGroupDef.ItemRef.forEach((itemRef: any) => {
                if (itemRef.Mandatory === 'Yes') {
                  // For multi-instance, we track the base ItemOID
                  mandatoryMap[itemRef.ItemOID] = true;
                }
              });
            }
          });
        }
      });

      setMandatoryQuestions(mandatoryMap);
    }
  }, [steps, itemGroup]);

  // ==================== STATE RESTORATION ====================
  useEffect(() => {
    // Prevent multiple restorations
    if (hasRestoredRef.current) {
      return;
    }

    // Get context from data prop
    const customerUid = data?.customerUid;
    const assetUid = data?.assetUid;
    const tradeOid = tradeType;

    if (!customerUid || !assetUid || !tradeOid) {
      hasRestoredRef.current = true;
      return;
    }

    const savedState = loadRequirementsState(customerUid, assetUid, tradeOid, data?.jobStartDate);
    if (savedState) {

      // Mark as restored
      hasRestoredRef.current = true;
      if (savedState.answersData) {
        setAnswersData(prev => {
          // FIX: Give priority to saved state values over prev state
          // Previously this was {...savedState.answersData, ...prev} which kept stale prev values
          const merged = { ...prev, ...savedState.answersData };
          return merged;
        });
      }

      // Restore active step
      if (savedState.activeStep !== undefined) {
        setActiveStep(savedState.activeStep);
      }

      // Restore completed steps
      if (savedState.completed) {
        setCompleted(savedState.completed);
      }

      // Restore other relevant state if saved
      if (savedState.itemGroupData) {
        setItemGroupData(savedState.itemGroupData);
      }

      if (savedState.repeatingInstanceData) {
        setRepeatingInstanceData(savedState.repeatingInstanceData);
      }

      if (savedState.currentQuestionIndex) {
        setCurrentQuestionIndex(savedState.currentQuestionIndex);
      }

      // Notify parent component of restored state
      if (onDataChange && savedState.answersData && Object.keys(savedState.answersData).length > 0) {
        // Use setTimeout to ensure this runs after component is fully mounted
        setTimeout(() => {
          // Organize all questions hierarchically to filter out hidden/conditional questions
          const organizedQuestionsData: { [stepId: string]: any[] } = {};
          Object.keys(allQuestionsData).forEach(stepId => {
            const stepQuestions = allQuestionsData[stepId] || [];
            organizedQuestionsData[stepId] = organizeQuestionsHierarchically(stepQuestions, stepId);
          });

          onDataChange({
            answersData: savedState.answersData,
            activeStep: savedState.activeStep,
            completed: savedState.completed,
            itemGroupData: savedState.itemGroupData,
            allQuestionsData: organizedQuestionsData // Pass ORGANIZED questions (filtered, visible only)
          });
        }, 100);
      }
    } else {
      hasRestoredRef.current = true;
    }
  }, []); // EMPTY dependencies - runs ONLY on mount

  // ==================== AUTO-SAVE ====================
  // Debounced auto-save function with composite key
  const debouncedSave = useRef(
    debounce(async (dataToSave: any, customerUid: string, assetUid: string, tradeOid: string, jobStartDate?: string | null) => {
      try {
        // Wait for any in-progress suffix-sync/ODM work to finish (bounded)
        await SaveGate.awaitClear(1000);
      } catch (e) {
        // ignore and proceed to save
      }
      saveRequirementsState(dataToSave, customerUid, assetUid, tradeOid, jobStartDate);
    }, 1000) // Save 1 second after last change
  ).current;

  // Auto-save when critical state changes
  useEffect(() => {
    // Don't save if we're still loading initial data
    if (loading) return;

    // Don't save if no answers have been provided yet
    // BUT DO save if we just cleaned items (recentlyCleanedItemsRef has items)
    // Otherwise the old data persists in session storage
    if (Object.keys(answersData).length === 0 && recentlyCleanedItemsRef.current.size === 0) {
      return;
    }


    // Get context from data prop
    const customerUid = data?.customerUid;
    const assetUid = data?.assetUid;
    const tradeOid = tradeType;

    if (!customerUid || !assetUid || !tradeOid) {
      return;
    }
    const instanceAnswerKeys = Object.keys(answersData).filter(k => /_\d+$/.test(k));
    const hasInstanceAnswers = instanceAnswerKeys.length > 0;

    // If we have instance answers but itemGroupData is empty/insufficient, don't save yet
    if (hasInstanceAnswers && itemGroupData.length === 0) {
      return;
    }

    // Check if itemGroupData count matches instance count in answersData
    const maxInstance = Math.max(...instanceAnswerKeys.map(k => {
      const match = k.match(/_(\d+)$/);
      return match ? parseInt(match[1]) : 0;
    }), 0);

    // (Allow saving with fewer itemGroups only if we have NO instance answers)
    if (hasInstanceAnswers && itemGroupData.length > 0 && itemGroupData.length < maxInstance) {
      return;
    }

    // Remove any instance entries (keys with suffixes like _1) that are missing required fields
    // Also filter out recently cleaned items from multi-instance deletion
    const cleanedAnswersData = { ...answersDataRef.current };
    Object.keys(cleanedAnswersData).forEach(key => {
      const entry = cleanedAnswersData[key];
      const keyParts = key.split('_');

      // If this is a 2-part or 3-part key (instance-specific) but missing itemGroupOID
      if (keyParts.length >= 2 && !entry?.itemGroupOID) {
        delete cleanedAnswersData[key];
      }

      if (recentlyCleanedItemsRef.current.has(key)) {
        // Check if this item has an actual answer (CodeList with IsEnabled=true)
        let hasActualAnswer = false;
        if (entry?.CodeList && Array.isArray(entry.CodeList)) {
          for (const cl of entry.CodeList) {
            if (cl?.CodeListItem && Array.isArray(cl.CodeListItem)) {
              const enabledItem = cl.CodeListItem.find((item: any) => item?.IsEnabled === true);
              if (enabledItem) {
                hasActualAnswer = true;
                break;
              }
            }
          }
        }
        // Also check if value is non-empty
        if (entry?.value && entry.value !== '') {
          hasActualAnswer = true;
        }

        if (hasActualAnswer) {
          // User answered this question - remove from cleanup ref and preserve it
          recentlyCleanedItemsRef.current.delete(key);
        } else {
          // No actual answer - filter it out
          delete cleanedAnswersData[key];
        }
      }
    });



    // FIX: Filter out recently cleaned items from itemGroupData before saving
    // Build a set of base OIDs that should be filtered (extract OID from keys like "I.35_1")
    const cleanedBaseOIDs = new Set<string>();
    const cleanedFullKeys = new Set<string>();
    const cleanedParentRepeatKeys = new Set<number>(); // NEW: Track deleted parent repeat keys
    recentlyCleanedItemsRef.current.forEach(key => {
      cleanedFullKeys.add(key);
      // Extract base OID (e.g., "I.35" from "I.35_1" or "I.24" from "I.24_1_1")
      const match = key.match(/^(I\.\d+)/);
      if (match) {
        cleanedBaseOIDs.add(match[1]);
      }
      // NEW: Extract parent repeat key from nested items (e.g., "1" from "I.24_1_1")
      const nestedMatch = key.match(/^I\.\d+_(\d+)_\d+$/);
      if (nestedMatch) {
        cleanedParentRepeatKeys.add(parseInt(nestedMatch[1]));
      }
      // NEW: Also extract repeat key from direct items (e.g., "1" from "I.35_1")
      // This represents the parent group's ItemGroupRepeatKey that's being deleted
      const directMatch = key.match(/^I\.\d+_(\d+)$/);
      if (directMatch) {
        cleanedParentRepeatKeys.add(parseInt(directMatch[1]));
      }
    });


    // Get nested group OIDs dynamically from repeatingGroupMappings (needed for ParentItemGroupRepeatKey fix)
    const nestedGroupOIDs = new Set(
      repeatingGroupMappings
        .filter(m => m.isNested === true)
        .map(m => m.repeatingGroupOID)
    );

    const cleanedItemGroupData = itemGroupData
      .filter(group => {
        const parentRepeatKey = group.ParentItemGroupRepeatKey;
        const isNestedGroup = parentRepeatKey !== undefined;
        if (isNestedGroup) {
          // If ParentItemGroupRepeatKey > 0, check directly
          if (parentRepeatKey !== undefined && parentRepeatKey > 0 && cleanedParentRepeatKeys.has(parentRepeatKey)) {
            return false;
          }
          // If ParentItemGroupRepeatKey is 0 (was reset), check if any items in this group match our cleaned nested keys
          if (parentRepeatKey === 0 || parentRepeatKey === undefined) {
            const cleanedKeysArray = Array.from(cleanedFullKeys);
            const hasMatchingNestedItem = (group.ItemData || []).some(item => {
              // Check if this item's OID with ANY parent repeat key is in cleaned items
              return cleanedKeysArray.some(cleanedKey => {
                // Match nested format like "I.24_1_1" where I.24 is the item OID
                return cleanedKey.startsWith(`${item.ItemOID}_`);
              });
            });
            if (hasMatchingNestedItem) {
              return false;
            }
          }
        }

        // Filter out entire groups that belong to deleted instances
        if (group.ItemGroupRepeatKey > 0) {
          // Check if any item in this group is in the cleaned list
          const hasCleanedItem = (group.ItemData || []).some(item => {
            // Try direct key format: I.35_1
            const directKey = `${item.ItemOID}_${group.ItemGroupRepeatKey}`;
            if (cleanedFullKeys.has(directKey)) return true;

            // Try nested key format: I.24_1_1 (for nested groups)
            const parentKey = group.ParentItemGroupRepeatKey || (item as any).ParentItemGroupRepeatKey || 0;
            if (parentKey > 0) {
              const nestedKey = `${item.ItemOID}_${parentKey}_${group.ItemGroupRepeatKey}`;
              if (cleanedFullKeys.has(nestedKey)) return true;
            }

            return false;
          });
          if (hasCleanedItem) {
            return false; // Remove this entire group
          }
        }
        return true;
      })
      .map(group => ({
        ...group,
        ItemData: (group.ItemData || []).filter(item => {
          // Build the answer key to check
          const repeatKey = item.ItemGroupRepeatKey || group.ItemGroupRepeatKey || 0;
          const answerKey = repeatKey > 0 ? `${item.ItemOID}_${repeatKey}` : item.ItemOID;
          return !cleanedFullKeys.has(answerKey);
        })
      }))
      .filter(group => group.ItemData && group.ItemData.length > 0)
      //  Correct ParentItemGroupRepeatKey: 0 for nested groups
      .map(group => {
        if (nestedGroupOIDs.has(group.ItemGroupOID) &&
          (group.ParentItemGroupRepeatKey === 0 || group.ParentItemGroupRepeatKey === undefined) &&
          group.ItemGroupRepeatKey > 0) {
          return {
            ...group,
            ParentItemGroupRepeatKey: group.ItemGroupRepeatKey
          };
        }
        return group;
      });

    const stateToSave = {
      answersData: cleanedAnswersData, // Use cleaned version
      activeStep,
      completed,
      itemGroupData: cleanedItemGroupData, // Use cleaned itemGroupData
      repeatingInstanceData,
      currentQuestionIndex,
      tradeType,
      questionTemplateVersion: tradeVersion?.current
    };

    debouncedSave(stateToSave, customerUid, assetUid, tradeOid, data?.jobStartDate);
  }, [answersData, activeStep, completed, itemGroupData, repeatingInstanceData, currentQuestionIndex, loading, tradeType, debouncedSave, data]);

  // Control accordion expansion — indices align with visibleSteps / horizontal stepper
  useEffect(() => {
    if (visibleSteps.length > 0) {
      const newExpanded: { [stepId: string]: boolean } = {};
      visibleSteps.forEach((step, index) => {
        newExpanded[step.id] = index === activeStep;
      });
      setExpandedAccordions(newExpanded);
    }
  }, [activeStep, visibleSteps]);

  useEffect(() => {
    // Check all RepeatCount questions and create instance answers
    const currentStep = visibleSteps[activeStep];
    if (!currentStep?.repeatingChildren) return;

    // PERFORMANCE: Collect all updates first, then apply in one batch
    const newAnswers: any = {};

    currentStep.repeatingChildren.forEach((child: any) => {
      const controlItemOID = child.controlItemOID;
      const repeatCount = answersData[controlItemOID] ? parseInt(answersData[controlItemOID].value) : 0;
      const processingKey = `${controlItemOID}_${repeatCount}`;

      // Skip if already processed this exact configuration
      if (processedRepeatingGroupsRef.current.has(processingKey)) {
        return;
      }

      if (repeatCount > 0 && child.itemGroupDef?.ItemRef) {
        let hasNewAnswers = false;

        // For each question in the repeating group, create instance answers
        child.itemGroupDef.ItemRef.forEach((itemRef: any) => {
          const baseQuestionId = itemRef.ItemOID;
          const baseAnswer = answersData[baseQuestionId];

          if (baseAnswer) {
            // Create instance answers for each repeat
            for (let i = 1; i <= repeatCount; i++) {
              const instanceKey = `${baseQuestionId}_${i}`;

              if (!answersData[instanceKey]) {
                newAnswers[instanceKey] = {
                  ...baseAnswer,
                  repeatKey: i,
                  itemGroupOID: child.itemGroupDef.OID,
                  timestamp: Date.now()
                };
                hasNewAnswers = true;
              }
            }
          }
        });

        // Mark this configuration as processed
        if (hasNewAnswers) {
          processedRepeatingGroupsRef.current.add(processingKey);
        }
      }
    });

    if (Object.keys(newAnswers).length > 0) {
      setAnswersData(prev => ({ ...prev, ...newAnswers }));
    }
  }, [activeStep, visibleSteps, answersData]);

  // Monitor control item changes for repeating groups
  useEffect(() => {
    if (repeatingGroupMappings.length === 0) return;

    if (deletionConfirmation.show) {
      if (suppressDeletionConfirmRef.current) {
        setDeletionConfirmation({ show: false });
        suppressDeletionConfirmRef.current = false;
      } else {
        return;
      }
    }

    if (skipNextCountCheckRef.current) {
      skipNextCountCheckRef.current = false;
      previousAnswersDataRef.current = { ...answersData };
      return;
    }

    let dialogTriggered = false;
    repeatingGroupMappings.forEach(mapping => {
      if (dialogTriggered) return;

      const currentCount = getRepeatingInstanceCount(mapping.controlItemOID);
      const storedCount = repeatingInstanceCounts[mapping.controlItemOID] || 0;

      let effectiveCurrentCount = currentCount;

      let controlItemRef: any = null;
      for (const step of steps) {
        if (step.itemGroupDef?.ItemRef) {
          controlItemRef = step.itemGroupDef.ItemRef.find((ir: any) => ir.ItemOID === mapping.controlItemOID);
          if (controlItemRef) break;
        }
      }

      const hasConditionalParent = controlItemRef?.CollectionExceptionConditionOID != null;

      if (hasConditionalParent) {
        // Find the parent question OID from the condition definition
        const conditionOID = controlItemRef.CollectionExceptionConditionOID;
        const condition = conditionDef?.find((cd: any) => cd.OID === conditionOID);
        if (condition?.FormalExpression?.text) {
          const conditionText = condition.FormalExpression.text;
          const parentBooleanOID = conditionText.split('==')[0]?.replace(/[&/\\#,+()$~%'":*?<>{}! ]/g, '').trim();
          const parentAnswer = answersData[parentBooleanOID];

          let parentHasSelection = false;

          // Check for direct value (boolean, Yes/No, numeric > 0)
          if (parentAnswer?.value !== undefined && parentAnswer?.value !== null && parentAnswer?.value !== '') {
            // Has a non-empty value
            if (parentAnswer.value === 'Yes' || parentAnswer.value === 'yes' || parentAnswer.value === true || parentAnswer.value === '1') {
              parentHasSelection = true;
            } else if (typeof parentAnswer.value === 'number' && parentAnswer.value > 0) {
              parentHasSelection = true;
            } else if (parentAnswer.value !== 'No' && parentAnswer.value !== 'no' && parentAnswer.value !== false && parentAnswer.value !== '0') {
              // Any other non-empty, non-negative value
              parentHasSelection = true;
            }
          }

          // Check for CodeList-based selection (singleselect/multiselect)
          if (!parentHasSelection && parentAnswer?.CodeList && Array.isArray(parentAnswer.CodeList)) {
            const codeList = parentAnswer.CodeList[0];
            if (codeList?.CodeListItem && Array.isArray(codeList.CodeListItem)) {
              // Check if ANY item is enabled (has IsEnabled: true)
              const hasEnabledItem = codeList.CodeListItem.some((item: any) => item.IsEnabled === true);
              if (hasEnabledItem) {
                parentHasSelection = true;
              }
            }
          }



          // Only hide if parent does NOT have a valid selection
          const parentSaysHide = parentAnswer && !parentHasSelection;
          const parentUnselected = !parentAnswer;

          if ((parentSaysHide || parentUnselected) && storedCount > 0 && currentCount > 0) {
            effectiveCurrentCount = 0;
          }
        }
      }

      if (effectiveCurrentCount !== storedCount) {
        handleCountChange(mapping.controlItemOID, effectiveCurrentCount, storedCount, mapping.repeatingGroupOID);
        dialogTriggered = true;
        if (effectiveCurrentCount === 0) {
          const repeatingGroupDef = itemGroup.find((ig: any) => ig.OID === mapping.repeatingGroupOID);
          if (repeatingGroupDef?.ItemRef) {
            const childItemOIDs = repeatingGroupDef.ItemRef
              .map((ref: any) => ref.ItemOID)
              .filter((oid: string) => oid !== mapping.controlItemOID);

            if (childItemOIDs.length > 0) {
              setAnswersData(prev => {
                const cleaned = { ...prev };
                let changed = false;
                childItemOIDs.forEach((oid: string) => {
                  if (cleaned[oid] !== undefined) {
                    delete cleaned[oid];
                    recentlyCleanedItemsRef.current.add(oid);
                    changed = true;
                  }
                });
                return changed ? cleaned : prev;
              });
              setConditionalQuestionVisibility(prev => {
                const updated = { ...prev };
                let changed = false;
                childItemOIDs.forEach((oid: string) => {
                  Object.values(allQuestionsData).forEach(stepQuestions => {
                    (stepQuestions as any[]).forEach((question: any) => {
                      if (question.id === oid && question.conditionOID) {
                        if (updated[question.conditionOID] !== false) {
                          updated[question.conditionOID] = false;
                          changed = true;
                        }
                      }
                    });
                  });
                });
                return changed ? updated : prev;
              });
            }
          }
        }

        if (effectiveCurrentCount > 0 && storedCount === 0) {
          const repeatingGroupDef = itemGroup.find((ig: any) => ig.OID === mapping.repeatingGroupOID);
          if (repeatingGroupDef?.ItemRef) {
            const childItemOIDs = repeatingGroupDef.ItemRef
              .map((ref: any) => ref.ItemOID)
              .filter((oid: string) => oid !== mapping.controlItemOID);
            if (childItemOIDs.length > 0) {
              setTimeout(() => {
                applyAutoFillForVisibleQuestions(childItemOIDs, answersDataRef.current);
              }, AUTO_FILL_DEBOUNCE_MS);
            }
          }
        }
      }
    });


    if (!dialogTriggered) {
      previousAnswersDataRef.current = { ...answersData };
    }
  }, [answersData, repeatingGroupMappings]);

  // Helper function to get step title from ItemGroup
  const getStepTitle = (itemGroup: any, index: number): string => {
    const description = itemGroup?.Description?.TranslatedText?.text;
    if (description) {
      return description;
    }

    // Use ItemGroup Name if Description not available
    if (itemGroup?.Name) {
      return itemGroup.Name;
    }

    // Last resort: Use OID (better than generic "Step X")
    if (itemGroup?.OID) {
      return itemGroup.OID.replace(/^IG\./, '').replace(/_/g, ' ');
    }

    // Absolute fallback if template data is completely missing
    return `Section ${index + 1}`;
  };

  // Helper function to get step description
  const getStepDescription = (itemGroup: any, index: number): string => {
    if (itemGroup?.Comment) {
      return itemGroup.Comment;
    }

    // Try extracting from first question
    const firstItemRef = itemGroup?.ItemRef?.[0];
    if (firstItemRef) {
      const firstItemOID = firstItemRef.ItemOID;
      // Could look up ItemDef here for more context, but keep it simple
      return `Configure ${itemGroup?.Name || 'requirements'} for this section`;
    }

    // Derive from title
    const title = getStepTitle(itemGroup, index);
    return `Configure ${title.toLowerCase()} settings`;
  };

  // Handle step navigation
  const handleStep = (step: number) => () => {
    setActiveStep(step);
  };

  // Handle step completion
  const handleComplete = () => {
    if (onDataChange) {
      // Organize all questions hierarchically to filter out hidden/conditional questions
      const organizedQuestionsData: { [stepId: string]: any[] } = {};
      Object.keys(allQuestionsData).forEach(stepId => {
        const stepQuestions = allQuestionsData[stepId] || [];
        organizedQuestionsData[stepId] = organizeQuestionsHierarchically(stepQuestions, stepId);
      });

      onDataChange({
        activeStep,
        completed, // Use the reactive completed state
        stepData: visibleSteps[activeStep],
        allQuestionsData: organizedQuestionsData,
        answersData: answersData // Add for checking answered status
      });
    }
  };

  useEffect(() => {
    if (steps.length === 0) return;

    // Helper function to count questions recursively (matches RequirementsSidebar logic exactly)
    const countQuestions = (questions: any[]): { total: number; answered: number } => {
      let total = 0;
      let answered = 0;

      questions.forEach(question => {
        // Skip counting repeating instances entirely to match sidebar display
        if (question.isRepeatingInstance) {
          return;
        }

        // Regular question
        total++;
        const answer = answersData[question.id];

        // FIXED: Properly validate if answer exists and has value (matches sidebar exactly)
        if (answer) {
          const actualValue = typeof answer === 'object' && answer.value !== undefined ? answer.value : answer;

          // MULTISELECT FIX: For multiselect, check CodeList instead of value
          const hasCodeList = typeof answer === 'object' && answer.CodeList !== null && answer.CodeList !== undefined && answer.CodeList.length > 0;
          const hasSelectedItems = hasCodeList && answer.CodeList[0]?.CodeListItem?.some((item: any) => item.IsEnabled);
          if ((actualValue !== '' && actualValue !== null && actualValue !== undefined) || hasSelectedItems) {
            answered++;
          }
        }
        // Count children recursively
        if (question.children && question.children.length > 0) {
          const childCounts = countQuestions(question.children);
          total += childCounts.total;
          answered += childCounts.answered;
        }
      });

      return { total, answered };
    };

    const newCompleted: { [k: number]: boolean } = {};
    let actualStepIndex = 0;

    visibleSteps.forEach((step) => {
      // Skip summary step but DON'T increment actualStepIndex
      if (step.id === 'summary') {
        return;
      }

      const stepQuestions = allQuestionsData[step.id] || [];
      const hierarchicalQuestions = organizeQuestionsHierarchically(stepQuestions, step.id);
      const counts = countQuestions(hierarchicalQuestions);
      const totalQuestions = counts.total;
      const answeredQuestions = counts.answered;
      const isComplete = totalQuestions > 0 && answeredQuestions === totalQuestions;
      newCompleted[actualStepIndex] = isComplete;
      actualStepIndex++; // Increment only for non-summary steps
    });

    setCompleted(newCompleted);
  }, [answersData, allQuestionsData, visibleSteps]);

  // This ensures Finalize page updates in real-time as user answers questions
  useEffect(() => {
    if (!onDataChange) return;

    // Only sync if we have actual question data loaded
    const hasQuestions = Object.keys(allQuestionsData).length > 0;
    if (!hasQuestions) {
      return;
    }

    // Organize all questions hierarchically to filter out hidden/conditional questions
    const organizedQuestionsData: { [stepId: string]: any[] } = {};
    Object.keys(allQuestionsData).forEach(stepId => {
      const stepQuestions = allQuestionsData[stepId] || [];
      organizedQuestionsData[stepId] = organizeQuestionsHierarchically(stepQuestions, stepId);
    });

    // Notify parent with current state
    onDataChange({
      activeStep,
      completed,
      stepData: visibleSteps[activeStep],
      itemGroupData: itemGroupData,
      tradeData: data,
      allQuestionsData: organizedQuestionsData,
      answersData: answersData
    });
  }, [answersData, allQuestionsData]);

  // Handle going back
  const handleBack = () => {
    setActiveStep((prevActiveStep) => prevActiveStep - 1);
    setAttemptedNext(false); // Reset validation attempt flag when going back
  };

  // ==================== NAVIGATION HELPERS ====================

  const showValidationAlert = (isSummaryValidation: boolean, explicitErrorCount?: number) => {
    const errorCount = explicitErrorCount !== undefined ? explicitErrorCount : Object.keys(validationErrors).length;
    const title = isSummaryValidation ? 'Missing Required Information' : 'Missing Required Information';
    const message = isSummaryValidation
      ? `Please complete ${errorCount} required ${errorCount === 1 ? 'question' : 'questions'} before continuing.\n\nLook for questions marked with *.`
      : `Please complete ${errorCount} required ${errorCount === 1 ? 'question' : 'questions'} on this page.\n\nLook for questions marked with *.`;

    setValidationWarningTitle(title);
    setValidationWarningMessage(message);
    setOpenValidationWarning(true);
  };


  const validateAndProceed = (currentStep: StepData | undefined, isNavigatingToSummary: boolean): boolean => {
    // Short-circuit validation when viewing Bid Details during Contract Approval
    const isContractApproval = typeof window !== 'undefined' ? sessionStorage.getItem('currentStatus') === 'Contract Approval' : false;
    if (isBidDetailsRoute(location.pathname) && isContractApproval) {
      return true;
    }

    setAttemptedNext(true);

    if (isNavigatingToSummary) {
      // Validate ALL steps when moving to summary
      const { isValid: allValid, totalCount } = validateAllSteps();
      if (!allValid) {
        showValidationAlert(true, totalCount);
        return false;
      }
    } else {
      // Validate only current step for regular navigation
      const errorCount = validateStep(currentStep?.id || '');
      if (errorCount > 0) {
        showValidationAlert(false, errorCount);
        return false;
      }
    }

    return true;
  };


  const saveDraftToBackend = async () => {
    // Guard: Check if autosave props are available
    if (!odmData || !odmVersion || !assetVersion || !odmProjectData ||
      !odmProjectVersion || !tradeVersion || !createBidFormRef ||
      !questionRendererRef || !userdata) {
      return;
    }

    try {
      setDraftError(null);

      // CRITICAL: Call CreateBidForm_v2 ref to populate odmData.current
      if (createBidFormRef?.current && typeof createBidFormRef.current.handleSaveFunctionality === 'function') {
        createBidFormRef.current.handleSaveFunctionality();
      }

      const buildingData = convertToODMFormat();

      if (buildingData) {

        // Analyze the ODM structure
        if (buildingData.EventData && buildingData.EventData[0]?.FormData && buildingData.EventData[0].FormData[0]?.ItemGroupData) {
          const itemGroupDataInODM = buildingData.EventData[0].FormData[0].ItemGroupData;

          let totalItems = 0;
          itemGroupDataInODM.forEach((group: any, index: number) => {
            const itemCount = group.ItemData?.length || 0;
            totalItems += itemCount;
          });
        }

        const dataStr = JSON.stringify(buildingData);
        const currentVersion = response?.data?.version || "1.0";
        const projectOIDString = tradeType || data?.tradeOid || data?.trade || "P.1";
        const tradeTypeString = tradeType || data?.tradeOid || data?.trade || "P.1";


        // Call parent callback to save to refs (CreateBidWizard will update odmProjectData.current)
        if (onSave && typeof onSave === 'function') {
          onSave(dataStr, tradeTypeString, currentVersion, projectOIDString);
        }
      } else {
        console.warn('⚠️ This might be because itemGroupData is empty or invalid');
      }


      // CRITICAL: Call QuestionRenderer ref to save asset questions
      if (questionRendererRef?.current && typeof questionRendererRef.current.handleSaveFunctionality === 'function') {
        questionRendererRef.current.handleSaveFunctionality();
      }

      // Helper function to format dates to ISO 8601
      const formatDateToISO = (dateValue: any) => {
        if (!dateValue) return null;

        try {
          if (dateValue instanceof Date) {
            return dateValue.toISOString();
          }

          if (typeof dateValue === 'string') {
            if (dateValue.includes('T') && dateValue.includes('Z')) {
              return dateValue;
            }
            const parsedDate = new Date(dateValue);
            if (!isNaN(parsedDate.getTime())) {
              return parsedDate.toISOString();
            }
          }

          if (typeof dateValue === 'number') {
            const parsedDate = new Date(dateValue);
            if (!isNaN(parsedDate.getTime())) {
              return parsedDate.toISOString();
            }
          }

          return null;
        } catch (error) {
          console.error('Error formatting date:', error);
          return null;
        }
      };

      // Parse version numbers
      const assetVersionNum = typeof assetVersion?.current === 'string'
        ? parseInt(assetVersion.current, 10)
        : assetVersion?.current;
      const tradeVersionNum = typeof tradeVersion?.current === 'string'
        ? parseInt(tradeVersion.current, 10)
        : tradeVersion?.current;

      // Read status from sessionStorage if available (for edit/resume scenarios)
      const sessionStatus = sessionStorage.getItem('currentstatus');

      // Prepare FULL payload (EXACTLY matching SelectTrade_v2 structure)
      const payload: any = {
        customerUid: data?.customerUid || '',
        assetUid: data?.assetUid || data?.asset || '',
        trade: data?.trade || data?.tradeName || tradeType || '',  // Trade NAME, not OID
        tradeOid: data?.tradeOid || tradeType || '',               // Trade OID
        tradeType: data?.trade || data?.tradeName || tradeType || '',
        status: sessionStatus || data?.status || 'draft', // 🔑 AUTOSAVE: Read from sessionStorage if available, preserve existing status, or default to 'draft' for new bids
        createdBy: (userdata?.firstname || '') + ' ' + (userdata?.lastname || ''),
        updatedBy: '',
        comment: '',
        description: '',
        jobDoneBy: data?.jobDoneBy || 'local',
        jobStartDate: formatDateToISO(data?.jobStartDate || data?.startDate),
        projectName: data?.projectName || data?.bidName || 'Untitled Project',
        constructionType: data?.constructionType || null,
        measurementType: data?.measurementType || null,
        answerData: [
          {
            tradeOid: odmVersion?.current || 'A.1',  // Asset template version OID
            questionTemplateVersion: assetVersionNum || null,
            answersData: odmData?.current || null
          },
          {
            tradeOid: odmProjectVersion?.current || data?.tradeOid || tradeType || '',  // Trade template version OID
            questionTemplateVersion: tradeVersionNum || null,
            answersData: odmProjectData?.current || null
          }
        ]
      };

      // 🆕 CRITICAL: Add bid ID from Continue button response for UPDATE operation
      if (data?.bid || data?.bidId) {
        payload.id = data.bid || data.bidId;
      } else {
        console.warn('⚠️ No bid ID found - this will be an INSERT operation');
      }

      // Ensure complete payload before autosave.
      // If payload is temporarily incomplete during UI transitions, skip autosave without raising critical error.
      try {
        const isPayloadComplete = await ensureCompletePayload(payload);
        if (!isPayloadComplete) {
          return;
        }
      } catch (ensureError: any) {
        return;
      }


      // Add sales field if available
      if (userdata?.objectId) {
        payload.sales = userdata.objectId;
      }

      // Add startDate if discussion date was selected
      if (data?.startDate) {
        payload.startDate = formatDateToISO(data.startDate);
      }

      try {
        if (odmData?.current) {
          const assetData = JSON.parse(odmData.current);
          if (assetData.EventData?.[0]?.FormData?.[0]?.ItemGroupData) {
            const assetItemGroups = assetData.EventData[0].FormData[0].ItemGroupData;
            assetItemGroups.forEach((group: any, idx: number) => {
            });
          }
        } else {
          console.warn('No asset data available');
        }
      } catch (e) {
        console.error('Could not parse asset data');
      }

      try {
        if (odmProjectData?.current) {
          const tradeData = JSON.parse(odmProjectData.current);
          if (tradeData.EventData?.[0]?.FormData?.[0]?.ItemGroupData) {
            const tradeItemGroups = tradeData.EventData[0].FormData[0].ItemGroupData;
            tradeItemGroups.forEach((group: any, idx: number) => {
              if (group.ParentItemGroupRepeatKey !== undefined) {
              }

              // Show first few items as sample
              if (group.ItemData && group.ItemData.length > 0) {
                group.ItemData.slice(0, 3).forEach((item: any, itemIdx: number) => {
                });
                if (group.ItemData.length > 3) {
                }
              }
            });
          }
        } else {
          console.warn('No requirements data available');
        }
      } catch (e) {
        console.error('Could not parse requirements data');
      }

      if (!isBidDetailsRoute(location.pathname)) {
        const validation = validateMandatoryFields(payload);

        if (!validation.isValid) {
          // Notify parent of error
          if (onAutosaveError) {
            onAutosaveError(`Missing required fields: ${validation.missingFields.join(', ')}`);
          }

          // Show error via the existing MUI validation-warning dialog (consistent
          // with validateAndProceed) instead of a thread-blocking browser alert().
          setValidationWarningTitle('Missing Required Information');
          setValidationWarningMessage(
            `Cannot save. Please fill in: ${validation.missingFields.join(', ')}`,
          );
          setOpenValidationWarning(true);

          return; // Stop execution - don't save or navigate
        }
      }


      // Call autoSaveBid API
      try {
        // Ensure questionTemplateVersion exists for each answerData entry
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
              } catch (e) {
                // ignore and leave null
              }
            }
          }
        };

        await ensureVersions(payload.answerData);

        const existingBidId = sessionStorage.getItem('currentBidId');
        if (existingBidId) {
          payload.id = parseInt(existingBidId, 10);
        }

        // Skip API call if answerData hasn't changed since the last successful save
        if (!hasAnswerDataChanged(payload, 'Requirements/saveDraftToBackend')) {
          return; // loader was never shown — no need to reset it
        }

        // Only show loading state now that we know the API will be called
        setSavingDraft(true);
        setDraftError(null);
        if (onAutosaveStart) {
          onAutosaveStart();
        }

        const result = await autoSaveBid(payload, accessToken);

        // If autosave was skipped by the service (asset switch/session clear), exit silently
        if (result?.skipped) {
          setSavingDraft(false);
          return;
        }

        // 🕐 Check for timestamps in response (DB returns these)
        if (result?.data && typeof result.data === 'object') {
        }

        handleUnauthorized(result);

        if (result?.status === 200 || result?.status === 201) {
          // 🆕 CALLBACK: Notify parent that save succeeded (show green checkmark)
          if (onAutosaveSuccess) {
            onAutosaveSuccess();
          }
          // Record new baseline for deduplication on the next save
          recordLastSavedAnswerData(payload);

          let projectUidValue: string | null = null;
          let numericId: number | null = null;

          // Extract UUID, numeric ID, and orderNo from response
          let orderNo: string | null = null;

          if (typeof result.data === 'object' && result.data !== null) {
            // Response is an object - look for UUID fields
            projectUidValue = result.data.bidOid || result.data.projectUid || result.data.uid;
            numericId = result.data.id || result.data.bidUid;
            orderNo = result.data.orderNO || result.data.orderNo;

          } else if (typeof result.data === 'number') {
            // Response is just a number - this is the numeric ID
            numericId = result.data;
            // Use the numeric ID as orderNo for version tracking (null-safe)
            orderNo = numericId?.toString() ?? null;
          }

          let versionKey = orderNo || (numericId ? numericId.toString() : null);

          if (!versionKey) {
            const existingOriginal = sessionStorage.getItem('createBid_originalOrderNo');
            if (!existingOriginal) {
              // First draft - create original ID
              versionKey = `session_draft_1`;
            } else {
              // Subsequent draft - increment counter
              const lastCounter = parseInt(sessionStorage.getItem('createBid_lastCounter') || '1');
              const nextCounter = lastCounter + 1;
              versionKey = `session_draft_${nextCounter}`;
              sessionStorage.setItem('createBid_lastCounter', nextCounter.toString());
            }
          }

          const bidUidToStore = projectUidValue || numericId;

          if (bidUidToStore) {
          } else {
            console.warn('⚠️ API response:', result.data);
          }

          lastSavedDataRef.current = { ...payload };

        } else {

          const errorMsg = 'Failed to save draft';
          setDraftError(errorMsg);

          // 🆕 CALLBACK: Notify parent that save failed (show red error icon)
          if (onAutosaveError) {
            onAutosaveError(errorMsg);
          }
        }

      } catch (error: any) {

        const errorMsg = error?.message || 'Failed to save draft';
        setDraftError(errorMsg);

        // 🆕 CALLBACK: Notify parent that save errored (show red error icon)
        if (onAutosaveError) {
          onAutosaveError(errorMsg);
        }
      } finally {
        setSavingDraft(false);
      }

    } catch (outerError: any) {
      const errorMsg = 'Critical error during autosave';
      setDraftError(errorMsg);
      setSavingDraft(false);

      // 🆕 CALLBACK: Notify parent of critical error (show red error icon)
      if (onAutosaveError) {
        onAutosaveError(errorMsg);
      }
    }
  };

  // Handle going to next step with validation
  const handleNext = async () => {
    const currentStep = visibleSteps[activeStep];
    const nextStepIndex = activeStep + 1;
    const isNavigatingToSummary = nextStepIndex === visibleSteps.length - 1;

    // Skip validation if already on summary
    if (currentStep?.id === 'summary') {
      if (activeStep < visibleSteps.length - 1) {
        setActiveStep(activeStep + 1);
        setAttemptedNext(false);
      }
      return;
    }

    // Validate before proceeding
    if (!validateAndProceed(currentStep, isNavigatingToSummary)) {
      return; // Validation failed, stop navigation
    }

    // Autosave for View Action flow
    if (isBidDetailsRoute(location.pathname)) {
      // Do NOT show loader yet — wait until we know the API will actually be called
      try {
        const storedBidDataString = sessionStorage.getItem('currentBidData');

        if (storedBidDataString) {
          const fullBidData = JSON.parse(storedBidDataString);

          // Generate fresh ODM data for trade questions
          const buildingData = convertToODMFormat();

          if (buildingData) {
            const freshTradeOdmString = JSON.stringify(buildingData);

            // Update the trade questions entry in answerData
            const updatedAnswerData = fullBidData.answerData?.map((answer: any) => {
              // Update trade questions entry (NOT "A.1" and NOT empty)
              if (answer.tradeOid && answer.tradeOid !== "A.1" && answer.tradeOid !== "") {
                return {
                  ...answer,
                  answersData: freshTradeOdmString,
                  updatedDate: new Date().toISOString()
                };
              }
              // Keep asset data as-is
              return answer;
            }) || [];

            const payload = stripSnapshotStatus({
              ...fullBidData,
              answerData: updatedAnswerData
            });

            const isPayloadComplete = await ensureCompletePayload(payload);

            if (!isPayloadComplete) {
              return;
            }


            const { autoSaveBid } = await import('../../../services/BidService');

            // Skip API call if answerData hasn't changed since the last successful save
            if (!hasAnswerDataChanged(payload, 'Requirements/handleNext')) {
            } else {
              // Only show loader when we are actually going to call the API
              setSavingDraft(true);
              // Allow React to render the loading state before the network call
              await new Promise(resolve => setTimeout(resolve, 0));
              try {
                const response = await autoSaveBid(payload, accessToken);
                // Record baseline only on a successful save
                if (response?.status === 200 || response?.status === 201) {
                  recordLastSavedAnswerData(payload);
                }
              } finally {
                setSavingDraft(false);
              }
            }

          }
        }

      } catch (error: any) {
        console.error('error', error?.message);
        setSavingDraft(false);
      }
    } else {
      // Normal draft save for create bid flow
      await saveDraftToBackend();
    }

    // Navigation successful - clear errors and move forward
    setValidationErrors({});
    setAttemptedNext(false);

    if (activeStep < visibleSteps.length - 1) {
      setActiveStep(activeStep + 1);
    }
  };

  // Handle reset
  const handleReset = () => {
    setActiveStep(0);
    setCompleted({});
  };

  // Check if all steps are completed
  const allStepsCompleted = () => {
    return visibleSteps.length > 0 && visibleSteps.every((_, index) => completed[index]);
  };

  const cleanupDependentAnswers = React.useCallback((changedItemOID: string, newValue: any, incomingData?: any) => {

    // Extract instance suffix from question ID (e.g., "I.38_1" → baseOID: "I.38", suffix: "_1")
    const suffixMatch = changedItemOID.match(/^(I\.\d+)(_\d+(?:_\d+)?)$/);
    const baseItemOID = suffixMatch ? suffixMatch[1] : changedItemOID;
    const instanceSuffix = suffixMatch ? suffixMatch[2] : '';


    const currentAnswer = answersData[changedItemOID];
    const currentValue = currentAnswer?.value;

    const isMultiselect = currentAnswer?.dataType === 'multiselect';
    const isSingleselect = currentAnswer?.dataType === 'singleselect';
    const usesCodeList = (isMultiselect || isSingleselect) && currentAnswer?.CodeList !== null && currentAnswer?.CodeList !== undefined;


    let newCodeList: any = null;
    if (usesCodeList) {
      if (incomingData?.ItemGroup && Array.isArray(incomingData.ItemGroup)) {
        for (const group of incomingData.ItemGroup) {
          const itemData = group.ItemData?.find((item: any) => item.ItemOID === changedItemOID);
          if (itemData?.CodeList) {
            newCodeList = itemData.CodeList;
            break;
          }
        }
      } else if (incomingData?.CodeList) {
        newCodeList = incomingData.CodeList;
      }
    }

    if (usesCodeList) {
      const oldCodeList = currentAnswer.CodeList;

      if (oldCodeList && newCodeList) {
        const oldSelected = (oldCodeList as any)[0]?.CodeListItem?.filter((item: any) => item.IsEnabled).map((item: any) => item.CodedValue).sort() || [];
        const newSelected = (newCodeList as any)[0]?.CodeListItem?.filter((item: any) => item.IsEnabled).map((item: any) => item.CodedValue).sort() || [];

        // REMOVED: if (oldSelected.length === 0) { return; }
        // This early return was blocking branch cleanup when the old selection
        // was empty but branch OIDs still needed clearing.

        if (JSON.stringify(oldSelected) === JSON.stringify(newSelected)) {
          return;
        }
      }
    } else {
      if (currentValue !== undefined && currentValue === newValue) {
        return;
      }
    }
    const mergedForCleanup = {
      ...answersData,
      [changedItemOID]: {
        ...currentAnswer,
        CodeList: newCodeList || currentAnswer?.CodeList,
        value: newValue
      }
    };

    let dependentsToClean: string[] = [];

    if (dependencyGraphParserRef.current) {
      if (instanceSuffix) {
        dependentsToClean = dependencyGraphParserRef.current.getCleanupTargetsWithInstance(
          baseItemOID,
          instanceSuffix,
          newValue,
          mergedForCleanup
        );
      } else {
        dependentsToClean = dependencyGraphParserRef.current.getCleanupTargets(
          changedItemOID,
          newValue,
          mergedForCleanup
        );
      }
    } else {
      throw new Error('DependencyGraphParser not initialized - cannot process dependencies');
    }

    const branchCleanupOIDs = getItemOIDsHiddenAfterBranchChange(
      steps,
      conditionDef || [],
      answersData,
      mergedForCleanup
    );
    dependentsToClean = Array.from(new Set([...dependentsToClean, ...branchCleanupOIDs]));

    if (dependentsToClean.length > 0) {
      dependentsToClean.forEach(oid => recentlyCleanedItemsRef.current.add(oid));
    }

    if (dependentsToClean.length > 0) {
      dependentsToClean.forEach(oid => recentlyCleanedItemsRef.current.add(oid));

      // BRANCH-RESET FIX: extend TTL when branch groups are being cleared
      if (branchCleanupOIDs.length > 0) {
        if (cleanupClearTimeoutRef.current) clearTimeout(cleanupClearTimeoutRef.current);
        cleanupClearTimeoutRef.current = setTimeout(() => {
          recentlyCleanedItemsRef.current.clear();
          cleanupClearTimeoutRef.current = null;
        }, 6000);
      }

      setAnswersData(prev => {
        const cleaned = { ...prev };

        dependentsToClean.forEach(dependentOID => {
          const keysToDelete = Object.keys(cleaned).filter(key =>
            key === dependentOID || key.startsWith(`${dependentOID}_`)
          );

          keysToDelete.forEach(key => {
            delete cleaned[key];
          });
        });

        return cleaned;
      });

      setItemGroupData(prev => {
        const cleaned = updateItemGroupDataAfterCleanupUtil(prev, dependentsToClean);
        return cleaned;
      });

      setConditionalQuestionVisibility(prev => {
        const updated = { ...prev };
        dependentsToClean.forEach(questionOID => {
          Object.values(allQuestionsData).forEach(stepQuestions => {
            stepQuestions.forEach((question: any) => {
              if (question.id === questionOID && question.conditionOID) {
                updated[question.conditionOID] = false;
              }
            });
          });
        });
        return updated;
      });

    }

    // Use the enhanced cleanup utility for comprehensive dependent answer cleanup
    const cleanupConfig: DependentCleanupConfig = {
      conditionDef: conditionDef || [],
      itemGroupDefs: steps.filter(s => s.itemGroupDef).map(s => s.itemGroupDef as ItemGroupDef),
      allQuestionsData: allQuestionsData
    };

    // Clear dependent answers using the new utility
    const { updatedAnswersData, cleanupResult } = clearDependentAnswersUtil(
      answersData,
      changedItemOID,
      cleanupConfig
    );

    // Apply the cleaned answers data
    if (cleanupResult.clearedAnswers.length > 0) {
      setAnswersData(updatedAnswersData);

      // Update ItemGroupData to match the cleanup
      setItemGroupData(prev => {
        const updated = updateItemGroupDataAfterCleanupUtil(prev, cleanupResult.clearedAnswers);
        return updated;
      });

      // Hide cleaned questions from sidebar
      setConditionalQuestionVisibility(prev => {
        const updated = { ...prev };
        cleanupResult.clearedAnswers.forEach(questionOID => {
          // Find all conditions that contain this question
          Object.values(allQuestionsData).forEach(stepQuestions => {
            stepQuestions.forEach(question => {
              if (question.id === questionOID && question.conditionOID) {
                updated[question.conditionOID] = false;
              }
            });
          });
        });
        return updated;
      });

    }

    // Handle instance group cleanup for multi-instance questions
    const instanceGroupsToClean: string[] = [];

    // Check if the changed item itself is a repeat count question
    const isChangedItemRepeatCount = mappings.some((m: any) => m.controlItemOID === changedItemOID);
    const newValueNum = parseInt(newValue);
    const oldValue = answersData[changedItemOID]?.value;
    const oldValueNum = parseInt(oldValue);

    const isClearing = newValue === null || newValue === undefined || newValue === '' || newValue === '0' || newValue === 0;
    const isReducing = !isNaN(newValueNum) && !isNaN(oldValueNum) && newValueNum < oldValueNum;

    if (isChangedItemRepeatCount && (isClearing || isReducing)) {
      // Clearing or reducing repeat count - clean affected instances
      const mapping = mappings.find((m: any) => m.controlItemOID === changedItemOID);
      if (mapping) {
        const groupOID = (mapping as any).repeatingGroupOID;
        instanceGroupsToClean.push(groupOID);

        // If reducing (not clearing), store the target count
        if (isReducing && !isClearing) {
          (mapping as any)._keepUpTo = newValueNum;
        }
      }
    }

    if (instanceGroupsToClean.length === 0) {
      return;
    }

    if (instanceGroupsToClean.length > 0) {

      // Clean instance answers from answersData
      setAnswersData(prev => {
        const cleaned = { ...prev };
        let cleanedCount = 0;

        const itemsBeingDeleted = new Set<string>();

        // First pass: identify all items that should be deleted based on instanceGroupsToClean
        Object.keys(cleaned).forEach(key => {
          if (key.includes('_')) {
            const parts = key.split('_');
            const instanceNum = parseInt(parts[1]);
            const baseKey = parts[0];

            // Check if this item belongs to a group being cleaned
            const itemDefEntry = itemDef?.find((def: any) => def.OID === baseKey);
            if (itemDefEntry) {
              const itemGroupOID = itemGroup?.find((ig: any) =>
                ig.ItemRef?.some((ref: any) => ref.ItemOID === baseKey)
              )?.OID;

              if (itemGroupOID && instanceGroupsToClean.includes(itemGroupOID)) {
                // Check if there's a keepUpTo limit
                const mapping = mappings.find((m: any) => (m as any)._keepUpTo !== undefined);
                const keepUpTo = mapping ? (mapping as any)._keepUpTo : undefined;

                if (keepUpTo !== undefined) {
                  if (instanceNum > keepUpTo) {
                    itemsBeingDeleted.add(key);
                  }
                } else {
                  // No limit, delete all instances in this group
                  itemsBeingDeleted.add(key);
                }
              }
            }
          }
        });

        // Second pass: cascade deletions to nested children
        // If a parent instance is deleted (e.g., I.35_1), delete its nested children (e.g., I.24_1_1)
        Object.keys(cleaned).forEach(key => {
          const parts = key.split('_');

          // Check for nested instances (3 parts: I.24_1_1)
          if (parts.length === 3) {
            const parentInstanceKey = parseInt(parts[1]); // Middle number
            const answerData = cleaned[key];

            // Check if this nested item's parent is being deleted
            if (answerData?.originalItemData?.ParentItemGroupRepeatKey !== undefined) {
              const parentRepeatKey = answerData.originalItemData.ParentItemGroupRepeatKey;

              // Find the parent ItemGroup that this item's parent belongs to
              const baseKey = parts[0];
              const itemDefEntry = itemDef?.find((def: any) => def.OID === baseKey);

              if (itemDefEntry) {
                const childGroupOID = itemGroup?.find((ig: any) =>
                  ig.ItemRef?.some((ref: any) => ref.ItemOID === baseKey)
                )?.OID;

                // Find the parent group by looking for groups that can have this child group as nested
                const parentGroupOID = itemGroup?.find((ig: any) => {
                  // Check if any item in the parent group is a RepeatCount control for child group
                  return mappings?.some((m: any) =>
                    m.repeatingGroupOID === childGroupOID &&
                    ig.ItemRef?.some((ref: any) => ref.ItemOID === m.controlItemOID)
                  );
                })?.OID;

                if (parentGroupOID) {
                  // Check if ANY item in the parent group with matching RepeatKey is being deleted
                  const parentItemsInSameInstance = Object.keys(cleaned).filter(k => {
                    const kParts = k.split('_');
                    if (kParts.length !== 2) return false; // Only single-level parents

                    const kInstanceNum = parseInt(kParts[1]);
                    const kBaseKey = kParts[0];

                    // Check if this item belongs to the parent group and has the same instance number
                    if (kInstanceNum === parentRepeatKey) {
                      const kItemDef = itemDef?.find((def: any) => def.OID === kBaseKey);
                      if (kItemDef) {
                        const kGroupOID = itemGroup?.find((ig: any) =>
                          ig.ItemRef?.some((ref: any) => ref.ItemOID === kBaseKey)
                        )?.OID;
                        return kGroupOID === parentGroupOID;
                      }
                    }
                    return false;
                  });

                  // If ANY parent item in this instance is being deleted, delete this nested child
                  const anyParentBeingDeleted = parentItemsInSameInstance.some(parentKey =>
                    itemsBeingDeleted.has(parentKey)
                  );

                  if (anyParentBeingDeleted) {
                    itemsBeingDeleted.add(key);
                  }
                }
              }
            }
          }
        });

        // Actually delete all items marked for deletion
        itemsBeingDeleted.forEach(key => {
          delete cleaned[key];
          cleanedCount++;
        });

        return cleaned;
      });

      // Clean instance groups from itemGroupData
      setItemGroupData(prev => {
        const cleaned = prev.filter(group => {
          // Check if this group should be removed
          if (group.ItemGroupRepeatKey > 0 && instanceGroupsToClean.includes(group.ItemGroupOID)) {
            // Check if there's a "_keepUpTo" limit for this group
            const mapping = mappings.find((m: any) => (m as any).repeatingGroupOID === group.ItemGroupOID);
            const keepUpTo = mapping ? (mapping as any)._keepUpTo : undefined;

            if (keepUpTo !== undefined && group.ItemGroupRepeatKey <= keepUpTo) {
              return true;
            } else {
              return false;
            }
          }
          return true;
        });

        return cleaned;
      });
    }
  }, [steps, conditionDef, allQuestionsData, mappings, answersData, setAnswersData, setItemGroupData]);


  // Sync answersDataRef whenever answersData changes
  useEffect(() => {
    if (recentlyCleanedItemsRef.current.size > 0) {
      return;
    }
    answersDataRef.current = answersData;
  }, [answersData]);

  // Keep current template/answers in refs so the applyBidAnswers handler (bound in a
  // useEffect whose closure can be stale) always reads the CURRENT template + data when
  // computing the structure guard — otherwise a select fired before the closure refreshes
  // would see an empty template and skip nothing.
  const itemGroupRef = useRef(itemGroup);
  const conditionDefRef = useRef(conditionDef);
  const itemGroupDataRef = useRef(itemGroupData);
  const formDefRef = useRef(formDef);
  const methodDefRef = useRef(methodDef);
  useEffect(() => { itemGroupRef.current = itemGroup; }, [itemGroup]);
  useEffect(() => { conditionDefRef.current = conditionDef; }, [conditionDef]);
  useEffect(() => { itemGroupDataRef.current = itemGroupData; }, [itemGroupData]);
  useEffect(() => { formDefRef.current = formDef; }, [formDef]);
  useEffect(() => { methodDefRef.current = methodDef; }, [methodDef]);

  // FIX: Corrective check - if state has wrong value compared to ref for recently cleaned items, correct it
  // This handles the race condition where another setAnswersData overwrites our cleanup
  useEffect(() => {
    if (recentlyCleanedItemsRef.current.size > 0) {
      let needsCorrection = false;
      const corrections: { [key: string]: any } = {};

      recentlyCleanedItemsRef.current.forEach((key: string) => {
        const stateValue = answersData[key]?.value;
        const refValue = answersDataRef.current[key]?.value;

        // If state has non-empty but ref has empty, state is wrong
        if (stateValue && stateValue !== '' && refValue === '') {
          needsCorrection = true;
          // Copy from ref (which has correct value)
          corrections[key] = { ...answersDataRef.current[key] };
        }
      });

      if (needsCorrection) {
        setAnswersData(prev => ({
          ...prev,
          ...corrections
        }));
      }
    }
  }, [answersData]);

  // Handle ItemGroup data exactly like original QuestionRendererCard.tsx
  const isProcessingDataChangeRef = React.useRef(false);
  // Queue for calls that arrive while mutex is held (e.g. auto-calc within 100ms of user input)
  const pendingDataChangeRef = React.useRef<any>(null);
  const handleItemGroupDataChangeRef = React.useRef<(data: any) => void>();
  const handleItemGroupDataChange = React.useCallback((data: any) => {

    if (isProcessingDataChangeRef.current) {
      // Don't silently drop — queue so calculated values are not lost
      pendingDataChangeRef.current = data;
      return;
    }

    isProcessingDataChangeRef.current = true;


    try {
      const incomingSummary = (data && data.ItemGroup && Array.isArray(data.ItemGroup))
        ? data.ItemGroup.map((g: any) => ({
          ItemGroupOID: g.ItemGroupOID,
          ItemGroupRepeatKey: g.ItemGroupRepeatKey,
          ParentItemGroupRepeatKey: g.ParentItemGroupRepeatKey,
          ItemDataCount: g.ItemData?.length || 0,
          Items: g.ItemData?.map((item: any) => ({ ItemOID: item.ItemOID, Value: item.Value, hasCodeList: !!item.CodeList })) || []
        }))
        : data && data.ItemGroup ? data.ItemGroup : 'NO_ITEMGROUP';

    } catch (e) {
      console.error('[REQUIREMENTS_RECEIVED] Failed to create summary:', e);
    }
    try {
      const answersData = answersDataRef.current;

      const batchedAnswersUpdates: { [key: string]: any } = {};
      const keysToDelete: string[] = [];

      const derivedItemOIDsSet = new Set<string>();
      (itemGroup || []).forEach((ig: any) => {
        (ig.ItemRef || []).forEach((ir: any) => {
          if (ir.MethodOID) {
            derivedItemOIDsSet.add(ir.ItemOID as string);
          }
        });
      });

      // BRANCH-EXCLUDED OID SET
      const branchExcludedOIDs = new Set<string>();
      {
        const mergedForBranchCheck: Record<string, any> = { ...answersDataRef.current };
        if (data.ItemGroup && Array.isArray(data.ItemGroup)) {
          data.ItemGroup.forEach((g: any) => {
            (g.ItemData || []).forEach((item: any) => {
              const k = (g.ItemGroupRepeatKey ?? 0) > 0
                ? `${item.ItemOID}_${g.ItemGroupRepeatKey}`
                : item.ItemOID;
              mergedForBranchCheck[k] = { value: item.Value, CodeList: item.CodeList };
            });
          });
        } else if (data.ItemOID) {
          mergedForBranchCheck[data.ItemOID] = { value: data.Value, CodeList: data.CodeList };
        }

        steps.forEach((step: any) => {
          if (step.id === 'summary' || !step.collectionExceptionConditionOID || !step.itemGroupDef) return;
          const cond = conditionDef?.find((cd: any) => cd.OID === step.collectionExceptionConditionOID);
          const expr = cond?.FormalExpression?.text;
          if (isCollectionExcludedByFormalExpression(expr, mergedForBranchCheck)) {
            (step.itemGroupDef.ItemRef || []).forEach((ref: any) => branchExcludedOIDs.add(ref.ItemOID));
          }
        });
      }

      if (data.ItemGroup && Array.isArray(data.ItemGroup)) {
        isApplyingSuffixSyncRef.current = true;
        try { SaveGate.start(); } catch (e) { /* defensive */ }
        setIsAnswersDataStable(false);
        const tempAnswersData = { ...answersDataRef.current };
        data.ItemGroup.forEach((group: any) => {
          if (group.ItemGroupRepeatKey >= 0 && group.ItemData && Array.isArray(group.ItemData)) {
            group.ItemData.forEach((item: any) => {
              // FIX: Include ALL items with CodeList (even when no selections) so condition evaluation sees unchecks
              const hasCodeList = item.CodeList && item.CodeList.length > 0;
              const hasCodeListWithSelection = hasCodeList && item.CodeList[0]?.CodeListItem?.some((cli: any) => cli.IsEnabled);
              if ((item.Value && item.Value !== '') || hasCodeListWithSelection || hasCodeList) {
                const instanceSuffix = group.ItemGroupRepeatKey === 0 ? '' : `_${group.ItemGroupRepeatKey}`;
                const answerKey = `${item.ItemOID}${instanceSuffix}`;
                tempAnswersData[answerKey] = {
                  value: item.Value,
                  itemOID: item.ItemOID,
                  itemGroupOID: group.ItemGroupOID,
                  repeatKey: group.ItemGroupRepeatKey || 0,
                  dataType: 'singleselect',
                  codeListItem: null,
                  isNull: false,
                  ...(item.CodeList && item.CodeList.length > 0 ? { CodeList: item.CodeList } : {})
                };
              }
            });
          }
        });

        // Now process items and check conditions using tempAnswersData
        data.ItemGroup.forEach((group: any) => {
          if (group.ItemGroupRepeatKey >= 0 && group.ItemData && Array.isArray(group.ItemData)) {
            group.ItemData.forEach((item: any) => {
              const instanceSuffix = group.ItemGroupRepeatKey === 0 ? '' : `_${group.ItemGroupRepeatKey}`;
              const answerKey = `${item.ItemOID}${instanceSuffix}`;


              // This handles integers, text, etc. being cleared by user
              if (!item.Value || item.Value === '') {

                // Check if this is a select/multiselect that uses CodeList
                const itemDefEntry = itemDef.find((def: any) => def.OID === item.ItemOID);
                const dataType = itemDefEntry?.DataType || 'singleselect';
                const isSelectType = dataType === 'singleselect' || dataType === 'multiselect';
                const isBooleanType = dataType === 'boolean';

                // FIX: For boolean questions, when deselected (empty), KEEP the answer with empty value
                // This ensures cascade cleanup only deletes dependents, not the question itself
                if (isBooleanType) {
                  // FIX: Add to recentlyCleanedItemsRef so other pending setAnswersData callbacks
                  // won't overwrite this with a stale "Yes" value
                  recentlyCleanedItemsRef.current.add(answerKey);
                  // Add to batchedAnswersUpdates with empty value so it's protected from deletion
                  batchedAnswersUpdates[answerKey] = {
                    value: '',
                    itemOID: item.ItemOID,
                    itemGroupOID: group.ItemGroupOID,
                    repeatKey: group.ItemGroupRepeatKey || 0,
                    dataType: dataType,
                    codeListItem: null,
                    isNull: false,
                    ...(item.CodeList && item.CodeList.length > 0 ? { CodeList: item.CodeList } : {})
                  };
                  return; // Don't delete the boolean question
                }

                // For select types with CodeList, keep the answer (CodeList shows state)
                // For other types (integer, text, etc.), delete the answer when empty
                if (!isSelectType) {
                  const currentAnswersData = answersDataRef.current;
                  if (currentAnswersData[answerKey]) {
                    const allMappings = [
                      ...(repeatingGroupMappings || []),
                      ...(mappings || []).filter((m: any) =>
                        m.controlItemOID &&
                        !(repeatingGroupMappings || []).some((rm: any) => rm.controlItemOID === m.controlItemOID)
                      )
                    ];

                    const isRepeatCountControl = allMappings?.some((m: any) => m.controlItemOID === answerKey || m.controlItemOID === item.ItemOID);
                    if (isRepeatCountControl) {
                      const mapping = allMappings?.find((m: any) => m.controlItemOID === answerKey || m.controlItemOID === item.ItemOID);
                      if (mapping) {
                        const repeatingGroupOID = (mapping as any).repeatingGroupOID;
                        const oldCount = parseInt(currentAnswersData[answerKey]?.value) || 0;

                        // Collect keys to delete but DON'T delete yet
                        const pendingKeysToDelete: string[] = [];
                        const deletedInstanceKeys = new Set<number>();

                        // Find all items in answersData that belong to this repeating group
                        Object.keys(currentAnswersData).forEach(key => {
                          const keyParts = key.split('_');
                          if (keyParts.length >= 2) { // Has instance suffix
                            const baseKey = keyParts[0];
                            const itemDefEntry = itemDef?.find((def: any) => def.OID === baseKey);
                            if (itemDefEntry) {
                              const itemGroupOID = itemGroup?.find((ig: any) =>
                                ig.ItemRef?.some((ref: any) => ref.ItemOID === baseKey)
                              )?.OID;

                              if (itemGroupOID === repeatingGroupOID) {
                                if (!pendingKeysToDelete.includes(key)) {
                                  pendingKeysToDelete.push(key);
                                  const instanceKey = parseInt(keyParts[1]);
                                  if (!isNaN(instanceKey)) {
                                    deletedInstanceKeys.add(instanceKey);
                                  }
                                }
                              }
                            }
                          }
                        });

                        // Find ALL nested children whose ParentItemGroupRepeatKey matches any deleted instance
                        Object.keys(currentAnswersData).forEach(key => {
                          const keyParts = key.split('_');
                          if (keyParts.length === 3) {
                            const parentRepeatKey = parseInt(keyParts[1]);
                            if (!isNaN(parentRepeatKey) && deletedInstanceKeys.has(parentRepeatKey)) {
                              if (!pendingKeysToDelete.includes(key)) {
                                pendingKeysToDelete.push(key);
                              }
                            }
                          }
                        });

                        // Add the control item itself
                        pendingKeysToDelete.push(answerKey);

                        // Generate instances to delete array - include ALL instances from 1 to oldCount
                        // (not just those found in answersData, because some instances might be empty)
                        const instancesToDelete = Array.from({ length: oldCount }, (_, i) => oldCount - i);

                        setDeletionConfirmation({
                          show: true,
                          controlItemOID: answerKey,
                          newCount: 0,
                          oldCount: oldCount,
                          repeatingGroupOID: repeatingGroupOID,
                          instancesToDelete: instancesToDelete,
                          keysToDelete: pendingKeysToDelete,
                          originalValue: currentAnswersData[answerKey]?.value,
                          originalAnswersData: { ...currentAnswersData } // Snapshot for cancel restore
                        });

                        // Return early - don't delete anything yet!
                        // The dialog onConfirm will handle the actual deletion
                        return;
                      }
                    }

                    // For non-control items, proceed with normal deletion.
                    // If the entry carries _crossLinkedFromAsset, preserve a tombstone
                    // instead of fully deleting it. A hard delete strips the marker so
                    // wasCrossLinked becomes false and auto-fill re-applies on next render.
                    const existingAnswer = currentAnswersData[answerKey];
                    const isEagleViewPrePopulated = existingAnswer?.isEagleViewPrePopulated === true;
                    if (!isEagleViewPrePopulated) {
                      if (existingAnswer?._crossLinkedFromAsset) {
                        batchedAnswersUpdates[answerKey] = {
                          ...existingAnswer,
                          value: '',
                          _crossLinkedFromAsset: true,
                        };
                      } else {
                        keysToDelete.push(answerKey);
                      }
                    }

                  }
                  return; // Skip further processing for cleared non-select fields

                }

                // For select types, check if there's a valid CodeList
                if (!item.CodeList || item.CodeList.length === 0) {

                  if (answersData[answerKey]) {
                    const existingForSelect = answersData[answerKey];
                    if (existingForSelect?._crossLinkedFromAsset) {
                      batchedAnswersUpdates[answerKey] = {
                        ...existingForSelect,
                        value: '',
                        _crossLinkedFromAsset: true,
                      };
                    } else {
                      keysToDelete.push(answerKey);
                    }
                  }

                  // Check conditional visibility
                  if (dependencyGraphParserRef.current) {
                    try {
                      const shouldShow = dependencyGraphParserRef.current.evaluateConditionWithInstance(
                        item.ItemOID,
                        tempAnswersData,
                        instanceSuffix
                      );

                      if (!shouldShow && answersData[answerKey] && !keysToDelete.includes(answerKey)) {
                        keysToDelete.push(answerKey);
                      }
                    } catch (error) {
                      // Ignore condition evaluation errors
                    }
                  }
                  return;
                }
              }

              const hasExplicitSelection = item.CodeList && item.CodeList.length > 0 && item.CodeList[0]?.CodeListItem?.some((cli: any) => cli.IsEnabled);
              const isNewItem = !answersData[answerKey];
              const skipConditionCheck = hasExplicitSelection && isNewItem;
              const isItemDerived = derivedItemOIDsSet.has(item.ItemOID);
              if (dependencyGraphParserRef.current && !skipConditionCheck && !isItemDerived) {
                try {
                  const shouldShow = dependencyGraphParserRef.current.evaluateConditionWithInstance(
                    item.ItemOID,
                    tempAnswersData,
                    instanceSuffix
                  );

                  if (!shouldShow) {
                    // Also DELETE the answer if it exists in answersData
                    if (answersData[answerKey]) {
                      keysToDelete.push(answerKey);
                    }

                    return;
                  }
                } catch (error) {
                  // No condition or error evaluating - allow the item
                }
              }

              // Preserve CodeList structure for multiselect questions
              const itemDefEntry = itemDef.find((def: any) => def.OID === item.ItemOID);
              const dataType = itemDefEntry?.DataType || 'singleselect';

              if (!isItemDerived && recentlyCleanedItemsRef.current.has(answerKey)) {
                const hasValue = item.Value && item.Value !== '';
                const hasEnabledCodeListItem = item.CodeList && item.CodeList.length > 0 &&
                  item.CodeList[0]?.CodeListItem?.some((cli: any) => cli.IsEnabled === true);

                if (!hasValue && !hasEnabledCodeListItem) {
                  return;
                } else {
                  recentlyCleanedItemsRef.current.delete(answerKey);
                }
              }

              // BRANCH-EXCLUDED GUARD
              if (branchExcludedOIDs.has(item.ItemOID)) {
                recentlyCleanedItemsRef.current.add(answerKey);
                if (!keysToDelete.includes(answerKey)) keysToDelete.push(answerKey);
                return;
              }

              const currentRefValue = answersDataRef.current[answerKey]?.value;
              const templateHasValue = item.Value && item.Value !== '';
              if (!isItemDerived && currentRefValue === '' && templateHasValue && !hasExplicitSelection) {
                return;
              }

              // Collect updates instead of immediate state update
              const existingEntry = answersDataRef.current[answerKey];
              batchedAnswersUpdates[answerKey] = {
                ...(isItemDerived && existingEntry ? existingEntry : {}),
                value: item.Value || '',
                itemOID: item.ItemOID,
                itemGroupOID: group.ItemGroupOID,
                repeatKey: group.ItemGroupRepeatKey || 0,
                dataType: dataType,
                codeListItem: null,
                isNull: false,
                timestamp: Date.now(),
                originalItemData: {
                  ItemOID: item.ItemOID,
                  Value: item.Value || '',
                  ItemGroupRepeatKey: group.ItemGroupRepeatKey || 0
                },
                ...(item.CodeList && item.CodeList.length > 0 ? { CodeList: item.CodeList } : {})
              };
            });
          }
        });

        if (dependencyGraphParserRef.current) {
          const mergedAnswersForCondition: Record<string, any> = {};
          Object.entries(answersData).forEach(([key, data]) => {
            if (data && typeof data === 'object') {
              mergedAnswersForCondition[key] = { ...(data as Record<string, any>) };
            }
          });
          Object.entries(batchedAnswersUpdates).forEach(([key, data]) => {
            if (data && typeof data === 'object') {
              mergedAnswersForCondition[key] = { ...(data as Record<string, any>) };
            }
          });



          // Check each existing item's condition
          // FIX: Also check items in batchedAnswersUpdates - they may need to be deleted if parent conditions change
          const allItemsToCheck = new Set([...Object.keys(answersData), ...Object.keys(batchedAnswersUpdates)]);
          allItemsToCheck.forEach(existingKey => {
            // Skip if already marked for deletion
            if (keysToDelete.includes(existingKey)) return;

            let itemOID = existingKey;
            let instanceSuffix = '';
            if (existingKey.includes('_')) {
              const parts = existingKey.split('_');
              itemOID = parts[0];
              instanceSuffix = `_${parts[1]}`;
            }

            if (derivedItemOIDsSet.has(itemOID)) return;

            try {
              const shouldShow = dependencyGraphParserRef.current!.evaluateConditionWithInstance(
                itemOID,
                mergedAnswersForCondition,
                instanceSuffix
              );



              if (!shouldShow) {

                keysToDelete.push(existingKey);
              }
            } catch (error) {

              // No condition or error - keep the item
            }
          });


          let previousDeleteCount = 0;
          let iterationCount = 0;
          const maxIterations = 10; // Prevent infinite loops

          while (keysToDelete.length > previousDeleteCount && iterationCount < maxIterations) {
            previousDeleteCount = keysToDelete.length;
            iterationCount++;

            // For each key marked for deletion, find its dependent children
            const currentDeleteList = [...keysToDelete];
            for (const deletedKey of currentDeleteList) {
              let deletedItemOID = deletedKey;
              let deletedInstanceSuffix = '';
              if (deletedKey.includes('_')) {
                const parts = deletedKey.split('_');
                deletedItemOID = parts[0];
                deletedInstanceSuffix = `_${parts[1]}`;
              }

              // Simulate the deleted item having no value to find children that should also be deleted
              const simulatedAnswers = { ...mergedAnswersForCondition };
              // Remove the deleted item's answer to simulate it being gone
              delete simulatedAnswers[deletedKey];



              // Check all remaining answers to see if any depend on the deleted item
              Object.keys(answersData).forEach(existingKey => {
                // Skip if already marked for deletion
                if (keysToDelete.includes(existingKey)) return;

                let itemOID = existingKey;
                let instanceSuffix = '';
                if (existingKey.includes('_')) {
                  const parts = existingKey.split('_');
                  itemOID = parts[0];
                  instanceSuffix = `_${parts[1]}`;
                }

                if (derivedItemOIDsSet.has(itemOID)) return;

                try {
                  const shouldShow = dependencyGraphParserRef.current!.evaluateConditionWithInstance(
                    itemOID,
                    simulatedAnswers,
                    instanceSuffix
                  );

                  if (!shouldShow) {
                    keysToDelete.push(existingKey);
                    // Also remove from simulatedAnswers for next cascade check
                    delete simulatedAnswers[existingKey];
                  }
                } catch (error) {
                  // No condition or error - keep the item
                }
              });

              // Also check batchedAnswersUpdates for items that depend on deleted items
              Object.keys(batchedAnswersUpdates).forEach(updateKey => {
                // Skip if already marked for deletion
                if (keysToDelete.includes(updateKey)) return;

                let itemOID = updateKey;
                let instanceSuffix = '';
                if (updateKey.includes('_')) {
                  const parts = updateKey.split('_');
                  itemOID = parts[0];
                  instanceSuffix = `_${parts[1]}`;
                }

                if (derivedItemOIDsSet.has(itemOID)) return;

                try {
                  const shouldShow = dependencyGraphParserRef.current!.evaluateConditionWithInstance(
                    itemOID,
                    simulatedAnswers,
                    instanceSuffix
                  );



                  if (!shouldShow) {

                    keysToDelete.push(updateKey);
                    delete batchedAnswersUpdates[updateKey];
                  }
                } catch (error) {
                  // No condition or error - keep the item
                }
              });
            }

            // Update mergedAnswersForCondition to remove newly deleted items for next iteration
            keysToDelete.forEach(key => {
              delete mergedAnswersForCondition[key];
            });
          }


          // FIX: Add deleted keys to recentlyCleanedItemsRef to prevent re-adding
          keysToDelete.forEach(key => {
            recentlyCleanedItemsRef.current.add(key);
          });

          // Clear the ref after a delay to allow for multiple update cycles
          if (cleanupClearTimeoutRef.current) {
            clearTimeout(cleanupClearTimeoutRef.current);
          }
          cleanupClearTimeoutRef.current = setTimeout(() => {
            recentlyCleanedItemsRef.current.clear();
          }, 3000);

          // FIX: Remove deleted keys from batchedAnswersUpdates to prevent re-adding them
          keysToDelete.forEach(key => {
            if (batchedAnswersUpdates[key]) {
              delete batchedAnswersUpdates[key];
            }
          });
        }

        if (keysToDelete.length > 0) {
          setConditionalQuestionVisibility(prev => {
            const updated = { ...prev };
            let changed = false;
            keysToDelete.forEach(deletedKey => {
              const baseOID = deletedKey.includes('_') ? deletedKey.split('_')[0] : deletedKey;
              Object.values(allQuestionsData).forEach(stepQuestions => {
                (stepQuestions as any[]).forEach((q: any) => {
                  if ((q.id === baseOID || q.id === deletedKey) && q.conditionOID) {
                    if (updated[q.conditionOID] !== false) {
                      updated[q.conditionOID] = false;
                      changed = true;
                    }
                  }
                });
              });
            });
            return changed ? updated : prev;
          });
        }

        if (Object.keys(batchedAnswersUpdates).length > 0 || keysToDelete.length > 0) {

          setAnswersData(prev => {
            const updated = { ...prev };

            keysToDelete.forEach(key => {
              delete updated[key];
            });


            const filteredBatchedUpdates: { [key: string]: any } = {};
            Object.entries(batchedAnswersUpdates).forEach(([key, data]) => {
              const newValue = (data as any)?.value;
              // Check if this item was recently cleaned (deselected) - it's in the Set
              const wasRecentlyCleaned = recentlyCleanedItemsRef.current.has(key);

              const baseOIDForKey = key.split('_')[0];
              const isKeyDerived = derivedItemOIDsSet.has(baseOIDForKey);

              if (!isKeyDerived && wasRecentlyCleaned && newValue && newValue !== '') {
                return; // Skip this entry
              }
              filteredBatchedUpdates[key] = data;
            });

            // Apply updates (now filtered to not overwrite recently-cleaned empty values)
            Object.assign(updated, filteredBatchedUpdates);

            // This replaces the auto-sync useEffect to prevent infinite loops
            const groupMap = new Map<string, Map<number, any[]>>();

            Object.entries(updated).forEach(([answerKey, answerInfo]: [string, any]) => {
              let itemOID = answerKey;
              let repeatKey = 0;
              if (answerKey.includes('_')) {
                const parts = answerKey.split('_');
                const lastPart = parts[parts.length - 1];
                if (!isNaN(parseInt(lastPart))) {
                  repeatKey = parseInt(lastPart);
                  const numericSuffixCount = parts.slice().reverse().findIndex(p => isNaN(parseInt(p)));
                  const itemOIDParts = numericSuffixCount === -1
                    ? parts.slice(0, -1)
                    : parts.slice(0, parts.length - numericSuffixCount);
                  itemOID = itemOIDParts.join('.');
                }
              }

              const itemDefEntry = itemDef.find((def: any) => def.OID === itemOID);
              if (!itemDefEntry) return;

              let itemGroupOID = 'IG.1';
              for (const group of itemGroup) {
                if (group.ItemRef?.some((ref: any) => ref.ItemOID === itemOID)) {
                  itemGroupOID = group.OID;
                  break;
                }
              }
              if (!itemGroupOID) return; // skip items with no matching group

              if (!groupMap.has(itemGroupOID)) {
                groupMap.set(itemGroupOID, new Map());
              }
              const repeatMap = groupMap.get(itemGroupOID)!;
              if (!repeatMap.has(repeatKey)) {
                repeatMap.set(repeatKey, []);
              }

              const itemData: any = {
                ItemOID: itemOID,
                Value: answerInfo.value || '',
                CodeList: [],
                ItemGroupRepeatKey: repeatKey
              };

              if (Object.prototype.hasOwnProperty.call(answerInfo, 'isDeleted') &&
                (answerInfo.isDeleted === true || answerInfo.isDeleted === 'true')) {
                itemData.isDeleted = true;
              }

              if (answerInfo.originalItemData?.ParentItemGroupRepeatKey !== undefined) {
                itemData._parentItemGroupRepeatKey = answerInfo.originalItemData.ParentItemGroupRepeatKey;
              }

              const metaKey = itemDefEntry.MetaKey || '';
              if (metaKey) itemData.MetaKey = metaKey;

              if (answerInfo.CodeList && answerInfo.CodeList.length > 0) {
                itemData.CodeList = answerInfo.CodeList.map((cl: any) => ({
                  ...cl,
                  ItemGroupRepeatKey: repeatKey,
                  Name: cl.Name || cl.OID,
                  DataType: cl.DataType || 'text'
                }));
              } else {
                const dataType = itemDefEntry.DataType || answerInfo.dataType;
                if (dataType === 'singleselect' || dataType === 'multiselect') {
                  const codeListOID = itemDefEntry.CodeListRef?.CodeListOID;
                  if (codeListOID) {
                    const codeListDef = codeList.find((cl: any) => cl.OID === codeListOID);
                    if (codeListDef && codeListDef.CodeListItem) {
                      const selectedValues = Array.isArray(answerInfo.value) ? answerInfo.value : [answerInfo.value];
                      const codeListItems = codeListDef.CodeListItem.map((item: any) => ({
                        ...item,
                        IsEnabled: selectedValues.includes(item.CodedValue)
                      }));
                      itemData.CodeList = [{
                        OID: codeListOID,
                        Name: codeListOID,
                        DataType: codeListDef.DataType,
                        CodeListItem: codeListItems,
                        ItemGroupRepeatKey: repeatKey
                      }];
                      itemData.Value = "";
                    }
                  }
                }
              }

              const existingItems = repeatMap.get(repeatKey)!;
              const isDuplicate = existingItems.some((existing: any) => existing.ItemOID === itemOID);
              if (!isDuplicate) {
                existingItems.push(itemData);
              }
            });

            // Convert map to ItemGroupData array
            const rebuiltItemGroupData: any[] = [];
            groupMap.forEach((repeatMap, itemGroupOID) => {
              repeatMap.forEach((itemDataArray, repeatKey) => {
                let parentItemGroupRepeatKey: number | undefined = undefined;

                for (const item of itemDataArray) {
                  const answerKey = Object.keys(updated).find(key => {
                    const answerInfo = updated[key];
                    return answerInfo.originalItemData?.ItemOID === item.ItemOID &&
                      answerInfo.originalItemData?.ItemGroupRepeatKey === repeatKey;
                  });

                  if (answerKey && updated[answerKey].originalItemData?.ParentItemGroupRepeatKey !== undefined) {
                    parentItemGroupRepeatKey = updated[answerKey].originalItemData.ParentItemGroupRepeatKey;
                    break;
                  }
                }

                if (parentItemGroupRepeatKey === undefined && repeatKey > 0 && itemDataArray.length > 0) {
                  const firstItemOID = itemDataArray[0].ItemOID;
                  const answerKey = Object.keys(updated).find(key => {
                    if (!key.includes('_')) return false;
                    const parts = key.split('_');
                    const instanceSuffix = parseInt(parts[parts.length - 1]);
                    if (isNaN(instanceSuffix) || instanceSuffix !== repeatKey) return false;
                    return key.startsWith(`${firstItemOID}_`);
                  });

                  if (answerKey && answerKey.split('_').length > 2) {
                    const parts = answerKey.split('_');
                    if (parts.length >= 3) {
                      const parentIndex = parseInt(parts[parts.length - 2]);
                      if (!isNaN(parentIndex)) {
                        parentItemGroupRepeatKey = parentIndex;
                      }
                    }
                  }
                }

                // Check if this group is nested using template metadata
                if (parentItemGroupRepeatKey === undefined) {
                  const mapping = repeatingGroupMappings.find(m => m.repeatingGroupOID === itemGroupOID);
                  if (mapping?.isNested) {
                    parentItemGroupRepeatKey = 0;
                  }
                }

                const groupData: any = {
                  ItemData: itemDataArray,
                  ItemGroupOID: itemGroupOID,
                  ItemGroupRepeatKey: repeatKey
                };

                if (parentItemGroupRepeatKey !== undefined) {
                  groupData.ParentItemGroupRepeatKey = parentItemGroupRepeatKey;
                }

                rebuiltItemGroupData.push(groupData);
              });
            });


            const pendingItemGroupData = rebuiltItemGroupData;
            requestAnimationFrame(() => {
              setItemGroupData(pendingItemGroupData);
            });

            answersDataRef.current = updated;

            return updated;
          });

          Object.entries(batchedAnswersUpdates).forEach(([key, data]) => {
            const value = (data as any)?.value;
            if (value !== undefined && value !== null && value !== '') {
              clearValidationError(key);
            }
          });

          setTimeout(() => {
            isApplyingSuffixSyncRef.current = false;
            try {
              setIsAnswersDataStable(true);

              const keys = Object.keys(batchedAnswersUpdates || {});
              if (keys.length > 0) {
                const mergedAnswersData = { ...answersDataRef.current, ...batchedAnswersUpdates };
                recomputeConditionalVisibilityForAnswerKeys(keys, mergedAnswersData);
              }

              if (keysToDelete.length > 0) {
                const cleanedAnswersData = { ...answersDataRef.current };
                keysToDelete.forEach(k => delete cleanedAnswersData[k]);
                const changedControlOIDs = repeatingGroupMappings
                  .filter((m: any) => keysToDelete.some(k => {
                    const base = k.includes('_') ? k.split('_')[0] : k;
                    return itemGroup.find((ig: any) => ig.OID === m.repeatingGroupOID)
                      ?.ItemRef?.some((ref: any) => ref.ItemOID === base);
                  }))
                  .map((m: any) => m.controlItemOID);
                if (changedControlOIDs.length > 0) {
                  recomputeConditionalVisibilityForAnswerKeys(changedControlOIDs, cleanedAnswersData);
                }
              }

              // Trigger a save (debounced) to improve persistence after sync
              const customerUid = data?.customerUid;
              const assetUid = data?.assetUid;
              const tradeOid = tradeType;
              if (customerUid && assetUid && tradeOid) {
                debouncedSave({ answersData: answersDataRef.current, activeStep, completed, itemGroupData, repeatingInstanceData, currentQuestionIndex, tradeType, questionTemplateVersion: tradeVersion?.current }, customerUid, assetUid, tradeOid, data?.jobStartDate);
              }
            } catch (e) {
              // Error in post-sync tasks
            } finally {
              // Decrement the save gate after post-sync stabilization
              try { SaveGate.end(); } catch (e) { /* defensive */ }
            }
          }, 0);
        } else {
          // No updates applied - still clear the guard (defensive)
          setTimeout(() => {
            isApplyingSuffixSyncRef.current = false;
            setIsAnswersDataStable(true);
            try { SaveGate.end(); } catch (e) { /* defensive */ }
          }, 0);
        }
      }

      if (data.ItemOID && data.ItemGroupRepeatKey > 0) {
        isApplyingSuffixSyncRef.current = true;
        try { SaveGate.start(); } catch (e) { /* defensive */ }
        setIsAnswersDataStable(false);
        const instanceSuffix = `_${data.ItemGroupRepeatKey}`;
        const answerKey = `${data.ItemOID}${instanceSuffix}`;

        // Look up itemGroupOID from itemGroupData if not provided
        let resolvedItemGroupOID = data.ItemGroupOID;
        if (!resolvedItemGroupOID) {
          for (const group of itemGroupData) {
            if (group.ItemData && group.ItemData.some((item: any) => item.ItemOID === data.ItemOID)) {
              if (group.ItemGroupRepeatKey === (data.ItemGroupRepeatKey || 0)) {
                resolvedItemGroupOID = group.ItemGroupOID;
                break;
              }
            }
          }
        }

        setAnswersData(prev => ({
          ...prev,
          [answerKey]: {
            value: data.Value,
            itemOID: data.ItemOID,
            itemGroupOID: resolvedItemGroupOID,
            repeatKey: data.ItemGroupRepeatKey || 0,
            dataType: 'singleselect', // Infer from value type
            codeListItem: null,
            isNull: false
          }
        }));
        setTimeout(() => {
          isApplyingSuffixSyncRef.current = false;
          try {
            setIsAnswersDataStable(true);

            const updatedAnswersData = {
              ...answersData,
              [answerKey]: {
                value: data.Value,
                itemOID: data.ItemOID,
                itemGroupOID: resolvedItemGroupOID,
                repeatKey: data.ItemGroupRepeatKey || 0,
                dataType: 'singleselect',
                codeListItem: null,
                isNull: false
              }
            };
            recomputeConditionalVisibilityForAnswerKeys([answerKey], updatedAnswersData);
            const customerUid = data?.customerUid;
            const assetUid = data?.assetUid;
            const tradeOid = tradeType;
            if (customerUid && assetUid && tradeOid) {
              debouncedSave({ answersData, activeStep, completed, itemGroupData, repeatingInstanceData, currentQuestionIndex, tradeType, questionTemplateVersion: tradeVersion?.current }, customerUid, assetUid, tradeOid);
            }
          } catch (e) {
            // Error in post-sync tasks
          } finally {
            try { SaveGate.end(); } catch (e) { /* defensive */ }
          }
        }, 0);
      }

      const cleanupTargets = checkForCleanupTrigger(data);
      let skipSyncAfterCleanup = false;


      if (cleanupTargets.length > 0) {
        skipSyncAfterCleanup = true;
        setAnswersData(prev => {
          const cleaned = { ...prev };
          let cleanedCount = 0;
          const cleanedKeys: string[] = [];
          cleanupTargets.forEach(target => {
            if (cleaned[target]) {
              delete cleaned[target];
              cleanedKeys.push(target);
              cleanedCount++;
            }
          });

          if (cleanedCount > 0) {
          }
          return cleaned;
        });
        setItemGroupData(prev => {
          // Instead of filtering out, reset the CodeList.IsEnabled for cleanup targets
          const updated = prev.map(group => ({
            ...group,
            ItemData: group.ItemData.map((item: any) => {
              // If this item is a cleanup target, reset its CodeList.IsEnabled
              if (cleanupTargets.includes(item.ItemOID)) {
                // Reset the CodeList: set all IsEnabled to false
                const resetCodeList = item.CodeList?.map((codeList: any) => ({
                  ...codeList,
                  CodeListItem: codeList.CodeListItem?.map((cli: any) => ({
                    ...cli,
                    IsEnabled: false
                  }))
                }));
                return {
                  ...item,
                  Value: null,
                  CodeList: resetCodeList || item.CodeList
                };
              }
              return item;
            }).filter((item: any) => {
              // Also remove items completely if they are cleanup targets with no CodeList
              // (They will be hidden anyway)
              if (cleanupTargets.includes(item.ItemOID) && !item.CodeList) {
                return false;
              }
              return true;
            })
          })).filter(group => group.ItemData.length > 0);



          return updated;
        });

        // Filter incoming data if it's ItemGroup array format
        if (data.ItemGroup && Array.isArray(data.ItemGroup)) {

          // FIX: Only filter out cleanup targets, NOT all multi-instance data
          data.ItemGroup = data.ItemGroup.map((group: any) => ({
            ...group,
            ItemData: group.ItemData.filter((item: any) => {
              // Remove cleanup targets from incoming data
              return !cleanupTargets.includes(item.ItemOID);
            })
          })).filter((group: any) => group.ItemData.length > 0); // Remove empty groups

        }
      }
      if (!isProcessingCleanupRef.current && data.ItemOID && data.Value !== undefined) {
        const answerKey = (data.ItemGroupRepeatKey && data.ItemGroupRepeatKey > 0)
          ? `${data.ItemOID}_${data.ItemGroupRepeatKey}`
          : data.ItemOID;
        const currentAnswer = answersData[answerKey];
        const currentValue = currentAnswer?.value;

        // Check if value has changed
        let hasValueChanged = currentValue !== data.Value;

        // For singleselect/multiselect, also check if CodeList selection has changed
        // because value is often empty string, but IsEnabled flags change
        if (!hasValueChanged && data.CodeList && currentAnswer?.CodeList) {
          const oldCodeList = currentAnswer.CodeList;
          const newCodeList = data.CodeList;

          const oldSelected = (oldCodeList as any)[0]?.CodeListItem
            ?.filter((item: any) => item.IsEnabled)
            .map((item: any) => item.CodedValue)
            .sort();
          const newSelected = (newCodeList as any)[0]?.CodeListItem
            ?.filter((item: any) => item.IsEnabled)
            .map((item: any) => item.CodedValue)
            .sort();

          hasValueChanged = JSON.stringify(oldSelected) !== JSON.stringify(newSelected);
        }

        if (hasValueChanged) {
          isProcessingCleanupRef.current = true;
          try {
            cleanupDependentAnswers(answerKey, data.Value, data);
          } finally {
            // Reset on next tick to allow subsequent changes
            setTimeout(() => {
              isProcessingCleanupRef.current = false;
            }, 0);
          }
        }
      } else if (data.ItemGroup && Array.isArray(data.ItemGroup)) {
        // ItemGroup array format - check each item
        data.ItemGroup.forEach((group: any) => {
          group.ItemData?.forEach((item: any) => {
            if (item.ItemOID && item.Value !== undefined) {
              // Build answer key with suffix if multi-instance
              const answerKey = (group.ItemGroupRepeatKey && group.ItemGroupRepeatKey > 0)
                ? `${item.ItemOID}_${group.ItemGroupRepeatKey}`
                : item.ItemOID;

              const currentAnswer = answersData[answerKey];
              const currentValue = currentAnswer?.value;

              // Check if value has changed
              let hasValueChanged = currentValue !== item.Value;

              // For singleselect/multiselect, also check if CodeList selection has changed
              // because value is often empty string, but IsEnabled flags change
              if (!hasValueChanged && item.CodeList && currentAnswer?.CodeList) {
                const oldCodeList = currentAnswer.CodeList;
                const newCodeList = item.CodeList;

                const oldSelected = (oldCodeList as any)[0]?.CodeListItem
                  ?.filter((i: any) => i.IsEnabled)
                  .map((i: any) => i.CodedValue)
                  .sort();
                const newSelected = (newCodeList as any)[0]?.CodeListItem
                  ?.filter((i: any) => i.IsEnabled)
                  .map((i: any) => i.CodedValue)
                  .sort();

                hasValueChanged = JSON.stringify(oldSelected) !== JSON.stringify(newSelected);
              }

              if (hasValueChanged) {
                isProcessingCleanupRef.current = true;
                try {
                  cleanupDependentAnswers(answerKey, item.Value, data);
                } finally {
                  // Reset on next tick to allow subsequent changes
                  setTimeout(() => {
                    isProcessingCleanupRef.current = false;
                  }, 0);
                }
              }
            }
          });
        });
      }
      if (data.ItemGroup && Array.isArray(data.ItemGroup)) {
        // Get list of items that were just cleaned up
        // Start with items from cleanupDependentAnswers to prevent sync from re-adding them
        const recentlyCleanedItems = new Set<string>(recentlyCleanedItemsRef.current);

        // Check each incoming item to see if it should be filtered
        data.ItemGroup.forEach((group: any) => {
          (group.ItemData || []).forEach((item: any) => {
            // Check if this item is a dependent of any changed item
            const isDependent = itemGroup.some((ig: any) =>
              (ig.ItemRef || []).some((itemRef: any) => {
                if (itemRef.ItemOID === item.ItemOID && itemRef.CollectionExceptionConditionOID) {
                  // This is a conditional question - check if its parent just changed
                  const condDef = conditionDef?.find((cd: any) => cd.OID === itemRef.CollectionExceptionConditionOID);
                  if (condDef) {
                    const expressionText = condDef.FormalExpression?.text;
                    if (expressionText) {
                      const parentOID = expressionText.split("==")[0]?.replace(/[&/\\#,+()$~%'":*?<>{}! ]/g, '');

                      const mergedParentAnswer = batchedAnswersUpdates[parentOID] !== undefined
                        ? batchedAnswersUpdates[parentOID]
                        : answersData[parentOID];
                      const isMultiselectParent = mergedParentAnswer?.dataType === 'multiselect';

                      let parentValue = mergedParentAnswer?.value;
                      let selectedValues: string[] = [];

                      if (isMultiselectParent && mergedParentAnswer?.CodeList) {
                        // For multiselect, get all selected CodedValues
                        selectedValues = (mergedParentAnswer.CodeList as any)[0]?.CodeListItem
                          ?.filter((item: any) => item.IsEnabled)
                          .map((item: any) => item.CodedValue) || [];
                      }

                      const requiredValue = expressionText.split("==")[1]?.replace(/[&/\\#,+()$~%'":*?<>{}! ]/g, '');

                      // Handle OR conditions (e.g., "1||I.26" means value can be "1" OR "I.26")
                      const requiredValues = requiredValue?.split('||') || [];

                      let conditionMet = false;
                      if (isMultiselectParent) {
                        conditionMet = requiredValues.some(val => selectedValues.includes(val));
                      } else {
                        // For singleselect, parentValue is always "" — check CodeList.IsEnabled instead
                        const hasSingleSelectCodeList = mergedParentAnswer?.CodeList &&
                          Array.isArray(mergedParentAnswer.CodeList) &&
                          mergedParentAnswer.CodeList.length > 0;
                        if (hasSingleSelectCodeList) {
                          const codeList = mergedParentAnswer.CodeList[0];
                          conditionMet = requiredValues.some((val: string) =>
                            codeList.CodeListItem?.some((i: any) => i.CodedValue === val && i.IsEnabled)
                          );
                        } else {
                          conditionMet = requiredValues.some(val => parentValue === val);
                        }
                      }

                      if (conditionMet) {
                        // Condition is now met — clear from cleaned sets so setItemGroupData can restore
                        recentlyCleanedItems.delete(item.ItemOID);
                        recentlyCleanedItemsRef.current.delete(item.ItemOID);
                      } else {
                        recentlyCleanedItems.add(item.ItemOID);
                        return true;
                      }
                    }
                  }
                }
                return false;
              })
            );
          });
        });

        let filteredCount = 0;
        const actuallyFilteredItems: string[] = []; // Track which items were actually filtered
        data.ItemGroup = data.ItemGroup.map((group: any) => ({
          ...group,
          ItemData: (group.ItemData || []).filter((item: any) => {
            if (recentlyCleanedItems.has(item.ItemOID)) {
              filteredCount++;
              actuallyFilteredItems.push(item.ItemOID); // Track the filtered item
              return false; // Filter out this item
            }
            return true; // Keep this item
          })
        })).filter((group: any) => group.ItemData.length > 0); // Remove empty groups

        if (actuallyFilteredItems.length > 0) {
          // Cancel any previous clear timeout
          if (cleanupClearTimeoutRef.current) {
            clearTimeout(cleanupClearTimeoutRef.current);
          }

          // Set new debounced clear with longer delay (1000ms to ensure all effects complete)
          cleanupClearTimeoutRef.current = setTimeout(() => {
            recentlyCleanedItemsRef.current.clear();
            cleanupClearTimeoutRef.current = null;
          }, 1000);
        }
      }

      // Re-evaluate items in recentlyCleanedItemsRef that weren't in the incoming data.
      // This handles the re-select case: conditional items (e.g., follow-up questions) were
      // cleaned during deselect and the card doesn't re-send them — but their condition may
      // now be satisfied again, so they should be unblocked from setItemGroupData's filter.
      if (recentlyCleanedItemsRef.current.size > 0) {
        Array.from(recentlyCleanedItemsRef.current).forEach(cleanedOID => {
          itemGroup.some((ig: any) =>
            (ig.ItemRef || []).some((itemRef: any) => {
              if (itemRef.ItemOID === cleanedOID && itemRef.CollectionExceptionConditionOID) {
                const condDef = conditionDef?.find((cd: any) => cd.OID === itemRef.CollectionExceptionConditionOID);
                if (condDef) {
                  const exprText = condDef.FormalExpression?.text;
                  if (exprText) {
                    const pOID = exprText.split("==")[0]?.replace(/[&/\\#,+()$~%'":*?<>{}! ]/g, '');
                    const rVal = exprText.split("==")[1]?.replace(/[&/\\#,+()$~%'":*?<>{}! ]/g, '');
                    const rVals = rVal?.split('||') || [];

                    const mergedAns = batchedAnswersUpdates[pOID] !== undefined
                      ? batchedAnswersUpdates[pOID]
                      : answersData[pOID];

                    const hasSingleSelectCodeList = mergedAns?.CodeList &&
                      Array.isArray(mergedAns.CodeList) &&
                      mergedAns.CodeList.length > 0;

                    let isMet = false;
                    if (hasSingleSelectCodeList) {
                      const cl = mergedAns.CodeList[0];
                      isMet = rVals.some((v: string) =>
                        cl.CodeListItem?.some((i: any) => i.CodedValue === v && i.IsEnabled)
                      );
                    } else {
                      isMet = rVals.some((v: string) => mergedAns?.value === v);
                    }

                    if (isMet) {
                      recentlyCleanedItemsRef.current.delete(cleanedOID);
                    }
                    return true;
                  }
                }
              }
              return false;
            })
          );
        });
      }

      if (data.ItemGroup && Array.isArray(data.ItemGroup)) {
        // Set merge guard to prevent rebuild useEffect from overwriting our merge
        isMergingItemGroupDataRef.current = true;

        setItemGroupData(prev => {
          let merged = prev.map(group => ({
            ...group,
            ItemData: (group.ItemData || []).filter(item =>
              !recentlyCleanedItemsRef.current.has(item.ItemOID)
            )
          })).filter(group => group.ItemData && group.ItemData.length > 0);

          let foundMatch = false;

          data.ItemGroup.forEach((newGroup: any) => {
            const existingIndex = merged.findIndex(g =>
              g.ItemGroupOID === newGroup.ItemGroupOID &&
              g.ItemGroupRepeatKey === newGroup.ItemGroupRepeatKey
            );
            if (newGroup.ItemGroupRepeatKey === 0) {
              const hasMultiInstanceForThisGroup = merged.some(g =>
                g.ItemGroupOID === newGroup.ItemGroupOID && g.ItemGroupRepeatKey > 0
              );

              if (hasMultiInstanceForThisGroup) {
                return;
              }
            }

            if (existingIndex >= 0) {
              foundMatch = true;
              // Merge ItemData arrays
              const existingGroup = merged[existingIndex];
              const mergedItemData = [...(existingGroup.ItemData || [])];

              (newGroup.ItemData || []).forEach((newItem: any) => {
                const existingItemIndex = mergedItemData.findIndex(item =>
                  item.ItemOID === newItem.ItemOID &&
                  item.ItemGroupRepeatKey === newItem.ItemGroupRepeatKey
                );

                if (existingItemIndex >= 0) {
                  mergedItemData[existingItemIndex] = newItem;
                } else {
                  // Add new item only if value is not empty
                  const newValueIsEmpty = !newItem.Value || newItem.Value === '';
                  const newItemIsDeleted = newItem.isDeleted === true;
                  if (!newValueIsEmpty || newItemIsDeleted) {
                    mergedItemData.push(newItem);
                  }
                }
              });

              const updatedGroup: any = {
                ...existingGroup,
                ...newGroup,
                ItemData: mergedItemData
              };

              // Preserve ParentItemGroupRepeatKey if it exists in the incoming data
              if (newGroup.ParentItemGroupRepeatKey !== undefined) {
                updatedGroup.ParentItemGroupRepeatKey = newGroup.ParentItemGroupRepeatKey;
              } else {
                // console.warn('[MERGE_DEBUG] newGroup.ParentItemGroupRepeatKey is undefined!', {
                //   ItemGroupOID: newGroup.ItemGroupOID,
                //   ItemGroupRepeatKey: newGroup.ItemGroupRepeatKey,
                //   newGroupKeys: Object.keys(newGroup)
                // });
              }

              merged[existingIndex] = updatedGroup;
            } else {

              const groupToAdd = { ...newGroup };

              // Preserve ParentItemGroupRepeatKey if it exists in the incoming data
              if (newGroup.ParentItemGroupRepeatKey !== undefined) {
                groupToAdd.ParentItemGroupRepeatKey = newGroup.ParentItemGroupRepeatKey;
              } else {
                // console.warn('[MERGE_DEBUG] newGroup.ParentItemGroupRepeatKey is undefined for new group!', {
                //   ItemGroupOID: newGroup.ItemGroupOID,
                //   ItemGroupRepeatKey: newGroup.ItemGroupRepeatKey,
                //   newGroupKeys: Object.keys(newGroup)
                // });
              }

              merged.push(groupToAdd);
            }
          });

          return merged;
        });

        setTimeout(() => {
          isMergingItemGroupDataRef.current = false;
        }, 100);
        if (!skipSyncAfterCleanup) {
          setIsAnswersDataStable(false);

          setAnswersData(prev => {
            const updated = { ...prev };
            let changesCount = 0;
            const syncDetails: any[] = [];
            const processItemGroup = (group: any, parentSuffix: string = '') => {
              const hasParentRepeatKey = group.ParentItemGroupRepeatKey !== undefined && group.ParentItemGroupRepeatKey !== null && group.ParentItemGroupRepeatKey > 0;
              const effectiveParentSuffix = hasParentRepeatKey ? `${group.ParentItemGroupRepeatKey}` : parentSuffix;

              const currentSuffix = effectiveParentSuffix ? `${effectiveParentSuffix}_${group.ItemGroupRepeatKey}` : `_${group.ItemGroupRepeatKey}`;

              if (group.ItemGroupRepeatKey > 0 && group.ItemData && Array.isArray(group.ItemData)) {
                group.ItemData.forEach((item: any) => {
                  const answerKey = effectiveParentSuffix
                    ? `${item.ItemOID}_${effectiveParentSuffix}_${group.ItemGroupRepeatKey}`
                    : `${item.ItemOID}_${group.ItemGroupRepeatKey}`;

                  const hasValue = item.Value !== undefined && item.Value !== null && item.Value !== '';
                  const hasCodeList = item.CodeList && item.CodeList.length > 0;
                  const hasIsDeleted = item.isDeleted === true;
                  const shouldSync = hasValue || hasCodeList || hasIsDeleted;

                  syncDetails.push({
                    answerKey,
                    itemOID: item.ItemOID,
                    repeatKey: group.ItemGroupRepeatKey,
                    parentSuffix: effectiveParentSuffix,
                    parentRepeatKey: group.ParentItemGroupRepeatKey,
                    value: item.Value,
                    hasValue,
                    hasCodeList,
                    reason: shouldSync ? 'Synced' : 'Empty value and no CodeList'
                  });

                  if (shouldSync) {
                    const itemDefEntry = itemDef.find((def: any) => def.OID === item.ItemOID);
                    const dataType = itemDefEntry?.DataType || 'singleselect';

                    updated[answerKey] = {
                      value: item.Value || '', // Can be empty for multiselect
                      itemGroupOID: group.ItemGroupOID,
                      repeatKey: group.ItemGroupRepeatKey,
                      timestamp: Date.now(),
                      dataType: dataType,
                      ...(Object.prototype.hasOwnProperty.call(item, 'isDeleted') &&
                        isDeletedFlag(item.isDeleted) ? { isDeleted: true } : {}),
                      // PRESERVE MetaKey for deleted items so it's available during rebuild
                      ...(item.MetaKey ? { MetaKey: item.MetaKey } : {}),
                      ...(hasCodeList ? { CodeList: item.CodeList } : {})
                    };
                    changesCount++;

                    clearValidationError(answerKey);
                  }
                });
              }

              if (group.ItemGroup && Array.isArray(group.ItemGroup) && group.ItemGroup.length > 0) {
                group.ItemGroup.forEach((nestedGroup: any) => {
                  processItemGroup(nestedGroup, currentSuffix.replace(/^_/, ''));
                });
              }
            };

            // Process all top-level ItemGroups
            data.ItemGroup.forEach((group: any) => {
              processItemGroup(group);
            });
            return updated;
          });
        }

        if (!skipSyncAfterCleanup) {
          setTimeout(() => {
            const multiInstanceAnswers: { [itemOID: string]: { repeatKey: number; value: any; isDeleted?: boolean }[] } = {};

            Object.entries(answersData).forEach(([key, value]: [string, any]) => {
              if (key.includes('_')) {
                const [baseItemOID, repeatKeyStr] = key.split('_');
                const repeatKey = parseInt(repeatKeyStr);

                if (!isNaN(repeatKey) && repeatKey > 0) {
                  if (!multiInstanceAnswers[baseItemOID]) {
                    multiInstanceAnswers[baseItemOID] = [];
                  }
                  multiInstanceAnswers[baseItemOID].push({
                    repeatKey,
                    value: value.value,
                    isDeleted: value?.isDeleted
                  });
                }
              }
            });

            if (Object.keys(multiInstanceAnswers).length > 0) {

              setItemGroupData(prev => {
                const updated = [...prev];

                Object.entries(multiInstanceAnswers).forEach(([itemOID, instances]) => {
                  instances.forEach(({ repeatKey, value, isDeleted }) => {
                    // Find which ItemGroupOID this ItemOID belongs to
                    const itemDefLocal = itemGroup.flatMap(ig =>
                      (ig.ItemRef || []).filter(ref => ref.ItemOID === itemOID).map(ref => ({ itemGroupOID: ig.OID, itemRef: ref }))
                    );

                    if (itemDefLocal.length > 0) {
                      const { itemGroupOID } = itemDefLocal[0];

                      // Check if this RepeatKey group exists
                      const groupIndex = updated.findIndex(g =>
                        g.ItemGroupOID === itemGroupOID && g.ItemGroupRepeatKey === repeatKey
                      );

                      if (groupIndex === -1) {
                        const itemDefTemplate = itemDef.find((def: ItemDef) => def.OID === itemOID);
                        const metaKey = itemDefTemplate?.MetaKey || '';
                        const answerKey = `${itemOID}_${repeatKey}`;
                        let parentItemGroupRepeatKey: number | undefined = undefined;

                        // Check if this is a nested instance by looking for triple-level keys
                        const nestedAnswerKeys = Object.keys(answersData).filter(k => {
                          const parts = k.split('_');
                          return parts.length === 3 && parts[0] === itemOID.split('.')[0] && parts[1] === itemOID.split('.')[1];
                        });

                        if (nestedAnswerKeys.length > 0) {
                          // Extract parent repeat key from triple-level key
                          const parts = nestedAnswerKeys[0].split('_');
                          if (parts.length === 3) {
                            const parentRepeat = parseInt(parts[1]);
                            if (!isNaN(parentRepeat) && parentRepeat > 0) {
                              parentItemGroupRepeatKey = parentRepeat;
                            }
                          }
                        }

                        let codeListData: any = null;
                        if (itemDefTemplate && itemDefTemplate.CodeListRef) {
                          const codeListOID = itemDefTemplate.CodeListRef.CodeListOID;
                          const codeListDef = codeList.find((cl: any) => cl.OID === codeListOID);
                          if (codeListDef && codeListDef.CodeListItem) {
                            const selectedValues = Array.isArray(value) ? value : [value];

                            // Build CodeList with IsEnabled flags
                            const codeListItems = codeListDef.CodeListItem.map((item: any) => ({
                              ...item,
                              IsEnabled: selectedValues.includes(item.CodedValue)
                            }));

                            codeListData = [{
                              OID: codeListOID,
                              Name: codeListOID,
                              DataType: codeListDef.DataType,
                              CodeListItem: codeListItems,
                              ItemGroupRepeatKey: repeatKey
                            }];
                          }
                        }

                        // Create new group with this RepeatKey
                        const newGroup: any = {
                          ItemGroupOID: itemGroupOID,
                          ItemGroupRepeatKey: repeatKey,
                          ItemData: [{
                            ItemOID: itemOID,
                            Value: codeListData ? "" : value,
                            ItemGroupRepeatKey: repeatKey,
                            CodeList: codeListData,
                            MetaKey: metaKey,
                            ...(typeof isDeleted === 'boolean' ? { isDeleted } : {})
                          } as any]
                        };

                        if (parentItemGroupRepeatKey !== undefined) {
                          newGroup.ParentItemGroupRepeatKey = parentItemGroupRepeatKey;
                        }

                        // Check if this group is nested using template metadata
                        if (parentItemGroupRepeatKey === undefined) {
                          const mapping = repeatingGroupMappings.find(m => m.repeatingGroupOID === itemGroupOID);
                          if (mapping?.isNested) {
                            newGroup.ParentItemGroupRepeatKey = 0;
                          }
                        }

                        updated.push(newGroup);
                      } else {
                        // Update existing group
                        const itemIndex = updated[groupIndex].ItemData.findIndex((item: any) => item.ItemOID === itemOID);
                        if (itemIndex === -1) {
                          const itemDefTemplate = itemDef.find((def: ItemDef) => def.OID === itemOID);
                          const metaKey = itemDefTemplate?.MetaKey || '';

                          let codeListData: any = null;
                          if (itemDefTemplate && itemDefTemplate.CodeListRef) {
                            const codeListOID = itemDefTemplate.CodeListRef.CodeListOID;
                            const codeListDef = codeList.find((cl: any) => cl.OID === codeListOID);
                            if (codeListDef && codeListDef.CodeListItem) {
                              // Determine selected values (handle both single value and array)
                              const selectedValues = Array.isArray(value) ? value : [value];

                              // Build CodeList with IsEnabled flags
                              const codeListItems = codeListDef.CodeListItem.map((item: any) => ({
                                ...item,
                                IsEnabled: selectedValues.includes(item.CodedValue)
                              }));

                              codeListData = [{
                                OID: codeListOID,
                                Name: codeListOID,
                                DataType: codeListDef.DataType,
                                CodeListItem: codeListItems,
                                ItemGroupRepeatKey: repeatKey
                              }];
                            }
                          }

                          updated[groupIndex].ItemData.push({
                            ItemOID: itemOID,
                            Value: codeListData ? "" : value,  // Empty string when CodeList present
                            ItemGroupRepeatKey: repeatKey,
                            CodeList: codeListData,  // Proper CodeList or null
                            MetaKey: metaKey,
                            ...(typeof isDeleted === 'boolean' ? { isDeleted } : {})
                          } as any);
                        }
                      }
                    }
                  });
                });

                return updated;
              });
            }
          }, 100);
        }
      }

      // setTradeData(prev => [...prev, data]);

      const newAnswersData: any = {};

      if (data.ItemGroup && Array.isArray(data.ItemGroup)) {
        data.ItemGroup.forEach((itemGroup: any, groupIndex: number) => {

          if (itemGroup.ItemData && Array.isArray(itemGroup.ItemData)) {
            itemGroup.ItemData.forEach((item: any, itemIndex: number) => {
              const hasValue = item.Value !== '' && item.Value !== null && item.Value !== undefined;
              const hasCodeListSelection = item.CodeList && item.CodeList.length > 0 &&
                item.CodeList.some((cl: any) =>
                  cl.CodeListItem && cl.CodeListItem.some((cli: any) => cli.IsEnabled === true)
                );
              const hasIsDeleted = item.isDeleted === true;

              if (item.ItemOID && (hasValue || hasCodeListSelection || hasIsDeleted)) {
                let answerKey = item.ItemOID;

                let parentKey = item.ParentItemGroupRepeatKey ?? itemGroup.ParentItemGroupRepeatKey;

                // Check if this group is nested using template metadata
                if (parentKey === undefined) {
                  const mapping = repeatingGroupMappings.find(m => m.repeatingGroupOID === itemGroup.ItemGroupOID);
                  if (mapping?.isNested) {
                    parentKey = 0;
                  }
                }

                if (parentKey && parentKey > 0) {
                  answerKey = `${item.ItemOID}_${parentKey}_${item.ItemGroupRepeatKey || 0}`;
                } else if (item.ItemGroupRepeatKey && item.ItemGroupRepeatKey > 0) {
                  answerKey = `${item.ItemOID}_${item.ItemGroupRepeatKey}`;
                }

                const itemDefEntry = itemDef.find((def: any) => def.OID === item.ItemOID);
                const dataType = itemDefEntry?.DataType || 'singleselect';
                const parentItemGroupRepeatKey = parentKey;

                newAnswersData[answerKey] = {
                  value: item.Value, // Can be empty string for multiselect
                  itemGroupOID: itemGroup.ItemGroupOID,
                  repeatKey: item.ItemGroupRepeatKey || 0,
                  timestamp: Date.now(),
                  dataType: dataType, // Preserve actual data type
                  ...(Object.prototype.hasOwnProperty.call(item, 'isDeleted')
                    ? { isDeleted: item.isDeleted === true || item.isDeleted === 'true' }
                    : {}),
                  // PRESERVE MetaKey for deleted items so it's available during rebuild
                  ...(item.MetaKey ? { MetaKey: item.MetaKey } : {}),
                  // Store original structure for compatibility
                  originalItemData: {
                    ItemOID: item.ItemOID,
                    Value: item.Value,
                    ItemGroupRepeatKey: item.ItemGroupRepeatKey,
                    ...(parentItemGroupRepeatKey !== undefined ? { ParentItemGroupRepeatKey: parentItemGroupRepeatKey } : {})
                  },
                  ...(item.CodeList && item.CodeList.length > 0 ? { CodeList: item.CodeList } : {})
                };
              }
            });
          }
        });

        setAnswersData(prev => {
          // Start with previous data
          const updated = { ...prev };
          const tripleLevelKeys = Object.keys(newAnswersData).filter(k => k.split('_').length === 3);
          tripleLevelKeys.forEach(tripleKey => {
            const parts = tripleKey.split('_');
            if (parts.length === 3) {
              const itemOID = parts[0];  // "I.24"
              const parentRepeatKey = parseInt(parts[1]);  // Parent instance: 2
              const childRepeatKey = parseInt(parts[2]);   // Child instance: 1

              // Delete both possible duplicate keys
              const doubleLevelKey1 = `${itemOID}_${parentRepeatKey}`;  // I.24_2
              const doubleLevelKey2 = `${itemOID}_${childRepeatKey}`;   // I.24_1

              if (updated[doubleLevelKey1]) {
                delete updated[doubleLevelKey1];
              }
              if (updated[doubleLevelKey2]) {
                delete updated[doubleLevelKey2];
              }
            }
          });

          // Add/update items from new data - MERGE instead of replace
          Object.keys(newAnswersData).forEach(key => {
            if (updated[key]) {
              const mergedOriginalItemData = {
                ...(updated[key].originalItemData || {}),
                ...(newAnswersData[key].originalItemData || {})
              };

              updated[key] = {
                ...updated[key],
                ...newAnswersData[key],
                originalItemData: Object.keys(mergedOriginalItemData).length > 0 ? mergedOriginalItemData : undefined
              };
            } else {
              // New entry: just add it
              updated[key] = newAnswersData[key];
            }
          });

          const allKeys = Object.keys(updated);
          const allTripleLevelKeys = allKeys.filter(k => k.split('_').length === 3);
          const keysToRemove: string[] = [];

          allTripleLevelKeys.forEach(tripleKey => {
            const parts = tripleKey.split('_');
            if (parts.length === 3) {
              const itemOID = parts[0];
              const parentRepeatKey = parseInt(parts[1]);  // Parent instance
              const childRepeatKey = parseInt(parts[2]);   // Child instance

              // Check both possible duplicate keys
              const doubleLevelKey1 = `${itemOID}_${parentRepeatKey}`;  // I.24_2
              const doubleLevelKey2 = `${itemOID}_${childRepeatKey}`;   // I.24_1

              if (updated[doubleLevelKey1] && !keysToRemove.includes(doubleLevelKey1)) {
                keysToRemove.push(doubleLevelKey1);
              }
              if (updated[doubleLevelKey2] && !keysToRemove.includes(doubleLevelKey2)) {
                keysToRemove.push(doubleLevelKey2);
              }
            }
          });

          keysToRemove.forEach(key => delete updated[key]);


          return updated;
        });

      }

      // Handle direct item data changes - Original Structure
      if (data.ItemOID && data.Value !== undefined && data.Value !== null) {
        let itemGroupOID = data.ItemGroupOID;
        if (!itemGroupOID) {
          // Look through itemGroupData to find which group contains this ItemOID
          for (const group of itemGroupData) {
            if (group.ItemData && group.ItemData.some((item: any) => item.ItemOID === data.ItemOID)) {
              // Check if RepeatKey matches
              if (group.ItemGroupRepeatKey === (data.ItemGroupRepeatKey || 0)) {
                itemGroupOID = group.ItemGroupOID;
                break;
              }
            }
          }

          if (!itemGroupOID) {
            itemGroupOID = 'unknown';
          }
        }

        setAnswersData(prev => {
          let answerKey = data.ItemOID;
          let effectiveParentKey = data.ParentItemGroupRepeatKey;

          // For nested groups, try to infer ParentItemGroupRepeatKey if not provided or is 0
          if ((!effectiveParentKey || effectiveParentKey === 0) && itemGroupOID) {
            // Method 1: Check repeatingGroupMappings for nested group info
            const mapping = repeatingGroupMappings.find(m => m.repeatingGroupOID === itemGroupOID);
            let isNestedGroup: boolean = !!(mapping?.isNested && mapping?.parentRepeatingGroupOID);

            // Method 2: Check if this itemGroupOID is a nested group by looking at formDef
            if (!isNestedGroup && formDef && Array.isArray(formDef)) {
              // Look for this group in formDef's ItemGroupRef to find its MethodOID
              for (const form of formDef) {
                const groupRef = form.ItemGroupRef?.find((ref: any) => ref.ItemGroupOID === itemGroupOID);
                if (groupRef?.MethodOID) {
                  // Check if this group has a method that references a parent repeating group's control item
                  const methodDefEntry = methodDef?.find(m => m.OID === groupRef.MethodOID);
                  if (methodDefEntry?.FormalExpression?.text?.includes('RepeatCount')) {
                    // This is a repeating group - check if it's nested by looking at the control item
                    const expressionMatch = methodDefEntry.FormalExpression.text.match(/RepeatCount\s*\(\s*['"]?([^'")\s]+)/);
                    const controlItemOID = expressionMatch ? expressionMatch[1] : null;

                    if (controlItemOID && itemGroup && Array.isArray(itemGroup)) {
                      // Find which group the control item belongs to
                      const parentGroupWithControlItem = itemGroup.find(g =>
                        g.ItemRef?.some((ref: any) => ref.ItemOID === controlItemOID)
                      );
                      // If control item is in a different repeating group, this is a nested group
                      if (parentGroupWithControlItem && parentGroupWithControlItem.OID !== itemGroupOID &&
                        parentGroupWithControlItem.Repeating === 'Yes') {
                        isNestedGroup = true;
                      }
                    }
                  }
                  break;
                }
              }
            }

            if (isNestedGroup) {
              // Look for existing entries in answersData for this nested group
              // to infer the parent key
              const nestedKey = Object.keys(prev).find(key => {
                const parts = key.split('_');
                if (parts.length === 3 && parts[0] === data.ItemOID) {
                  // Found a triple-level key for this item, use its parent key
                  return true;
                }
                return false;
              });

              if (nestedKey) {
                const parts = nestedKey.split('_');
                effectiveParentKey = parseInt(parts[1]) || data.ItemGroupRepeatKey;
              } else {
                // If no existing key, use ItemGroupRepeatKey as parent key for nested groups
                // This assumes nested instance 1 is in parent instance 1, etc.
                effectiveParentKey = data.ItemGroupRepeatKey;
              }
            }
          }

          if (effectiveParentKey && effectiveParentKey > 0) {
            answerKey = `${data.ItemOID}_${effectiveParentKey}_${data.ItemGroupRepeatKey || 0}`;
          } else if (data.ItemGroupRepeatKey && data.ItemGroupRepeatKey > 0) {
            answerKey = `${data.ItemOID}_${data.ItemGroupRepeatKey}`;
          }

          const itemDefEntry = itemDef.find((def: any) => def.OID === data.ItemOID);
          const dataType = itemDefEntry?.DataType || 'singleselect';

          const newAnswer = {
            value: data.Value,
            itemGroupOID: itemGroupOID,
            repeatKey: data.ItemGroupRepeatKey || 0,
            timestamp: Date.now(),
            dataType: dataType, // Use actual dataType from ItemDef
            originalItemData: {
              ItemOID: data.ItemOID,
              Value: data.Value,
              ItemGroupRepeatKey: data.ItemGroupRepeatKey || 0,
              // Use effectiveParentKey if we inferred it, otherwise use original
              ...(effectiveParentKey !== undefined && effectiveParentKey > 0
                ? { ParentItemGroupRepeatKey: effectiveParentKey }
                : (data.ParentItemGroupRepeatKey !== undefined ? { ParentItemGroupRepeatKey: data.ParentItemGroupRepeatKey } : {}))
            },
            ...(data.CodeList && data.CodeList.length > 0 ? { CodeList: data.CodeList } : {})
          };

          return {
            ...prev,
            [answerKey]: newAnswer
          };
        });

        setItemGroupData(prev => {
          const updated = [...prev];

          const groupIndex = updated.findIndex(g =>
            g.ItemGroupOID === itemGroupOID &&
            g.ItemGroupRepeatKey === (data.ItemGroupRepeatKey || 0)
          );



          if (groupIndex >= 0) {
            const group = updated[groupIndex];
            const itemDataArray = [...(group.ItemData || [])];

            // Find or create the ItemData entry
            const itemIndex = itemDataArray.findIndex(item =>
              item.ItemOID === data.ItemOID
            );



            if (itemIndex >= 0) {
              // Update existing ItemData
              const updatedItem: any = {
                ...itemDataArray[itemIndex],
                Value: data.Value,
                ItemGroupRepeatKey: data.ItemGroupRepeatKey || 0
              };

              if (data.ParentItemGroupRepeatKey !== undefined && data.ParentItemGroupRepeatKey !== null) {
                updatedItem.ParentItemGroupRepeatKey = data.ParentItemGroupRepeatKey;
              }

              itemDataArray[itemIndex] = updatedItem;
            } else {
              const itemDefTemplate = itemDef.find((def: ItemDef) => def.OID === data.ItemOID);
              const metaKey = itemDefTemplate?.MetaKey || '';

              // Add new ItemData with proper structure
              const newItem: any = {
                ItemOID: data.ItemOID,
                Value: data.Value,
                ItemGroupRepeatKey: data.ItemGroupRepeatKey || 0,
                CodeList: [],
                MetaKey: metaKey
              };

              if (data.ParentItemGroupRepeatKey !== undefined && data.ParentItemGroupRepeatKey !== null) {
                newItem.ParentItemGroupRepeatKey = data.ParentItemGroupRepeatKey;
              }

              itemDataArray.push(newItem);
            }

            const updatedGroup: any = {
              ...group,
              ItemData: itemDataArray
            };

            if (data.ParentItemGroupRepeatKey !== undefined && data.ParentItemGroupRepeatKey !== null) {
              updatedGroup.ParentItemGroupRepeatKey = data.ParentItemGroupRepeatKey;
            }

            updated[groupIndex] = updatedGroup;
          }


          return updated;
        });
      }

      // Notify parent component
      if (onDataChange) {
        // Organize all questions hierarchically to filter out hidden/conditional questions
        const organizedQuestionsData: { [stepId: string]: any[] } = {};
        Object.keys(allQuestionsData).forEach(stepId => {
          const stepQuestions = allQuestionsData[stepId] || [];
          organizedQuestionsData[stepId] = organizeQuestionsHierarchically(stepQuestions, stepId);
        });

        onDataChange({
          activeStep,
          completed,
          stepData: visibleSteps[activeStep],
          itemGroupData: data.ItemGroup,
          tradeData: data,
          allQuestionsData: organizedQuestionsData,
          answersData: answersData
        });
      }
    } catch (error) {
      // Error in data change handler - logged for debugging
    } finally {
      // Use longer delay to ensure all state updates have propagated
      setTimeout(() => {
        isProcessingDataChangeRef.current = false;
        // Replay any queued call (e.g. auto-calc update that was blocked by the mutex)
        if (pendingDataChangeRef.current) {
          const pending = pendingDataChangeRef.current;
          pendingDataChangeRef.current = null;
          handleItemGroupDataChangeRef.current?.(pending);
        }
      }, 100);
    }
  }, [activeStep, completed, steps, onDataChange]);

  // Keep ref in sync so the queued-call replay always uses the latest closure
  handleItemGroupDataChangeRef.current = handleItemGroupDataChange;

  const debouncedHandleItemGroupDataChange = handleItemGroupDataChange;
  useEffect(() => {
    if (itemGroupData && itemGroupData.length > 0) {
      const currentAnswersCount = Object.keys(answersData).length;
      let foundMultiInstanceData = false;
      let needsSync = false;

      // Derived (MethodOID) fields are OUTPUTS computed from other answers — they must
      // never be synced itemGroupData -> answersData. itemGroupData lags the live calc,
      // so copying it back overwrites a freshly-computed value (e.g. I.73 reverting 7->3,
      // I.76 reverting true->0). The calc effects own these fields in answersData; the ODM
      // is rebuilt from answersData, so skipping them here is both correct and sufficient.
      const derivedOIDsToSkip = new Set<string>();
      (itemGroup || []).forEach((g: any) => (g?.ItemRef || []).forEach((r: any) => {
        if (!r?.MethodOID) return;
        const md = methodDef?.find((m: any) => m.OID === r.MethodOID);
        if (md?.FormalExpression?.text?.includes('RepeatCount')) return;
        derivedOIDsToSkip.add(r.ItemOID);
      }));

      itemGroupData.forEach((itemGroupEntry: any) => {
        if (itemGroupEntry.ItemData && Array.isArray(itemGroupEntry.ItemData)) {
          itemGroupEntry.ItemData.forEach((item: any) => {
            if (derivedOIDsToSkip.has(item.ItemOID)) return; // derived output — never sync back
            // Sync ALL items with values OR CodeList items with enabled options
            // FIX: CodeList items (like single-select dropdowns) may have empty Value but enabled CodeListItem options
            const hasEnabledCodeListOption = item.CodeList?.some((cl: any) =>
              cl.CodeListItem?.some((cli: any) => cli.IsEnabled === true)
            );
            const hasValue = item.ItemOID && item.Value !== undefined && item.Value !== null && item.Value !== '';

            if (hasValue || (item.ItemOID && hasEnabledCodeListOption)) {
              // Skip syncing if this item was recently cleaned
              if (recentlyCleanedItemsRef.current.has(item.ItemOID)) {
                return; // Skip this item
              }

              const answerKey = item.ItemGroupRepeatKey && item.ItemGroupRepeatKey > 0
                ? `${item.ItemOID}_${item.ItemGroupRepeatKey}`
                : item.ItemOID;

              if (item.ItemGroupRepeatKey && item.ItemGroupRepeatKey > 0) {
                foundMultiInstanceData = true;
              }

              // Skip syncing if the parent condition is not satisfied
              const parser = dependencyGraphParserRef.current;
              if (parser) {
                const graph = parser.buildGraph();
                const node = graph.get(item.ItemOID);

                // If this question has dependencies (is conditional)
                if (node && node.dependsOn && node.dependsOn.length > 0) {
                  // Check if ALL parents in the dependency chain exist
                  let shouldSkipSync = false;

                  for (const dep of node.dependsOn) {
                    const parentOID = dep.questionOID;
                    const parentAnswer = answersData[parentOID];

                    // If parent doesn't exist in answersData, skip syncing this child
                    if (!parentAnswer) {
                      shouldSkipSync = true;
                      break;
                    }

                    // Check if parent has a valid selection (for CodeList types)
                    const parentHasEnabledOption = parentAnswer.CodeList?.some((cl: any) =>
                      cl.CodeListItem?.some((cli: any) => cli.IsEnabled === true)
                    );

                    // Check if parent has a value (for non-CodeList types)
                    const parentHasValue = parentAnswer.value !== undefined &&
                      parentAnswer.value !== '' &&
                      parentAnswer.value !== null;

                    if (!parentHasValue && !parentHasEnabledOption) {
                      shouldSkipSync = true;
                      break;
                    }
                  }

                  if (shouldSkipSync) {
                    return; // Skip this item
                  }
                }
              }

              // Check if this answer is missing or different from answersData
              //Skip items that were recently deleted by multi-instance cleanup
              if (recentlyCleanedItemsRef.current.has(answerKey)) {
                return; // Skip - this item was recently cleaned
              }

              if (!answersData[answerKey] || answersData[answerKey]?.value !== item.Value || hasEnabledCodeListOption) {
                needsSync = true;

                // Force sync the data - include CodeList for items with selections
                setAnswersData(prev => {
                  const existing = prev[answerKey] || {};
                  return {
                    ...prev,
                    [answerKey]: {
                      // MERGE onto the existing entry rather than replacing it. For a
                      // singleselect operand (e.g. I.1) the selected code lives in CodeList,
                      // NOT in Value (Value is ''), so a wholesale replace dropped the
                      // CodeList — and cross-group methods like M.12 (I.73) then read the
                      // operand as empty and stuck at their fallback. Spreading `existing`
                      // first keeps that CodeList (and itemOID/dataType) when the incoming
                      // itemGroupData snapshot omits them.
                      ...existing,
                      value: item.Value || '',
                      itemGroupOID: itemGroupEntry.ItemGroupOID,
                      repeatKey: item.ItemGroupRepeatKey || 0,
                      timestamp: Date.now(),
                      syncedFromItemGroupData: true,
                      ...(item.CodeList && item.CodeList.length > 0 ? { CodeList: item.CodeList } : {}),
                      // Preserve the derived deletion flag (e.g. JobType I.69/70/71) carried by
                      // the source itemGroupData. Without this, syncing strips `isDeleted` and the
                      // submit ODM omits it — so a deselected service's tasks would not be deleted.
                      ...(Object.prototype.hasOwnProperty.call(item, 'isDeleted')
                        ? { isDeleted: isDeletedFlag(item.isDeleted) }
                        : {})
                    }
                  };
                });
              }
            }
          });
        }
      });
    }
  }, [itemGroupData, conditionDef]);

  useEffect(() => {
    try {
      if (!isAnswersDataStable && itemGroupData && itemGroupData.length > 0) {
        let hasPopulatedItemData = false;
        let totalItemDataCount = 0;

        for (const group of itemGroupData) {
          if (group.ItemData && Array.isArray(group.ItemData) && group.ItemData.length > 0) {
            hasPopulatedItemData = true;
            totalItemDataCount += group.ItemData.length;
          }
        }

        if (!hasPopulatedItemData) {
          return;
        }

        // Now check if answersData has keys for multi-instance ItemData
        const missingKeys: string[] = [];
        let expectedMultiInstanceCount = 0;

        for (const group of itemGroupData) {
          const repeat = group.ItemGroupRepeatKey || 0;
          if (repeat > 0) { // Only check multi-instance groups
            for (const it of group.ItemData || []) {
              expectedMultiInstanceCount++;
              const key = `${it.ItemOID}_${repeat}`;
              if (!answersData[key]) {
                missingKeys.push(key);
                if (missingKeys.length >= 10) break;
              }
            }
          }
          if (missingKeys.length >= 10) break;
        }

        if (expectedMultiInstanceCount === 0) {
          return;
        }

        if (missingKeys.length === 0) {
          setIsAnswersDataStable(true);
        }
      }
    } catch (e) {
      // Error in stability check - non-critical
    }
  }, [itemGroupData, answersData, isAnswersDataStable]);

  // Handle show/hide functionality for conditional questions
  const handleShow = React.useCallback((shouldShow: boolean, conditionOID: string, parentItemOID: string, value: any, isRepeat: boolean, repeatIndex: number) => {

    if (shouldShow) {
      const conflictingConditions: string[] = [];

      Object.values(allQuestionsData).forEach(stepQuestions => {
        stepQuestions.forEach(question => {
          if (question.isConditional && question.conditionOID !== conditionOID) {
            const condition = conditionDef.find(cd => cd.OID === question.conditionOID);
            if (condition && condition.FormalExpression?.text?.includes(parentItemOID)) {
              conflictingConditions.push(question.conditionOID);
            }
          }
        });
      });

      if (conflictingConditions.length > 0) {
        setConditionalQuestionVisibility(prev => {
          const newVisibility = { ...prev };
          conflictingConditions.forEach(conflictCondOID => {
            newVisibility[conflictCondOID] = false;
          });
          newVisibility[conditionOID] = true;
          return newVisibility;
        });

        const questionsToCleanup: string[] = [];
        Object.values(allQuestionsData).forEach(stepQuestions => {
          stepQuestions.forEach(question => {
            if (question.isConditional && conflictingConditions.includes(question.conditionOID)) {
              questionsToCleanup.push(question.id);
            }
          });
        });

        if (questionsToCleanup.length > 0) {
          setAnswersData(prev => {
            const newAnswersData = { ...prev };
            questionsToCleanup.forEach(questionId => {
              delete newAnswersData[questionId];
            });
            return newAnswersData;
          });

          setItemGroupData(prev => {
            return prev.map(itemGroup => ({
              ...itemGroup,
              ItemData: itemGroup.ItemData.filter(itemData =>
                !questionsToCleanup.includes(itemData.ItemOID)
              )
            }));
          });
        }
      } else {
        setConditionalQuestionVisibility(prev => ({
          ...prev,
          [conditionOID]: true
        }));
      }
    } else {
      // Hiding a conditional question
      setConditionalQuestionVisibility(prev => ({
        ...prev,
        [conditionOID]: false
      }));

      // Find all questions that depend on this condition and clean up their answers
      const questionsToCleanup: string[] = [];
      Object.values(allQuestionsData).forEach(stepQuestions => {
        stepQuestions.forEach(question => {
          if (question.isConditional && question.conditionOID === conditionOID) {
            questionsToCleanup.push(question.id);
          }
        });
      });

      if (questionsToCleanup.length > 0) {
        setAnswersData(prev => {
          const newAnswersData = { ...prev };
          questionsToCleanup.forEach(questionId => {
            delete newAnswersData[questionId];
          });
          return newAnswersData;
        });

        // Also clean up from itemGroupData
        setItemGroupData(prev => {
          return prev.map(itemGroup => ({
            ...itemGroup,
            ItemData: itemGroup.ItemData.filter(itemData =>
              !questionsToCleanup.includes(itemData.ItemOID)
            )
          }));
        });
      }
    }
  }, [allQuestionsData, conditionDef]);

  const handleShowFromCard = React.useCallback((item: any) => {
  }, []);

  const handleShowRepeat = (data: any) => {

    // Check if this contains instance answer data
    if (data && Array.isArray(data)) {
      // Check if any of these items contain answer data
      data.forEach((item, index) => {
        if (item?.ItemData && Array.isArray(item.ItemData)) {

          // Try to process this data as answers
          item.ItemData.forEach(itemData => {
            if (itemData.ItemOID && itemData.Value !== undefined && itemData.Value !== null) {

              let answerKey = itemData.ItemOID;
              if (itemData.ItemGroupRepeatKey && itemData.ItemGroupRepeatKey > 0) {
                answerKey = `${itemData.ItemOID}_${itemData.ItemGroupRepeatKey}`;
              }

              setAnswersData(prev => ({
                ...prev,
                [answerKey]: {
                  value: itemData.Value,
                  itemGroupOID: item.OID,
                  repeatKey: itemData.ItemGroupRepeatKey || 0,
                  timestamp: Date.now()
                }
              }));
            }
          });
        }
      });
    }
  };

  // Function to get filtered repeating children that excludes hidden RepeatCount questions
  const getFilteredRepeatingChildren = (repeatingChildren: any[], itemGroupData: any[]): any[] => {
    if (!repeatingChildren || repeatingChildren.length === 0) {
      return [];
    }

    const hiddenRepeatCountQuestions = getHiddenRepeatCountQuestions(repeatingChildren, itemGroupData);

    if (hiddenRepeatCountQuestions.length === 0) {
      return repeatingChildren;
    }

    const filtered = repeatingChildren.filter(child => {
      const isHidden = hiddenRepeatCountQuestions.includes(child.controlItemOID);
      return !isHidden;
    });

    return filtered;
  };

  const getFilteredItemGroupData = React.useCallback((originalItemGroupData: any[], repeatingChildren: any[]): any[] => {
    if (!repeatingChildren || repeatingChildren.length === 0) {
      return originalItemGroupData;
    }

    const hiddenRepeatCountQuestions = getHiddenRepeatCountQuestions(repeatingChildren, originalItemGroupData);

    if (hiddenRepeatCountQuestions.length === 0) {
      return originalItemGroupData;
    }

    return originalItemGroupData.map(itemGroup => ({
      ...itemGroup,
      ItemData: itemGroup.ItemData.filter(itemData => {
        const isInstanceData = itemData.ItemOID.includes('_') && !isNaN(parseInt(itemData.ItemOID.split('_')[1]));

        if (isInstanceData) {
          const baseItemOID = itemData.ItemOID.split('_').slice(0, -1).join('_');

          // Find which RepeatCount question controls this instance
          for (const child of repeatingChildren) {
            if (child.itemGroupDef?.ItemRef?.some((ref: any) => ref.ItemOID === baseItemOID)) {
              if (hiddenRepeatCountQuestions.includes(child.controlItemOID)) {
                return false;
              }
            }
          }
        }

        return true;
      })
    }));
  }, [activeStep, allQuestionsData, steps]); // Dependencies for getHiddenRepeatCountQuestions

  const getHiddenRepeatCountQuestions = (repeatingChildren: any[], itemGroupData: any[]): string[] => {
    const hiddenQuestions: string[] = [];

    if (!repeatingChildren || !itemGroupData) {
      return hiddenQuestions;
    }

    repeatingChildren.forEach(child => {
      const controlItemOID = child.controlItemOID;

      // Find the RepeatCount question in step questions
      const stepQuestions = allQuestionsData[visibleSteps[activeStep]?.id || ''] || [];
      const repeatCountQuestion = stepQuestions.find(q => q.id === controlItemOID);

      if (repeatCountQuestion && repeatCountQuestion.isConditional && repeatCountQuestion.itemRef) {
        const isVisible = evaluateConditionalQuestionVisibility(
          repeatCountQuestion.itemRef,
          repeatCountQuestion.id,
          itemGroupData
        );

        if (!isVisible) {
          hiddenQuestions.push(controlItemOID);
        }
      }
    });

    return hiddenQuestions;
  };

  const handleShowGroupQus = (data: any) => {

    // Check if this contains answer data that we need to process
    if (data) {
      // Try to find any answer data in various formats
      if (Array.isArray(data)) {
        data.forEach((item, index) => {
          if (item?.ItemData) {
          }
        });
      } else if (data && typeof data === 'object') {
        if (data.ItemData || data.ItemGroup) {
        }
      }
    }
  };

  // ==================== VALIDATION FUNCTIONS ====================


  const getItemsWithValidationErrors = (): ItemDef[] => {
    if (!attemptedNext || Object.keys(validationErrors).length === 0) {
      return [];
    }

    // Get all items that have validation errors and are mandatory
    const itemsWithErrors: ItemDef[] = [];

    Object.keys(validationErrors).forEach(questionId => {
      // For multi-instance questions, extract the ItemOID (before the underscore)
      const itemOID = questionId.includes('_') ? questionId.split('_')[0] : questionId;

      // Check if this item is mandatory
      if (mandatoryQuestions[itemOID]) {
        const itemDef_obj = itemDef.find(item => item.OID === itemOID);
        if (itemDef_obj && !itemsWithErrors.some(item => item.OID === itemOID)) {
          itemsWithErrors.push(itemDef_obj);
        }
      }
    });

    return itemsWithErrors;
  };

  const validateAnswer = (questionId: string, answer: any, questionDef?: ItemDef): string | null => {
    if (!questionDef) return null;

    // Check if answer exists
    const actualValue = typeof answer === 'object' && answer.value !== undefined ? answer.value : answer;

    if (actualValue === undefined || actualValue === null || actualValue === '') {
      return 'This field is required';
    }

    // Validate based on data type
    switch (questionDef.DataType) {
      case 'integer':
        if (!/^-?\d+$/.test(String(actualValue))) {
          return 'Please enter a valid integer';
        }
        break;

      case 'float':
        if (!/^-?(?:\d+(?:\.\d+)?|\.\d+)$/.test(String(actualValue).trim())) {
          return 'Please enter a valid number';
        }
        break;

      case 'text':
      case 'string':
      case 'multilinetext':
        if (String(actualValue).trim() === '') {
          return 'This field cannot be empty';
        }
        break;

      case 'singleselect':
      case 'multiselect':
        if (Array.isArray(actualValue) && actualValue.length === 0) {
          return 'Please select at least one option';
        }
        break;

      case 'boolean':
        // Boolean is always valid if it exists
        break;
    }

    return null;
  };


  const validateStep = (stepId: string): number => {

    const stepQuestions = allQuestionsData[stepId] || [];
    const errors: { [questionId: string]: string } = {};
    let hasErrors = false;

    // Get hierarchically organized questions to handle instances
    const hierarchicalQuestions = organizeQuestionsHierarchically(stepQuestions, stepId);

    // Convert answersData to ItemGroupData format for conditional evaluation
    const currentItemGroupData = convertAnswersDataToItemGroupData(answersData);

    // Helper to validate questions recursively
    const validateQuestionRecursively = (question: any) => {
      if (question.isRepeatingInstance && question.repeatingChild) {
        // Validate each question in the repeating instance
        const itemRefs = question.repeatingChild.itemGroupDef?.ItemRef || [];
        const repeatKey = question.instanceNum;

        itemRefs.forEach((itemRef: any) => {
          const isMandatory = itemRef.Mandatory === 'Yes';
          if (isMandatory) {
            const questionKey = `${itemRef.ItemOID}_${repeatKey}`;
            const answer = answersData[questionKey];
            const questionDef = itemDef.find(item => item.OID === itemRef.ItemOID);
            const error = validateAnswer(questionKey, answer, questionDef);



            if (error) {
              errors[questionKey] = error;
              hasErrors = true;
            }
          }
        });
      } else {
        // Regular question validation
        const itemOID = question.itemOID || question.id;
        // Prefer live ItemRef (same tick as template) — mandatoryQuestions state is filled in an effect and can lag after load.
        const isMandatory =
          mandatoryQuestions[itemOID] || question.itemRef?.Mandatory === 'Yes';

        if (isMandatory) {
          // Check if question is conditionally visible
          if (question.isConditional && question.itemRef) {
            const isVisible = evaluateConditionalQuestionVisibility(
              question.itemRef,
              question.id,
              currentItemGroupData
            );

            if (!isVisible) {
              return;
            }
          }

          const answer = answersData[question.id];
          const questionDef = itemDef.find(item => item.OID === itemOID);
          const error = validateAnswer(question.id, answer, questionDef);

          if (error) {
            errors[question.id] = error;
            hasErrors = true;
          }
        }
      }

      if (question.children && question.children.length > 0) {
        question.children.forEach((child: any) => validateQuestionRecursively(child));
      }
    };

    // Validate all questions
    hierarchicalQuestions.forEach(question => validateQuestionRecursively(question));

    setValidationErrors(errors);
    return Object.keys(errors).length;
  };

  const clearValidationError = (questionId: string) => {
    setValidationErrors(prev => {
      const newErrors = { ...prev };
      delete newErrors[questionId];
      return newErrors;
    });
  };

  useEffect(() => {
    if (!attemptedNext) return;
    setValidationErrors(prev => {
      const errorKeys = Object.keys(prev);
      if (errorKeys.length === 0) return prev;
      const keysToClean = errorKeys.filter(errorKey => {
        const baseKey = errorKey.includes('_') ? errorKey.split('_')[0] : errorKey;
        const answer = answersData[errorKey] ?? answersData[baseKey];
        const value = typeof answer === 'object' ? (answer as any)?.value : answer;
        return value !== undefined && value !== null && value !== '';
      });
      if (keysToClean.length === 0) return prev;
      const newErrors = { ...prev };
      keysToClean.forEach(key => delete newErrors[key]);
      return newErrors;
    });
  }, [answersData, attemptedNext]);

  const validateAllSteps = (): { isValid: boolean; totalCount: number } => {
    let allValid = true;
    let totalCount = 0;

    visibleSteps.forEach((step) => {
      if (step.id === 'summary') return;

      const count = validateStep(step.id);
      if (count > 0) {
        allValid = false;
        totalCount += count;
      }
    });

    return { isValid: allValid, totalCount };
  };

  // ==================== END VALIDATION FUNCTIONS ====================

  // Handle step click for CustomStepper
  const handleStepClick = (stepIndex: number) => {
    // Allow navigation to any step
    setActiveStep(stepIndex);
    setAttemptedNext(false); // Reset validation attempt flag on manual navigation
  };

  /** Sidebar accordion header: jump to section (same indices as visibleSteps). */
  const handleSidebarSectionClick = (stepIndex: number) => {
    setActiveStep(stepIndex);
    setAttemptedNext(false);
    const sid = visibleSteps[stepIndex]?.id;
    if (sid) {
      setExpandedAccordions(prev => ({ ...prev, [sid]: true }));
    }
  };

  // Question navigation handlers
  const handleQuestionChange = (questionIndex: number) => {
    const currentStepId = visibleSteps[activeStep]?.id;
    if (currentStepId) {
      setCurrentQuestionIndex(prev => ({
        ...prev,
        [currentStepId]: questionIndex
      }));
    }
  };

  const handleQuestionComplete = (questionData: any) => {

    const currentStepId = visibleSteps[activeStep]?.id;
    const currentQIndex = currentQuestionIndex[currentStepId] || 0;

    if (currentStepId) {
      setQuestionCompletionStatus(prev => ({
        ...prev,
        [currentStepId]: {
          ...prev[currentStepId],
          [currentQIndex]: true
        }
      }));
    }
  };

  // Check if current step can proceed
  const canProceed = () => {
    return true;
  };

  const findCorrectCodeListForQuestion = (questionId: string, codedValue: string): string => {
    let baseQuestionId = questionId;
    if (questionId.includes('_')) {
      baseQuestionId = questionId.split('_')[0]; // I.28_1 -> I.28
    }

    const questionDef = itemDef.find(item => item.OID === baseQuestionId);

    if (!questionDef) {
      return codedValue;
    }

    // Use the CodeListRef to find the correct CodeList (same as main area)
    const correctCodeListOID = questionDef.CodeListRef?.CodeListOID;
    if (!correctCodeListOID) {
      return codedValue;
    }

    // Find the correct CodeList using the reference
    const correctCodeList = codeList.find(cl => cl.OID === correctCodeListOID);
    if (!correctCodeList) {
      return codedValue;
    }

    // Find the correct display text
    const codeItem = correctCodeList.CodeListItem?.find(cli => cli.CodedValue === codedValue);
    const displayText = codeItem?.Decode?.TranslatedText?.text;

    return displayText || codedValue;
  };

  // Get answer display text
  const getAnswerDisplayText = (questionId: string, dataType: string) => {
    let answer = answersData[questionId];

    // Generic instance answer lookup - works for ANY question with instance suffix
    if (!answer && questionId.includes('_')) {
      const baseQuestionId = questionId.split('_')[0];
      answer = answersData[baseQuestionId];
    }

    // Return null if no answer found
    if (!answer || answer.value === undefined || answer.value === null) {
      return null;
    }

    // Handle different data types
    switch (dataType) {
      case 'boolean':
        // Handle both boolean values (true/false) and string values (Yes/No)
        let boolResult;
        if (answer.value === 'Yes' || answer.value === 'No') {
          boolResult = answer.value; // Already in correct format
        } else {
          boolResult = answer.value === 'true' || answer.value === true ? 'Yes' : 'No';
        }
        return boolResult;
      case 'integer':
      case 'float':
        return answer.value.toString();
      case 'string':

        return answer.value;
      case 'singleselect':
      default:
        if (answer.value === '') {
          return 'Not selected';
        }

        const correctDisplayText = findCorrectCodeListForQuestion(questionId, answer.value);
        return correctDisplayText;
    }
  };

  // Check if question has conditional dependencies
  const getConditionalQuestions = (parentQuestionId: string, stepId: string) => {
    const stepQuestions = allQuestionsData[stepId] || [];

    const conditionals = stepQuestions.filter(q => {
      if (!q.isConditional || !q.conditionOID) return false;

      // Find the condition definition
      const condition = conditionDef.find(cd => cd.OID === q.conditionOID);
      if (!condition) return false;

      // Parse the condition to see if it depends on the parent question
      const conditionText = condition.FormalExpression?.text || '';
      const conditionItemOID = conditionText.split(/[=!<>]/)[0].trim();

      const isChild = conditionItemOID === parentQuestionId;

      return isChild;
    });

    return conditionals;
  };

  const handleQuestionJump = (stepIndex: number, questionIndex: number) => {
    // Navigate to the step first
    setActiveStep(stepIndex);
  };

  const createQuestionPositionMap = (stepId: string) => {
    const step = steps.find(s => s.id === stepId);
    if (!step || !step.itemGroupDef || !step.itemGroupDef.ItemRef) {
      return new Map();
    }

    const questionMap = new Map();
    let mainQuestionIndex = 0;

    // Build map based on actual ItemRef order but only count main questions for navigation
    step.itemGroupDef.ItemRef.forEach((itemRef: any, originalIndex: number) => {
      const itemDefinition = itemDef.find(item => item.OID === itemRef.ItemOID);
      if (!itemDefinition) return;

      const question = {
        id: itemRef.ItemOID,
        itemRef,
        itemDef: itemDefinition,
        isConditional: !!itemRef.CollectionExceptionConditionOID,
        conditionOID: itemRef.CollectionExceptionConditionOID,
        originalIndex: originalIndex,
        mainQuestionIndex: mainQuestionIndex // Navigation index (only main questions)
      };

      // Always map the question, but only increment mainQuestionIndex for non-conditional, non-derived questions
      questionMap.set(itemRef.ItemOID, question);

      // Only increment the navigation index for main (non-conditional, non-derived) questions
      // This must match the allVisibleQuestions filter in QuestionRendererCard_v2.tsx, which excludes:
      //   - isConditional (CollectionExceptionConditionOID present)
      //   - MethodOID (derived/calculated fields like I.200, I.201, I.209-I.212)
      if (!question.isConditional && !itemRef.MethodOID) {
        mainQuestionIndex++;
      }
    });

    return questionMap;
  };

  const navigateToQuestion = (stepId: string, questionId: string) => {

    // Find the step index
    const stepIndex = visibleSteps.findIndex(step => step.id === stepId);
    if (stepIndex === -1) {
      // Step not found - navigation aborted
      return;
    }

    // Get accurate question position mapping
    const questionMap = createQuestionPositionMap(stepId);
    const questionInfo = questionMap.get(questionId);

    if (!questionInfo) {
      return;
    }

    // Navigate to step
    setActiveStep(stepIndex);

    let navigationIndex = 0;
    // The MAIN (non-conditional) question whose card page actually shows this question.
    // For a conditional sub-question that is the root parent; for a main question itself.
    // The card resolves this OID→index against its own list (see navigateToMainOID).
    let targetMainOID = questionId;

    if (questionInfo.isConditional) {
      const findRootParent = (currentQuestion: any): any => {
        const allQuestions = allQuestionsData[stepId] || [];

        // Create a question object with the proper structure for findParentQuestion
        const questionWithCondition = {
          id: currentQuestion.id,
          conditionOID: currentQuestion.conditionOID || currentQuestion.itemRef?.CollectionExceptionConditionOID
        };

        const parent = findParentQuestionUtil(questionWithCondition, allQuestions, conditionDef);

        if (!parent || !parent.conditionOID) {
          // This parent is a main question, return it
          return parent;
        } else {
          // This parent is also conditional, keep going up
          return findRootParent(parent);
        }
      };

      const rootParent = findRootParent(questionInfo);
      if (rootParent) {
        const parentInfo = questionMap.get(rootParent.id);
        navigationIndex = parentInfo ? parentInfo.mainQuestionIndex : 0;
        targetMainOID = rootParent.id || questionId;
      }
    } else {
      // For main questions, use their main question index
      navigationIndex = questionInfo.mainQuestionIndex;
    }

    // Fallback index (used until the card resolves the OID by identity); the card's
    // navigateToMainOID effect is authoritative and corrects any index drift.
    setCurrentQuestionIndex(prev => ({
      ...prev,
      [stepId]: navigationIndex
    }));

    // Authoritative jump: let the active card page to this question by OID.
    setNavigateToMainOID({
      stepId: stepId,
      oid: targetMainOID,
      timestamp: Date.now()
    });

    // Store navigation target for highlighting
    setNavigationTarget({
      stepId: stepId,
      questionId: questionId,
      timestamp: Date.now()
    });
  };

  // Enhanced navigation with visual feedback
  const navigateToQuestionWithFeedback = (stepId: string, questionId: string) => {

    // Set navigation target for visual feedback
    setNavigationTarget({ stepId, questionId, timestamp: Date.now() });

    // Navigate to the question
    navigateToQuestion(stepId, questionId);

    // Clear navigation target after animation completes
    setTimeout(() => {
      setNavigationTarget(null);
    }, 3000);
  };

  // Helper function to find parent question for a conditional question
  const findParentQuestion = (conditionalQuestion: any, stepQuestions: any[]) => {
    if (!conditionalQuestion.conditionOID) return null;

    const condition = conditionDef.find(cd => cd.OID === conditionalQuestion.conditionOID);
    if (!condition) return null;

    const conditionText = condition.FormalExpression?.text || '';
    let parentItemOID: string | null = null;

    // Handle OR conditions
    if (conditionText.includes('||')) {
      const orParts = conditionText.split('||');
      // For now, use the first condition's parent (could be enhanced for multiple parents)
      parentItemOID = orParts[0].split('==')[0]?.replace(/[&/\\#,+()$~%'":*?<>{}! ]/g, '') || null;
    } else {
      // Handle simple conditions
      parentItemOID = conditionText.split('==')[0]?.replace(/[&/\\#,+()$~%'":*?<>{}! ]/g, '') || null;
    }

    return stepQuestions.find(q => q.id === parentItemOID) || null;
  };

  // Helper function to find all child questions for a parent question
  const findChildQuestions = (parentQuestion: any, conditionalQuestions: any[]) => {
    return conditionalQuestions.filter(conditionalQuestion => {
      const parent = findParentQuestion(conditionalQuestion, [parentQuestion]);
      return parent && parent.id === parentQuestion.id;
    });
  };


  const organizeQuestionsHierarchically = React.useCallback((stepQuestions: any[], stepId?: string) => {
    const state: HierarchyUtilsState = {
      conditionDef,
      answersData,
      allQuestionsData,
      codeList,
      steps
    };

    return organizeQuestionsHierarchicallyUtil(
      stepQuestions,
      stepId,
      getRepeatingInstanceCount,
      state
    );
  }, [conditionDef, answersData, allQuestionsData, codeList, steps, getRepeatingInstanceCount]);

  const evaluateConditionalQuestionVisibility = (itemRef: any, conditionalQuestionOID: string, itemGroupDatas: any[]) => {
    if (isApplyingSuffixSyncRef.current) {
      return true;
    }

    return evaluateConditionalQuestionVisibilityUtil(
      itemRef,
      conditionalQuestionOID,
      itemGroupDatas,
      conditionDef,
      answersData  // Pass V2 answers for stale data validation
    );
  };

  const applyAutoFillForVisibleQuestions = React.useCallback((visibleOIDs: string[], currentAnswers: Record<string, any>) => {
    try {
      const currentItemDefs: any[] = itemDef || [];
      if (currentItemDefs.length === 0 || !data?.answerData) return;
      const patch: { [key: string]: any } = {};

      visibleOIDs.forEach((oid) => {
        const def = currentItemDefs.find((d: any) => d.OID === oid);
        if (!def?.AutoFillRef?.TradeOID || !def?.AutoFillRef?.ItemOID) return;

        const { TradeOID, ItemOID, Index } = def.AutoFillRef;
        const sourceIndex = Index ?? 0;
        const sourceGroups = getSourceItemGroups(TradeOID);

        const valuesByIndex: string[] = [];
        sourceGroups.forEach((group: any) => {
          (group.ItemData || []).forEach((item: any) => {
            if (item.ItemOID === ItemOID && item.Value) {
              const idx = item.ItemGroupRepeatKey ?? 0;
              valuesByIndex[idx] = item.Value;
            }
          });
        });

        const compactValues = valuesByIndex.filter(Boolean);
        const resolvedValue = compactValues[sourceIndex];
        if (!resolvedValue) return;
        let targetGroupOID = '';
        for (const ig of (itemGroup || [])) {
          if (ig.ItemRef?.some((ref: any) => ref.ItemOID === oid)) {
            targetGroupOID = ig.OID;
            break;
          }
        }
        if (!targetGroupOID) return;
        const existing = currentAnswers[oid];
        const isEmpty = !existing || existing.value === '' || existing.value === null || existing.value === undefined;
        const wasCrossLinked = existing?._crossLinkedFromAsset === true;
        if (isEmpty || wasCrossLinked) {
          patch[oid] = {
            value: resolvedValue,
            itemGroupOID: targetGroupOID,
            repeatKey: 0,
            timestamp: Date.now(),
            dataType: def.DataType || 'text',
            originalItemData: { ItemOID: oid, Value: resolvedValue, ItemGroupRepeatKey: 0 },
            _crossLinkedFromAsset: true,
          };
        }
      });

      if (Object.keys(patch).length > 0) {
        setAnswersData(prev => ({ ...prev, ...patch }));
        const patchesByGroup: { [groupOID: string]: any[] } = {};
        Object.values(patch).forEach((entry: any) => {
          if (!patchesByGroup[entry.itemGroupOID]) patchesByGroup[entry.itemGroupOID] = [];
          patchesByGroup[entry.itemGroupOID].push({ ItemOID: entry.originalItemData.ItemOID, Value: entry.value, ItemGroupRepeatKey: 0 });
        });
        setItemGroupData(prev => {
          const updated = [...prev];
          Object.entries(patchesByGroup).forEach(([groupOID, newItems]) => {
            const igIdx = updated.findIndex((g: any) => g.ItemGroupOID === groupOID && g.ItemGroupRepeatKey === 0);
            if (igIdx === -1) {
              updated.push({ ItemGroupOID: groupOID, ItemGroupRepeatKey: 0, ItemData: newItems } as any);
            } else {
              const group: any = { ...updated[igIdx], ItemData: [...((updated[igIdx] as any).ItemData || [])] };
              newItems.forEach((newItem: any) => {
                const existIdx = group.ItemData.findIndex((i: any) => i.ItemOID === newItem.ItemOID);
                // Every item here already passed the isEmpty||wasCrossLinked gate that
                // built `patch`, so mirror that decision into itemGroupData
                // unconditionally. Skipping a non-empty existing value here (the old
                // guard) let itemGroupData keep the STALE value while answersData took
                // the new one; a later answersData⇄itemGroupData sync then reverted the
                // answer — e.g. changing the Asset Information square feet snapped the
                // Ball Park Estimate back to the previous value.
                if (existIdx === -1) group.ItemData.push(newItem);
                else group.ItemData[existIdx] = newItem;
              });
              updated[igIdx] = group;
            }
          });
          return updated;
        });
      }
    } catch (error: any) {
      setError(true);
      setErrorCode(error?.response?.status);
      setErrorReason(error?.response?.data?.message || 'Failed to load template. Please try again or contact support.');
      setSteps([]);
    }
  }, [itemDef, itemGroup, data, odmData, getSourceItemGroups]);

  // ── Re-propagate asset cross-links when the asset ODM changes mid-session ────
  // The load-time cross-link (guarded by assetCrossLinkAppliedRef) only runs when
  // the template is (re)fetched — i.e. on trade/customer/asset change, NOT when the
  // asset ODM answers change. Because Requirements_v3 stays mounted across wizard
  // steps (CSS display:none), editing the Asset Information square feet and returning
  // updates data.answerData[A.1] but never re-pulled the new value into the
  // cross-linked trade answer — leaving BallParkEstimate stuck on "Enter square feet".
  // This effect re-applies the AutoFillRef cross-link whenever the asset ODM source
  // changes, so downstream (bpeData.sqFt, qa_responses) reflects the new value.
  const autoFillSourceSig = React.useMemo(() => {
    try { return JSON.stringify(data?.answerData ?? null); } catch { return ''; }
  }, [data]);
  useEffect(() => {
    // Baseline the first run — the load-time cross-link handles the initial values.
    if (lastAutoFillSourceSigRef.current === null) {
      lastAutoFillSourceSigRef.current = autoFillSourceSig;
      return;
    }
    if (lastAutoFillSourceSigRef.current === autoFillSourceSig) return;
    lastAutoFillSourceSigRef.current = autoFillSourceSig;

    const autoFillOIDs = (itemDef || [])
      .filter((d: any) => d?.AutoFillRef?.TradeOID && d?.AutoFillRef?.ItemOID)
      .map((d: any) => d.OID);
    if (autoFillOIDs.length > 0) {
      applyAutoFillForVisibleQuestions(autoFillOIDs, answersDataRef.current || {});
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoFillSourceSig, itemDef]);

  const recomputeConditionalVisibilityForAnswerKeys = (answerKeys: string[], updatedAnswersData?: Record<string, any>) => {
    try {
      const parser = dependencyGraphParserRef.current;
      if (!parser) {
        return;
      }
      const currentAnswers = updatedAnswersData || { ...answersData };

      const newlyVisibleOIDs: string[] = [];

      answerKeys.forEach((fullKey) => {
        const match = fullKey.match(/^(I\.\d+)(_\d+(?:_\d+)?)$/);
        const baseOID = match ? match[1] : fullKey;
        const instanceSuffix = match ? match[2] : '';

        const children = parser.getChildQuestions(baseOID) || [];

        children.forEach((childBase: string) => {
          // Evaluate visibility for this child within the instance suffix
          const shouldShow = parser.evaluateConditionWithInstance(
            childBase,
            currentAnswers,
            instanceSuffix
          );

          // Find conditionOID for the child question from allQuestionsData
          let conditionOID: string | null = null;
          Object.values(allQuestionsData).forEach((stepQuestions: any[]) => {
            stepQuestions.forEach((q: any) => {
              if (q.id === childBase && q.conditionOID) {
                conditionOID = q.conditionOID;
              }
            });
          });

          if (conditionOID) {
            setConditionalQuestionVisibility(prev => ({
              ...prev,
              [conditionOID!]: shouldShow
            }));
          }

          if (shouldShow) {
            newlyVisibleOIDs.push(childBase);
          }
        });
      });

      if (newlyVisibleOIDs.length > 0) {
        applyAutoFillForVisibleQuestions(newlyVisibleOIDs, currentAnswers);
      }
    } catch (e) {
      // Error recomputing conditional visibility - non-critical
    }
  };

  // Helper function to convert answersData to ItemGroupData format for consistency with main content area
  const convertAnswersDataToItemGroupData = (answersData: any) => {
    return convertAnswersDataToItemGroupDataUtil(
      answersData,
      allQuestionsData,
      codeList
    );
  };

  // Get all conditional questions recursively (for multi-level conditional questions)
  const getAllConditionalQuestions = (parentQuestionId: string, stepId: string, level: number = 0): any[] => {
    if (level > 5) return []; // Prevent infinite recursion

    const directConditionals = getConditionalQuestions(parentQuestionId, stepId);
    let allConditionals: any[] = [];

    directConditionals.forEach(condQuestion => {
      // Use unified logic for consistency
      const itemGroupDatas = convertAnswersDataToItemGroupData(answersData);
      const shouldShow = condQuestion.itemRef ?
        evaluateConditionalQuestionVisibility(condQuestion.itemRef, condQuestion.id, itemGroupDatas) :
        false;

      if (shouldShow) {
        allConditionals.push({ ...condQuestion, level });
        // Recursively get conditional questions of this conditional question
        const nestedConditionals = getAllConditionalQuestions(condQuestion.id, stepId, level + 1);
        allConditionals = allConditionals.concat(nestedConditionals);
      }
    });

    return allConditionals;
  };

  // Get MethodDef repeat count for a question
  const getMethodDefRepeatCount = (methodOIDOrQuestionId: string) => {
    let methodDefItem = methodDef.find(md => md.OID === methodOIDOrQuestionId);

    if (!methodDefItem) {
      methodDefItem = methodDef.find(md => {
        const expression = md.FormalExpression?.text || '';
        return expression.includes(methodOIDOrQuestionId) &&
          expression.includes('RepeatCount');
      });
    }

    if (!methodDefItem) {
      return 0;
    }
    const expression = methodDefItem.FormalExpression?.text || '';
    const match = expression.match(/!\(([\w.]+)\s*:\s*RepeatCount\)/);

    if (!match) {
      // Could not parse RepeatCount question from expression
      return 0;
    }

    const repeatCountQuestionOID = match[1];

    // Now look up the answer using the ACTUAL QUESTION OID
    const answer = answersData[repeatCountQuestionOID];

    if (answer && answer.value && !isNaN(parseInt(answer.value))) {
      const repeatCount = parseInt(answer.value);

      if (repeatCount > 0 && repeatCount <= 20) {
        return repeatCount;
      } else if (repeatCount > 20) {
        return 0;
      }
    }

    return 0;
  };


  const getCurrentQuestionIndex = (stepId: string): number => {
    return currentQuestionIndex[stepId] || 0;
  };

  // Get item data for a specific repeating instance
  const getRepeatingInstanceItemData = (repeatingGroupOID: string, instanceIndex: number): ItemGroupData[] => {
    const instanceKey = `${repeatingGroupOID}_${instanceIndex}`;
    const instanceData = repeatingInstanceData[instanceKey];

    // Always return a structure with the correct ItemGroupOID and RepeatKey
    const itemGroupData: ItemGroupData = {
      ItemGroupOID: repeatingGroupOID,
      ItemGroupRepeatKey: instanceIndex,
      ItemData: instanceData?.ItemData || []
    };

    return [itemGroupData];
  };

  // Handle data changes for repeating group instances
  const handleRepeatingItemGroupDataChange = (data: any, instanceIndex: number) => {
    if (!data.ItemGroup || !Array.isArray(data.ItemGroup) || data.ItemGroup.length === 0) {
      return;
    }

    const itemGroup = data.ItemGroup[0];
    const repeatingGroupOID = itemGroup.ItemGroupOID;
    const instanceKey = `${repeatingGroupOID}_${instanceIndex}`;

    // Update repeating instance data
    setRepeatingInstanceData(prev => ({
      ...prev,
      [instanceKey]: {
        ItemGroupOID: repeatingGroupOID,
        ItemGroupRepeatKey: instanceIndex,
        ItemData: itemGroup.ItemData || []
      }
    }));

    // Update answers data for sidebar with repeatKey
    if (itemGroup.ItemData && Array.isArray(itemGroup.ItemData)) {
      setAnswersData(prev => {
        const newAnswersData = { ...prev }; // Start with EXISTING data

        itemGroup.ItemData.forEach((item: any) => {
          if (item.ItemOID && item.Value !== undefined && item.Value !== null) {
            // Determine if this group is a nested group by checking repeatingGroupMappings
            const nestedGroupOIDs = new Set(
              repeatingGroupMappings
                .filter(m => m.isNested === true)
                .map(m => m.repeatingGroupOID)
            );
            const isFromNestedGroup = nestedGroupOIDs.has(repeatingGroupOID);

            let answerKey: string;
            if (isFromNestedGroup) {
              // Nested instance: use parent repeat key or fallback to instanceIndex
              const parentKey = item.ParentItemGroupRepeatKey || instanceIndex;
              answerKey = `${item.ItemOID}_${parentKey}_${instanceIndex}`;
            } else {
              // Regular instance: create double-level key (I.35_2)
              answerKey = `${item.ItemOID}_${instanceIndex}`;
            }

            newAnswersData[answerKey] = {
              value: item.Value,
              itemGroupOID: repeatingGroupOID,
              repeatKey: instanceIndex,
              timestamp: Date.now()
            };
          }
        });

        return newAnswersData;
      });
    }
  };

  // Build ItemGroupData from answersData (used before ODM conversion)
  const buildItemGroupDataFromAnswersData = (
    answersData: any,
    itemDef: any[],
    itemGroup: any[],
    codeList: any[]
  ): ItemGroupData[] => {

    // Build a map of ItemGroupOID → { RepeatKey → ItemData[] }
    const groupMap = new Map<string, Map<number, any[]>>();

    // Process each answer in answersData
    Object.entries(answersData).forEach(([answerKey, answerInfo]: [string, any]) => {
      let itemOID = answerKey;
      let repeatKey = 0;
      let parentItemGroupRepeatKey: number | undefined = undefined;

      // Parse the key to extract itemOID, repeatKey, and parentItemGroupRepeatKey
      if (answerKey.includes('_') && !isNaN(parseInt(answerKey.split('_').pop() || ''))) {
        const parts = answerKey.split('_');

        if (parts.length === 3) {
          // Triple-suffix key: I.24_2_1 (itemOID_parentInstance_childInstance)
          itemOID = parts[0];
          parentItemGroupRepeatKey = parseInt(parts[1]);
          repeatKey = parseInt(parts[2]);
        } else if (parts.length === 2) {
          // Double-suffix key: I.35_2 (itemOID_instance)
          itemOID = parts[0];
          repeatKey = parseInt(parts[1]);
        }
      }

      // Find the ItemDef for this question
      const itemDefEntry = itemDef.find((def: any) => def.OID === itemOID);
      if (!itemDefEntry) {
        return;
      }

      // Find the ItemGroupOID that contains this item
      let itemGroupOID = answerInfo.itemGroupOID || 'IG.3'; // Use stored itemGroupOID or default

      // If itemGroupOID not stored in answerInfo, search the template
      if (!answerInfo.itemGroupOID) {
        for (const group of itemGroup) {
          if (group.ItemRef?.some((ref: any) => ref.ItemOID === itemOID)) {
            itemGroupOID = group.OID;
            break;
          }
        }
      }


      // Initialize group map structure if needed
      if (!groupMap.has(itemGroupOID)) {
        groupMap.set(itemGroupOID, new Map());
      }
      const repeatMap = groupMap.get(itemGroupOID)!;
      if (!repeatMap.has(repeatKey)) {
        repeatMap.set(repeatKey, []);
      }

      // Build ItemData entry
      const itemData: any = {
        ItemOID: itemOID,
        Value: answerInfo.value || '',
        CodeList: [],
        ItemGroupRepeatKey: repeatKey
      };

      if (Object.prototype.hasOwnProperty.call(answerInfo, 'isDeleted')) {
        itemData.isDeleted = isDeletedFlag(answerInfo.isDeleted);
      }

      // Add ParentItemGroupRepeatKey if this is a nested instance
      if (parentItemGroupRepeatKey !== undefined) {
        itemData.ParentItemGroupRepeatKey = parentItemGroupRepeatKey;
      }

      const metaKey = answerInfo.MetaKey || itemDefEntry.MetaKey || '';
      if (metaKey) {
        itemData.MetaKey = metaKey;
      }

      // Handle CodeList data
      if (answerInfo.CodeList && answerInfo.CodeList.length > 0) {
        itemData.CodeList = answerInfo.CodeList.map((cl: any) => ({
          ...cl,
          ItemGroupRepeatKey: repeatKey
        }));
      } else {
        const dataType = itemDefEntry.DataType || answerInfo.dataType;
        if (dataType === 'singleselect' || dataType === 'multiselect') {
          const codeListOID = itemDefEntry.CodeListRef?.CodeListOID;
          if (codeListOID) {
            const codeListDef = codeList.find((cl: any) => cl.OID === codeListOID);
            if (codeListDef && codeListDef.CodeListItem) {
              const selectedValues = Array.isArray(answerInfo.value)
                ? answerInfo.value
                : [answerInfo.value];

              const codeListItems = codeListDef.CodeListItem.map((item: any) => ({
                ...item,
                IsEnabled: selectedValues.includes(item.CodedValue)
              }));

              itemData.CodeList = [{
                OID: codeListOID,
                Name: codeListOID,
                DataType: codeListDef.DataType,
                CodeListItem: codeListItems,
                ItemGroupRepeatKey: repeatKey
              }];
              itemData.Value = "";
            }
          }
        }
      }

      // Check for duplicates before adding
      const existingItems = repeatMap.get(repeatKey)!;
      const isDuplicate = existingItems.some((existing: any) => existing.ItemOID === itemOID);

      if (!isDuplicate) {
        existingItems.push(itemData);
      }
    });

    // Convert the map into ItemGroupData array
    const newItemGroupData: ItemGroupData[] = [];
    groupMap.forEach((repeatMap, itemGroupOID) => {
      repeatMap.forEach((itemDataArray, repeatKey) => {
        // Check if this group is nested using template metadata
        let parentItemGroupRepeatKey: number | undefined = undefined;

        // First, try to extract ParentItemGroupRepeatKey from ItemData
        for (const item of itemDataArray) {
          if (item.ParentItemGroupRepeatKey !== undefined) {
            parentItemGroupRepeatKey = item.ParentItemGroupRepeatKey;
            break;
          }
        }

        // If not found, check if this is a nested group
        if (parentItemGroupRepeatKey === undefined) {
          const mapping = repeatingGroupMappings.find(m => m.repeatingGroupOID === itemGroupOID);
          if (mapping?.isNested && mapping?.parentRepeatingGroupOID) {
            // Try to find the parent group in itemGroupData by matching repeat keys
            // For nested groups, the repeatKey typically matches the parent's ItemGroupRepeatKey
            // Look for a parent group (e.g., IG.4) that has this repeat key
            const parentGroups = itemGroupData.filter(g =>
              g.ItemGroupOID === mapping.parentRepeatingGroupOID &&
              g.ItemGroupRepeatKey > 0
            );
            if (parentGroups.length > 0) {
              // If there's a parent group with matching repeat key, use it
              const matchingParent = parentGroups.find(g => g.ItemGroupRepeatKey === repeatKey);
              if (matchingParent) {
                parentItemGroupRepeatKey = repeatKey;
              } else {
                // Default to the first available parent's repeat key
                parentItemGroupRepeatKey = parentGroups[0].ItemGroupRepeatKey;
              }
            }
          }
        }

        const groupData: ItemGroupData = {
          ItemData: itemDataArray,
          ItemGroupOID: itemGroupOID,
          ItemGroupRepeatKey: repeatKey
        };

        if (parentItemGroupRepeatKey !== undefined) {
          groupData.ParentItemGroupRepeatKey = parentItemGroupRepeatKey;
        }

        newItemGroupData.push(groupData);
      });
    });

    return newItemGroupData;
  };

  // =========================================================================================
  // ODM FORMAT CONVERSION - Convert Requirements data to BuildingData format for API
  // =========================================================================================

  const convertToODMFormat = useCallback((): BuildingData | null => {

    try {

      // Validate tradeType is provided - no hardcoded fallback
      if (!tradeType) {
        return null;
      }

      // CRITICAL: Recompute derived MethodDef fields from the CURRENT answers before
      // serializing (port from Requirements_v2). The per-question card effect that
      // maintains these does not reliably fire for cross-card derived flags (e.g. the
      // job-type deletion flags I.69/I.70/I.71 driven by I.66), so on submit the stored
      // answersData can be missing their isDeleted flag. Recomputing here guarantees the
      // ODM carries the correct, current isDeleted for every derived item.
      let derivedAnswers = answersData;
      try {
        const methodItemRefs: any[] = [];
        (itemGroup || []).forEach((g: any) =>
          (g?.ItemRef || []).forEach((r: any) => {
            if (!r?.MethodOID) return;
            const md = methodDef?.find((m: any) => m.OID === r.MethodOID);
            if (md?.FormalExpression?.text?.includes('RepeatCount')) return;
            methodItemRefs.push({ ...r, ItemGroupOID: g.OID, ItemGroupRepeatKey: 0 });
          }),
        );
        if (methodItemRefs.length > 0) {
          derivedAnswers = calculateDerivedFields(
            methodItemRefs,
            methodDef,
            answersData,
            undefined, // no last-edited OID → recompute every derived field
            buildWasteCalcPairs(methodDef, itemGroup as any),
          );
        }
      } catch (e) {
        // Non-fatal — fall back to the stored answers if recompute fails.
        derivedAnswers = answersData;
      }

      // CRITICAL: Rebuild itemGroupData from answersData before conversion
      // This ensures nested items (IG.5, IG.6) are included in the ODM output
      const rebuiltItemGroupData = buildItemGroupDataFromAnswersData(derivedAnswers, itemDef, itemGroup, codeList);

      rebuiltItemGroupData.forEach((group: any) => {
        const trackedGroup = itemGroupData.find(
          (g: any) => g.ItemGroupOID === group.ItemGroupOID && g.ItemGroupRepeatKey === group.ItemGroupRepeatKey
        );
        if (trackedGroup && (trackedGroup as any).SummarizeMode === true) {
          group.SummarizeMode = true;
        }
      });

      // Use the utility function to convert itemGroupData to proper ODM format.
      // The 3rd arg (metaDataVersionOID) is REQUIRED: validateODMStructure rejects ODM
      // missing MetaDataVersionOID, and without it convertToODMFormat returns null on
      // EVERY call → onSave never fires → odmProjectData.current stays frozen at the
      // initial-load value → newly answered questions are never saved. Requirements_v2
      // passes this arg; v3 had dropped it (the autosave-not-saving root cause).
      const odmString = convertItemGroupDataToODM(
        rebuiltItemGroupData,
        tradeType,
        getActiveMetaDataVersionOID(templateData)
      );

      if (!odmString) {
        return null;
      }

      // Validate the ODM structure
      const validation = validateODMStructure(odmString);
      if (!validation.isValid) {
        return null;
      }

      // Parse back to BuildingData object for return
      const buildingData: BuildingData = JSON.parse(odmString);

      return buildingData;

    } catch (error) {
      // Error converting to ODM format
      return null;
    }
  }, [answersData, itemDef, itemGroup, codeList, tradeType, templateData, methodDef]);

  // =========================================================================================
  // EXPOSE DATA TO PARENT VIA REF - useImperativeHandle
  // =========================================================================================

  // Tracks the GBB job-type "reassert" timers scheduled in applyExternalSelection so
  // they can be cleared if the component unmounts before they fire — otherwise the
  // callbacks run against unmounted state setters ("state update on unmounted component").
  const reassertTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  useEffect(() => () => {
    reassertTimersRef.current.forEach(clearTimeout);
    reassertTimersRef.current = [];
  }, []);

  useImperativeHandle(ref, () => ({
    handleSaveFunctionality: () => {

      const buildingData = convertToODMFormat();

      if (!buildingData) {
        // Failed to convert data to ODM format
        return null;
      }

      const dataStr = JSON.stringify(buildingData);
      const currentVersion = templateVersion || ""; // Use stored template version
      const projectOIDString = tradeType || ""; // Use tradeType prop
      const tradeTypeString = tradeType || ""; // Use tradeType prop

      // Call parent callback if provided
      if (onSave) {
        onSave(dataStr, tradeTypeString, currentVersion, projectOIDString);
      }

      return buildingData;
    },

    // FIX BUG #1: Expose validation method to parent component
    validateBeforeProceed: (): boolean => {
      setAttemptedNext(true);

      // Collect all validation errors across all steps
      const allErrors: { [key: string]: string } = {};

      steps.forEach((step) => {
        if (step.id === 'summary') return;

        const stepQuestions = allQuestionsData[step.id] || [];

        const hierarchicalQuestions = organizeQuestionsHierarchically(stepQuestions, step.id);

        const checkQuestion = (question: any) => {
          if (!question || question.hidden) return;

          // Check if question is mandatory - check itemRef.Mandatory property
          const isMandatory = question.itemRef?.Mandatory === 'Yes';
          const questionText = question.itemDef?.Question?.TranslatedText?.text || question.question || question.id;


          if (!isMandatory) return;

          // Check if question has an answer
          const hasAnswer = answersData[question.id] &&
            answersData[question.id].value !== undefined &&
            answersData[question.id].value !== null &&
            answersData[question.id].value !== '';


          if (!hasAnswer) {
            allErrors[question.id] = `${questionText} is required`;
          }

          // Recursively check children
          if (question.children && Array.isArray(question.children)) {
            question.children.forEach(checkQuestion);
          }
        };

        hierarchicalQuestions.forEach(checkQuestion);
      });

      // Update validation errors state
      setValidationErrors(allErrors);

      const errorCount = Object.keys(allErrors).length;

      if (errorCount > 0) {
        setValidationWarningTitle('Missing Required Information');
        setValidationWarningMessage(
          `Please complete ${errorCount} required ${errorCount === 1 ? 'question' : 'questions'} before continuing.\n\n` +
          `Look for questions marked with *.`
        );
        setOpenValidationWarning(true);
        return false;
      }

      // All validations passed
      setValidationErrors({});
      setAttemptedNext(false);
      return true;
    },

    // Apply an external multiselect/job-type selection (e.g. from GBB confirm)
    // the SAME way a real answer change does: update BOTH answersData AND
    // itemGroupData, then remount the question content (restoreKey) so the
    // displayed control re-initialises its internal state from the new
    // selection instead of reverting it. The existing effects then cascade:
    // derived MethodDef recompute (QuestionRendererCard), odmProjectData sync
    // (debounced ODM effect), sessionStorage persist (debounced auto-save),
    // and onDataChange → bidData.requirements. Nothing is hand-patched downstream.
    applyExternalSelection: (oid: string, codes: string) => {
      if (!oid) return;
      const selectedSet = new Set(
        String(codes ?? '').split(',').map((s) => s.trim()).filter(Boolean),
      );
      const flip = (cl: any): any =>
        Array.isArray(cl)
          ? cl.map((c: any) => ({
              ...c,
              CodeListItem: Array.isArray(c?.CodeListItem)
                ? c.CodeListItem.map((it: any) => ({
                    ...it,
                    IsEnabled: selectedSet.has(String(it?.CodedValue ?? '')),
                  }))
                : c?.CodeListItem,
            }))
          : cl;

      // Build the updated answersData with the target multiselect flipped.
      const baseEntry = (answersData && answersData[oid]) || null;
      const updatedAnswers: Record<string, any> =
        baseEntry && Array.isArray(baseEntry.CodeList)
          ? { ...answersData, [oid]: { ...baseEntry, CodeList: flip(baseEntry.CodeList), value: codes } }
          : { ...answersData };

      // Recompute derived MethodDef fields (e.g. I.69/70/71 isDeleted) OURSELVES.
      // The QuestionRendererCard that normally runs calculateDerivedFields is NOT
      // the mounted/displayed card at the Finalize step, so its effect never fires
      // here — leaving the derived flags frozen at the pre-GBB selection. So we run
      // the same calc inline and write the results into both stores.
      let finalAnswers: Record<string, any> = updatedAnswers;
      const derivedOIDs = new Set<string>();
      try {
        const itemRefsWithMethod: any[] = [];
        (itemGroup || []).forEach((g: any) => {
          (g?.ItemRef || []).forEach((ref: any) => {
            if (!ref?.MethodOID) return;
            derivedOIDs.add(ref.ItemOID);
            const md = methodDef?.find((m: any) => m.OID === ref.MethodOID);
            if (md?.FormalExpression?.text?.includes('RepeatCount')) return;
            itemRefsWithMethod.push({ ...ref, ItemGroupOID: g.OID, ItemGroupRepeatKey: 0 });
          });
        });
        if (itemRefsWithMethod.length > 0) {
          const pairs = buildWasteCalcPairs(methodDef, itemGroup as any);
          finalAnswers = calculateDerivedFields(itemRefsWithMethod, methodDef, updatedAnswers, oid, pairs);
        }
      } catch (e) {
        // Non-fatal — fall back to the selection-only update.
        finalAnswers = updatedAnswers;
      }

      setAnswersData(finalAnswers);

      // Pure transform that reflects BOTH the selection and the recomputed derived
      // flags into an itemGroupData array (the store the control + ODM read).
      const dtypeByOID: Record<string, string> = {};
      (itemDef || []).forEach((d: any) => { if (d?.OID) dtypeByOID[d.OID] = d.DataType || ''; });
      const applyToGroups = (groups: any): any =>
        Array.isArray(groups)
          ? groups.map((grp: any) => {
              if (!Array.isArray(grp?.ItemData)) return grp;
              let touched = false;
              const ItemData = grp.ItemData.map((it: any) => {
                const id = String(it?.ItemOID);
                if (id === oid && Array.isArray(it.CodeList) && it.CodeList.length) {
                  touched = true;
                  return { ...it, CodeList: flip(it.CodeList), Value: codes };
                }
                if (derivedOIDs.has(id)) {
                  const fd = finalAnswers[id];
                  if (fd && typeof fd === 'object') {
                    const del = fd.isDeleted === true;
                    const dt = (dtypeByOID[id] || '').toLowerCase();
                    const val = del
                      ? (dt === 'integer' ? '0' : '')
                      : (fd.value != null ? String(fd.value) : (it.Value ?? ''));
                    const next: any = { ...it, Value: val };
                    if (del) next.isDeleted = true;
                    else if ('isDeleted' in next) delete next.isDeleted;
                    touched = true;
                    return next;
                  }
                }
                return it;
              });
              return touched ? { ...grp, ItemData } : grp;
            })
          : groups;

      const newGroups = applyToGroups(itemGroupData);
      setItemGroupData((prev: any) => applyToGroups(prev));

      // AUTHORITATIVE sessionStorage write — synchronous, from the values we just
      // computed (immune to any later clobber). saveRequirementsState merges with
      // the existing blob, so passing just answersData+itemGroupData preserves the
      // rest. This eliminates the stale window where the debounced save lagged and
      // the blob still showed the pre-GBB selection.
      try {
        saveRequirementsState(
          { answersData: finalAnswers, itemGroupData: newGroups },
          data?.customerUid, data?.assetUid, tradeType, data?.jobStartDate,
        );
      } catch (e) {
        console.warn('[GBB→JobType] authoritative session write failed:', e);
      }

      // Remount the question content so the displayed control re-inits from the
      // updated selection.
      setRestoreKey((k) => k + 1);

      // SELF-HEAL against transient stale-closure reverts. Other effects can,
      // for a few hundred ms after this update, write back the pre-GBB selection
      // (the symptom: odmProjectData ends correct but the sessionStorage blob
      // reverts to the single code). Re-assert the selection + derived flags a
      // few times via FUNCTIONAL updates (which always see the latest state) so
      // we are the LAST writer. Each pass logs whether it had to re-apply.
      const enabledCodesOf = (entry: any): string => {
        const cl = entry?.CodeList?.[0]?.CodeListItem;
        return Array.isArray(cl)
          ? cl.filter((c: any) => c.IsEnabled).map((c: any) => String(c.CodedValue)).sort().join(',')
          : '';
      };
      const reassert = (tag: string) => {
        let wasReverted = false;
        setAnswersData((prevA: any) => {
          const e = prevA?.[oid];
          if (!e || !Array.isArray(e.CodeList)) return prevA;
          if (enabledCodesOf(e) === codes && e.value === codes) return prevA; // already correct
          wasReverted = true;
          const flippedAns = { ...prevA, [oid]: { ...e, CodeList: flip(e.CodeList), value: codes } };
          // re-run derived so I.69/70/71 stay consistent with the re-asserted I.66
          try {
            const refs: any[] = [];
            (itemGroup || []).forEach((g: any) => (g?.ItemRef || []).forEach((r: any) => {
              if (!r?.MethodOID) return;
              const md = methodDef?.find((m: any) => m.OID === r.MethodOID);
              if (md?.FormalExpression?.text?.includes('RepeatCount')) return;
              refs.push({ ...r, ItemGroupOID: g.OID, ItemGroupRepeatKey: 0 });
            }));
            return refs.length
              ? calculateDerivedFields(refs, methodDef, flippedAns, oid, buildWasteCalcPairs(methodDef, itemGroup as any))
              : flippedAns;
          } catch { return flippedAns; }
        });
        setItemGroupData((prevG: any) => Array.isArray(prevG)
          ? prevG.map((grp: any) => {
              if (!Array.isArray(grp?.ItemData)) return grp;
              let touched = false;
              const ItemData = grp.ItemData.map((it: any) => {
                if (String(it?.ItemOID) === oid && Array.isArray(it.CodeList) && it.CodeList.length) {
                  if (enabledCodesOf(it) === codes) return it;
                  touched = true;
                  return { ...it, CodeList: flip(it.CodeList), Value: codes };
                }
                return it;
              });
              return touched ? { ...grp, ItemData } : grp;
            })
          : prevG);
        // Re-write the authoritative blob from the clobber-immune local values so
        // any debounced clobber-save that fired in between is overridden.
        try {
          saveRequirementsState(
            { answersData: finalAnswers, itemGroupData: newGroups },
            data?.customerUid, data?.assetUid, tradeType, data?.jobStartDate,
          );
        } catch { /* non-fatal */ }
        if (process.env.NODE_ENV === 'development') {
          console.log('[GBB→JobType] reassert(%s): %s → target %s', tag, wasReverted ? 'was reverted, re-applied' : 'stable', codes);
        }
      };
      // Store the reassert timers so a mid-flight unmount can clear them.
      [600, 1300, 2100].forEach((delay) => {
        reassertTimersRef.current.push(setTimeout(() => reassert(`t${delay}`), delay));
      });
    },

    // Resolve + apply the GBB job-type (I.66) selection from this component's
    // COMPLETE in-memory template. Unlike applyExternalSelection — which only flips
    // an entry that already exists — this rebuilds the full itemGroupData from
    // answers+template (so the job-type question is present with its CodeList even
    // when the user never answered it), locates the multiselect whose option Decodes
    // cover the whole service universe, then INSERTS-OR-FLIPS the selection into
    // answersData and rebuilds itemGroupData from it. Returns {oid, codes} or null.
    applyJobTypeFromServices: (allServiceNames: string[], selectedServiceNames: string[]): { oid: string; codes: string } | null => {
      try {
        const normLocal = (s: any): string => String(s ?? '').trim().toLowerCase();
        const universe = (allServiceNames || []).map(normLocal).filter(Boolean);
        if (universe.length === 0) return null;
        const selectedSet = new Set((selectedServiceNames || []).map(normLocal));

        // Locate the job-type question DIRECTLY from the template (itemDef + codeList),
        // NOT from answersData. buildItemGroupDataFromAnswersData only emits questions
        // that HAVE an answer, so when the user answered nothing in the trade section it
        // produces no job-type node. The definitions are always present once the trade
        // template has loaded. The job-type question is the (multi)select whose CodeList
        // option Decodes cover the whole GBB service universe.
        let jobOid: string | null = null;
        let jobCodeListDef: any = null;
        for (const def of itemDef || []) {
          const dt = String(def?.DataType || '').toLowerCase();
          if (dt !== 'multiselect' && dt !== 'singleselect') continue;
          const clOid = def?.CodeListRef?.CodeListOID;
          if (!clOid) continue;
          const clDef = (codeList || []).find((cl: any) => cl.OID === clOid);
          const items = clDef?.CodeListItem;
          if (!Array.isArray(items) || items.length === 0) continue;
          const decodes = items.map((c: any) => normLocal(c?.Decode?.TranslatedText?.text));
          // Exact Decode match FIRST — this locator is deliberately name-agnostic and must
          // keep working for whatever service names the caller passes. Only when that fails
          // do we read each option as a SET of services: the job-type question now offers the
          // 5 COMBO options ("Enhancement + Tuneup"), where no bare service name appears as a
          // Decode at all, so exact matching located nothing and confirming a package
          // silently stopped updating the answer. The parse understands RoofMaxx's own
          // vocabulary, hence a fallback rather than a replacement.
          let covers = universe.every((svc) => decodes.includes(svc));
          if (!covers) {
            const covered = new Set(
              items.flatMap((c: any) =>
                servicesInAnswerText(String(c?.Decode?.TranslatedText?.text ?? '')).map(normLocal)),
            );
            covers = universe.every((svc) => covered.has(svc));
          }
          if (!covers) continue;
          jobOid = String(def.OID);
          jobCodeListDef = clDef;
          break;
        }
        if (!jobOid || !jobCodeListDef) {
          return null;
        }

        // Build the CodeList with IsEnabled flipped to the selected tier's services.
        // A single option whose OWN service set equals the confirmed pack wins: that is the
        // 5-combo shape, where exactly one option can describe the pack. It has to be tried
        // FIRST — a pack of Enhancement + Tuneup also matches the bare "Tuneup" option by
        // name, so per-service membership alone would tick Tuneup and silently drop
        // Enhancement, which has no bare option of its own.
        //
        // Falling back to per-service membership keeps the legacy one-option-per-service
        // shape working, including for service names this parser does not know (it
        // understands RoofMaxx's vocabulary; the locator above is deliberately name-agnostic).
        const codeListItems = (jobCodeListDef.CodeListItem || []) as any[];
        const comboIndex = selectedSet.size === 0 ? -1 : codeListItems.findIndex((c: any) => {
          const svcs = servicesInAnswerText(String(c?.Decode?.TranslatedText?.text ?? ''))
            .map(normLocal);
          return svcs.length === selectedSet.size && svcs.every((svc) => selectedSet.has(svc));
        });
        const flippedItems = codeListItems.map((c: any, i: number) => ({
          ...c,
          IsEnabled: comboIndex >= 0
            ? i === comboIndex
            : selectedSet.has(normLocal(c?.Decode?.TranslatedText?.text)),
        }));
        const flippedCL = [{
          OID: jobCodeListDef.OID,
          Name: jobCodeListDef.OID,
          DataType: jobCodeListDef.DataType,
          CodeListItem: flippedItems,
        }];
        const codes = flippedItems
          .filter((c: any) => c?.IsEnabled === true && c?.CodedValue != null)
          .map((c: any) => String(c.CodedValue))
          .sort()
          .join(',');

        // Insert-or-flip the job-type answer, then recompute derived MethodDef fields
        // (I.69/70/71 isDeleted) so they track the new I.66 selection.
        const base = (answersData && (answersData as any)[jobOid]) || {};
        const updatedAnswers: Record<string, any> = {
          ...answersData,
          [jobOid]: { ...base, CodeList: flippedCL, value: codes },
        };
        let finalAnswers: Record<string, any> = updatedAnswers;
        try {
          const refs: any[] = [];
          (itemGroup || []).forEach((g: any) =>
            (g?.ItemRef || []).forEach((r: any) => {
              if (!r?.MethodOID) return;
              const md = methodDef?.find((m: any) => m.OID === r.MethodOID);
              if (md?.FormalExpression?.text?.includes('RepeatCount')) return;
              refs.push({ ...r, ItemGroupOID: g.OID, ItemGroupRepeatKey: 0 });
            }),
          );
          if (refs.length > 0) {
            finalAnswers = calculateDerivedFields(
              refs, methodDef, updatedAnswers, jobOid, buildWasteCalcPairs(methodDef, itemGroup as any),
            );
          }
        } catch {
          finalAnswers = updatedAnswers;
        }

        setAnswersData(finalAnswers);

        // Rebuild itemGroupData from the updated answers (mirrors convertToODMFormat)
        // so the job-type node is present + consistent and survives ODM serialization.
        const newGroups = buildItemGroupDataFromAnswersData(finalAnswers, itemDef, itemGroup, codeList);
        setItemGroupData(newGroups);

        // Authoritative session write + remount the displayed control.
        try {
          saveRequirementsState(
            { answersData: finalAnswers, itemGroupData: newGroups },
            data?.customerUid, data?.assetUid, tradeType, data?.jobStartDate,
          );
        } catch {
          /* non-fatal */
        }
        setRestoreKey((k: number) => k + 1);

        return { oid: jobOid, codes };
      } catch (e) {
        console.warn('[GBB→JobType] applyJobTypeFromServices failed:', e);
        return null;
      }
    },
  }), [convertToODMFormat, onSave, response, tradeType, steps, allQuestionsData, answersData, organizeQuestionsHierarchically, methodDef, itemGroup, itemDef, codeList, data]);

  // ── Ballpark Estimate live data (BPE) ────────────────────────────────────
  // NOTE: This useMemo MUST stay above any early returns to satisfy React Rules of Hooks.
  const bpeData: BpeData = useMemo(() => {
    const tenantId = String((tenantSessionDetails as any)?.tenantid || '');
    const td = Array.isArray(tradeData) ? {} : (tradeData as any) || {};
    let sqFt = parseFloat(td?.TotalMeasurements?.TotalArea || td?.TotalArea || td?.total_area || td?.sqft || td?.sq_ft || '0') || 0;

    const parseOdmItems = (raw: string | object | null | undefined): any[] => {
      try {
        if (!raw) return [];
        const odm: any = typeof raw === 'string' ? JSON.parse(raw) : raw;
        const items: any[] = [];
        (odm?.EventData ?? []).forEach((ev: any) => {
          (ev?.FormData ?? []).forEach((form: any) => {
            (form?.ItemGroupData ?? []).forEach((grp: any) => {
              (grp?.ItemData ?? []).forEach((it: any) => items.push(it));
            });
          });
        });
        (odm?.ClinicalData?.SubjectData ?? []).forEach((subj: any) => {
          (subj?.FormData ?? []).forEach((form: any) => {
            (form?.ItemGroupData ?? []).forEach((grp: any) => {
              (grp?.ItemData ?? []).forEach((it: any) => items.push(it));
            });
          });
        });
        return items;
      } catch { return []; }
    };

    const areaKeywords = ['area', 'sqft', 'squarefeet', 'square_feet', 'totalarea', 'sq_ft', 'floorarea', 'livearea', 'buildingarea', 'roofarea', 'livingarea'];
    const findAreaInItems = (items: any[]): number => {
      const hit = items.find((item: any) => {
        const mk = String(item?.MetaKey || item?.ItemOID || '').toLowerCase().replace(/[_\s-]/g, '');
        return areaKeywords.some(k => mk.includes(k));
      });
      if (hit?.Value) {
        const v = parseFloat(String(hit.Value));
        if (!isNaN(v) && v > 0) return v;
      }
      return 0;
    };

    if (!sqFt && assetOdmSnapshot) sqFt = findAreaInItems(parseOdmItems(assetOdmSnapshot));
    if (!sqFt && (data as any)?.assetTemplateData) sqFt = findAreaInItems(parseOdmItems((data as any).assetTemplateData));

    if (!sqFt) {
      const areaItem = (itemDef as any[]).find((id: any) => {
        const mk = String(id?.MetaKey || '').toLowerCase().replace(/[_\s-]/g, '');
        return areaKeywords.some(k => mk.includes(k));
      });
      if (areaItem) sqFt = parseFloat((answersData as any)[areaItem.OID]?.value || '0') || 0;
    }

    if (!sqFt) {
      // Deterministic fallback: scan answers in a STABLE (key-sorted) order so the
      // same bid always derives the same area regardless of how answersData was
      // populated (user-interaction insertion order on create vs. saved-structure
      // order on resume). A non-deterministic pick here would feed a different area
      // to the cost model and make the estimate change between sessions.
      const sortedEntries = Object.entries(answersData).sort(([a], [b]) => a.localeCompare(b));
      for (const [, ans] of sortedEntries) {
        const val = parseFloat((ans as any)?.value || '0');
        if (val >= 100 && val <= 200000) { sqFt = val; break; }
      }
    }

    let projectType: 'residential' | 'commercial' | 'industrial' = 'residential';
    if (tradeType) {
      const t = String(tradeType).toLowerCase();
      if (t.includes('commercial') || t.includes('office') || t.includes('retail')) projectType = 'commercial';
      else if (t.includes('industrial') || t.includes('warehouse') || t.includes('factory')) projectType = 'industrial';
    }

    // Iterate answers in a STABLE (key-sorted) order so qaResponses is byte-identical
    // between create (interaction insertion order) and resume (saved-structure order).
    // The cost model is keyed by OID so order does not change its output, but a stable
    // order keeps JSON.stringify(qa_responses) stable downstream — preventing spurious
    // re-fetches (and cost "flips") when the user navigates back into Requirements.
    const sortedAnswerEntries = Object.entries(answersData).sort(([a], [b]) => a.localeCompare(b));
    const qaResponses: QAResponse[] = sortedAnswerEntries.reduce<QAResponse[]>((acc, [qid, ans]) => {
      const a = ans as any;
      if (a?.CodeList?.length) {
        const enabledLabels: string[] = (a.CodeList[0]?.CodeListItem || [])
          .filter((item: any) => item?.IsEnabled)
          .map((item: any) => item?.Decode?.TranslatedText?.text || item?.CodedValue)
          .filter(Boolean);
        if (enabledLabels.length > 0) { acc.push({ question_id: qid, answer_value: enabledLabels }); return acc; }
      }
      if (a?.value != null && a?.value !== '') acc.push({ question_id: qid, answer_value: a.value });
      return acc;
    }, []);

    // Extracted to detectServicesFromAnswers.ts (and unit-tested there) so a
    // combo answer like "Enhancement + Tuneup" — the single-select "What kind
    // of job is going to be done?" format — detects EVERY named service, not
    // just the first partial-keyword match.
    const detectedServices = detectServicesFromAnswers(qaResponses);

    return {
      tenantId,
      sqFt,
      projectType,
      qaResponses,
      customerUid: (data as any)?.customerUid || '',
      assetUid: (data as any)?.assetUid || '',
      tradeNameVal: (data as any)?.trade || (data as any)?.tradeName || tradeType || '',
      services: detectedServices,
    };
  }, [tenantSessionDetails, tradeData, answersData, itemDef, codeList, data, tradeType, assetOdmSnapshot]);

  // Only propagate bpeData upward when its CONTENT changes. The useMemo above can
  // return a new object reference on renders where the underlying answers are
  // unchanged (an unstable memo input such as the `data` prop is enough). Emitting
  // on every such render caused a feedback loop: onBpeData → parent setBpeData →
  // parent re-render → new props down → bpeData recomputes → re-emit. That loop made
  // BallParkEstimate re-fire POST /api/bpe/estimate continuously (capped to ~600ms by
  // its debounce, hence identical repeated requests). Content-comparing breaks it here.
  const lastEmittedBpeRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!onBpeData) return;
    const sig = JSON.stringify(bpeData);
    if (sig === lastEmittedBpeRef.current) return;  // unchanged content — do not re-emit
    lastEmittedBpeRef.current = sig;
    onBpeData(bpeData);
  }, [bpeData, onBpeData]);

  // Continuously lift the serialized trade ODM to the parent (debounced) on every
  // answer change, so the wizard's odmProjectData.current always reflects the latest
  // edits — even after this component unmounts (e.g. when the user moves to the
  // Finalize step and submits from there). Without this, submitting from Finalize
  // (where Requirements is unmounted, so handleSaveFunctionality can't be called)
  // persists the trade answers captured at the last explicit save/navigation, which
  // drops edits the user made and then navigated past. The explicit save paths
  // (handleSaveFunctionality on Next/submit) still run; this only keeps the ref fresh.
  const odmSyncTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  React.useEffect(() => {
    if (!onSave || !tradeType) return;
    if (odmSyncTimerRef.current) clearTimeout(odmSyncTimerRef.current);
    odmSyncTimerRef.current = setTimeout(() => {
      try {
        const odm = convertToODMFormat();
        if (odm) {
          onSave(JSON.stringify(odm), tradeType || '', templateVersion || '', tradeType || '');
        }
      } catch { /* non-fatal — explicit save paths still persist on Next/submit */ }
    }, 500);
    return () => { if (odmSyncTimerRef.current) clearTimeout(odmSyncTimerRef.current); };
    // convertToODMFormat is intentionally included: it changes (via useCallback) when
    // itemDef/codeList finish loading, so this effect re-fires then and pushes the first
    // valid ODM into odmProjectData.current even when the template arrives after hydration.
    // onSave is excluded — it's a stable wizard callback and including it would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [answersData, tradeType, convertToODMFormat]);

  if (loading) {
    return (
      <Box sx={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        height: '400px'
      }}>
        <Typography variant="h6" color="textSecondary">
          Loading requirements...
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{
      width: '100%',
      position: 'relative'
    }}>
      <Box sx={{
        width: '100%',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden'
      }}>
        {/* Top Stepper Section */}
        {/* Top Stepper Section */}
        <RequirementsStepperHeader
          steps={visibleSteps}
          activeStep={activeStep}
          completed={completed}
          onNext={handleNext}
          onBack={handleBack}
          onStepClick={handleStepClick}
          onComplete={handleComplete}
          canProceed={canProceed()}
          allStepsCompleted={allStepsCompleted()}
          savingDraft={savingDraft}
          savingText={isBidDetailsRoute(location.pathname) ? 'Saving...' : 'Saving Draft...'}
        />

        {/* Main Content Area with Conditional Sidebar */}
        <Box sx={{ display: 'flex', flex: 1, gap: 0, minHeight: 0 }}>
          {/* Main Content */}
          <CatalogueImageContext.Provider value={catalogueSasMap}>
          <MainContentArea
            key={`content-${restoreKey}`}
            steps={visibleSteps}
            activeStep={activeStep}
            completed={completed}
            loading={loading}
            allStepsCompleted={allStepsCompleted()}
            answersData={answersData}
            allQuestionsData={allQuestionsData}
            itemGroupData={itemGroupData}
            itemDef={itemDef}
            codeList={codeList}
            mandatoryQuestions={mandatoryQuestions}
            template={template}
            measureUnit={measureUnit}
            mappings={mappings}
            formDef={formDef}
            conditionDef={conditionDef}
            methodDef={methodDef}
            tradeData={tradeData}
            currentQuestionIndex={currentQuestionIndex}
            navigateToMainOID={navigateToMainOID}
            handleItemGroupDataChange={debouncedHandleItemGroupDataChange}
            handleShowFromCard={handleShowFromCard}
            handleShowRepeat={handleShowRepeat}
            handleShowGroupQus={handleShowGroupQus}
            handleQuestionChange={handleQuestionChange}
            handleQuestionComplete={handleQuestionComplete}
            handleBack={handleBack}
            handleNext={handleNext}
            handleComplete={handleComplete}
            readonly={isBidDetailsRoute(location.pathname) && sessionStorage.getItem('currentStatus') === 'Contract Approval'}
            handleReset={handleReset}
            navigateToQuestionWithFeedback={navigateToQuestionWithFeedback}
            organizeQuestionsHierarchically={organizeQuestionsHierarchically}
            getFilteredItemGroupData={getFilteredItemGroupData}
            getFilteredRepeatingChildren={getFilteredRepeatingChildren}
            getItemsWithValidationErrors={getItemsWithValidationErrors}
            getCurrentQuestionIndex={getCurrentQuestionIndex}
            getRepeatingInstanceCount={getRepeatingInstanceCount}
            updateRepeatingInstanceCount={updateRepeatingInstanceCount}
            getMethodDefRepeatCount={getMethodDefRepeatCount}
            multiInstanceParser={multiInstanceParserRef.current}
            dependencyParser={dependencyGraphParserRef.current}
            canProceed={canProceed}
            isAnswersDataStable={isAnswersDataStable}
            savingDraft={savingDraft}
            imageBaseUrl={imageBaseUrl}
            summarizedGroupsRef={summarizedGroupsRef}
            suppressDeletionConfirmRef={suppressDeletionConfirmRef}
            onSummarizeGroupClear={onSummarizeGroupClear}
          />
          </CatalogueImageContext.Provider>


          {/* Sidebar Section */}
          <SidebarSection
            steps={visibleSteps}
            activeStep={activeStep}
            completed={completed}
            answersData={answersData}
            allQuestionsData={allQuestionsData}
            expandedAccordions={expandedAccordions}
            setExpandedAccordions={setExpandedAccordions}
            onNavigateToQuestion={navigateToQuestionWithFeedback}
            onNavigateToSection={handleSidebarSectionClick}
            organizeQuestionsHierarchically={organizeQuestionsHierarchically}
            itemDef={itemDef}
            codeList={codeList}
            mandatoryQuestions={mandatoryQuestions}
            show={activeStep !== visibleSteps.length - 1}
          />
        </Box>

        {/* Deletion Confirmation Dialog */}
        <DeletionConfirmDialog
          open={deletionConfirmation.show}
          onClose={() => {
            if (deletionConfirmation.originalAnswersData) {
              const snapshot = deletionConfirmation.originalAnswersData;
              setAnswersData(prev => ({
                ...prev,
                ...snapshot
              }));

              setItemGroupData(prevItemGroupData => {
                const parentBooleanOID = deletionConfirmation.controlItemOID?.replace(/\d+$/, (m) => String(parseInt(m) - 1)); // NOSONAR: Safe — /\d+$/ on controlled OID string, no backtracking risk.
                const parentSnapshot = parentBooleanOID ? snapshot[parentBooleanOID] : null;


                const updatedItemGroupData = prevItemGroupData.map(group => {
                  if (!group.ItemData || !Array.isArray(group.ItemData)) {
                    return group;
                  }

                  let updatedItemData = group.ItemData.map(item => {
                    // Check if this item should be restored from snapshot
                    const snapshotKey = item.ItemGroupRepeatKey && item.ItemGroupRepeatKey > 0
                      ? `${item.ItemOID}_${item.ItemGroupRepeatKey}`
                      : item.ItemOID;
                    const snapshotData = snapshot[snapshotKey] || snapshot[item.ItemOID];

                    const hasValueToRestore = snapshotData && (snapshotData.value !== undefined || snapshotData.CodeList);

                    if (hasValueToRestore) {
                      let restoredCodeList = item.CodeList;
                      if (snapshotData.CodeList && Array.isArray(snapshotData.CodeList)) {
                        restoredCodeList = snapshotData.CodeList;
                      }

                      return {
                        ...item,
                        Value: snapshotData.value !== undefined ? snapshotData.value : item.Value,
                        CodeList: restoredCodeList
                      };
                    }
                    return item;
                  });

                  if (parentBooleanOID && parentSnapshot && group.ItemGroupOID === parentSnapshot.itemGroupOID) {
                    const hasParent = updatedItemData.some(item => item.ItemOID === parentBooleanOID);
                    if (!hasParent) {
                      // Add the parent item to the ItemData array
                      updatedItemData.push({
                        ItemOID: parentBooleanOID,
                        Value: parentSnapshot.value,
                        ItemGroupRepeatKey: 0,
                        CodeList: parentSnapshot.CodeList || [],
                        MetaKey: parentSnapshot.originalItemData?.MetaKey || ''
                      });
                    }
                  }

                  return {
                    ...group,
                    ItemData: updatedItemData
                  };
                });

                return updatedItemGroupData;
              });
            } else if (deletionConfirmation.controlItemOID && deletionConfirmation.originalValue) {
              // Legacy: restore just the control item value
              const controlItemOID = deletionConfirmation.controlItemOID;
              const originalValue = deletionConfirmation.originalValue;

              setAnswersData(prev => ({
                ...prev,
                [controlItemOID]: {
                  ...prev[controlItemOID],
                  value: originalValue
                }
              }));

            } else if (deletionConfirmation.controlItemOID && deletionConfirmation.oldCount !== undefined) {
              const controlItemOID = deletionConfirmation.controlItemOID;
              const oldCountValue = String(deletionConfirmation.oldCount);

              setAnswersData(prev => ({
                ...prev,
                [controlItemOID]: {
                  ...prev[controlItemOID],
                  value: oldCountValue
                }
              }));
            }

            if (deletionConfirmation.controlItemOID && deletionConfirmation.oldCount !== undefined) {
              setRepeatingInstanceCounts(prev => ({
                ...prev,
                [deletionConfirmation.controlItemOID!]: deletionConfirmation.oldCount!
              }));
            }

            skipNextCountCheckRef.current = true;
            setRestoreKey(k => k + 1);

            setDeletionConfirmation({ show: false });
          }}
          onConfirm={() => {
            if (deletionConfirmation.keysToDelete && deletionConfirmation.keysToDelete.length > 0) {
              deletionConfirmation.keysToDelete.forEach(key => {
                recentlyCleanedItemsRef.current.add(key);
              });
              if (cleanupClearTimeoutRef.current) {
                clearTimeout(cleanupClearTimeoutRef.current);
              }
              cleanupClearTimeoutRef.current = setTimeout(() => {
                recentlyCleanedItemsRef.current.clear();
              }, 3000);

              // Delete all collected keys from answersData
              setAnswersData(prev => {
                const updated = { ...prev };
                deletionConfirmation.keysToDelete?.forEach(key => {
                  delete updated[key];
                });
                return updated;
              });

              // Also clean up itemGroupData for the repeating group instances
              // AND clear the control item value in its parent group
              setItemGroupData(prev => {
                const filtered = prev.filter((group: any) => {
                  if (group.ItemGroupOID === deletionConfirmation.repeatingGroupOID) {
                    return !deletionConfirmation.instancesToDelete?.includes(group.ItemGroupRepeatKey);
                  }
                  if (group.ParentItemGroupRepeatKey !== undefined &&
                    deletionConfirmation.instancesToDelete?.includes(group.ParentItemGroupRepeatKey)) {
                    return false;
                  }
                  return true;
                }).map((group: any) => {
                  if (group.ItemData) {
                    const updatedItemData = group.ItemData.map((item: any) => {
                      const controlBaseKey = deletionConfirmation.controlItemOID?.split('_')[0];
                      if (item.ItemOID === controlBaseKey) {
                        return { ...item, Value: '' };
                      }
                      return item;
                    });
                    return { ...group, ItemData: updatedItemData };
                  }
                  return group;
                });
                return filtered;
              });

              // Clean up repeatingInstanceData
              if (deletionConfirmation.repeatingGroupOID && deletionConfirmation.instancesToDelete) {
                setRepeatingInstanceData(prev => {
                  const updated = { ...prev };
                  deletionConfirmation.instancesToDelete?.forEach(instanceKey => {
                    const key = `${deletionConfirmation.repeatingGroupOID}_${instanceKey}`;
                    delete updated[key];
                  });
                  return updated;
                });
              }

            } else if (deletionConfirmation.instancesToDelete && deletionConfirmation.instancesToDelete.length > 0) {

              // Delete all answers that belong to the instances being deleted
              setAnswersData(prev => {
                const updated = { ...prev };
                const instancesToDelete = deletionConfirmation.instancesToDelete || [];

                Object.keys(updated).forEach(key => {
                  for (const instanceIndex of instancesToDelete) {
                    if (key.endsWith(`_${instanceIndex}`)) {
                      const answer = updated[key];
                      if (answer?.itemGroupOID === deletionConfirmation.repeatingGroupOID) {
                        recentlyCleanedItemsRef.current.add(key);
                        delete updated[key];
                      }
                    }
                  }
                });

                if (deletionConfirmation.controlItemOID && deletionConfirmation.newCount === 0) {
                  delete updated[deletionConfirmation.controlItemOID];

                  let currentOID = deletionConfirmation.controlItemOID;
                  let iterationCount = 0;
                  const maxIterations = 10; // Prevent infinite loops

                  while (iterationCount < maxIterations) {
                    iterationCount++;
                    const parentBooleanOID = currentOID.replace(/\d+$/, (m) => String(parseInt(m) - 1)); // NOSONAR: Safe — /\d+$/ on controlled OID string, no backtracking risk.

                    // Stop if we've reached the beginning (I.0 or invalid)
                    if (parentBooleanOID === currentOID || parentBooleanOID.endsWith('.0')) {
                      break;
                    }

                    if (!updated[parentBooleanOID]) {
                      const wasInOriginal = deletionConfirmation.originalAnswersData?.[parentBooleanOID] !== undefined;
                      if (!wasInOriginal) {
                        break;
                      }
                      // Parent doesn't exist in current data but was in original, continue up
                      currentOID = parentBooleanOID;
                      continue;
                    }

                    const parentAnswer = updated[parentBooleanOID];
                    const parentValue = parentAnswer?.value;

                    const originalParent = deletionConfirmation.originalAnswersData?.[parentBooleanOID];
                    const originalHadValue = originalParent?.value === 'Yes' || originalParent?.value === 'yes' ||
                      originalParent?.value === true || originalParent?.value === '1';

                    let parentHasSelection = false;

                    // Check direct value
                    if (parentValue !== undefined && parentValue !== null && parentValue !== '') {
                      if (parentValue === 'Yes' || parentValue === 'yes' || parentValue === true || parentValue === '1') {
                        parentHasSelection = true;
                      } else if (parentValue !== 'No' && parentValue !== 'no' && parentValue !== false && parentValue !== '0') {
                        parentHasSelection = true;
                      }
                    }

                    // Check CodeList selection
                    if (!parentHasSelection && parentAnswer?.CodeList && Array.isArray(parentAnswer.CodeList)) {
                      const codeList = parentAnswer.CodeList[0];
                      if (codeList?.CodeListItem && Array.isArray(codeList.CodeListItem)) {
                        parentHasSelection = codeList.CodeListItem.some((item: any) => item.IsEnabled === true);
                      }
                    }

                    const wasDeselectedButNotCommitted = originalHadValue && parentHasSelection && deletionConfirmation.newCount === 0;

                    if (!parentHasSelection || wasDeselectedButNotCommitted) {
                      delete updated[parentBooleanOID];
                      currentOID = parentBooleanOID;
                    } else {
                      // Parent has a selection, stop here
                      break;
                    }
                  }
                }

                return updated;
              });

              // Also clean up itemGroupData
              setItemGroupData(prev => {
                return prev.filter((group: any) => {
                  if (group.ItemGroupOID === deletionConfirmation.repeatingGroupOID) {
                    return !deletionConfirmation.instancesToDelete?.includes(group.ItemGroupRepeatKey);
                  }
                  return true;
                }).map((group: any) => {
                  if (deletionConfirmation.controlItemOID && deletionConfirmation.newCount === 0 && group.ItemData) {
                    const parentBooleanOID = deletionConfirmation.controlItemOID.replace(/\d+$/, (m) => String(parseInt(m) - 1)); // NOSONAR: Safe — /\d+$/ on controlled OID string, no backtracking risk.
                    const parentItem = group.ItemData.find((item: any) => item.ItemOID === parentBooleanOID);
                    const parentWasUnselected = !parentItem?.Value || parentItem.Value === '';

                    const filteredItemData = group.ItemData.filter((item: any) => {
                      if (item.ItemOID === deletionConfirmation.controlItemOID) return false;
                      if (item.ItemOID === parentBooleanOID && parentWasUnselected) return false;
                      return true;
                    });
                    if (filteredItemData.length !== group.ItemData.length) {
                      return { ...group, ItemData: filteredItemData };
                    }
                  }
                  return group;
                });
              });

              // Clean up repeatingInstanceData
              if (deletionConfirmation.repeatingGroupOID) {
                setRepeatingInstanceData(prev => {
                  const updated = { ...prev };
                  deletionConfirmation.instancesToDelete?.forEach(instanceKey => {
                    const key = `${deletionConfirmation.repeatingGroupOID}_${instanceKey}`;
                    delete updated[key];
                  });
                  return updated;
                });
              }

              // Clear cleanup ref after delay
              if (cleanupClearTimeoutRef.current) {
                clearTimeout(cleanupClearTimeoutRef.current);
              }
              cleanupClearTimeoutRef.current = setTimeout(() => {
                recentlyCleanedItemsRef.current.clear();
              }, 3000);
            }

            if (deletionConfirmation.controlItemOID) {
              setRepeatingInstanceCounts(prev => ({
                ...prev,
                [deletionConfirmation.controlItemOID!]: deletionConfirmation.newCount || 0
              }));
            }

            setRestoreKey(k => k + 1);
            skipNextCountCheckRef.current = true;

            setDeletionConfirmation({ show: false });
          }}
          deletionInfo={deletionConfirmation}
        />

        {/* Error Dialog */}
        <Dialog open={error} onClose={() => setError(false)}>
          <DialogTitle className='warning' sx={{ backgroundColor: '#d32f2f', color: 'white' }}>
            <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', py: 2 }}>
              <WarningAmberRoundedIcon sx={{ fontSize: "50px", color: "white", mb: 1 }} />
              <Typography fontSize='20px' fontWeight='400'>
                Error
              </Typography>
            </Box>
          </DialogTitle>
          <DialogContent sx={{ mt: 2 }}>
            <DialogContentText sx={{ fontWeight: "500", fontSize: "16px", color: 'text.primary' }}>
              {errorReason || 'An error occurred. Please try again.'}
              {errorCode && (
                <Typography variant="caption" display="block" sx={{ mt: 1, color: 'text.secondary' }}>
                  Error Code: {errorCode}
                </Typography>
              )}
            </DialogContentText>
          </DialogContent>
          <DialogActions sx={{ justifyContent: 'center', pb: 2 }}>
            <Button
              id="createbid-requirements-button-close-error"
              onClick={() => {
                setError(false);
                setErrorCode(undefined);
                setErrorReason('');
              }}
              className="p-btn p-btnError"
            >
              Close
            </Button>
          </DialogActions>
        </Dialog>

        {/* Validation Warning Dialog */}
        <Dialog open={openValidationWarning} onClose={() => setOpenValidationWarning(false)}>
          <DialogTitle className='warning' sx={{ backgroundColor: '#ff9800', color: 'white' }}>
            <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', py: 2 }}>
              <WarningAmberRoundedIcon sx={{ fontSize: "50px", color: "white", mb: 1 }} />
              <Typography fontSize='20px' fontWeight='400'>
                {validationWarningTitle}
              </Typography>
            </Box>
          </DialogTitle>
          <DialogContent sx={{ mt: 2 }}>
            <DialogContentText sx={{ fontWeight: "500", fontSize: "16px", color: 'text.primary', whiteSpace: 'pre-line' }}>
              {validationWarningMessage}
            </DialogContentText>
          </DialogContent>
          <DialogActions sx={{ justifyContent: 'center', pb: 2 }}>
            <Button
              id="createbid-requirements-button-ok-validation"
              onClick={() => setOpenValidationWarning(false)}
              className="p-btn p-btnPrimary"
            >
              OK
            </Button>
          </DialogActions>
        </Dialog>

      </Box>
    </Box>
  );
});

Requirements.displayName = 'Requirements';

export default Requirements;
