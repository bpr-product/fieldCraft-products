/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Core Template Types - Structure of parsed SDM template data
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

/**
 * Core Template Types
 * Defines the structure of parsed SDM template data
 */

export interface TemplateMetadata {
  tradeName: string;           // "Roof Replacement", "Roof Max Trade"
  tradeType: string;            // "P.7", "P.X", etc.
  description: string;
  protocolName: string;
  formOID: string;              // "F.1"
  eventOID: string;             // "E.1"
}

export interface QuestionMetadata {
  oid: string;                  // "I.24"
  questionText: string;         // "How many vents do you need?"
  dataType: string;             // "text", "integer", "float", "date", "datetime"
  codeListOID?: string;         // Reference to answer options
  answerOptions?: AnswerOption[];
  mandatory?: string;           // "Yes" or "No"
}

export interface AnswerOption {
  value: string;                // "1", "Yes", etc.
  displayText: string;          // Display label
}

export interface ItemGroupMetadata {
  oid: string;                  // "IG.5"
  name: string;
  description: string;          // "Customization & Warranty"
  repeating: boolean;           // true if Repeating === "Yes"
  questions: string[];          // Array of ItemOIDs
}

export interface ConditionMetadata {
  oid: string;                  // "C.12"
  expression: string;           // "!(I.23 == \"1\")|| !(I.26 == \"1\")"
  context: string;              // "OpenEDC"
}

export interface MethodMetadata {
  oid: string;                  // "M.1"
  expression: string;           // "!(I.24 : RepeatCount)"
  repeatCountQuestionOID?: string; // Parsed from expression
}
