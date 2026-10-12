/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2024
* @author      : Vaisnave IM
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Bid State Manager - Handles saving/loading bid state to/from sessionStorage/localStorage
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------

*/

// Base storage key prefixes for different section
export const STORAGE_KEY_PREFIXES = {
  SELECT_TRADE: 'createBid_selectTrade',
  REQUIREMENTS: 'createBid_requirements',
  ASSETS: 'createBid_assets',
  FINALIZE: 'createBid_finalize',
  GENERAL: 'createBid_general',
  TIMESTAMP: 'createBid_lastSaved',
  BID_ID: 'createBid_bidId',
  AGGREGATED_ESTIMATE: 'createBid_aggregatedEstimate',
  BPE_DATA: 'createBid_bpeData'
};

// Legacy keys (for backward compatibility during migration)
export const STORAGE_KEYS = STORAGE_KEY_PREFIXES;

export const generateCompositeKey = (
  baseKey: string, 
  customerUid?: string | null, 
  assetUid?: string | null, 
  tradeOid?: string | null,
  jobStartDate?: string | null
): string => {
  const parts = [baseKey];
  
  if (customerUid) parts.push(customerUid);
  if (assetUid) parts.push(assetUid);
  if (tradeOid) parts.push(tradeOid);
  
  // Add jobStartDate for bid-specific storage
  if (jobStartDate) {
    // Convert to simple date format (YYYY-MM-DD) for cleaner keys
    try {
      const dateStr = new Date(jobStartDate).toISOString().split('T')[0];
      parts.push(dateStr);
    } catch (e) {
      // If date parsing fails, use original string
      parts.push(jobStartDate);
    }
  }
  
  return parts.join('_');
};

// Type definitions for each section's data
export interface CustomerInfoState {
  customerUid: string | null;
  customerName: string | null;
  assetUid: string | null;
  assetName: string | null;
  propertyAddress: string | null;
  jobStartDate: string | null;
  jobDoneBy?: string;
  timestamp: string;
}

export interface SelectTradeState {
  selectedTrade: string | null;
  selectedTradeOid: string | null;
  selectedSubtrade: string | null;
  constructionType?: string;
  measurementType?: string;
  questionnaireAnswers: any[];
  roofingAnswers?: any[];
  roofingFollowUpAnswers?: any;
  showSubtrades?: boolean;
  showQuestionnaire?: boolean;
  timestamp: string;
}

export interface RequirementsState {
  answersData: { [questionId: string]: any };
  activeStep: number;
  completed: { [k: number]: boolean };
  itemGroupData?: any[];
  repeatingInstanceData?: any;
  currentQuestionIndex?: { [stepId: string]: number };
  tradeType?: string;
  questionTemplateVersion?: number | null;
  metaDataVersionOID?: string;
  /** Serialized trade ODM kept in sync with answersData on every change. */
  odm?: string;
  timestamp: string;
}

export interface AssetsState {
  dynamicQuestionData: { [questionId: string]: any };
  itemGroupData?: any[];
  activeStep: number;
  assetOID?: string;
  assetVersion?: string;
  questionTemplateVersion?: number | null;
  metaDataVersionOID?: string;
  timestamp: string;
}

export interface FinalizeState {
  bidDetails: any;
  selectedDate?: string;
  customerInfo?: any;
  timestamp: string;
}

export interface GeneralBidState {
  activeWizardStep: number;
  completedSteps?: { [k: number]: boolean };
  bidData: any;
  timestamp: string;
}

/**
 * Validates that payload has complete data for autosave
 */
export const validatePayloadForAutosave = (payload: any): { isValid: boolean; errors: string[] } => {
  const errors: string[] = [];

  // Check asset data
  if (!payload.answerData?.[0]?.questionTemplateVersion) {
    errors.push('Asset questionTemplateVersion is missing');
  }
  if (!payload.answerData?.[0]?.answersData || payload.answerData[0].answersData === '') {
    errors.push('Asset answersData is empty');
  }

  // Check requirements data
  if (!payload.answerData?.[1]?.questionTemplateVersion) {
    errors.push('Requirements questionTemplateVersion is missing');
  }
  if (!payload.answerData?.[1]?.answersData || payload.answerData[1].answersData === '') {
    errors.push('Requirements answersData is empty');
  }

  return {
    isValid: errors.length === 0,
    errors
  };
};

/**
 * Ensures payload is complete before autosave by restoring from session storage if needed
 * First attempts restoration, then validates the final result
 */
export const ensureCompletePayload = async (payload: any): Promise<boolean> => {
  // Try to restore any missing data from session storage
  await restoreAnswerData(payload);
  
  // After restoration attempt, validate the final payload
  const validation = validatePayloadForAutosave(payload);
  
  if (!validation.isValid) {
    // Build user-friendly error message
    const missingItems: string[] = [];
    if (validation.errors.includes('Asset answersData is empty')) {
      missingItems.push('asset information');
    }
    if (validation.errors.includes('Requirements answersData is empty')) {
      missingItems.push('requirements');
    }
    if (validation.errors.includes('Asset questionTemplateVersion is missing')) {
      missingItems.push('asset template version');
    }
    if (validation.errors.includes('Requirements questionTemplateVersion is missing')) {
      missingItems.push('requirements template version');
    }
    
    const errorMessage = missingItems.length > 0 
      ? `Missing ${missingItems.join(' and ')}. Please complete all required fields.`
      : `Validation failed: ${validation.errors.join(', ')}`;
    
    throw new Error(errorMessage);
  }
  
  return true;
};

/**
 * Creates a manual autosave function that ensures complete payload before saving
 */
export const createManualAutosave = (accessToken: string) => {
  return async (bidData: any): Promise<{ success: boolean; error?: string }> => {
    try {
      // Get current bid data and ensure structure
      const fullBidData = {
        ...bidData,
        customerUid: bidData?.customerUid || '',
        assetUid: bidData?.assetUid || '',
        jobStartDate: bidData?.jobStartDate || new Date().toISOString(),
        answerData: bidData?.answerData || []
      };
      
      // Use ensureCompletePayload to restore and validate
      const isPayloadComplete = await ensureCompletePayload(fullBidData);
      
      if (!isPayloadComplete) {
        return { success: false, error: 'Unable to restore complete data for saving' };
      }
      
      // Attempt the autosave
      const { autoSaveBid } = await import('../services/BidService');
      await autoSaveBid(fullBidData, accessToken);
      
      return { success: true };
      
    } catch (error: any) {
      return { 
        success: false, 
        error: error?.response?.data?.message || error?.message || 'Autosave failed' 
      };
    }
  };
};

export const saveToSessionStorage = <T>(key: string, data: T): boolean => {
  try {
    const serialized = JSON.stringify(data);
    sessionStorage.setItem(key, serialized);
    sessionStorage.setItem(STORAGE_KEYS.TIMESTAMP, new Date().toISOString());
    return true;
  } catch (error) {
    return false;
  }
};

export const loadFromSessionStorage = <T>(key: string): T | null => {
  try {
    const serialized = sessionStorage.getItem(key);
    if (!serialized) {
      return null;
    }
    const data = JSON.parse(serialized) as T;
    return data;
  } catch (error) {
    return null;
  }
};


export const saveToLocalStorage = saveToSessionStorage;

export const loadFromLocalStorage = loadFromSessionStorage;

export const clearSectionData = (key: string): void => {
  try {
    sessionStorage.removeItem(key);
  } catch (error) {
  }
};

export const clearAllBidData = (): void => {
  try {
    // Clear all keys that start with createBid_
    const keysToRemove = getKeysMatchingPattern('createBid_');
    keysToRemove.forEach(key => {
      sessionStorage.removeItem(key);
    });
  } catch (error) {
  }
};

export const hasSavedData = (): boolean => {
  try {
    return Object.values(STORAGE_KEYS).some(key => {
      const data = sessionStorage.getItem(key);
      return data !== null && data !== undefined;
    });
  } catch (error) {
    return false;
  }
};

export const getLastSavedTime = (): Date | null => {
  try {
    const timestamp = sessionStorage.getItem(STORAGE_KEYS.TIMESTAMP);
    return timestamp ? new Date(timestamp) : null;
  } catch (error) {
    return null;
  }
};

export const saveSelectTradeState = (
  state: Partial<SelectTradeState>,
  customerUid?: string | null,
  assetUid?: string | null,
  jobStartDate?: string | null
): boolean => {
  const compositeKey = generateCompositeKey(
    STORAGE_KEY_PREFIXES.SELECT_TRADE,
    customerUid,
    assetUid,
    null,  // tradeOid not used for SelectTrade
    jobStartDate
  );
  
  const currentState = loadFromSessionStorage<SelectTradeState>(compositeKey) || {} as SelectTradeState;
  const newState: SelectTradeState = {
    ...currentState,
    ...state,
    timestamp: new Date().toISOString()
  };
  return saveToSessionStorage(compositeKey, newState);
};

export const saveRequirementsState = (
  state: Partial<RequirementsState>,
  customerUid?: string | null,
  assetUid?: string | null,
  tradeOid?: string | null,
  jobStartDate?: string | null
): boolean => {
  const compositeKey = generateCompositeKey(
    STORAGE_KEY_PREFIXES.REQUIREMENTS,
    customerUid,
    assetUid,
    tradeOid,
    jobStartDate
  );
  
  const currentState = loadFromSessionStorage<RequirementsState>(compositeKey) || {} as RequirementsState;
  // Check if new state has itemGroupData that's smaller than current state
  if (state.itemGroupData !== undefined && 
      currentState.itemGroupData && 
      currentState.itemGroupData.length > 0 &&
      state.itemGroupData.length < currentState.itemGroupData.length) {
    
    const answersToCheck = state.answersData || currentState.answersData || {};
    const instanceAnswerKeys = Object.keys(answersToCheck).filter(k => /_\d+$/.test(k));
    
    if (instanceAnswerKeys.length > 0) {
      const { itemGroupData, ...restOfState } = state;
      const newState: RequirementsState = {
        ...currentState,
        ...restOfState,
        timestamp: new Date().toISOString()
      };
      return saveToSessionStorage(compositeKey, newState);
    }
  }
  const newState: RequirementsState = {
    ...currentState,
    ...state,
    timestamp: new Date().toISOString()
  };
  return saveToSessionStorage(compositeKey, newState);
};

export const saveAssetsState = (
  state: Partial<AssetsState>,
  customerUid?: string | null,
  assetUid?: string | null,
  jobStartDate?: string | null
): boolean => {
  const compositeKey = generateCompositeKey(
    STORAGE_KEY_PREFIXES.ASSETS,
    customerUid,
    assetUid,
    null,  // tradeOid not used for Assets
    jobStartDate
  );
  
  const currentState = loadFromSessionStorage<AssetsState>(compositeKey) || {} as AssetsState;
  
  if (state.itemGroupData !== undefined && 
      currentState.itemGroupData && 
      currentState.itemGroupData.length > 0 &&
      state.itemGroupData.length < currentState.itemGroupData.length) {
    
    const answersToCheck = state.dynamicQuestionData || currentState.dynamicQuestionData || {};
    const instanceAnswerKeys = Object.keys(answersToCheck).filter(k => /_\d+$/.test(k));
    
    if (instanceAnswerKeys.length > 0) {
      const { itemGroupData, ...restOfState } = state;
      const newState: AssetsState = {
        ...currentState,
        ...restOfState,
        timestamp: new Date().toISOString()
      };
      return saveToSessionStorage(compositeKey, newState);
    }
  }
  
  const newState: AssetsState = {
    ...currentState,
    ...state,
    timestamp: new Date().toISOString()
  };
  const result = saveToSessionStorage(compositeKey, newState);
  return result;
};

export const saveFinalizeState = (state: Partial<FinalizeState>): boolean => {
  const currentState = loadFromSessionStorage<FinalizeState>(STORAGE_KEYS.FINALIZE) || {} as FinalizeState;
  const newState: FinalizeState = {
    ...currentState,
    ...state,
    timestamp: new Date().toISOString()
  };
  return saveToSessionStorage(STORAGE_KEYS.FINALIZE, newState);
};


export const saveGeneralBidState = (state: Partial<GeneralBidState>): boolean => {
  const currentState = loadFromSessionStorage<GeneralBidState>(STORAGE_KEYS.GENERAL) || {} as GeneralBidState;
  const newState: GeneralBidState = {
    ...currentState,
    ...state,
    timestamp: new Date().toISOString()
  };
  return saveToSessionStorage(STORAGE_KEYS.GENERAL, newState);
};

// SessionStorage key holding the active bid id. Owned by useBidState
// (BID_ID_KEY = 'currentBidId'); referenced here as a literal so the customer-info
// helpers can scope their storage per bid without importing the hook.
export const CURRENT_BID_ID_KEY = 'currentBidId';

/**
 * Build the base key for customer-info storage. When a bidId is present the key
 * becomes `createBid_general_<bidId>` so each bid keeps its own slot; otherwise it
 * falls back to the shared `createBid_general` key (new bid before its first save).
 */
const customerInfoBaseKey = (bidId?: string | null): string =>
  bidId ? `${STORAGE_KEY_PREFIXES.GENERAL}_${bidId}` : STORAGE_KEY_PREFIXES.GENERAL;

/**
 * Read the active bid id from sessionStorage. Returns the raw string id, or null
 * when no bid is active. Used to scope customer-info storage and to sync the
 * displayed customer name to the bid being (re)opened.
 */
export const getCurrentBidId = (): string | null => {
  try {
    const id = sessionStorage.getItem(CURRENT_BID_ID_KEY);
    return id && id.trim() !== '' ? id : null;
  } catch {
    return null;
  }
};

/**
 * Decide which customer name to display when (re)loading a bid into the selector.
 * Returns the name to apply, or null when the field already holds a value or no
 * incoming name exists — so a user's manual selection/clear is never overwritten.
 */
export const resolveIncomingCustomerName = (
  selectedCustomer: unknown,
  bidDataCustomerName: unknown,
  currentValue: unknown
): string | null => {
  // Never clobber a value the user already has in the field.
  if (typeof currentValue === 'string' && currentValue.trim() !== '') return null;
  const incoming =
    (typeof selectedCustomer === 'string' && selectedCustomer.trim() !== '' && selectedCustomer) ||
    (typeof bidDataCustomerName === 'string' && bidDataCustomerName.trim() !== '' && bidDataCustomerName) ||
    null;
  return incoming || null;
};

export const saveCustomerInfoState = (
  state: Partial<CustomerInfoState>,
  customerUid?: string | null,
  jobStartDate?: string | null,
  bidId?: string | null
): boolean => {
  const compositeKey = generateCompositeKey(
    customerInfoBaseKey(bidId),
    customerUid,
    null,
    null,
    jobStartDate
  );

  const currentState = loadFromSessionStorage<CustomerInfoState>(compositeKey) || {} as CustomerInfoState;
  const newState: CustomerInfoState = {
    ...currentState,
    ...state,
    timestamp: new Date().toISOString()
  };
  return saveToSessionStorage(compositeKey, newState);
};


export const loadSelectTradeState = (
  customerUid?: string | null,
  assetUid?: string | null,
  jobStartDate?: string | null
): SelectTradeState | null => {
  const compositeKey = generateCompositeKey(
    STORAGE_KEY_PREFIXES.SELECT_TRADE,
    customerUid,
    assetUid,
    null,  // tradeOid not used for SelectTrade
    jobStartDate
  );
  return loadFromSessionStorage<SelectTradeState>(compositeKey);
};

export const loadRequirementsState = (
  customerUid?: string | null,
  assetUid?: string | null,
  tradeOid?: string | null,
  jobStartDate?: string | null
): RequirementsState | null => {
  const compositeKey = generateCompositeKey(
    STORAGE_KEY_PREFIXES.REQUIREMENTS,
    customerUid,
    assetUid,
    tradeOid,
    jobStartDate
  );
  return loadFromSessionStorage<RequirementsState>(compositeKey);
};


export const loadAssetsState = (
  customerUid?: string | null,
  assetUid?: string | null,
  jobStartDate?: string | null
): AssetsState | null => {
  const compositeKey = generateCompositeKey(
    STORAGE_KEY_PREFIXES.ASSETS,
    customerUid,
    assetUid,
    null,  // tradeOid not used for Assets
    jobStartDate
  );
  const result = loadFromSessionStorage<AssetsState>(compositeKey);
  return result;
};


export const saveAggregatedEstimate = (
  estimate: any,
  customerUid?: string | null,
  assetUid?: string | null,
  tradeOid?: string | null,
  jobStartDate?: string | null
): boolean => {
  const compositeKey = generateCompositeKey(
    STORAGE_KEY_PREFIXES.AGGREGATED_ESTIMATE,
    customerUid,
    assetUid,
    tradeOid,
    jobStartDate
  );
  return saveToSessionStorage(compositeKey, { estimate, timestamp: new Date().toISOString() });
};

export const loadAggregatedEstimate = (
  customerUid?: string | null,
  assetUid?: string | null,
  tradeOid?: string | null,
  jobStartDate?: string | null
): any | null => {
  const compositeKey = generateCompositeKey(
    STORAGE_KEY_PREFIXES.AGGREGATED_ESTIMATE,
    customerUid,
    assetUid,
    tradeOid,
    jobStartDate
  );
  const stored = loadFromSessionStorage<{ estimate: any; timestamp: string }>(compositeKey);
  return stored?.estimate ?? null;
};

export const saveBpeData = (
  data: any,
  customerUid?: string | null,
  assetUid?: string | null,
  tradeOid?: string | null,
  jobStartDate?: string | null
): boolean => {
  const compositeKey = generateCompositeKey(
    STORAGE_KEY_PREFIXES.BPE_DATA,
    customerUid,
    assetUid,
    tradeOid,
    jobStartDate
  );
  return saveToSessionStorage(compositeKey, { data, timestamp: new Date().toISOString() });
};

export const loadBpeData = (
  customerUid?: string | null,
  assetUid?: string | null,
  tradeOid?: string | null,
  jobStartDate?: string | null
): any | null => {
  const compositeKey = generateCompositeKey(
    STORAGE_KEY_PREFIXES.BPE_DATA,
    customerUid,
    assetUid,
    tradeOid,
    jobStartDate
  );
  const stored = loadFromSessionStorage<{ data: any; timestamp: string }>(compositeKey);
  return stored?.data ?? null;
};

export const loadFinalizeState = (): FinalizeState | null => {
  return loadFromSessionStorage<FinalizeState>(STORAGE_KEYS.FINALIZE);
};


export const loadGeneralBidState = (): GeneralBidState | null => {
  return loadFromSessionStorage<GeneralBidState>(STORAGE_KEYS.GENERAL);
};

export const loadCustomerInfoState = (
  customerUid?: string | null,
  jobStartDate?: string | null,
  bidId?: string | null
): CustomerInfoState | null => {
  const compositeKey = generateCompositeKey(
    customerInfoBaseKey(bidId),
    customerUid,
    null,
    null,
    jobStartDate
  );
  return loadFromSessionStorage<CustomerInfoState>(compositeKey);
};

/**
 * Restore answerData from session storage if empty or null
 * This function checks if answerData is empty and restores it from session storage
 * @param payload - The autosave payload to check and potentially modify
 * @returns Promise<void> - Modifies payload in-place
 */
export const restoreAnswerData = async (payload: any): Promise<void> => {
  try {
    // Check if answerData needs restoration
    if (!payload.answerData || !Array.isArray(payload.answerData)) {
      return;
    }

  // Only restore answersData when it is empty — never overwrite populated answers.
  // A missing questionTemplateVersion is a separate concern: fill it from session
  // storage below without touching the answers. The old behaviour (restoring answers
  // whenever questionTemplateVersion was null) was overwriting the user's latest edits
  // with the stale session-storage snapshot captured when the bid was first opened.
  const needsAssetAnswersRestoration =
    !payload.answerData[0]?.answersData || payload.answerData[0].answersData === '';
  const needsAssetVersionRestoration =
    payload.answerData[0]?.questionTemplateVersion == null;
  const needsAssetRestoration = needsAssetAnswersRestoration || needsAssetVersionRestoration;

  const needsRequirementsAnswersRestoration =
    !payload.answerData[1]?.answersData || payload.answerData[1].answersData === '';
  const needsRequirementsVersionRestoration =
    payload.answerData[1]?.questionTemplateVersion == null;
  const needsRequirementsRestoration = needsRequirementsAnswersRestoration || needsRequirementsVersionRestoration;

  if (!needsAssetRestoration && !needsRequirementsRestoration) {
    return;
  }
  // Extract session storage keys from payload
  const customerUid = payload.customerUid || payload.customer?.customerUid;
  const assetUid = payload.assetUid || payload.assets?.[0]?.assetUid;
  const tradeOid = payload.answerData?.[1]?.tradeOid || payload.tradeOid;
  const jobStartDate = payload.jobStartDate;

  // Restore asset data if needed
  if (needsAssetRestoration && customerUid && assetUid) {
    const assetState = loadAssetsState(customerUid, assetUid, jobStartDate);
    if (assetState && assetState.itemGroupData) {
      // Only overwrite answersData when it was empty — never replace existing edits.
      if (needsAssetAnswersRestoration) {
        const odmFormat = JSON.stringify({
          EventData: [{ FormData: [{ ItemGroupData: assetState.itemGroupData }] }]
        });
        payload.answerData[0].answersData = odmFormat;
      }
      // Always fill in a missing questionTemplateVersion from session storage.
      if (needsAssetVersionRestoration &&
          assetState.questionTemplateVersion != null) {
        payload.answerData[0].questionTemplateVersion = assetState.questionTemplateVersion;
      }
    }
  }

  // Restore requirements data if needed
  if (needsRequirementsRestoration && customerUid && assetUid && tradeOid) {
    const reqState = loadRequirementsState(customerUid, assetUid, tradeOid, jobStartDate);
    if (reqState && reqState.itemGroupData) {
      // Only overwrite answersData when it was empty — never replace existing edits.
      if (needsRequirementsAnswersRestoration) {
        const odmFormat = JSON.stringify({
          EventData: [{ FormData: [{ ItemGroupData: reqState.itemGroupData }] }]
        });
        payload.answerData[1].answersData = odmFormat;
      }
      // Always fill in a missing questionTemplateVersion from session storage.
      if (needsRequirementsVersionRestoration &&
          reqState.questionTemplateVersion != null) {
        payload.answerData[1].questionTemplateVersion = reqState.questionTemplateVersion;
      }
    }
  }

  } catch (error) {
    console.warn('restoreAnswerData failed:', error);
  }
};

/**
 * Debounce function for auto-save
 */
export const debounce = <T extends (...args: any[]) => any>(
  func: T,
  wait: number
): ((...args: Parameters<T>) => void) => {
  let timeout: NodeJS.Timeout | null = null;
  
  return (...args: Parameters<T>) => {
    if (timeout) {
      clearTimeout(timeout);
    }
    timeout = setTimeout(() => {
      func(...args);
    }, wait);
  };
};

// ==================================================
// Save gate - used to prevent saves while a complex
// suffix-sync / ODM conversion is in progress.
// ==================================================
export const SaveGate = {
  _count: 0 as number,
  start() {
    this._count += 1;
  },
  end() {
    this._count = Math.max(0, this._count - 1);
  },
  isActive() {
    return this._count > 0;
  },

  awaitClear(timeoutMs = 1000): Promise<void> {
    return new Promise((resolve) => {
      if (!this.isActive()) {
        return resolve();
      }

      const start = Date.now();
      const check = () => {
        if (!this.isActive()) return resolve();
        if (Date.now() - start > timeoutMs) return resolve();
        setTimeout(check, 25);
      };
      check();
    });
  }
};

// ============================================
// UTILITY FUNCTIONS FOR COMPOSITE KEYS
// ============================================


export const getKeysMatchingPattern = (pattern: string): string[] => {
  const keys: string[] = [];
  for (let i = 0; i < sessionStorage.length; i++) {
    const key = sessionStorage.key(i);
    if (key && key.startsWith(pattern)) {
      keys.push(key);
    }
  }
  return keys;
};

export const clearCustomerData = (customerUid: string): void => {
  const patterns = Object.values(STORAGE_KEY_PREFIXES).map(prefix => `${prefix}_${customerUid}`);
  
  patterns.forEach(pattern => {
    const keysToRemove = getKeysMatchingPattern(pattern);
    keysToRemove.forEach(key => {
      sessionStorage.removeItem(key);
    });
  });
};


export const clearAssetData = (customerUid: string, assetUid: string): void => {
  const patterns = Object.values(STORAGE_KEY_PREFIXES).map(prefix => `${prefix}_${customerUid}_${assetUid}`);
  
  // Set a temporary flag so autosave (and other save flows) can detect asset switch in progress
  sessionStorage.setItem('createBid_assetSwitchInProgress', '1');

  patterns.forEach(pattern => {
    const keysToRemove = getKeysMatchingPattern(pattern);
    keysToRemove.forEach(key => {
      sessionStorage.removeItem(key);
    });
  });

  // Clear the flag after session cleanup is complete
  sessionStorage.removeItem('createBid_assetSwitchInProgress');
};

// Frontend versioning was removed — keep storage focused on current draft state only.
// If a future need arises for versioning/rollback UI we can reintroduce a simplified solution.

// Clear all sessionStorage keys starting with a given prefix
export const clearKeysByPrefix = (prefix: string): void => {
  try {
    const keysToRemove = getKeysMatchingPattern(prefix);
    keysToRemove.forEach(key => {
      sessionStorage.removeItem(key);
    });
  } catch (error) {
  }
}

// Explicit utility functions for requirements and select trade clearance
export const clearRequirementsSessionStorage = (): void => {
  clearKeysByPrefix('createBid_requirements');
};

export const clearSelectTradeSessionStorage = (): void => {
  clearKeysByPrefix('createBid_selectTrade');
};

export const clearCurrentBidIdSessionStorage = (): void => {
  clearKeysByPrefix('currentBidId');
};
const AUTOSAVE_ANSWER_SNAPSHOT_KEY = 'createBid_lastSavedAnswerData';
const ITEM_DATA_VOLATILE_FIELDS = new Set(['CodeList', 'MetaKey', 'updatedDate']);

const canonicalizeJson = (str: string): string => {
  if (!str) return str ?? '';
  try {
    const normalize = (val: any): any => {
      if (Array.isArray(val)) return val.map(normalize);
      if (val !== null && typeof val === 'object') {
        // Strip volatile fields from ItemData entries (they are arrays of item objects)
        const cleaned: Record<string, any> = {};
        for (const k of Object.keys(val)) {
          if (k === 'ItemData' && Array.isArray(val[k])) {
            cleaned[k] = val[k].map((item: any) => {
              if (item !== null && typeof item === 'object') {
                const stripped: Record<string, any> = {};
                for (const ik of Object.keys(item)) {
                  if (!ITEM_DATA_VOLATILE_FIELDS.has(ik)) {
                    stripped[ik] = normalize(item[ik]);
                  }
                }
                return stripped;
              }
              return item;
            });
          } else {
            cleaned[k] = normalize(val[k]);
          }
        }
        // Sort keys for deterministic output
        return Object.keys(cleaned).sort().reduce<Record<string, any>>((acc, k) => {
          acc[k] = cleaned[k];
          return acc;
        }, {});
      }
      return val;
    };
    return JSON.stringify(normalize(JSON.parse(str)));
  } catch {
    return str; // Not valid JSON — compare as-is
  }
};

const extractAnswersContent = (answerData: any[]): Record<string, string> => {
  const map: Record<string, string> = {};
  for (const entry of (answerData ?? [])) {
    if (entry?.tradeOid != null) {
      map[entry.tradeOid] = canonicalizeJson(entry?.answersData ?? '');
    }
  }
  return map;
};

export const recordLastSavedAnswerData = (payload: any): void => {
  sessionStorage.setItem(
    AUTOSAVE_ANSWER_SNAPSHOT_KEY,
    JSON.stringify(extractAnswersContent(payload?.answerData ?? []))
  );
};

export const hasAnswerDataChanged = (payload: any, callSite = 'unknown'): boolean => {
  const currentMap = extractAnswersContent(payload?.answerData ?? []);
  const current = JSON.stringify(currentMap);
  const previous = sessionStorage.getItem(AUTOSAVE_ANSWER_SNAPSHOT_KEY);
  return previous === null || current !== previous;
};
