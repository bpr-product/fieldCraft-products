/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Multi-Instance Parser - Extracts RepeatCount configurations from MethodDef
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

/**
 * Multi-Instance Parser
 * Extracts RepeatCount configurations from MethodDef
 * Supports multiple multi-instance scenarios (Roof Maxx has 3!)
 */

import type { MultiInstanceConfig, DependencyNode } from '../types/DependencyTypes';

export class MultiInstanceParser {
  private methodDefs: any[];
  private formDef: any;
  private itemGroupDefs: any[];
  private dependencyGraph: Map<string, DependencyNode>;

  constructor(templateVersion: any, dependencyGraph: Map<string, DependencyNode>) {
    this.methodDefs = templateVersion?.MethodDef || [];
    this.formDef = templateVersion?.FormDef?.[0];
    this.itemGroupDefs = templateVersion?.ItemGroupDef || [];
    this.dependencyGraph = dependencyGraph;
  }

  /**
   * Parse MethodDef expression: "!(I.24 : RepeatCount)"
   * Returns the question OID that controls the instance count
   */
  private parseMethodExpression(expression: string): string | null {
    const match = expression.match(/!\(([\w.]+)\s*:\s*RepeatCount\)/);
    return match ? match[1] : null;
  }

  /**
   * Find which questions control the RepeatCount question visibility
   * Uses dependency graph to discover controller questions
   */
  private findControllerQuestions(repeatCountQuestionOID: string): string[] {
    const node = this.dependencyGraph.get(repeatCountQuestionOID);

    if (!node || !node.dependsOn) return [];

    return node.dependsOn.map(trigger => trigger.questionOID);
  }


  private findRepeatingGroup(methodOID: string, repeatCountQuestionOID: string): string | undefined {
    if (!this.formDef) return undefined;

    const itemGroupRefs = this.formDef.ItemGroupRef || [];
    const repeatingGroupRef = itemGroupRefs.find(
      (ref: any) => ref.MethodOID === methodOID
    );

    if (repeatingGroupRef) {
      const groupOID = repeatingGroupRef.ItemGroupOID;
      const groupDef = this.itemGroupDefs.find((g: any) => g.OID === groupOID);

      if (groupDef && groupDef.Repeating === 'Yes') {
        return groupOID;
      } else {      }
    }

    for (const group of this.itemGroupDefs) {
      if (group.Repeating === 'Yes') {
        const hasQuestion = group.ItemRef?.some(
          (ref: any) => ref.ItemOID === repeatCountQuestionOID
        );
        
        if (hasQuestion) {
          return group.OID;
        }
      }
    }

    return undefined;
  }

  private findParentGroup(repeatCountQuestionOID: string): string | undefined {
    // Search all ItemGroups (including repeating ones for nested scenarios)
    for (const group of this.itemGroupDefs) {
      const hasQuestion = group.ItemRef?.some(
        (ref: any) => ref.ItemOID === repeatCountQuestionOID
      );

      if (hasQuestion) {
        return group.OID;
      }
    }

    return undefined;
  }

  /**
   * Parse all multi-instance configurations
   * Returns a map of RepeatCount question OID to config
   */
  parseAll(): Map<string, MultiInstanceConfig> {
    const configs = new Map<string, MultiInstanceConfig>();

    this.methodDefs.forEach((methodDef: any) => {
      const repeatCountQuestionOID = this.parseMethodExpression(
        methodDef.FormalExpression?.text || ''
      );

      if (!repeatCountQuestionOID) {
        return;
      }

      // Find parent group (contains RepeatCount question)
      const parentGroupOID = this.findParentGroup(repeatCountQuestionOID) || '';

      const repeatingGroupOID = this.findRepeatingGroup(methodDef.OID, repeatCountQuestionOID);
      const controllerQuestions = this.findControllerQuestions(repeatCountQuestionOID);

      const config: MultiInstanceConfig = {
        methodOID: methodDef.OID,
        repeatCountQuestionOID,
        parentGroupOID,
        repeatingGroupOID,
        controllerQuestions,
        defaultInstanceCount: 2,  // Could be configurable
        maxInstances: 20           // Could be configurable
      };

      configs.set(repeatCountQuestionOID, config);
    });

    return configs;
  }

  /**
   * Check if a question is a RepeatCount question
   */
  isRepeatCountQuestion(questionOID: string): boolean {
    return this.parseAll().has(questionOID);
  }

  /**
   * Get config for a RepeatCount question
   */
  getConfig(questionOID: string): MultiInstanceConfig | undefined {
    return this.parseAll().get(questionOID);
  }

  /**
   * Get all RepeatCount questions in the template
   */
  getAllRepeatCountQuestions(): string[] {
    return Array.from(this.parseAll().keys());
  }

  /**
   * Check if a question is a controller for any multi-instance
   */
  isMultiInstanceController(questionOID: string): boolean {
    const allConfigs = this.parseAll();
    const configsArray = Array.from(allConfigs.values());
    
    return configsArray.some(config => 
      config.controllerQuestions.includes(questionOID)
    );
  }

  /**
   * Get all multi-instance configs where this question is a controller
   */
  getControlledMultiInstances(questionOID: string): MultiInstanceConfig[] {
    const allConfigs = this.parseAll();
    const configsArray = Array.from(allConfigs.values());

    return configsArray.filter(config =>
      config.controllerQuestions.includes(questionOID)
    );
  }

  /**
   * Get count of multi-instance scenarios in template
   * Roof Replacement = 1, Roof Maxx = 3
   */
  getMultiInstanceCount(): number {
    return this.methodDefs.length;
  }
}
