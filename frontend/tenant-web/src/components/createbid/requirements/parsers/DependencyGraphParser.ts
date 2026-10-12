/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Dependency Graph Parser - Builds complete question dependency tree from ConditionDef
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

/**
 * Dependency Graph Parser
 * Builds complete question dependency tree from ConditionDef
 * Handles simple, OR, AND, and comma-separated conditional patterns
 */

import type {
  DependencyNode,
  DependencyTrigger,
  ConditionalExpression
} from '../types/DependencyTypes';

export class DependencyGraphParser {
  private conditionDefs: any[];
  private itemGroupDefs: any[];

  constructor(templateVersion: any) {
    this.conditionDefs = templateVersion?.ConditionDef || [];
    this.itemGroupDefs = templateVersion?.ItemGroupDef || [];
  }


  parseExpression(expression: string): ConditionalExpression {
    try {
      // Remove outer negation wrapper if exists
      let unwrapped = expression.trim();

      // Check for logical operators
      const hasOR = unwrapped.includes('||');
      const hasAND = unwrapped.includes('&&');

      if (hasOR) {
        const parts = unwrapped.split('||').map(p => p.trim());
        return {
          triggers: parts.map(part => this.parseSingleCondition(part)),
          logic: 'OR'
        };
      } else if (hasAND) {
        const parts = unwrapped.split('&&').map(p => p.trim());
        return {
          triggers: parts.map(part => this.parseSingleCondition(part)),
          logic: 'AND'
        };
      } else {
        return {
          triggers: [this.parseSingleCondition(unwrapped)],
          logic: 'SINGLE'
        };
      }
    } catch (error) {
      // console.error('Error parsing expression:', expression, error);
      return {
        triggers: [],
        logic: 'SINGLE'
      };
    }
  }


  private parseSingleCondition(condition: string): DependencyTrigger {
    // Remove outer negation: !(...)
    const negationMatch = condition.match(/!\((.*)\)/);
    const innerCondition = negationMatch ? negationMatch[1] : condition;
    const hasNegation = !!negationMatch;  // NEW: Track if negation operator present

    // Parse: QuestionOID == "Value"
    const equalityMatch = innerCondition.match(/([\w.]+)\s*==\s*"([^"]*)"/); // NOSONAR: Safe — deterministic regex on internal ODM expression, no backtracking risk.

    if (equalityMatch) {
      const questionOID = equalityMatch[1].trim();
      const value = equalityMatch[2];

      // Check if it's a comma-separated value (multi-select)
      if (value.startsWith(',')) {
        return {
          questionOID,
          expectedValue: value,
          operator: 'contains',
          negated: hasNegation
        };
      } else {
        return {
          questionOID,
          expectedValue: value,
          operator: '==',
          negated: hasNegation
        };
      }
    }

    // Fallback - couldn't parse
    // console.warn('Could not parse condition:', condition);
    return {
      questionOID: '',
      expectedValue: '',
      operator: '=='
    };
  }

  /**
   * Build complete dependency graph
   */
  buildGraph(): Map<string, DependencyNode> {
    const graph = new Map<string, DependencyNode>();

    // Iterate through all ItemGroups
    this.itemGroupDefs.forEach((itemGroup: any) => {
      itemGroup.ItemRef?.forEach((itemRef: any) => {
        const questionOID = itemRef.ItemOID;

        // Initialize node if not exists
        if (!graph.has(questionOID)) {
          graph.set(questionOID, {
            questionOID,
            dependsOn: [],
            controls: []
          });
        }

        // If has conditional visibility
        if (itemRef.CollectionExceptionConditionOID) {
          const conditionOID = itemRef.CollectionExceptionConditionOID;
          const condition = this.conditionDefs.find((c: any) => c.OID === conditionOID);

          if (condition) {
            const { triggers, logic } = this.parseExpression(
              condition.FormalExpression?.text || ''
            );

            const node = graph.get(questionOID)!;
            node.conditionOID = conditionOID;
            node.dependsOn = triggers;
            node.logic = logic;
            node.expression = condition.FormalExpression?.text;

            // Update parent questions to know they control this question
            triggers.forEach(trigger => {
              if (!trigger.questionOID) return; // Skip invalid triggers

              if (!graph.has(trigger.questionOID)) {
                graph.set(trigger.questionOID, {
                  questionOID: trigger.questionOID,
                  dependsOn: [],
                  controls: []
                });
              }

              const parentNode = graph.get(trigger.questionOID)!;
              if (!parentNode.controls.includes(questionOID)) {
                parentNode.controls.push(questionOID);
              }
            });
          }
        }
      });
    });


    return graph;
  }

  /**
   * Get all questions that should be cleaned when a question changes
   * Returns array of question OIDs that are no longer valid
   */
  getCleanupTargets(
    questionOID: string,
    newValue: string,
    currentAnswers: Record<string, any> = {}
  ): string[] {
    const graph = this.buildGraph();
    const node = graph.get(questionOID);


    if (!node) {
      return [];
    }

    const targets: Set<string> = new Set();

    const existingAnswer = currentAnswers[questionOID] || {};
    let updatedAnswers = {
      ...currentAnswers,
      [questionOID]: {
        ...existingAnswer,
        value: newValue
      }
    };

    const directTargets: string[] = [];
    node.controls.forEach(controlledQuestionOID => {
      const controlledNode = graph.get(controlledQuestionOID);
      if (!controlledNode) return;

      const shouldShow = this.evaluateCondition(controlledNode, updatedAnswers);

      if (!shouldShow) {
        directTargets.push(controlledQuestionOID);
      }
    });


    directTargets.forEach(targetOID => {
      const targetExisting = updatedAnswers[targetOID] || {};
      
      let clearedCodeList = targetExisting.CodeList;
      if (Array.isArray(clearedCodeList) && clearedCodeList.length > 0) {
        clearedCodeList = clearedCodeList.map((cl: any) => ({
          ...cl,
          CodeListItem: cl.CodeListItem?.map((item: any) => ({
            ...item,
            IsEnabled: false
          }))
        }));
      }
      
      updatedAnswers = {
        ...updatedAnswers,
        [targetOID]: {
          ...targetExisting,
          value: '',
          CodeList: clearedCodeList
        }
      };
    });

    directTargets.forEach(controlledQuestionOID => {
      targets.add(controlledQuestionOID);
      
      const cascadingTargets = this.getCleanupTargets(
        controlledQuestionOID,
        '',
        updatedAnswers
      );
      cascadingTargets.forEach(target => targets.add(target));
    });

    return Array.from(targets);
  }

  /**
   * Evaluate if a question should be visible given current answers
   */
  evaluateCondition(
    node: DependencyNode,
    currentAnswers: Record<string, any>
  ): boolean {
    if (!node.dependsOn || node.dependsOn.length === 0) {
      return true; // No dependencies, always visible
    }

    const results = node.dependsOn.map(trigger => {
      const answerObj = currentAnswers[trigger.questionOID];


      // Extract actual value from answersData object
      let currentValue: string | undefined;

      if (answerObj === null || answerObj === undefined) {
        currentValue = undefined;
      } else if (typeof answerObj === 'object') {
        // Check CodeList first (for singleselect/multiselect)
        if (answerObj.CodeList && Array.isArray(answerObj.CodeList) && answerObj.CodeList.length > 0) {
          const codeList = answerObj.CodeList[0];
          if (codeList && codeList.CodeListItem) {
            // FIX: Strip leading comma from expectedValue when comparing with CodedValue
            const rawExpectedValue = Array.isArray(trigger.expectedValue)
              ? trigger.expectedValue[0]
              : trigger.expectedValue;
            const expectedValueToMatch = rawExpectedValue.startsWith(',')
              ? rawExpectedValue.substring(1)
              : rawExpectedValue;
            const enabledItem = codeList.CodeListItem.find((item: any) =>
              item.CodedValue === expectedValueToMatch && item.IsEnabled
            );
            const enabledItems = codeList.CodeListItem.filter((i: any) => i.IsEnabled).map((i: any) => i.CodedValue);
            return !!enabledItem;
          }
          return false;
        }
        currentValue = answerObj.value;
      } else {
        currentValue = answerObj;
      }

      if (trigger.operator === '==') {
        const result = currentValue === trigger.expectedValue;
        return result;
      } else if (trigger.operator === 'contains') {
        const valueToCheck = Array.isArray(trigger.expectedValue)
          ? trigger.expectedValue[0]
          : trigger.expectedValue;
        return currentValue?.includes(valueToCheck) || false;
      } else if (trigger.operator === '!=') {
        return currentValue !== trigger.expectedValue;
      }
      return false;
    });

    let finalResult: boolean;
    if (node.logic === 'OR') {
      finalResult = results.some(r => r);
    } else if (node.logic === 'AND') {
      finalResult = results.every(r => r);
    } else {
      finalResult = results[0] || false;
    }
    
    return finalResult;
  }

  /**
   * Get all parent questions for a given question
   */
  getParentQuestions(questionOID: string): string[] {
    const graph = this.buildGraph();
    const node = graph.get(questionOID);

    if (!node || !node.dependsOn) return [];

    return node.dependsOn.map(trigger => trigger.questionOID);
  }

  /**
   * Get all child questions for a given question
   */
  getChildQuestions(questionOID: string): string[] {
    const graph = this.buildGraph();
    const node = graph.get(questionOID);

    if (!node) return [];

    return node.controls || [];
  }

  /**
   * Check if a question has conditional visibility
   */
  isConditional(questionOID: string): boolean {
    const graph = this.buildGraph();
    const node = graph.get(questionOID);

    return !!(node && node.conditionOID);
  }


  evaluateConditionWithInstance(
    baseQuestionOID: string,
    currentAnswers: Record<string, any>,
    instanceSuffix: string = ''
  ): boolean {
    const graph = this.buildGraph();
    const node = graph.get(baseQuestionOID);
    // No dependencies = always visible
    if (!node || !node.dependsOn || node.dependsOn.length === 0) {
      return true;
    }

    // Evaluate each dependency with instance suffix
    const results = node.dependsOn.map(trigger => {
      // Add instance suffix to parent question ID
      const parentQuestionKey = instanceSuffix
        ? `${trigger.questionOID}${instanceSuffix}`
        : trigger.questionOID;

      // Get answer value for this specific instance
      const answerObj = currentAnswers[parentQuestionKey];
      // Handle both {value: "x"} and direct value formats
      let currentValue;
      if (answerObj === null || answerObj === undefined) {
        currentValue = undefined;
      } else if (typeof answerObj === 'object' && 'value' in answerObj) {
        currentValue = answerObj.value;
      } else {
        currentValue = answerObj;
      }


      if (currentValue === undefined || currentValue === null || currentValue === '') {
        const hasCodeList = answerObj && typeof answerObj === 'object' && answerObj.CodeList;

        if (!hasCodeList) {
          return false;
        }

        // Continue processing multiselect below
      }

      // Evaluate based on operator
      let conditionResult: boolean;

      if (answerObj && typeof answerObj === 'object' && answerObj.CodeList && Array.isArray(answerObj.CodeList)) {

        // Find the CodeList entry
        const codeList = answerObj.CodeList[0]; // Usually single CodeList

        if (codeList && codeList.CodeListItem && Array.isArray(codeList.CodeListItem)) {
          // FIX: Strip leading comma from expectedValue when comparing with CodedValue
          const rawExpectedValue = Array.isArray(trigger.expectedValue)
            ? trigger.expectedValue[0]
            : trigger.expectedValue;
          const expectedValueToMatch = rawExpectedValue.startsWith(',')
            ? rawExpectedValue.substring(1)
            : rawExpectedValue;
          // Check if the expected value has IsEnabled: true
          const matchingItem = codeList.CodeListItem.find((item: any) =>
            item.CodedValue === expectedValueToMatch && item.IsEnabled
          );

          conditionResult = !!matchingItem;
        } else {
          conditionResult = false;
        }
      } else if (trigger.operator === '==') {
        conditionResult = currentValue === trigger.expectedValue;
      } else if (trigger.operator === 'contains') {
        // Handle comma-separated values (alternative multi-select format)
        const valueToCheck = Array.isArray(trigger.expectedValue)
          ? trigger.expectedValue[0]
          : trigger.expectedValue;
        conditionResult = currentValue?.includes(valueToCheck) || false;
      } else if (trigger.operator === '!=') {
        conditionResult = currentValue !== trigger.expectedValue;
      } else {
        conditionResult = false;
      }

      const finalResult = conditionResult; // Negation already accounted for in template

      return finalResult;
    });

    // Apply logic (OR, AND, SINGLE)
    let result: boolean;
    if (node.logic === 'OR') {
      result = results.some(r => r);
    } else if (node.logic === 'AND') {
      result = results.every(r => r);
    } else {
      result = results[0] || false;
    }

    return result;
  }


  getCleanupTargetsWithInstance(
    baseQuestionOID: string,
    instanceSuffix: string,
    newValue: string,
    currentAnswers: Record<string, any> = {}
  ): string[] {
    const graph = this.buildGraph();
    const node = graph.get(baseQuestionOID);

    if (!node) return [];

    const targets: Set<string> = new Set();

    // Build updated answers with the new value
    const answerKey = `${baseQuestionOID}${instanceSuffix}`;
    const existingAnswer = currentAnswers[answerKey] || currentAnswers[baseQuestionOID] || {};
    let updatedAnswers = {
      ...currentAnswers,
      [answerKey]: {
        ...existingAnswer,
        value: newValue
      }
    };

    // First pass: collect all direct dependents that will be cleaned
    const directTargets: { questionOID: string; fullKey: string }[] = [];
    node.controls.forEach(controlledQuestionOID => {
      const controlledNode = graph.get(controlledQuestionOID);
      if (!controlledNode) return;

      const shouldShow = this.evaluateConditionWithInstance(
        controlledQuestionOID,
        updatedAnswers,
        instanceSuffix
      );

      if (!shouldShow) {
        const fullKey = `${controlledQuestionOID}${instanceSuffix}`;
        directTargets.push({ questionOID: controlledQuestionOID, fullKey });
      }
    });

    directTargets.forEach(target => {
      const targetExisting = updatedAnswers[target.fullKey] || updatedAnswers[target.questionOID] || {};
      
      // Clear CodeList items' IsEnabled flag for singleselect/multiselect
      let clearedCodeList = targetExisting.CodeList;
      if (Array.isArray(clearedCodeList) && clearedCodeList.length > 0) {
        clearedCodeList = clearedCodeList.map((cl: any) => ({
          ...cl,
          CodeListItem: cl.CodeListItem?.map((item: any) => ({
            ...item,
            IsEnabled: false
          }))
        }));
      }
      
      updatedAnswers = {
        ...updatedAnswers,
        [target.fullKey]: {
          ...targetExisting,
          value: '',
          CodeList: clearedCodeList // Clear CodeList IsEnabled flags
        }
      };
    });

    // Second pass: add targets and recursively get cascading targets
    directTargets.forEach(target => {
      targets.add(target.fullKey);

      // Recursively check children (cascade cleanup)
      const cascadingTargets = this.getCleanupTargetsWithInstance(
        target.questionOID,
        instanceSuffix,
        '',
        updatedAnswers
      );
      cascadingTargets.forEach(t => targets.add(t));
    });

    return Array.from(targets);
  }
}
