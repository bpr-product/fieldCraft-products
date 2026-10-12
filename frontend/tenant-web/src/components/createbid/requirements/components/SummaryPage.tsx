/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Summary Page Component - Final review page showing all answers
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

import React from 'react';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Typography,
  Button,
  Card,
  CardContent,
  Chip,
  Accordion,
  AccordionSummary,
  AccordionDetails
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import EditIcon from '@mui/icons-material/Edit';
import { ItemDef, CodeList } from '../../../../interface/SdmInterfaces';
import { isHiddenDerived } from '../utils/requirementsUtils';

interface StepData {
  id: string;
  title: string;
  description?: string;
  completed: boolean;
}

interface SummaryPageProps {
  steps: StepData[];
  answersData: { [questionId: string]: any };
  allQuestionsData: { [stepId: string]: any[] };
  itemDef: ItemDef[];
  codeList: CodeList[];
  onEdit: (stepId: string, questionId: string) => void;
  organizeQuestionsHierarchically: (questions: any[], stepId: string) => any[];
  mandatoryQuestions?: { [itemOID: string]: boolean };
}

export const SummaryPage: React.FC<SummaryPageProps> = ({
  steps,
  answersData,
  allQuestionsData,
  itemDef,
  codeList,
  onEdit,
  organizeQuestionsHierarchically,
  mandatoryQuestions = {}
}) => {
  const { t } = useTranslation();
  const formatAnswer = (questionId: string, answer: any, questionDef?: ItemDef) => {
    if (!answer || answer === '') return t('createbid.summary.notAnswered');
    
    // Handle objects with value property
    const actualValue = typeof answer === 'object' && answer.value !== undefined ? answer.value : answer;
    
    if (!questionDef) return String(actualValue);
    
    // Format based on question type
    if (questionDef.DataType === 'singleselect' || questionDef.DataType === 'multiselect') {
      const codeListOID = questionDef.CodeListRef?.CodeListOID;
      if (!codeListOID) return String(actualValue);
      
      const codeListDef = codeList.find(cl => cl.OID === codeListOID);
      if (!codeListDef) return String(actualValue);
      
      // MULTISELECT FIX: Check if answer has CodeList structure (from v2 multiselect)
      if (typeof answer === 'object' && answer.CodeList && answer.CodeList.length > 0) {
        // Extract selected values from CodeList.CodeListItem where IsEnabled = true
        const selectedItems = answer.CodeList[0]?.CodeListItem?.filter((item: any) => item.IsEnabled) || [];
        
        if (selectedItems.length === 0) {
          return t('createbid.summary.notAnswered'); // No items selected
        }
        
        // Decode the selected items
        const displayTexts = selectedItems.map((item: any) => {
          const templateItem = codeListDef.CodeListItem?.find((cli: any) => cli.CodedValue === item.CodedValue);
          return templateItem?.Decode?.TranslatedText?.text || item.CodedValue;
        });
        return displayTexts.join(', ');
      }
      
      if (Array.isArray(actualValue)) {
        // Multi-select (legacy array format)
        const displayTexts = actualValue.map(val => {
          const item = codeListDef.CodeListItem?.find((cli: any) => cli.CodedValue === String(val));
          return item?.Decode?.TranslatedText?.text || val;
        });
        return displayTexts.join(', ');
      } else {
        // Single-select
        const item = codeListDef.CodeListItem?.find((cli: any) => cli.CodedValue === String(actualValue));
        return item?.Decode?.TranslatedText?.text || String(actualValue);
      }
    }
    
    if (questionDef.DataType === 'boolean') {
      return actualValue === '1' || actualValue === 'Yes' || actualValue === true ? t('common.yes') : t('common.no');
    }
    
    return String(actualValue);
  };

  const renderSummaryQuestion = (question: any, stepId: string, level: number = 0) => {
    const isRepeatingInstance = question.isRepeatingInstance;
    
    // Skip rendering repeating instances in summary
    if (isRepeatingInstance) {
      return null;
    }

    // Derived fields the SDM marks IsVisible='No' are never shown to the user by the
    // question cards or the sidebar, so the review list must not surface them either.
    // Any conditional children hanging off one are real user questions, so they are
    // still rendered — at the parent's own level, since the parent row is gone.
    if (isHiddenDerived(question)) {
      if (!question.children || question.children.length === 0) return null;
      return (
        <React.Fragment key={question.id}>
          {question.children.map((child: any) => renderSummaryQuestion(child, stepId, level))}
        </React.Fragment>
      );
    }

    // Check if this question is mandatory
    const itemOID = question.itemOID || question.id;
    const isMandatory = mandatoryQuestions[itemOID] || false;
    
    // Regular question
    const answer = answersData[question.id];
    
    // MULTISELECT FIX: For multiselect, check CodeList instead of value
    // Multiselect has empty value but CodeList with IsEnabled items
    const hasCodeList = typeof answer === 'object' && answer?.CodeList && answer.CodeList.length > 0;
    const hasSelectedItems = hasCodeList && answer.CodeList[0]?.CodeListItem?.some((item: any) => item.IsEnabled);
    
    // Consider answer valid if:
    // 1. Has truthy value (for normal questions), OR  
    // 2. Has CodeList with at least one selected item (for multiselect)
    const hasAnswer = (answer !== undefined && answer !== null && answer !== '') || hasSelectedItems;

    return (
      <Box key={question.id} sx={{ mb: 2, ml: level * 2 }}>
        <Box sx={{ 
          display: 'flex', 
          justifyContent: 'space-between', 
          alignItems: 'flex-start',
          p: 1.5,
          backgroundColor: hasAnswer ? '#e8f0f5' : '#f5f5f5',
          borderRadius: '8px',
          border: hasAnswer ? '1px solid #d1dfe8' : '1px solid #e0e0e0',
          transition: 'all 0.2s',
          '&:hover': {
            backgroundColor: hasAnswer ? '#d9e7f0' : '#eeeeee',
            borderColor: hasAnswer ? 'primary.main' : '#bdbdbd'
          }
        }}>
          <Box sx={{ flex: 1 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
              <Typography variant="body2" sx={{ fontWeight: 600, color: 'primary.main' }}>
                {question.question || question.label || question.itemOID}
                {isMandatory && (
                  <Typography component="span" sx={{ color: '#d32f2f', ml: 0.5, fontWeight: 'bold' }}>
                    *
                  </Typography>
                )}
              </Typography>
            </Box>
            
            {/* Show answers */}
            {hasAnswer ? (
              <Typography variant="body2" sx={{ 
                color: 'primary.main',
                fontSize: '0.875rem',
                fontWeight: 500
              }}>
                {formatAnswer(question.id, answer, itemDef.find(item => item.OID === question.itemOID || item.OID === question.id))}
              </Typography>
            ) : (
              <Typography variant="body2" sx={{ 
                color: '#d32f2f',
                fontSize: '0.875rem',
                fontWeight: 400
              }}>
                 {t('createbid.summary.notAnswered')}
              </Typography>
            )}
          </Box>
          
          <Button
            size="small"
            variant={hasAnswer ? "outlined" : "contained"}
            startIcon={<EditIcon sx={{ fontSize: '14px' }} />}
            onClick={() => onEdit(stepId, question.id)}
            sx={{ 
              minWidth: 'auto',
              textTransform: 'none',
              fontSize: '0.75rem',
              color: hasAnswer ? 'primary.main' : 'white',
              backgroundColor: hasAnswer ? 'transparent' : '#d32f2f',
              borderColor: hasAnswer ? 'primary.main' : '#d32f2f',
              '&:hover': {
                backgroundColor: hasAnswer ? '#e8f0f5' : '#b71c1c',
                borderColor: hasAnswer ? 'primary.main' : '#b71c1c'
              }
            }}
            id={hasAnswer ? `summary-button-edit-${question.id}` : `summary-button-answer-${question.id}`}
          >
            {hasAnswer ? t('common.edit') : t('createbid.summary.answer')}
          </Button>
        </Box>

        {/* Render Children with Enhanced Hierarchical Visualization */}
        {question.children && question.children.length > 0 && (
          <Box sx={{ 
            mt: 1.5, 
            ml: level === 0 ? 3 : 2,  // More indentation for first level children
            borderLeft: `3px solid ${level === 0 ? '#2196f3' : hasAnswer ? 'primary.main' : '#d32f2f'}`, 
            pl: 2,
            borderRadius: '1px',
            position: 'relative',
            '&::before': {
              content: '""',
              position: 'absolute',
              left: -3,
              top: -8,
              width: '15px',
              height: '1px',
              backgroundColor: level === 0 ? '#2196f3' : hasAnswer ? 'primary.main' : '#d32f2f'
            }
          }}>
            {question.children.map((child: any) => 
              renderSummaryQuestion(child, stepId, level + 1)
            )}
          </Box>
        )}
      </Box>
    );
  };

  // Helper function to count questions recursively, excluding multi-instance questions
  const countQuestions = (questions: any[]): { total: number; answered: number } => {
    let total = 0;
    let answered = 0;
    
    questions.forEach(question => {
      if (question.isRepeatingInstance) {
        // Skip repeating instances - don't count them
        return;
      }

      // Hidden derived fields are not rendered, so counting them would make the
      // "answered / total" caption disagree with the rows on screen.
      if (isHiddenDerived(question)) {
        if (question.children && question.children.length > 0) {
          const childCounts = countQuestions(question.children);
          total += childCounts.total;
          answered += childCounts.answered;
        }
        return;
      }

      // Regular question
      total++;
      const answer = answersData[question.id];
      if (answer !== undefined && answer !== null && answer !== '') {
        answered++;
      }
      
      // Count children recursively
      if (question.children && question.children.length > 0) {
        const childCounts = countQuestions(question.children);
        total += childCounts.total;
        answered += childCounts.answered;
      }
    });
    
    return { total, answered };
  };

  // Count only mandatory questions
  const countMandatoryQuestions = (questions: any[]): { total: number; answered: number } => {
    let total = 0;
    let answered = 0;
    
    questions.forEach(question => {
      // A hidden derived field has no row to click, so letting it count as an
      // unanswered requirement would leave the step permanently Incomplete with
      // nothing the user could do about it. Its children are still checked.
      if (isHiddenDerived(question)) {
        if (question.children && question.children.length > 0) {
          const childCounts = countMandatoryQuestions(question.children);
          total += childCounts.total;
          answered += childCounts.answered;
        }
        return;
      }

      if (question.isRepeatingInstance && question.repeatingChild) {
        // For repeating instances, check each question inside
        const itemRefs = question.repeatingChild.itemGroupDef?.ItemRef || [];
        const repeatKey = question.instanceNum;
        
        itemRefs.forEach((itemRef: any) => {
          const isMandatory = mandatoryQuestions[itemRef.ItemOID];
          if (isMandatory) {
            total++;
            const questionKey = `${itemRef.ItemOID}_${repeatKey}`;
            const answer = answersData[questionKey];
            if (answer !== undefined && answer !== null && answer !== '') {
              answered++;
            }
          }
        });
      } else {
        // Regular question
        const itemOID = question.itemOID || question.id;
        const isMandatory = mandatoryQuestions[itemOID];
        
        if (isMandatory) {
          total++;
          const answer = answersData[question.id];
          if (answer !== undefined && answer !== null && answer !== '') {
            answered++;
          }
        }
      }
      
      // Recursively count children
      if (question.children && question.children.length > 0) {
        const childCounts = countMandatoryQuestions(question.children);
        total += childCounts.total;
        answered += childCounts.answered;
      }
    });
    
    return { total, answered };
  };

  // Check if all required (mandatory) questions are answered
  const allCompleted = steps.every((step, index) => {
    if (step.id === 'summary') return true;
    const stepQuestions = allQuestionsData[step.id] || [];
    const hierarchicalQuestions = organizeQuestionsHierarchically(stepQuestions, step.id);
    const counts = countMandatoryQuestions(hierarchicalQuestions);
    // If there are no mandatory questions in this step, consider it complete
    if (counts.total === 0) return true;
    return counts.answered === counts.total;
  });

  return (
    <Box>
      {/* Header */}
      <Card sx={{ mb: 3, backgroundColor: '#e8f0f5', borderLeft: '4px solid primary.main' }}>
        <CardContent>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <CheckCircleIcon sx={{ fontSize: '32px', color: 'primary.main' }} />
            <Box>
              <Typography variant="h6" sx={{ fontWeight: 600, color: 'primary.main' }}>
                {t('createbid.summary.reviewTitle')}
              </Typography>
              <Typography variant="body2" sx={{ color: 'primary.main', opacity: 0.8 }}>
                {t('createbid.summary.reviewSubtitle')}
              </Typography>
            </Box>
          </Box>
        </CardContent>
      </Card>

      {/* Steps Summary */}
      <Box>
        {steps.map((step, stepIndex) => {
          if (step.id === 'summary') return null;
          
          const stepQuestions = allQuestionsData[step.id] || [];
          const hierarchicalQuestions = organizeQuestionsHierarchically(stepQuestions, step.id);
          
          // Count mandatory questions for validation
          const mandatoryCounts = countMandatoryQuestions(hierarchicalQuestions);
          const totalMandatory = mandatoryCounts.total;
          const answeredMandatory = mandatoryCounts.answered;
          
          // Count all questions for display
          const counts = countQuestions(hierarchicalQuestions);
          const totalQuestions = counts.total;
          const answeredQuestions = counts.answered;
          
          // Step is complete if all mandatory questions are answered
          const isComplete = totalMandatory === 0 || answeredMandatory === totalMandatory;
          
          return (
            <Accordion 
              key={step.id}
              defaultExpanded={true}
              sx={{ 
                mb: 2, 
                boxShadow: '0 2px 4px rgba(0,0,0,0.1)',
                border: isComplete ? '1px solid #d1dfe8' : '1px solid #e0e0e0',
                '&:before': { display: 'none' }
              }}
            >
              <AccordionSummary 
                expandIcon={<ExpandMoreIcon />}
                sx={{ 
                  backgroundColor: isComplete ? '#e8f0f5' : '#f5f5f5',
                  '&:hover': {
                    backgroundColor: isComplete ? '#d9e7f0' : '#eeeeee'
                  }
                }}
              >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, width: '100%' }}>
                  <CheckCircleIcon sx={{ 
                    color: isComplete ? 'primary.main' : '#9e9e9e',
                    fontSize: '24px'
                  }} />
                  <Box sx={{ flex: 1 }}>
                    <Typography variant="subtitle1" sx={{ fontWeight: 600, color: 'primary.main' }}>
                      {step.title}
                    </Typography>
                    <Typography variant="caption" sx={{ color: isComplete ? 'primary.main' : '#666', opacity: 0.8 }}>
                      {totalMandatory > 0 ? (
                        <>
                          <Typography component="span" sx={{ color: '#d32f2f', fontWeight: 'bold' }}>*</Typography>
                          {' '}{t('createbid.summary.requiredAnswered', { answered: answeredMandatory, total: totalMandatory })}
                          {'  '}{t('createbid.summary.totalCount', { answered: answeredQuestions, total: totalQuestions })}
                        </>
                      ) : (
                        <>{t('createbid.summary.noneRequired', { answered: answeredQuestions, total: totalQuestions })}</>
                      )}
                    </Typography>
                  </Box>
                  <Chip
                    label={isComplete ? t('createbid.summary.complete') : t('createbid.summary.incomplete')}
                    size="small"
                    sx={{
                      backgroundColor: isComplete ? 'primary.main' : '#757575',
                      color: 'white',
                      fontWeight: 'bold'
                    }}
                  />
                </Box>
              </AccordionSummary>
              
              <AccordionDetails sx={{ p: 2, backgroundColor: '#fafafa' }}>
                {hierarchicalQuestions.length === 0 ? (
                  <Typography variant="body2" sx={{ color: '#999', fontStyle: 'italic' }}>
                    {t('createbid.summary.noQuestions')}
                  </Typography>
                ) : (
                  hierarchicalQuestions.map(question => 
                    renderSummaryQuestion(question, step.id)
                  )
                )}
              </AccordionDetails>
            </Accordion>
          );
        })}
      </Box>
    </Box>
  );
};
