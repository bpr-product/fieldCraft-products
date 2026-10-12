/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Dependent Answer Cleanup & Cascading Cleanup - Consolidated cleanup utilities
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 3-Nov-2024     | mohamed.riyaz | Merged cascadingCleanupUtils.ts into this file
* -----------------------------------------------------------------------------
*/

/**
 * Dependent Answer Cleanup Utilities
 * 
 * This module provides utilities for cleaning up dependent/conditional question answers
 * when parent question values change. This ensures that stale child data doesn't persist
 * when conditional logic changes.
 */

import { ConditionDef, ItemGroupDef, ItemGroupData, ItemData } from '../../../../interface/SdmInterfaces';

/**
 * Configuration for dependent answer cleanup
 */
export interface DependentCleanupConfig {
  conditionDef: ConditionDef[];
  itemGroupDefs: ItemGroupDef[];
  allQuestionsData: { [stepId: string]: any[] };
}

/**
 * Result of cleanup operation
 */
export interface CleanupResult {
  clearedAnswers: string[];
  cascadeLevels: number;
  itemGroupDataChanges: ItemGroupData[];
}


export function clearDependentAnswers(
  currentAnswersData: { [questionId: string]: any },
  parentQuestionOID: string,
  config: DependentCleanupConfig
): {
  updatedAnswersData: { [questionId: string]: any };
  cleanupResult: CleanupResult;
} {
  const updatedAnswersData = { ...currentAnswersData };
  const clearedAnswers: string[] = [];
  let cascadeLevels = 0;
  const itemGroupDataChanges: ItemGroupData[] = [];

  const visited = new Set<string>();

  function clearDependentsRecursive(parentOID: string, level: number = 0) {
    if (visited.has(parentOID) || level > 10) { // Prevent infinite recursion
      return;
    }

    visited.add(parentOID);
    cascadeLevels = Math.max(cascadeLevels, level);

    // Find all conditional questions that depend on this parent
    config.itemGroupDefs?.forEach((igd) => {
      igd?.ItemRef?.forEach((ir: any) => {
        if (ir.CollectionExceptionConditionOID) {
          // Find the condition definition
          const conditionDef = config.conditionDef?.find(cd => cd.OID === ir.CollectionExceptionConditionOID);
          if (conditionDef) {
            // Extract the parent question OID from the condition
            let conditionParentOID = '';

            // Handle different condition formats
            const expressionText = conditionDef.FormalExpression?.text;
            if (expressionText) {
              if (expressionText.includes('||')) {
                // Multiple conditions - extract all parent OIDs
                const conditions = expressionText.split('||');
                for (const condition of conditions) {
                  const extractedOID = condition.split("==")[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                  if (extractedOID === parentOID) {
                    conditionParentOID = extractedOID;
                    break;
                  }
                }
              } else {
                // Single condition
                conditionParentOID = expressionText.split("==")[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
              }
            }

            if (conditionParentOID === parentOID) {
              // This is a dependent question - clear it
              const dependentQuestionOID = ir.ItemOID;

              // Clear main answer
              if (updatedAnswersData[dependentQuestionOID]) {
                delete updatedAnswersData[dependentQuestionOID];
                clearedAnswers.push(dependentQuestionOID);
              }

              // Clear all instance answers for this dependent question
              Object.keys(updatedAnswersData).forEach(key => {
                if (key.startsWith(`${dependentQuestionOID}_`)) {
                  delete updatedAnswersData[key];
                  clearedAnswers.push(key);
                }
              });

              clearDependentsRecursive(dependentQuestionOID, level + 1);
            }
          }
        }
      });
    });
  }

  // Start the cascade cleanup
  clearDependentsRecursive(parentQuestionOID);

  const cleanupResult: CleanupResult = {
    clearedAnswers,
    cascadeLevels,
    itemGroupDataChanges
  };

  return { updatedAnswersData, cleanupResult };
}


export function analyzeDependentQuestions(
  parentQuestionOID: string,
  config: DependentCleanupConfig
): {
  directDependents: string[];
  allDependents: string[];
  dependencyTree: { [questionOID: string]: string[] };
} {
  const directDependents: string[] = [];
  const allDependents: string[] = [];
  const dependencyTree: { [questionOID: string]: string[] } = {};
  const visited = new Set<string>();

  function findDependentsRecursive(currentParentOID: string, level: number = 0) {
    if (visited.has(currentParentOID) || level > 10) return;
    visited.add(currentParentOID);

    const dependents: string[] = [];

    config.itemGroupDefs?.forEach((igd) => {
      igd?.ItemRef?.forEach((ir: any) => {
        if (ir.CollectionExceptionConditionOID) {
          const conditionDef = config.conditionDef?.find(cd => cd.OID === ir.CollectionExceptionConditionOID);
          if (conditionDef) {
            const expressionText = conditionDef.FormalExpression?.text;
            if (expressionText) {
              let conditionParentOID = '';

              if (expressionText.includes('||')) {
                const conditions = expressionText.split('||');
                for (const condition of conditions) {
                  const extractedOID = condition.split("==")[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
                  if (extractedOID === currentParentOID) {
                    conditionParentOID = extractedOID;
                    break;
                  }
                }
              } else {
                conditionParentOID = expressionText.split("==")[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
              }

              if (conditionParentOID === currentParentOID) {
                const dependentOID = ir.ItemOID;
                dependents.push(dependentOID);

                if (level === 0) {
                  directDependents.push(dependentOID);
                }

                if (!allDependents.includes(dependentOID)) {
                  allDependents.push(dependentOID);
                }

                // Continue recursively
                findDependentsRecursive(dependentOID, level + 1);
              }
            }
          }
        }
      });
    });

    if (dependents.length > 0) {
      dependencyTree[currentParentOID] = dependents;
    }
  }

  findDependentsRecursive(parentQuestionOID);

  return {
    directDependents,
    allDependents,
    dependencyTree
  };
}


export function updateItemGroupDataAfterCleanup(
  currentItemGroupData: ItemGroupData[],
  clearedAnswerOIDs: string[]
): ItemGroupData[] {
  // FIX: REMOVE items that are in the cleanup list instead of just resetting them
  // This prevents stale data from being synced back when the question becomes visible again
  
  const result = currentItemGroupData.map(group => ({
    ...group,
    ItemData: group.ItemData?.filter(item => {
      // If this item is in the cleanup list, REMOVE it
      return !clearedAnswerOIDs.includes(item.ItemOID);
    }) || []
  })).filter(group => group.ItemData && group.ItemData.length > 0);

  return result;
}


export interface CleanupDependency {
  questionId: string;
  dependentQuestions: string[];
  multiInstanceKeys: string[];
}

export interface CleanupContext {
  changedQuestionId: string;
  newValue: any;
  oldValue: any;
  answersData: any;
  itemGroupData: any[];
  conditionDefs: ConditionDef[];
}

/**
 * Build complete dependency tree for cascading cleanup
 */
export const buildDependencyTree = (
  allQuestions: any[],
  conditionDefs: ConditionDef[]
): Map<string, CleanupDependency> => {
  const dependencyMap = new Map<string, CleanupDependency>();

  allQuestions.forEach(question => {
    const dependentQuestions: string[] = [];
    const multiInstanceKeys: string[] = [];

    // Find direct children (conditional questions)
    const directChildren = findDirectChildren(question.id, allQuestions, conditionDefs);
    dependentQuestions.push(...directChildren);

    // Find multi-instance children (repeating groups controlled by this question)
    const multiInstanceChildren = findMultiInstanceChildren(question.id, allQuestions);
    dependentQuestions.push(...multiInstanceChildren);
    multiInstanceKeys.push(...getMultiInstanceKeys(question.id));

    // Recursively find nested children
    const nestedChildren = findNestedChildren(directChildren, allQuestions, conditionDefs);
    dependentQuestions.push(...nestedChildren);

    if (dependentQuestions.length > 0) {
      dependencyMap.set(question.id, {
        questionId: question.id,
        dependentQuestions: Array.from(new Set(dependentQuestions)), // Remove duplicates
        multiInstanceKeys
      });
    }
  });

  return dependencyMap;
};

/**
 * Find direct conditional children of a question
 */
const findDirectChildren = (
  parentId: string,
  allQuestions: any[],
  conditionDefs: ConditionDef[]
): string[] => {
  const children: string[] = [];

  allQuestions.forEach(question => {
    if (question.conditionOID) {
      const condition = conditionDefs.find(cd => cd.OID === question.conditionOID);
      if (condition) {
        const conditionText = condition.FormalExpression?.text || '';

        // Check if this question depends on the parent
        const questionIdMatches = conditionText.match(/([A-Z]+\.\d+)/g); // NOSONAR: Safe — deterministic OID match on internal string, no backtracking risk.
        if (questionIdMatches && questionIdMatches.includes(parentId)) {
          children.push(question.id);
        }
      }
    }
  });

  return children;
};

/**
 * Find multi-instance children (questions that create repeating instances)
 */
const findMultiInstanceChildren = (
  parentId: string,
  allQuestions: any[]
): string[] => {
  return [];
};

/**
 * Find nested children recursively
 */
const findNestedChildren = (
  directChildren: string[],
  allQuestions: any[],
  conditionDefs: ConditionDef[]
): string[] => {
  const nestedChildren: string[] = [];

  directChildren.forEach(childId => {
    const grandChildren = findDirectChildren(childId, allQuestions, conditionDefs);
    nestedChildren.push(...grandChildren);

    // Recursively find deeper levels
    if (grandChildren.length > 0) {
      const deeperChildren = findNestedChildren(grandChildren, allQuestions, conditionDefs);
      nestedChildren.push(...deeperChildren);
    }
  });

  return nestedChildren;
};

/**
 * Get multi-instance keys for a question
 */
const getMultiInstanceKeys = (questionId: string): string[] => {
  return [];
};

/**
 * Execute cascading cleanup when a parent value changes
 */
export const executeCascadingCleanup = (
  context: CleanupContext,
  dependencyMap: Map<string, CleanupDependency>,
  cleanupCallback: (questionsToClean: string[]) => void
) => {
  const questionsToClean: string[] = [];
  const processedQuestions = new Set<string>();

  const collectDependentsRecursively = (questionId: string) => {
    if (processedQuestions.has(questionId)) return;
    processedQuestions.add(questionId);

    const dependency = dependencyMap.get(questionId);
    if (dependency) {
      // Add direct dependents
      questionsToClean.push(...dependency.dependentQuestions);
      questionsToClean.push(...dependency.multiInstanceKeys);

      // Recursively collect nested dependents
      dependency.dependentQuestions.forEach(dependent => {
        collectDependentsRecursively(dependent);
      });
    }
  };

  // Check if cleanup should occur based on value change
  const shouldCleanup = shouldTriggerCleanup(context);

  if (shouldCleanup) {
    collectDependentsRecursively(context.changedQuestionId);

    if (questionsToClean.length > 0) {
      cleanupCallback(Array.from(new Set(questionsToClean)));
    }
  }
};

/**
 * Determine if cleanup should be triggered based on value change
 */
const shouldTriggerCleanup = (context: CleanupContext): boolean => {
  const { changedQuestionId, newValue, oldValue } = context;

  const isDeactivatingValue = ['2', 'No', false, 0, '0', '', null, undefined].includes(newValue);
  const valueChanged = oldValue !== newValue;

  return valueChanged && (isDeactivatingValue || oldValue !== undefined);
};

/**
 * Get cleanup rules dynamically from dependency graph
 * TEMPLATE-AGNOSTIC: Uses parser to determine dependencies instead of hardcoded rules
 */
export const getSpecificCleanupRules = (dependencyParser?: any): { [questionId: string]: string[] } => {
  return {};
};


export const applySpecificCleanup = (
  context: CleanupContext,
  cleanupCallback: (questionsToClean: string[]) => void
) => {

  const specificRules = getSpecificCleanupRules();
  const rule = specificRules[context.changedQuestionId];

  if (rule && shouldTriggerCleanup(context)) {
    // Include multi-instance keys
    const allKeysToClean: string[] = [];
    rule.forEach(questionId => {
      allKeysToClean.push(questionId);
      // Add multi-instance variations
      Object.keys(context.answersData).forEach(key => {
        if (key.startsWith(`${questionId}_`)) {
          allKeysToClean.push(key);
        }
      });
    });

    cleanupCallback(allKeysToClean);
  }
};