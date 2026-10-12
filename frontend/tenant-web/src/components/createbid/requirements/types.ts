/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Type definitions for Requirements v2.0 component
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

import { ItemGroupDef } from '../../../interface/SdmInterfaces';
import React from 'react';
import { QAResponse } from '../../../services/BallParkEstimateService';

export interface BpeData {
  tenantId: string;
  sqFt: number;
  projectType: 'residential' | 'commercial' | 'industrial';
  qaResponses: QAResponse[];
  customerUid: string;
  assetUid: string;
  tradeNameVal: string;
  services: string[];
}

export interface RequirementsProps {
  data?: any;
  onDataChange?: (data: any) => void;
  onSave?: (tradeData: string, tradeType: string, tradeVersion: string, projectOID: string) => void;
  tradeType?: string;
  projectId?: string | number;

  // 🆕 AUTOSAVE SUPPORT: Refs and context needed to build complete payload
  odmData?: React.MutableRefObject<string>;
  odmVersion?: React.MutableRefObject<string>;
  assetVersion?: React.MutableRefObject<string>;
  odmProjectData?: React.MutableRefObject<string>;
  odmProjectVersion?: React.MutableRefObject<string>;
  tradeVersion?: React.MutableRefObject<string>;
  createBidFormRef?: React.MutableRefObject<any>;
  questionRendererRef?: React.MutableRefObject<any>;
  summarizedGroupsRef?: React.MutableRefObject<{ [groupOID: string]: boolean }>;
  userdata?: any;

  // 🆕 AUTOSAVE STATUS CALLBACKS: Update parent AutosaveStatus component
  onAutosaveStart?: () => void;
  onAutosaveSuccess?: () => void;
  onAutosaveError?: (errorMessage: string) => void;
  onLoaded?: () => void;

  onSubsectionAdvance?: (info: {
    fromSubsectionId: string;
    fromSubsectionTitle: string;
    toSubsectionId: string;
    answersData: any;
  }) => void;

  // Lifts computed BPE data to the wizard so BallParkEstimate renders on all steps
  onBpeData?: (data: BpeData) => void;
}

export interface StepData {
  id: string;
  title: string;
  name?: string;  // Added for StepContentRenderer compatibility
  description?: string;
  completed: boolean;
  itemGroupDef?: ItemGroupDef;
  repeatingChildren?: RepeatingChildGroup[];
  sections?: any[];  // Added for StepContentRenderer compatibility
  collectionExceptionConditionOID?: any;
}

export interface RepeatingChildGroup {
  itemGroupDef: ItemGroupDef;
  controlItemOID: string;
  label: string;
  methodOID?: string;
  // NEW: Support for nested multi-instance
  isNested?: boolean;           // Is this a nested repeating group?
  parentRepeatingGroupOID?: string;  // Parent repeating group OID if nested
}

export interface RepeatingGroupMapping {
  repeatingGroupOID: string;
  parentGroupOID: string;
  controlItemOID: string;
  methodOID: string;
  label: string;
  // NEW: Support for nested multi-instance groups
  isNested?: boolean;          // Is this group nested inside another repeating group?
  nestingLevel?: number;       // Nesting depth (1 = top-level, 2 = nested, etc.)
  parentRepeatingGroupOID?: string;  // If nested, which repeating group is the parent?
}
