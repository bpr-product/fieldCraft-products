/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Dependency Graph Types - Question dependencies and relationships
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

/**
 * Dependency Graph Types
 * Defines the structure for question dependencies and relationships
 */

export interface DependencyNode {
  questionOID: string;
  conditionOID?: string;
  dependsOn: DependencyTrigger[];  // What questions control this one
  controls: string[];               // What questions this one controls
  expression?: string;
  logic?: 'AND' | 'OR' | 'SINGLE';
}

export interface DependencyTrigger {
  questionOID: string;
  expectedValue: string | string[];
  operator: '==' | '!=' | 'contains';
  negated?: boolean;  // NEW: True if condition has ! negation operator
}

export interface MultiInstanceConfig {
  methodOID: string;
  repeatCountQuestionOID: string;      // The question controlling instance count
  parentGroupOID: string;              // ItemGroup containing RepeatCount question
  repeatingGroupOID?: string;          // ItemGroup that gets repeated (if exists)
  controllerQuestions: string[];       // Questions that trigger RepeatCount visibility
  defaultInstanceCount: number;
  maxInstances: number;
}

export interface DependencyGraph {
  nodes: Map<string, DependencyNode>;
  multiInstanceConfigs: Map<string, MultiInstanceConfig>;
}

export interface ConditionalExpression {
  triggers: DependencyTrigger[];
  logic: 'AND' | 'OR' | 'SINGLE';
}
