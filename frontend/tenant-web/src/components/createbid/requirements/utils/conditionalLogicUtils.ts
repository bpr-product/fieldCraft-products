/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Conditional Logic Utilities - Consolidated conditional question visibility & dynamic parsing
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* -----------------------------------------------------------------------------
*/

import { ConditionDef, CodeList, MethodDef } from '../../../../interface/SdmInterfaces';

const MAX_MULTI_INSTANCE_COUNT = 20;

export interface ConditionalLogicState {
  conditionDef: ConditionDef[];
  allQuestionsData: { [stepId: string]: any[] };
  codeList: CodeList[];
  answersData: { [questionId: string]: any };
  methodDef: any[];
}


/**
 * Compute the set of OIDs a SELECTED/SUGGESTED reference bid must NOT load into the
 * current QA, so selecting a project can only FILL answers for questions already
 * visible — never reveal or expand a conditional/repeating group the main flow does
 * not show (e.g. "Roof Vents", "Pipes", "Skylights"). A bid can carry such OIDs,
 * often because it was authored under a different template version where the same OID
 * was a flat question (OID drift).
 *
 * Returns the union of:
 *   • currently-hidden conditional questions (their ItemRef has a
 *     CollectionExceptionConditionOID that is not satisfied by the current answers), and
 *   • the parent/driver OID(s) referenced by those hidden conditions — whose value
 *     could flip a hidden group ON (conservative: skipped even if the bid's specific
 *     value would not reveal it; never loading a driver is safe, revealing is not).
 *
 * It also covers GROUP-LEVEL conditions: a conditional repeating group (e.g.
 * "Skylights") gated by a FormDef ItemGroupRef.CollectionExceptionConditionOID — its
 * children AND its repeat-count control would otherwise be applied, creating an
 * instance and showing the whole group even though the parent is unanswered.
 *
 * Non-conditional questions (e.g. the roof-facets count, an always-visible group) are
 * never in the set, so legitimate main-flow answers still load. Pure + deterministic.
 */
export const computeReferenceBidSkipOIDs = (
  itemGroupDefs: any[],
  conditionDef: ConditionDef[],
  itemGroupDatas: any[],
  answersData: Record<string, any>,
  formDef: any[] = [],
  methodDef: any[] = [],
  // When true, ``answersData`` is a SIMULATED snapshot of what the answers would
  // look like if the bid being applied were already fully applied — not the raw
  // pre-apply state. In that mode, visibility (evaluateConditionalQuestionVisibility
  // below) already reflects the real, final effect of the bid's own driver values,
  // so defensively ALSO skipping the driver itself (addDrivers) is unnecessary and
  // actively wrong when one driver satisfies one condition while failing another
  // (e.g. I.1 reveals group A but not group B — group B correctly stays hidden via
  // the visibility check itself; skipping I.1 too would wrongly block a plain,
  // always-visible question from ever loading its own answer). Defaults to false so
  // every pre-existing caller/test — which passes the true pre-apply state and
  // relies on the conservative driver-skip — is completely unaffected.
  isPostApplySnapshot: boolean = false,
  // AB#30521 frontend parity fix — mirrors EstimateAutomationService.buildTradeAnswersJson's
  // sourceIsSynthetic guard (Java, PRs #8611/#8616). A synthetic reference-bid match's own
  // reported count for a count-driven accessory group (Roof Vents/Pipes/Skylights) is generic
  // fixture data, not this property's truth — real staging evidence (tenant tn88351775) showed
  // the SAME fabricated skylight/vent/pipe counts bleeding into unrelated real projects via
  // Create Bid 3.0's manual "select a reference bid" flow, independent of the Java fix (this
  // function has no synthetic awareness at all before this change). Defaults to false so every
  // pre-existing caller/test — none of which know about synthetic matches — is unaffected.
  sourceIsSynthetic: boolean = false,
  // The control OID(s) of the property's own real per-facet MEASUREMENT group (e.g.
  // TotalRoofFacets), resolved by the caller from the template's MetaKey (same two aliases the
  // Java FACET_COUNT_METAKEY_ALIASES uses: 'facetcount'/'totalrooffacets'). Exempted from the
  // sourceIsSynthetic force-skip below, unconditionally — mirrors the Java fix's own correction
  // (PR #8616): a first version there blanket-skipped every count-driven group for a synthetic
  // match, which also wiped the real facet-measurement section (22 of 56 items in one real
  // template) and had to be corrected. Never skipping this group for that reason alone keeps
  // this function's behavior consistent with the already-shipped, already-verified backend fix.
  facetCountControlOids: Set<string> = new Set(),
): Set<string> => {
  const skip = new Set<string>();
  // Add every driver OID referenced in a condition expression (left of each "==",
  // across || / && clauses) — whose value could flip a hidden group ON. Skipped
  // entirely when isPostApplySnapshot (see param doc above) — the visibility check
  // itself already accounts for the driver's real, final value in that mode.
  //
  // Assumes FIELDCRAFT condition format: "I.x == value" (simple equality only). The LHS
  // is taken before "==", then non-OID chars are stripped. This stays correct for
  // !=, >=, <= because the strip regex below removes >, <, ! as well — the OID
  // still survives. It would misparse only an exotic operator the regex doesn't
  // cover. Even then this is fail-safe: addDrivers ONLY adds OIDs to the skip set
  // (it never reads the RHS value), so a misparse can at worst over-skip a driver
  // (a select loads one fewer answer) — it can never reveal a hidden group.
  const addDrivers = (conditionOID: string | undefined) => {
    if (!conditionOID || isPostApplySnapshot) return;
    const cd = (conditionDef || []).find((c: any) => c.OID === conditionOID);
    const expr = cd?.FormalExpression?.text || '';
    expr.split(/\|\||&&/).forEach((part: string) => {
      const drv = part.split('==')[0]?.replace(/[&/\\#,+()$~%'":*?<>{}! ]/g, '').trim();
      if (drv) skip.add(drv);
    });
  };

  // (1) Per-ItemRef conditional questions (e.g. Roof Vents / Pipes children).
  (Array.isArray(itemGroupDefs) ? itemGroupDefs : []).forEach((g: any) => {
    (g?.ItemRef || []).forEach((ir: any) => {
      if (!ir?.CollectionExceptionConditionOID) return;
      const visible = evaluateConditionalQuestionVisibility(
        ir, ir.ItemOID, itemGroupDatas, conditionDef, answersData,
      );
      if (visible) return;
      skip.add(ir.ItemOID);                          // hidden conditional child
      addDrivers(ir.CollectionExceptionConditionOID); // its parent/driver(s)
    });
  });

  // (2) GROUP-LEVEL conditional groups (e.g. the Skylights repeating group). A group's
  // condition lives on the FormDef ItemGroupRef. When it is not satisfied, the whole
  // group is hidden in the main flow — so skip ALL its OIDs (children + repeat control)
  // and the condition's driver(s), so a selected bid can neither fill them nor create
  // an instance that would reveal the group.
  const groupCondByOID = new Map<string, string>();
  (Array.isArray(formDef) ? formDef : []).forEach((fd: any) => {
    (fd?.ItemGroupRef || []).forEach((igRef: any) => {
      if (igRef?.CollectionExceptionConditionOID && igRef?.ItemGroupOID) {
        groupCondByOID.set(igRef.ItemGroupOID, igRef.CollectionExceptionConditionOID);
      }
    });
  });
  (Array.isArray(itemGroupDefs) ? itemGroupDefs : []).forEach((g: any) => {
    const groupCondOID = groupCondByOID.get(g?.OID);
    if (!groupCondOID) return;
    const groupVisible = evaluateConditionalQuestionVisibility(
      { CollectionExceptionConditionOID: groupCondOID }, g.OID, itemGroupDatas, conditionDef, answersData,
    );
    if (groupVisible) return;
    (g?.ItemRef || []).forEach((ir: any) => { if (ir?.ItemOID) skip.add(ir.ItemOID); });
    addDrivers(groupCondOID);
  });

  // (3) COUNT-DRIVEN repeating groups (no conditional gate) — e.g. Skylights / Roof Vents /
  // Pipes. These repeating ItemGroups are shown purely by an instance count driven by a
  // RepeatCount MethodDef on their FormDef ItemGroupRef:
  //   MethodDef.FormalExpression.text = "!(I.x : RepeatCount)"  → control OID = I.x.
  // In the main flow the group only appears once the user sets that count > 0. So when the
  // group is NOT currently active (control count is 0 / unanswered in the user's answers),
  // selecting a reference bid must not spawn it: skip the control OID AND the group's
  // member OIDs. A group already active (e.g. Roof Facets, default ≥1) is left to load.
  const methodById = new Map<string, any>((methodDef || []).map((m: any) => [m?.OID, m]));
  const igByOID = new Map<string, any>((itemGroupDefs || []).map((g: any) => [g?.OID, g]));
  (Array.isArray(formDef) ? formDef : []).forEach((fd: any) => {
    (fd?.ItemGroupRef || []).forEach((igRef: any) => {
      if (!igRef?.MethodOID || !igRef?.ItemGroupOID) return;
      const md = methodById.get(igRef.MethodOID);
      const m = (md?.FormalExpression?.text || '').match(/\(\s*(I\.\d+)\s*:\s*RepeatCount\s*\)/);
      if (!m) return;                       // not a RepeatCount-driven group
      const controlOID = m[1];
      const raw = answersData?.[controlOID];
      const valStr = (raw && typeof raw === 'object') ? (raw.value ?? raw.Value) : raw;
      const cnt = parseInt(String(valStr ?? ''), 10);
      // A synthetic source's own reported count is never trusted, even when positive — EXCEPT
      // for the facet-measurement group itself (see facetCountControlOids doc above). Every
      // other count-driven group (the accessory class this fix targets) still gets force-skipped
      // for a synthetic match regardless of what count it reports.
      const isFacetCountGroup = facetCountControlOids.has(controlOID);
      if ((!sourceIsSynthetic || isFacetCountGroup) && Number.isFinite(cnt) && cnt > 0) return;  // group already active → allow loading
      skip.add(controlOID);                          // don't apply the count → no instances
      const grp = igByOID.get(igRef.ItemGroupOID);
      if (grp?.Repeating === 'Yes') {
        (grp.ItemRef || []).forEach((ir: any) => { if (ir?.ItemOID) skip.add(ir.ItemOID); });
      }
    });
  });

  return skip;
};

export const evaluateConditionalQuestionVisibility = (
  itemRef: any,
  conditionalQuestionOID: string,
  itemGroupDatas: any[],
  conditionDef: ConditionDef[],
  answersData?: Record<string, any>  // Optional: V2 answers data for validation
): boolean => {
  let showItem = true;

  if (itemRef.CollectionExceptionConditionOID !== null && itemRef.CollectionExceptionConditionOID !== undefined) {
    let itemConDef = conditionDef?.find((cd) => cd.OID === itemRef.CollectionExceptionConditionOID);
    if (itemConDef) {
      let cdf = itemConDef?.FormalExpression?.text.includes("||");

      if (cdf === true) {
        let cdd = itemConDef?.FormalExpression?.text.split("||");
        for (let i = 0; i < cdd.length; i++) {
          let cdnItemOID = cdd[i].split("==")[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
          let cdnValue = cdd[i].split("==")[1]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
          let itemDataArr = itemGroupDatas?.map((igd) => igd?.ItemData?.find((id) => id?.ItemOID === cdnItemOID));
          if (itemDataArr && itemDataArr.length > 0) {
            let iData = itemDataArr[0];
            if (iData != null && iData !== undefined) {
              if (iData.CodeList !== null && iData.CodeList !== undefined && iData.CodeList.length > 0) {
                if (iData && iData.CodeList) {
                  let codeListItems = iData.CodeList[0].CodeListItem;
                  let isEnabled = codeListItems.find((c: any) => c.CodedValue === cdnValue && c.IsEnabled);
                  if (isEnabled) {
                    showItem = true;
                    break;
                  } else {
                    showItem = false;
                  }
                }
              }
              else if (iData.Value !== null && iData.Value !== undefined && iData.Value === cdnValue) {
                showItem = true;
                break;
              } else {
                showItem = false;
              }
            }
            else {
              showItem = false;
            }
          } else {
            showItem = false;
          }
        }
      }
      else {
        let cdnItemOID = itemConDef.FormalExpression.text.split("==")[0]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
        let cdnValue = itemConDef.FormalExpression.text.split("==")[1]?.replace(/[&\/\\#,+()$~%'":*?<>{}! ]/g, '');
        let itemDataArr = itemGroupDatas?.map((igd) => igd?.ItemData?.find((id) => id?.ItemOID === cdnItemOID));



        const parentExistsInV2 = answersData && cdnItemOID && answersData[cdnItemOID];
        if (answersData && !parentExistsInV2) {
          showItem = false;
        }
        else if (parentExistsInV2 && answersData[cdnItemOID].CodeList) {
          // Use V2 CodeList (most up-to-date)
          const v2CodeList = answersData[cdnItemOID].CodeList;

          let foundEnabledMatch = false;
          v2CodeList.forEach((cd: any) => {
            let codeListItem = cd.CodeListItem?.find((c: any) => c.CodedValue === cdnValue);
            if (codeListItem && codeListItem.IsEnabled) {
              foundEnabledMatch = true;
            }
          });

          if (!foundEnabledMatch) {
            showItem = false;
          }
        }
        else if (itemDataArr && itemDataArr.length > 0) {
          // Fallback to V1 data
          let iData = itemDataArr[0];
          if (iData != null && iData !== undefined) {
            if (iData.CodeList !== null && iData.CodeList !== undefined && iData.CodeList.length > 0) {

              let foundEnabledMatch = false;
              if (iData && iData.CodeList) {
                iData.CodeList.forEach((cd: any) => {
                  let codeListItem = cd.CodeListItem.find((c: any) => c.CodedValue === cdnValue);
                  if (codeListItem && codeListItem.IsEnabled) {
                    foundEnabledMatch = true;
                  }
                });
              }
              if (!foundEnabledMatch) {
                showItem = false;
              }
            }
            // For non-CodeList questions: check Value directly
            else if (iData.Value === null || iData.Value === undefined || iData.Value !== cdnValue) {
              showItem = false;
            }
          }
          else {
            showItem = false;
          }
        } else {
          showItem = false;
        }
      }
    }
  }

  return showItem;
};


export const convertAnswersDataToItemGroupData = (
  answersData: any,
  allQuestionsData: { [stepId: string]: any[] },
  codeList: CodeList[]
): any[] => {
  const itemGroupData: any[] = [];
  const itemDataMap: { [key: string]: any } = {};


  Object.entries(answersData).forEach(([answerKey, answerInfo]: [string, any]) => {

    let itemOID = answerKey;
    let repeatKey = 0;
    let parentItemGroupRepeatKey: number | undefined = undefined;

    // Parse the key to extract itemOID, repeatKey, and parentItemGroupRepeatKey
    if (answerKey.includes('_') && !isNaN(parseInt(answerKey.split('_').pop() || ''))) {
      const parts = answerKey.split('_');

      if (parts.length === 3) {
        // Triple-suffix key: I.24_2_1 (itemOID_parentInstance_childInstance)
        itemOID = parts[0];
        parentItemGroupRepeatKey = parseInt(parts[1]);
        repeatKey = parseInt(parts[2]);
      } else if (parts.length === 2) {
        // Double-suffix key: I.35_2 (itemOID_instance)
        itemOID = parts[0];
        repeatKey = parseInt(parts[1]);
      }
    } else {
    }

    let questionDataType = null;
    let codeListRef = null;

    for (const stepId of Object.keys(allQuestionsData)) {
      const stepQuestions = allQuestionsData[stepId] || [];
      const questionData = stepQuestions.find(q => q.id === itemOID);
      if (questionData) {
        questionDataType = questionData.dataType;
        codeListRef = questionData.itemDef?.CodeListRef?.CodeListOID;
        break;
      }
    }

    const itemData: any = {
      ItemOID: itemOID,
      Value: null,
      CodeList: null,
      ItemGroupRepeatKey: repeatKey || answerInfo.repeatKey || 0
    };

    // Add ParentItemGroupRepeatKey if this is a nested instance
    if (parentItemGroupRepeatKey !== undefined) {
      itemData.ParentItemGroupRepeatKey = parentItemGroupRepeatKey;
    }

    if (questionDataType === 'singleselect' || questionDataType === 'multiselect') {

      const hasPreservedCodeList = answerInfo.CodeList && answerInfo.CodeList.length > 0;

      if (hasPreservedCodeList) {
        // Use the preserved CodeList structure directly (already has IsEnabled flags)
        itemData.CodeList = answerInfo.CodeList.map((cl: any) => ({
          ...cl,
          ItemGroupRepeatKey: itemData.ItemGroupRepeatKey
        }));
      } else if (codeListRef) {
        // Fallback: rebuild CodeList from template (for backward compatibility)
        const codeListData = codeList.find(cl => cl.OID === codeListRef);
        if (codeListData && codeListData.CodeListItem) {
          const selectedItems = Array.isArray(answerInfo.value) ? answerInfo.value : [answerInfo.value];
          const codeListItems = codeListData.CodeListItem.map((item: any) => ({
            ...item,
            IsEnabled: selectedItems.includes(item.CodedValue)
          }));

          itemData.CodeList = [{
            OID: codeListRef,
            CodeListItem: codeListItems,
            ItemGroupRepeatKey: itemData.ItemGroupRepeatKey
          }];
        }
      }
    } else {
      itemData.Value = answerInfo.value;
    }

    itemDataMap[answerKey] = itemData;
  });

  if (Object.keys(itemDataMap).length > 0) {
    itemGroupData.push({
      ItemData: Object.values(itemDataMap),
      ItemGroupOID: 'unified',
      ItemGroupRepeatKey: 1
    });
  }

  return itemGroupData;
};

export const shouldShowConditionalQuestionInSidebar = (
  question: any,
  state: ConditionalLogicState
): boolean => {
  const itemGroupDatas = convertAnswersDataToItemGroupData(
    state.answersData,
    state.allQuestionsData,
    state.codeList
  );

  const stepQuestions = state.allQuestionsData[question.stepId] || [];
  const questionData = stepQuestions.find(q => q.id === question.id);

  if (!questionData || !questionData.itemRef) {
    return false;
  }

  return evaluateConditionalQuestionVisibility(
    questionData.itemRef,
    question.id,
    itemGroupDatas,
    state.conditionDef
  );
};


export const getAllConditionalQuestions = (
  parentQuestionId: string,
  stepId: string,
  getConditionalQuestions: (parentId: string, stepId: string) => any[],
  state: ConditionalLogicState,
  level: number = 0
): any[] => {
  if (level > 5) return [];

  const directConditionals = getConditionalQuestions(parentQuestionId, stepId);
  let allConditionals: any[] = [];

  directConditionals.forEach(condQuestion => {
    const itemGroupDatas = convertAnswersDataToItemGroupData(
      state.answersData,
      state.allQuestionsData,
      state.codeList
    );
    const shouldShow = condQuestion.itemRef ?
      evaluateConditionalQuestionVisibility(condQuestion.itemRef, condQuestion.id, itemGroupDatas, state.conditionDef) :
      false;

    if (shouldShow) {
      allConditionals.push({ ...condQuestion, level });
      const nestedConditionals = getAllConditionalQuestions(
        condQuestion.id,
        stepId,
        getConditionalQuestions,
        state,
        level + 1
      );
      allConditionals = allConditionals.concat(nestedConditionals);
    }
  });

  return allConditionals;
};

/**
 * Gets MethodDef repeat count for a question
 */
export const getMethodDefRepeatCount = (
  questionId: string,
  methodDef: any[],
  answersData: { [questionId: string]: any }
): number => {
  const methodDefItem = methodDef.find(md => {
    const expression = md.FormalExpression?.text || '';
    return expression.includes(questionId) &&
      expression.includes('RepeatCount') &&
      (expression.includes('ItemGroup') || expression.includes('Repeat'));
  });

  if (methodDefItem) {
    const answer = answersData[questionId];
    if (answer && answer.value && !isNaN(parseInt(answer.value))) {
      const repeatCount = parseInt(answer.value);

      if (repeatCount > 0 && repeatCount <= MAX_MULTI_INSTANCE_COUNT) {
        return repeatCount;
      } else if (repeatCount > MAX_MULTI_INSTANCE_COUNT) {
        return 0;
      }
    }
  }

  return 0;
};

export interface ConditionalRelationship {
  targetQuestion: string;
  triggerQuestions: string[];
  condition: string;
  conditionOID: string;
}

export interface RepeatCountRelationship {
  repeatQuestion: string;
  controlQuestions: string[];
  methodOID: string;
  expression: string;
}

/**
 * Parse ConditionDef to find all conditional relationships
 */
export const parseConditionalRelationships = (conditionDefs: ConditionDef[]): ConditionalRelationship[] => {
  const relationships: ConditionalRelationship[] = [];

  conditionDefs.forEach(condition => {
    const expression = condition.FormalExpression?.text || '';

    const questionMatches = expression.match(/I\.\d+/g) || [];
    const uniqueQuestions = Array.from(new Set(questionMatches));

    if (uniqueQuestions.length > 0) {

      relationships.push({
        targetQuestion: '', // Will be filled when we match with ItemDef
        triggerQuestions: uniqueQuestions,
        condition: expression,
        conditionOID: condition.OID
      });
    }
  });

  return relationships;
};

/**
 * Parse MethodDef to find repeat count relationships
 */
export const parseRepeatCountRelationships = (methodDefs: MethodDef[]): RepeatCountRelationship[] => {
  const relationships: RepeatCountRelationship[] = [];

  methodDefs.forEach(method => {
    const expression = method.FormalExpression?.text || '';

    // Parse expressions that determine repeat counts
    const questionMatches = expression.match(/I\.\d+/g) || [];

    if (questionMatches.length > 0) {
      relationships.push({
        repeatQuestion: '', // Will be determined by usage context
        controlQuestions: questionMatches,
        methodOID: method.OID,
        expression: expression
      });
    }
  });

  return relationships;
};

/**
 * Find which questions control a specific repeating question
 */
export const findControllingQuestions = (
  targetQuestion: string,
  conditionalRelationships: ConditionalRelationship[]
): string[] => {
  const controllers: string[] = [];

  conditionalRelationships.forEach(relationship => {
    if (relationship.targetQuestion === targetQuestion) {
      controllers.push(...relationship.triggerQuestions);
    }
  });

  // Remove duplicates
  return Array.from(new Set(controllers));
};


export const getRepeatingControllers = (
  repeatingQuestion: string,
  conditionalRelationships: ConditionalRelationship[]
): string[] => {  // Fallback to parsed relationships (template-agnostic)
  return findControllingQuestions(repeatingQuestion, conditionalRelationships);
};

export const shouldShowRepeatingInstances = (
  parentQuestion: string,
  repeatingQuestion: string,
  currentAnswers: any,
  conditionalRelationships: ConditionalRelationship[]
): boolean => {
  const controllers = getRepeatingControllers(repeatingQuestion, conditionalRelationships);

  // Check if the parent is one of the controlling questions
  if (!controllers.includes(parentQuestion)) {
    return false;
  }

  // Check if any controller has "Yes" value (generic check, not template-specific)
  return controllers.some(controllerId => {
    const answerObj = currentAnswers[controllerId];
    const answer = answerObj?.value || answerObj;
    return answer === '1' || answer === 'Yes' || answer === true || answer === 1;
  });
};

/**
 * Parse condition expression to determine visibility
 */
export const evaluateConditionExpression = (
  expression: string,
  currentAnswers: any
): boolean => {
  try {
    if (!expression) return false;

    // Helper to check a single "I.<N> == "<value>"" condition against current answers
    const checkCondition = (part: string): boolean => {
      const trimmedPart = part.trim();
      const isNegated = trimmedPart.startsWith('!');
      
      const cleanPart = trimmedPart.replace(/^!/, '').replace(/[()]/g, '').trim();
      const sides = cleanPart.split('==');
      if (sides.length < 2) return false;
      
      const itemOID = sides[0].trim().replace(/["'\\]/g, '');
      let expectedValue = sides[1].trim().replace(/["'\\]/g, '');
      
      if (expectedValue === 'Yes') expectedValue = '1';
      if (expectedValue === 'No') expectedValue = '0';

      const answerObj = currentAnswers[itemOID];
      let rawAnswer = answerObj;
      if (answerObj && typeof answerObj === 'object') {
        rawAnswer = answerObj.value !== undefined ? answerObj.value : answerObj.Value;
      }

      let answer = '';
      if (rawAnswer === 'Yes' || rawAnswer === true || rawAnswer === 1) {
        answer = '1';
      } else if (rawAnswer === 'No' || rawAnswer === false || rawAnswer === 0) {
        answer = '0';
      } else if (rawAnswer != null) {
        answer = String(rawAnswer);
      }
        
      const isMatch = answer === expectedValue;
      return isNegated ? !isMatch : isMatch;
    }

    let conditionMatches: boolean;

    if (expression.includes('||')) {
      const orParts = expression.split('||').map(part => part.trim());
      conditionMatches = orParts.some(part => checkCondition(part));
    }
    else if (expression.includes('&&')) {
      const andParts = expression.split('&&').map(part => part.trim());
      conditionMatches = andParts.every(part => checkCondition(part));
    }
    else {
      conditionMatches = checkCondition(expression);
    }

    // Return inverted result to match original behavior where true=hide, false=show
    return !conditionMatches;
  } catch (error) {
    return false;
  }
};
