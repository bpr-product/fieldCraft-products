/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Requirements Utilities - Question hierarchy and nested visibility (formerly hierarchyUtils.ts)
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* -----------------------------------------------------------------------------
*/

import { ConditionDef } from '../../../../interface/SdmInterfaces';
import { StepData } from '../types';
import { evaluateConditionalQuestionVisibility, convertAnswersDataToItemGroupData } from './conditionalLogicUtils';
import {
  getRepeatingControllers,
  shouldShowRepeatingInstances,
  parseConditionalRelationships
} from './conditionalLogicUtils';

/**
 * State dependencies for hierarchy utilities
 */
export interface HierarchyUtilsState {
  conditionDef: ConditionDef[];
  answersData: { [questionId: string]: any };
  allQuestionsData: { [stepId: string]: any[] };
  codeList: any[];
  steps: StepData[];
}

/**
 * True when the question is a derived/calculated field, i.e. its SDM ItemRef
 * carries a MethodOID. The flag is read from both shapes because some callers
 * pass the wrapped question ({ itemRef }) and others the raw ItemRef.
 */
const hasMethodOID = (q: any): boolean =>
  !!(q?.itemRef?.MethodOID || q?.MethodOID);

/**
 * A derived field is hidden only when the SDM explicitly marks it IsVisible='No';
 * a derived field with no IsVisible stays visible. Keeping this predicate in one
 * place is what stops the question cards, the sidebar and the summary from
 * disagreeing about which derived questions the user can see.
 */
export const isHiddenDerived = (q: any): boolean =>
  hasMethodOID(q) &&
  (q?.itemRef?.IsVisible === 'No' || q?.IsVisible === 'No');

/**
 * Finds the parent question for a conditional question
 */
export const findParentQuestion = (
  conditionalQuestion: any,
  stepQuestions: any[],
  conditionDef: ConditionDef[]
): any | null => {
  if (!conditionalQuestion.conditionOID) return null;

  const condition = conditionDef.find(cd => cd.OID === conditionalQuestion.conditionOID);
  if (!condition) return null;

  let conditionText = condition.FormalExpression?.text || '';

  let parentItemOID: string | null = null;

  if (conditionText.includes('||')) {
    const orParts = conditionText.split('||');
    parentItemOID = orParts[0].split('==')[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '') || null;
  } else {
    parentItemOID = conditionText.split('==')[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '') || null;
  }

  return stepQuestions.find(q => q.id === parentItemOID) || null;
};

/**
 * Check if a conditional question should appear under a specific parent
 * Handles multi-parent scenarios like I.24 appearing under both I.23 and I.26
 */
export const checkIfQuestionAppearsUnderParent = (
  questionId: string,
  parentId: string,
  stepQuestions: any[],
  conditionDef: ConditionDef[]
): boolean => {
  const conditionalQuestion = stepQuestions.find(q => q.id === questionId);
  if (!conditionalQuestion || !conditionalQuestion.conditionOID) return false;

  const condition = conditionDef.find(cd => cd.OID === conditionalQuestion.conditionOID);
  if (!condition) return false;

  let conditionText = condition.FormalExpression?.text || '';

  // Extract all question IDs from the condition
  const questionIdMatches = conditionText.match(/([A-Z]+\.\d+)/g); // NOSONAR: Safe — deterministic OID match on internal string, no backtracking risk.
  if (!questionIdMatches) return false;

  // Check if the parent is one of the controlling questions in the condition
  const isControllingParent = questionIdMatches.includes(parentId);

  return isControllingParent;
};

/**
 * Finds all child questions for a parent question
 */
export const findChildQuestions = (
  parentQuestion: any,
  conditionalQuestions: any[],
  conditionDef: ConditionDef[]
): any[] => {
  return conditionalQuestions.filter(conditionalQuestion => {
    const parent = findParentQuestion(conditionalQuestion, [parentQuestion], conditionDef);
    return parent && parent.id === parentQuestion.id;
  });
};

/**
 * Organizes questions into hierarchical structure with multi-level nesting
 */
export const organizeQuestionsHierarchically = (
  stepQuestions: any[],
  stepId: string | undefined,
  getRepeatingInstanceCount: (controlItemOID: string) => number,
  state: HierarchyUtilsState
): any[] => {
  const mainQuestions = stepQuestions.filter(q => !q.isConditional);
  const conditionalQuestions = stepQuestions.filter(q => q.isConditional);

  const itemGroupDatas = convertAnswersDataToItemGroupData(state.answersData, state.allQuestionsData, state.codeList);
  const visibleConditionalQuestions = conditionalQuestions.filter(question => {
    if (!question.itemRef) return false;
    return evaluateConditionalQuestionVisibility(question.itemRef, question.id, itemGroupDatas, state.conditionDef, state.answersData);
  });

  const findChildren = (parentId: string, level: number = 1): any[] => {
    const directChildren = visibleConditionalQuestions.filter(conditionalQuestion => {
      // Check if this conditional question should appear under the current parent
      const shouldAppearUnderParent = checkIfQuestionAppearsUnderParent(
        conditionalQuestion.id,
        parentId,
        stepQuestions,
        state.conditionDef
      );

      return shouldAppearUnderParent;
    });

    return directChildren.map(child => {
      const childWithChildren = {
        ...child,
        isChild: true,
        parentId: parentId,
        level: level,
        children: findChildren(child.id, level + 1) // Recursively find children of children
      };

      if (childWithChildren.children.length > 0) {
      }

      return childWithChildren;
    });
  };

  const findRepeatingInstances = (parentQuestionId: string): any[] => {
    if (!stepId) {
      return [];
    }

    const step = state.steps.find(s => s.id === stepId);

    if (!step?.repeatingChildren) {
      return [];
    }

    const conditionalRelationships = parseConditionalRelationships(state.conditionDef);

    const controlledRepeatingGroups = step.repeatingChildren.filter(
      repeatingChild => {
        // Get dynamic controlling questions for this repeating child
        const controllers = getRepeatingControllers(repeatingChild.controlItemOID, conditionalRelationships);

        // Check if parent is one of the controllers
        const isController = controllers.includes(parentQuestionId);

        // Also check if this should show based on current answers
        const shouldShow = shouldShowRepeatingInstances(
          parentQuestionId,
          repeatingChild.controlItemOID,
          state.answersData,
          conditionalRelationships
        );

        if (isController && shouldShow) {
        }

        return isController && shouldShow;
      }
    );

    const repeatingInstances: any[] = [];

    controlledRepeatingGroups.forEach(repeatingChild => {
      const instanceCount = getRepeatingInstanceCount(repeatingChild.controlItemOID);

      if (instanceCount > 0) {
        for (let instanceIndex = 0; instanceIndex < instanceCount; instanceIndex++) {
          const instanceNum = instanceIndex + 1;
          const instanceKey = `${repeatingChild.itemGroupDef.OID}_${instanceNum}`;

          repeatingInstances.push({
            id: instanceKey,
            question: `${repeatingChild.label} - Instance ${instanceNum}`,
            isRepeatingInstance: true,
            isChild: true,
            parentId: parentQuestionId,
            level: 1,
            repeatingChild: repeatingChild,
            instanceNum: instanceNum,
            instanceKey: instanceKey,
            children: []
          });
        }
      }
    });

    return repeatingInstances;
  };

  const hierarchicalQuestions: any[] = [];
  const usedConditionalQuestions = new Set<string>();

  const markUsedRecursively = (children: any[]) => {
    children.forEach(child => {
      if (!child.isRepeatingInstance) {
        usedConditionalQuestions.add(child.id);
      }
      if (child.children && child.children.length > 0) {
        markUsedRecursively(child.children);
      }
    });
  };

  mainQuestions.forEach(mainQuestion => {
    const conditionalChildren = findChildren(mainQuestion.id);
    const repeatingInstances = findRepeatingInstances(mainQuestion.id);

    if (repeatingInstances.length > 0) {
    }

    const allChildren = [...conditionalChildren, ...repeatingInstances];

    markUsedRecursively(conditionalChildren);

    const questionGroup = {
      ...mainQuestion,
      isParent: true,
      children: allChildren
    };

    hierarchicalQuestions.push(questionGroup);
  });


  hierarchicalQuestions.forEach(parentGroup => {
    if (parentGroup.children) {
      processConditionalQuestionsRecursively(parentGroup.children);
    }
  });

  function processConditionalQuestionsRecursively(questions: any[]) {
    questions.forEach(conditionalQuestion => {
      if (conditionalQuestion.isChild) {
        const conditionalRepeatingInstances = findRepeatingInstances(conditionalQuestion.id);

        if (conditionalRepeatingInstances.length > 0) {
          // Add repeating instances to this conditional question's children
          if (!conditionalQuestion.children) {
            conditionalQuestion.children = [];
          }
          conditionalQuestion.children.push(...conditionalRepeatingInstances);
        }

        // Recursively check children of this conditional question
        if (conditionalQuestion.children && conditionalQuestion.children.length > 0) {
          processConditionalQuestionsRecursively(conditionalQuestion.children);
        }
      }
    });
  }

  if (stepId) {
    const currentStep = state.steps.find(s => s.id === stepId);
    if (currentStep?.repeatingChildren) {
      currentStep.repeatingChildren.forEach(repeatingChild => {
        const controlItemOID = repeatingChild.controlItemOID;
        const instanceCount = getRepeatingInstanceCount(controlItemOID);

        if (instanceCount > 0) {
          let repeatCountQuestion = hierarchicalQuestions.find(hq => hq.id === controlItemOID);

          const findInNestedChildren = (questions: any[]): any => {
            for (const question of questions) {
              if (question.id === controlItemOID) {
                return question;
              }
              if (question.children && question.children.length > 0) {
                const found = findInNestedChildren(question.children);
                if (found) return found;
              }
            }
            return null;
          };

          if (!repeatCountQuestion) {
            repeatCountQuestion = findInNestedChildren(hierarchicalQuestions);
          }

          const originalQuestion = stepQuestions.find(q => q.id === controlItemOID);

          const questionToCheck = repeatCountQuestion || originalQuestion;
          let shouldShowInstances = true;

          if (questionToCheck) {
            if (questionToCheck.isConditional && questionToCheck.itemRef) {
              const itemGroupDatasForCheck = convertAnswersDataToItemGroupData(
                state.answersData,
                state.allQuestionsData,
                state.codeList
              );

              shouldShowInstances = evaluateConditionalQuestionVisibility(
                questionToCheck.itemRef,
                questionToCheck.id,
                itemGroupDatasForCheck,
                state.conditionDef,
                state.answersData  // Pass V2 answersData for stale data validation
              );
            }
          }

          if (!shouldShowInstances) {
            return;
          }

          if (!repeatCountQuestion && originalQuestion) {
            for (const hq of hierarchicalQuestions) {
              if (hq.children) {
                const foundInChildren = hq.children.find((child: any) => child.id === controlItemOID);
                if (foundInChildren) {
                  repeatCountQuestion = foundInChildren;
                  break;
                }
              }
            }

            if (!repeatCountQuestion) {
              return;
            }
          }

          if (repeatCountQuestion) {

            if (!repeatCountQuestion.children) {
              repeatCountQuestion.children = [];
            }

            for (let instanceIndex = 0; instanceIndex < instanceCount; instanceIndex++) {
              const instanceNum = instanceIndex + 1;
              const instanceKey = `${repeatingChild.itemGroupDef.OID}_${instanceNum}`;

              const instance = {
                id: instanceKey,
                question: `${repeatingChild.label} - Instance ${instanceNum}`,
                isRepeatingInstance: true,
                isChild: true,
                parentId: controlItemOID,
                level: repeatCountQuestion.level ? repeatCountQuestion.level + 1 : 1,
                repeatingChild: repeatingChild,
                instanceNum: instanceNum,
                instanceKey: instanceKey,
                children: []
              };

              repeatCountQuestion.children.push(instance);
            }

            repeatCountQuestion.isParent = true;

          } else {
            for (let instanceIndex = 0; instanceIndex < instanceCount; instanceIndex++) {
              const instanceNum = instanceIndex + 1;
              const instanceKey = `${repeatingChild.itemGroupDef.OID}_${instanceNum}`;

              const standaloneInstance = {
                id: instanceKey,
                question: `${repeatingChild.label} - Instance ${instanceNum}`,
                isRepeatingInstance: true,
                isParent: true,
                level: 0,
                repeatingChild: repeatingChild,
                instanceNum: instanceNum,
                instanceKey: instanceKey,
                children: []
              };

              hierarchicalQuestions.push(standaloneInstance);
            }
          }
        }
      });
    }
  }

  const orphanedConditionals = visibleConditionalQuestions.filter(q => !usedConditionalQuestions.has(q.id));
  orphanedConditionals.forEach(orphan => {
    const orphanChildren = findChildren(orphan.id);
    markUsedRecursively(orphanChildren);

    hierarchicalQuestions.push({
      ...orphan,
      isOrphan: true,
      level: 0,
      children: orphanChildren
    });
  });

  return hierarchicalQuestions;
};

/**
 * Gets conditional questions for a parent question
 */
export const getConditionalQuestions = (
  parentQuestionId: string,
  stepId: string,
  allQuestionsData: { [stepId: string]: any[] }
): any[] => {
  const stepQuestions = allQuestionsData[stepId] || [];
  const conditionalQuestions = stepQuestions.filter(q => q.isConditional);

  return conditionalQuestions.filter(q => {
    return true; // Placeholder - actual implementation would check dependencies
  });
};
