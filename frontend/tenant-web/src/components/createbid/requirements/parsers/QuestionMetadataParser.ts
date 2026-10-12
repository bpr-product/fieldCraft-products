/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Question Metadata Parser - Extracts question details from ItemDef and CodeList
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

/**
 * Question Metadata Parser
 * Extracts question details from ItemDef and CodeList
 */

import type { QuestionMetadata, AnswerOption, ItemGroupMetadata } from '../types/TemplateTypes';

export class QuestionMetadataParser {
  private itemDefs: any[];
  private codeLists: any[];
  private itemGroupDefs: any[];

  constructor(templateVersion: any) {
    this.itemDefs = templateVersion?.ItemDef || [];
    this.codeLists = templateVersion?.CodeList || [];
    this.itemGroupDefs = templateVersion?.ItemGroupDef || [];
  }

  /**
   * Get question metadata by OID
   */
  getQuestion(oid: string): QuestionMetadata | null {
    const itemDef = this.itemDefs.find((item: any) => item.OID === oid);

    if (!itemDef) {
      return null;
    }

    const metadata: QuestionMetadata = {
      oid: itemDef.OID,
      questionText: itemDef.Question?.TranslatedText?.text || '',
      dataType: itemDef.DataType || 'text',
      codeListOID: itemDef.CodeListRef?.CodeListOID,
      mandatory: itemDef.Mandatory
    };

    // If has CodeList, fetch answer options
    if (metadata.codeListOID) {
      metadata.answerOptions = this.getAnswerOptions(metadata.codeListOID);
    }

    return metadata;
  }

  /**
   * Get answer options from CodeList
   */
  getAnswerOptions(codeListOID: string): AnswerOption[] {
    const codeList = this.codeLists.find((cl: any) => cl.OID === codeListOID);

    if (!codeList || !codeList.CodeListItem) {
      return [];
    }

    return codeList.CodeListItem.map((item: any) => ({
      value: item.CodedValue,
      displayText: item.Decode?.TranslatedText?.text || item.CodedValue
    }));
  }

  /**
   * Get all questions in an ItemGroup
   */
  getQuestionsInGroup(itemGroupOID: string): QuestionMetadata[] {
    const group = this.itemGroupDefs.find((g: any) => g.OID === itemGroupOID);

    if (!group || !group.ItemRef) {
      return [];
    }

    return group.ItemRef
      .map((ref: any) => this.getQuestion(ref.ItemOID))
      .filter((q: QuestionMetadata | null) => q !== null) as QuestionMetadata[];
  }

  /**
   * Get ItemGroup metadata
   */
  getItemGroup(itemGroupOID: string): ItemGroupMetadata | null {
    const group = this.itemGroupDefs.find((g: any) => g.OID === itemGroupOID);

    if (!group) {
      return null;
    }

    return {
      oid: group.OID,
      name: group.Name || group.OID,
      description: group.Description?.TranslatedText?.text || '',
      repeating: group.Repeating === 'Yes',
      questions: group.ItemRef?.map((ref: any) => ref.ItemOID) || []
    };
  }

  /**
   * Get all ItemGroups
   */
  getAllItemGroups(): ItemGroupMetadata[] {
    return this.itemGroupDefs
      .map((group: any) => this.getItemGroup(group.OID))
      .filter((g: ItemGroupMetadata | null) => g !== null) as ItemGroupMetadata[];
  }

  /**
   * Check if a question has answer options (dropdown/radio)
   */
  hasAnswerOptions(questionOID: string): boolean {
    const question = this.getQuestion(questionOID);
    return !!(question?.codeListOID);
  }

  /**
   * Get data type of a question
   */
  getDataType(questionOID: string): string | null {
    const question = this.getQuestion(questionOID);
    return question?.dataType || null;
  }
}
