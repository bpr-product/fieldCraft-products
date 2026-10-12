/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Repeating Group & Instance Management - Consolidated utilities for multi-instance logic
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* AB#30581       | 10-09-2026  | Mohamed Riyaz A | The Contract-Approval short-circuit in
*                                                 handleInstanceCountChange asked whether
*                                                 this is the Bid Details page by comparing
*                                                 against /soldproduction/projectinformation
*                                                 alone, so it never fired on the same
*                                                 page's /salesestimate route. Now
*                                                 isBidDetailsRoute, which also carries the
*                                                 typeof-window guard.
* -----------------------------------------------------------------------------
*/

import { ItemGroupDef, FormDef, MethodDef, ItemGroupData, ItemDef } from '../../../../interface/SdmInterfaces';
import { RepeatingGroupMapping } from '../types';
import { isBidDetailsRoute } from '../../../../utils/bidDetailsRoute';

/**
 * Parses a MethodDef expression to extract the control ItemOID
 * Example: "I.24 : RepeatCount" => "I.24"
 */
export const parseMethodDefExpression = (expression: string): string | null => {
  try {
    const patterns = [
      /I\.(\d+)\s*:\s*RepeatCount/i,
      /I\.(\d+)\s*RepeatCount/i,
      /!\(I\.(\d+)\s*:\s*RepeatCount\)/i,
      /I\.(\d+)/
    ];

    for (const pattern of patterns) {
      const match = expression.match(pattern);
      if (match && match[1]) {
        const controlItemOID = `I.${match[1]}`;
        return controlItemOID;
      }
    }

    return null;
  } catch (error) {
    return null;
  }
};

/**
 * Finds the parent ItemGroup that contains a specific ItemOID
 */
export const findParentItemGroup = (itemOID: string, itemGroupDefs: ItemGroupDef[]): ItemGroupDef | null => {
  for (const itemGroup of itemGroupDefs) {
    if (itemGroup.ItemRef?.some(itemRef => itemRef.ItemOID === itemOID)) {
      return itemGroup;
    }
  }
  return null;
};


export const buildRepeatingGroupMappings = (
  itemGroupDefs: ItemGroupDef[],
  formDefs: FormDef[],
  methodDefs: MethodDef[]
): RepeatingGroupMapping[] => {
  const mappings: RepeatingGroupMapping[] = [];

  const repeatingGroups = itemGroupDefs.filter(ig => ig.Repeating === 'Yes');

  for (const repeatingGroup of repeatingGroups) {
    try {
      let methodOID: string | null = null;

      // Find the MethodOID for this repeating group
      for (const formDef of formDefs) {
        const itemGroupRef = formDef.ItemGroupRef?.find(ref => ref.ItemGroupOID === repeatingGroup.OID);
        if (itemGroupRef?.MethodOID) {
          methodOID = itemGroupRef.MethodOID;
          break;
        }
      }

    
      if (!methodOID && (repeatingGroup as any).MethodOID) {
        methodOID = (repeatingGroup as any).MethodOID;
      }

      if (!methodOID) {
        continue;
      }

      const methodDef = methodDefs.find(md => md.OID === methodOID);
      if (!methodDef || !methodDef.FormalExpression?.text) {
        continue;
      }

      const controlItemOID = parseMethodDefExpression(methodDef.FormalExpression.text);
      if (!controlItemOID) {
        continue;
      }

      // Find the parent ItemGroup that contains the control question
      const parentGroup = findParentItemGroup(controlItemOID, itemGroupDefs);
      if (!parentGroup) {
        continue;
      }

      // NEW: Detect if this is a NESTED multi-instance group
      // Check if the parent group is ALSO a repeating group
      const isNested = parentGroup.Repeating === 'Yes';

      const mapping: RepeatingGroupMapping = {
        repeatingGroupOID: repeatingGroup.OID,
        parentGroupOID: parentGroup.OID,
        controlItemOID,
        methodOID,
        label: repeatingGroup.Description?.TranslatedText?.text || repeatingGroup.Name || `Repeating Group ${repeatingGroup.OID}`,
        // NEW: Mark if this is a nested group
        isNested: isNested,
        // NEW: Store nesting level (1 = top-level, 2 = nested, etc.)
        nestingLevel: isNested ? 2 : 1  // Will be updated later for deeper nesting
      };

      mappings.push(mapping);

    } catch (error) {
      // Error processing repeating group - skip
    }
  }

  return mappings;
};

// ============================================================================
// INSTANCE MANAGEMENT FUNCTIONS (Merged from instanceManagementUtils.ts)
// ============================================================================

/**
 * State interface for instance management
 */
export interface InstanceManagementState {
  answersData: { [questionId: string]: any };
  itemGroupData: ItemGroupData[];
  itemDef: ItemDef[];
  itemGroup: any[];
  repeatingInstanceData: { [groupOID_instanceIndex: string]: any };
  repeatingInstanceCounts: { [controlItemOID: string]: number };
  previousAnswersData?: { [questionId: string]: any };
}

/**
 * State setters interface for instance management
 */
export interface InstanceManagementSetters {
  setRepeatingInstanceCounts: React.Dispatch<React.SetStateAction<{ [controlItemOID: string]: number }>>;
  setRepeatingInstanceData: React.Dispatch<React.SetStateAction<{ [groupOID_instanceIndex: string]: any }>>;
  setItemGroupData: React.Dispatch<React.SetStateAction<ItemGroupData[]>>;
  setAnswersData: React.Dispatch<React.SetStateAction<{ [questionId: string]: any }>>;
  setDeletionConfirmation: React.Dispatch<React.SetStateAction<any>>;
}

/**
 * Gets the count of repeating instances for a control question
 * NOW SUPPORTS NESTED MULTI-INSTANCE with parent suffix!
 * 
 * @param controlItemOID - The control question OID (e.g., "I.21" or "I.35")
 * @param answersData - All answer data
 * @param parentSuffix - Optional parent instance suffix for nested groups (e.g., "_1")
 * @returns The instance count
 * 

 */
export const getRepeatingInstanceCount = (
  controlItemOID: string,
  answersData: { [questionId: string]: any },
  parentSuffix?: string
): number => {
  // Build the full key including parent suffix for nested groups
  const answerKey = parentSuffix ? `${controlItemOID}${parentSuffix}` : controlItemOID;
  const answer = answersData[answerKey];

  if (!answer || !answer.value) {
    return 0;
  }

  const count = parseInt(answer.value);
  const finalCount = isNaN(count) || count < 0 ? 0 : Math.min(count, 20);

  return finalCount;
};

/**
 * Handles count changes with confirmation for deletions
 */
export const handleCountChange = (
  controlItemOID: string,
  newCount: number,
  oldCount: number,
  repeatingGroupOID: string,
  setters: InstanceManagementSetters,
  state: InstanceManagementState
) => {
  // If we're on the Bid Details page during Contract Approval, do not prompt or delete — ignore the count decrease
  const isBidDetailsPage = isBidDetailsRoute();
  const isContractApproval = typeof window !== 'undefined' ? sessionStorage.getItem('currentStatus') === 'Contract Approval' : false;
  if (isBidDetailsPage && isContractApproval) {
    return; // short-circuit: don't prompt or modify data
  }

  const shouldShowDialog = newCount < oldCount && oldCount > 0;

  if (shouldShowDialog) {
    setters.setDeletionConfirmation({
      show: true,
      controlItemOID,
      newCount,
      oldCount,
      repeatingGroupOID,
      instancesToDelete: Array.from({ length: oldCount - newCount }, (_, i) => oldCount - i),
      originalAnswersData: state.previousAnswersData && Object.keys(state.previousAnswersData).length > 0
        ? { ...state.previousAnswersData }
        : { ...state.answersData }
    });
  } else {
    updateRepeatingInstanceCount(controlItemOID, newCount, repeatingGroupOID, setters, state);
  }
};

/**
 * Updates repeating instance count and manages instance data
 */
export const updateRepeatingInstanceCount = (
  controlItemOID: string,
  newCount: number,
  repeatingGroupOID: string,
  setters: InstanceManagementSetters,
  state: InstanceManagementState
) => {
  const oldCount = state.repeatingInstanceCounts[controlItemOID] || 0;

  setters.setRepeatingInstanceCounts(prev => ({
    ...prev,
    [controlItemOID]: newCount
  }));

  if (newCount > oldCount) {
    const newInstanceData = { ...state.repeatingInstanceData };
    for (let i = oldCount + 1; i <= newCount; i++) {
      const instanceKey = `${repeatingGroupOID}_${i}`;
      if (!newInstanceData[instanceKey]) {
        newInstanceData[instanceKey] = {
          ItemGroupOID: repeatingGroupOID,
          ItemGroupRepeatKey: i,
          ItemData: []
        };
      }
    }
    setters.setRepeatingInstanceData(newInstanceData);

    setters.setItemGroupData(prev => {
      const repeatingGroupDef = state.itemGroup.find((ig: any) => ig.OID === repeatingGroupOID);
      if (!repeatingGroupDef || !repeatingGroupDef.ItemRef) {
        return prev;
      }

      const updated = [...prev];

      for (let instanceIndex = oldCount + 1; instanceIndex <= newCount; instanceIndex++) {
        const existingIndex = updated.findIndex(g =>
          g.ItemGroupOID === repeatingGroupOID &&
          g.ItemGroupRepeatKey === instanceIndex
        );

        if (existingIndex >= 0) {
          continue;
        }

        const itemDataForInstance: any[] = [];

        repeatingGroupDef.ItemRef.forEach((itemRef: any) => {
          const itemDefinition = state.itemDef.find((id: any) => id.OID === itemRef.ItemOID);

          if (itemDefinition) {
            const answerKey = `${itemRef.ItemOID}_${instanceIndex}`;
            const existingAnswer = state.answersData[answerKey];

            itemDataForInstance.push({
              ItemOID: itemRef.ItemOID,
              Value: existingAnswer?.value || '',
              ItemGroupRepeatKey: instanceIndex
            });
          }
        });

        updated.push({
          ItemGroupOID: repeatingGroupOID,
          ItemGroupRepeatKey: instanceIndex,
          ParentItemGroupRepeatKey: 0,
          ItemData: itemDataForInstance
        });
      }

      return updated;
    });
  }

  if (newCount < oldCount) {
    const newInstanceData = { ...state.repeatingInstanceData };
    for (let i = newCount + 1; i <= oldCount; i++) {
      const instanceKey = `${repeatingGroupOID}_${i}`;
      delete newInstanceData[instanceKey];
    }
    setters.setRepeatingInstanceData(newInstanceData);

    // Get instances to delete (for cascading)
    const instancesToDelete = Array.from({ length: oldCount - newCount }, (_, i) => oldCount - i);

    setters.setItemGroupData(prev => {
      return prev.filter(g => {
        // Delete direct instances that exceed newCount
        if (g.ItemGroupOID === repeatingGroupOID && g.ItemGroupRepeatKey > newCount) {
          return false;
        }

        // CASCADE DELETE: Delete nested children whose parent is being deleted
        if (g.ParentItemGroupRepeatKey !== undefined && g.ParentItemGroupRepeatKey !== null) {
          // Check if this is a child of the instances being deleted
          const isChildOfDeletedInstance = instancesToDelete.some(deletedIndex =>
            g.ParentItemGroupRepeatKey === deletedIndex
          );

          if (isChildOfDeletedInstance) {
            return false;
          }
        }

        return true;
      });
    });

    setters.setAnswersData(prev => {
      const newAnswersData = { ...prev };
      Object.keys(newAnswersData).forEach(questionId => {
        const answer = newAnswersData[questionId];

        // Delete answers from direct instances being deleted
        if (answer.itemGroupOID === repeatingGroupOID && answer.repeatKey > newCount) {
          delete newAnswersData[questionId];
          return;
        }

        // CASCADE DELETE: Delete answers from nested children
        if (answer.originalItemData?.ParentItemGroupRepeatKey !== undefined) {
          const parentRepeatKey = answer.originalItemData.ParentItemGroupRepeatKey;

          // Check if parent is being deleted
          const isParentDeleted = instancesToDelete.some(deletedIndex =>
            parentRepeatKey === deletedIndex
          );

          if (isParentDeleted) {
            delete newAnswersData[questionId];
          }
        }
      });
      return newAnswersData;
    });
  }
};

// ============================================================================
// MULTI-INSTANCE RENDERING FUNCTIONS (Merged from multiInstanceRenderingFix.ts)
// ============================================================================

/**
 * Multi-Instance Rendering Fix - Handles MethodDef RepeatCount inconsistencies
 * Solves Problem 1: I.21(Yes)I.23(Yes)I.24 works, I.21(No)I.26(Yes)I.24 fails
 */

export interface MultiInstanceContext {
  questionId: string;
  controllerQuestionId: string;
  controllerValue: any;
  parentPath: string[];
  methodDefs: MethodDef[];
  itemGroupData: ItemGroupData[];
  answersData: any;
}

export interface MultiInstanceRenderingResult {
  shouldRender: boolean;
  instanceCount: number;
  renderingPath: string;
  debugInfo: any;
}

/**
 * Unified multi-instance rendering logic that works for ALL parent-child combinations
 */
export const shouldRenderMultiInstance = (
  context: MultiInstanceContext
): MultiInstanceRenderingResult => {
  // Get the RepeatCount value using multiple strategies
  const strategies = [
    () => getRepeatCountFromAnswersData(context),
    () => getRepeatCountFromMethodDef(context),
    () => getRepeatCountFromItemGroupData(context),
    () => getRepeatCountFromControllerValue(context)
  ];

  let bestResult: MultiInstanceRenderingResult | null = null;

  for (let i = 0; i < strategies.length; i++) {
    try {
      const result = strategies[i]();

      if (result.shouldRender && result.instanceCount > 0) {
        bestResult = result;
        break; // Use first successful strategy
      }
    } catch (error) {
      // Strategy failed - try next
    }
  }

  if (!bestResult) {
    bestResult = {
      shouldRender: false,
      instanceCount: 0,
      renderingPath: 'No valid strategy found',
      debugInfo: { strategies: strategies.length, allFailed: true }
    };
  }

  return bestResult;
};

/**
 * Strategy 1: Get RepeatCount from answersData (most reliable)
 */
const getRepeatCountFromAnswersData = (context: MultiInstanceContext): MultiInstanceRenderingResult => {
  const answer = context.answersData[context.questionId];
  const instanceCount = answer ? parseInt(answer.value || answer) || 0 : 0;

  return {
    shouldRender: instanceCount > 0 && isControllerActive(context),
    instanceCount,
    renderingPath: 'Strategy 1: answersData',
    debugInfo: {
      answer,
      rawValue: answer?.value || answer,
      parsedCount: instanceCount,
      controllerActive: isControllerActive(context)
    }
  };
};

/**
 * Strategy 2: Get RepeatCount from MethodDef
 */
const getRepeatCountFromMethodDef = (context: MultiInstanceContext): MultiInstanceRenderingResult => {
  const methodDef = context.methodDefs?.find(md => {
    const expressionText = md?.FormalExpression?.text || '';
    return expressionText.includes(context.questionId) && expressionText.includes('RepeatCount');
  });

  if (!methodDef) {
    throw new Error('No matching MethodDef found');
  }

  const expressionText = methodDef.FormalExpression?.text || '';
  const itemOIDMatch = expressionText.match(/([A-Z]+\.\d+)/); // NOSONAR: Safe — deterministic OID match on internal expression, no backtracking risk.
  const itemOID = itemOIDMatch ? itemOIDMatch[1] : context.questionId;

  // Find the actual value from itemGroupData
  let instanceCount = 0;
  context.itemGroupData.forEach(group => {
    const itemData = group.ItemData?.find(item => item.ItemOID === itemOID);
    if (itemData) {
      instanceCount = parseInt(itemData.Value || '0') || 0;
    }
  });

  return {
    shouldRender: instanceCount > 0 && isControllerActive(context),
    instanceCount,
    renderingPath: 'Strategy 2: MethodDef',
    debugInfo: {
      methodDef: methodDef.OID,
      expressionText,
      itemOID,
      foundValue: instanceCount,
      controllerActive: isControllerActive(context)
    }
  };
};

/**
 * Strategy 3: Get RepeatCount from ItemGroupData
 */
const getRepeatCountFromItemGroupData = (context: MultiInstanceContext): MultiInstanceRenderingResult => {
  let instanceCount = 0;

  context.itemGroupData.forEach(group => {
    const itemData = group.ItemData?.find(item => item.ItemOID === context.questionId);
    if (itemData) {
      instanceCount = Math.max(instanceCount, parseInt(itemData.Value || '0') || 0);
    }
  });

  return {
    shouldRender: instanceCount > 0 && isControllerActive(context),
    instanceCount,
    renderingPath: 'Strategy 3: ItemGroupData',
    debugInfo: {
      searchedGroups: context.itemGroupData.length,
      foundValue: instanceCount,
      controllerActive: isControllerActive(context)
    }
  };
};

/**
 * Strategy 4: Infer RepeatCount from controller value pattern
 */
const getRepeatCountFromControllerValue = (context: MultiInstanceContext): MultiInstanceRenderingResult => {
  // If controller is "Yes"/"1", check if we have any existing instances
  const controllerActive = isControllerActive(context);

  if (!controllerActive) {
    return {
      shouldRender: false,
      instanceCount: 0,
      renderingPath: 'Strategy 4: Controller inactive',
      debugInfo: { controllerValue: context.controllerValue, controllerActive }
    };
  }

  // Look for existing multi-instance data
  const existingInstances = Object.keys(context.answersData).filter(key =>
    key.startsWith(`${context.questionId}_`)
  );

  const instanceCount = Math.max(1, existingInstances.length); // Default to 1 if controller is active

  return {
    shouldRender: true,
    instanceCount,
    renderingPath: 'Strategy 4: Controller pattern',
    debugInfo: {
      controllerActive,
      existingInstances,
      inferredCount: instanceCount
    }
  };
};

/**
 * Check if the controlling question is active
 */
const isControllerActive = (context: MultiInstanceContext): boolean => {
  const controllerAnswer = context.answersData[context.controllerQuestionId];
  const value = controllerAnswer?.value || controllerAnswer;

  // If the controller question ID is the same as the question ID, it's a direct RepeatCount
  const isDirectRepeatCount = context.controllerQuestionId === context.questionId;

  if (isDirectRepeatCount) {
    // For direct RepeatCount, ANY positive number means active
    const numericValue = parseInt(value || '0');
    const isActive = numericValue > 0;

    return isActive;
  }

  // For separate controller questions (Yes/No pattern), check for active values
  // Support: Yes, 1, true, numeric values > 0
  const activeValues = ['1', 'Yes', 'yes', 'YES', true, 1];
  let isActive = activeValues.includes(value);

  // Also check if it's a positive number (for templates that use numbers instead of Yes/No)
  if (!isActive && typeof value === 'string') {
    const numericValue = parseInt(value);
    isActive = !isNaN(numericValue) && numericValue > 0;
  }

  return isActive;
};

/**
 * Fix multi-instance rendering for specific problematic paths
 */
export const fixMultiInstancePath = (
  questionId: string,
  parentPath: string[],
  methodDefs: MethodDef[],
  itemGroupData: ItemGroupData[],
  answersData: any
): MultiInstanceRenderingResult => {
  // Identify the controlling question from the parent path
  const controllerQuestionId = getControllerFromPath(questionId, parentPath);

  if (!controllerQuestionId) {
    return {
      shouldRender: false,
      instanceCount: 0,
      renderingPath: 'No controller found in path',
      debugInfo: { parentPath }
    };
  }

  const context: MultiInstanceContext = {
    questionId,
    controllerQuestionId,
    controllerValue: answersData[controllerQuestionId],
    parentPath,
    methodDefs,
    itemGroupData,
    answersData
  };

  return shouldRenderMultiInstance(context);
};

/**
 * Extract the controlling question from parent path
 */
const getControllerFromPath = (questionId: string, parentPath: string[]): string | null => {
  // Use the immediate parent as the controller
  return parentPath.length > 0 ? parentPath[parentPath.length - 1] : null;
};

/**
 * Validate multi-instance rendering decision
 */
export const validateMultiInstanceDecision = (
  result: MultiInstanceRenderingResult,
  context: MultiInstanceContext
): boolean => {
  const isValid = result.shouldRender && result.instanceCount > 0;

  return isValid;
};
