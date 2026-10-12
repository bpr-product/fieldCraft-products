/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 2-Nov-2024
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Requirements Sidebar Component - Navigation and question overview
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

import React from 'react';
import {
  Box,
  Typography,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Chip
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import CheckIcon from '@mui/icons-material/Check';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked';
import { ItemDef, CodeList } from '../../../../interface/SdmInterfaces';
import { isHiddenDerived } from '../utils/requirementsUtils';

interface StepData {
  id: string;
  title: string;
  description?: string;
  completed: boolean;
  itemGroupDef?: any;
  repeatingChildren?: any[];
}

interface RequirementsSidebarProps {
  steps: StepData[];
  activeStep: number;
  completed: { [k: number]: boolean };
  answersData: { [questionId: string]: any };
  allQuestionsData: { [stepId: string]: any[] };
  expandedAccordions: { [stepId: string]: boolean };
  setExpandedAccordions: React.Dispatch<React.SetStateAction<{ [stepId: string]: boolean }>>;
  onNavigateToQuestion: (stepId: string, questionId: string) => void;
  onNavigateToSection?: (stepIndex: number) => void;
  organizeQuestionsHierarchically: (questions: any[], stepId: string) => any[];
  itemDef: ItemDef[];
  codeList: CodeList[];
  mandatoryQuestions?: { [itemOID: string]: boolean };
}

export const RequirementsSidebar: React.FC<RequirementsSidebarProps> = ({
  steps,
  activeStep,
  completed,
  answersData,
  allQuestionsData,
  expandedAccordions,
  setExpandedAccordions,
  onNavigateToQuestion,
  onNavigateToSection,
  organizeQuestionsHierarchically,
  itemDef,
  codeList,
  mandatoryQuestions = {}
}) => {
  if (steps.length === 0) {
    return (
      <Typography variant="body2" color="textSecondary" sx={{ textAlign: 'center', mt: 4 }}>
        No questions available
      </Typography>
    );
  }
  // Helper function to format answer for display
  const getAnswerDisplayText = (questionId: string, question: any): string => {
    const answer = answersData[questionId];
    if (!answer) return '';

    // Handle object with value property
    const actualValue = typeof answer === 'object' && answer.value !== undefined ? answer.value : answer;

    const itemOID = question.itemOID || question.id;
    const questionDef = itemDef.find(item => item.OID === itemOID);

    if (!questionDef) return String(actualValue);

    // If it's a select question, try to decode the value
    if (questionDef.DataType === 'singleselect' || questionDef.DataType === 'multiselect') {
      const codeListOID = questionDef.CodeListRef?.CodeListOID;
      if (!codeListOID) return String(actualValue);

      const codeListDef = codeList.find(cl => cl.OID === codeListOID);
      if (!codeListDef) return String(actualValue);

      if (typeof answer === 'object' && answer.CodeList && answer.CodeList.length > 0) {
        // Extract selected values from CodeList.CodeListItem where IsEnabled = true
        const selectedItems = answer.CodeList[0]?.CodeListItem?.filter((item: any) => item.IsEnabled) || [];

        if (selectedItems.length === 0) {
          return ''; // No items selected
        }

        // Decode the selected items
        const displayTexts = selectedItems.map((item: any) => {
          const templateItem = codeListDef.CodeListItem?.find((cli: any) => cli.CodedValue === item.CodedValue);
          return templateItem?.Decode?.TranslatedText?.text || item.CodedValue;
        });
        return displayTexts.join(', ');
      }

      // Return empty string if value is empty/null/undefined
      if (actualValue === '' || actualValue === null || actualValue === undefined) {
        return '';
      }

      if (Array.isArray(actualValue)) {
        // Multi-select: decode each value (legacy array format)
        const displayTexts = actualValue.map(val => {
          const item = codeListDef.CodeListItem?.find((cli: any) => cli.CodedValue === String(val));
          return item?.Decode?.TranslatedText?.text || val;
        });
        return displayTexts.join(', ');
      } else {
        // Single-select: decode the value
        const item = codeListDef.CodeListItem?.find((cli: any) => cli.CodedValue === String(actualValue));
        return item?.Decode?.TranslatedText?.text || String(actualValue);
      }
    }

    // Return empty string if value is empty/null/undefined (moved after select handling)
    if (actualValue === '' || actualValue === null || actualValue === undefined) {
      return '';
    }

    // For boolean values
    if (questionDef.DataType === 'boolean') {
      return actualValue === '1' || actualValue === 'Yes' || actualValue === true ? 'Yes' : 'No';
    }

    return String(actualValue);
  };

  const renderQuestionItem = (question: any, stepId: string, level: number = 0) => {
    if (question.isRepeatingInstance) return null;
    if (isHiddenDerived(question)) return null;

    // ✅ FIX: Detect empty question text (option-only items like I.13)
    const rawQuestionText = question.itemDef?.Question?.TranslatedText?.text ?? '';
    const isQuestionTextEmpty = rawQuestionText.trim() === '';

    let hasAnswer = false;
    let answerText = '';

    const answer = answersData[question.id];
    if (answer) {
      const actualValue = typeof answer === 'object' && answer.value !== undefined
        ? answer.value : answer;
      const hasCodeList = typeof answer === 'object' && answer.CodeList &&
        answer.CodeList.length > 0;
      const hasSelectedItems = hasCodeList &&
        answer.CodeList[0]?.CodeListItem?.some((item: any) => item.IsEnabled);
      hasAnswer = (actualValue !== '' && actualValue !== null &&
        actualValue !== undefined) || hasSelectedItems;
    }
    answerText = getAnswerDisplayText(question.id, question);

    // ✅ FIX: If question text is empty AND no answer yet, render nothing at all
    if (isQuestionTextEmpty && !hasAnswer) {
      return null;
    }

    // ✅ FIX: If question text is empty AND has answer, show ONLY the answer text
    // as the label — no fallback to itemOID or "Question I.xx"
    const displayLabel = isQuestionTextEmpty
      ? answerText  // only the selected option text, e.g. "Walls"
      : (rawQuestionText || question.label || question.itemOID);

    return (
      <Box key={question.id} sx={{ ml: level * 2 }}>
        <Box
          onClick={() => onNavigateToQuestion(stepId, question.id)}
          sx={{
            display: 'flex',
            flexDirection: 'column',
            gap: 0.5,
            py: 0.75,
            px: 1,
            cursor: 'pointer',
            borderRadius: '6px',
            transition: 'all 0.2s',
            backgroundColor: hasAnswer ? '#f0f7ff' : 'transparent',
            border: hasAnswer ? '1px solid #e3f2fd' : '1px solid transparent',
            '&:hover': {
              backgroundColor: hasAnswer ? '#e8f4ff' : '#f5f5f5',
              borderColor: '#e0e0e0'
            }
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Box sx={{ flexShrink: 0 }}>
              {hasAnswer ? (
                <CheckCircleIcon sx={{ fontSize: '16px', color: '#4caf50' }} />
              ) : (
                <RadioButtonUncheckedIcon sx={{ fontSize: '16px', color: '#ccc' }} />
              )}
            </Box>

            <Typography variant="body2" sx={{
              fontSize: '0.813rem',
              color: hasAnswer ? '#1a1a1a' : '#666',
              fontWeight: hasAnswer ? 500 : 400,
              flex: 1,
              lineHeight: 1.3
            }}>
              {/* ✅ FIX: for empty-question items, displayLabel IS the answer text,
                so don't render a second answer block below */}
              {isQuestionTextEmpty ? answerText : displayLabel}
              {/* mandatory asterisk — only for non-empty question items */}
              {!isQuestionTextEmpty && (() => {
                const itemOID = question.itemOID || question.id;
                const isMandatory = mandatoryQuestions[itemOID] || false;
                return isMandatory ? (
                  <Typography component="span" sx={{ color: '#d32f2f', ml: 0.5, fontWeight: 'bold' }}>
                    *
                  </Typography>
                ) : null;
              })()}
            </Typography>
          </Box>

          {/* ✅ FIX: Only show the answer sub-row for normal (non-empty) questions.
            For empty-question items the answerText is already the label above. */}
          {!isQuestionTextEmpty && hasAnswer && answerText && (
            <Box sx={{ ml: 3, pl: 1, borderLeft: '2px solid primary.main' }}>
              <Typography variant="caption" sx={{
                fontSize: '0.75rem',
                color: 'primary.main',
                fontWeight: 500
              }}>
                {answerText}
              </Typography>
            </Box>
          )}
        </Box>

        {question.children && question.children.length > 0 && (
          <Box sx={{
            ml: level === 0 ? 2 : 1.5,
            borderLeft: '2px solid #e0e0e0',
            pl: 1.5,
            mt: 0.5,
            borderRadius: '1px'
          }}>
            {question.children.map((child: any) =>
              renderQuestionItem(child, stepId, level + 1)
            )}
          </Box>
        )}
      </Box>
    );
  };
  const countQuestions = (questions: any[]): { total: number; answered: number } => {
    let total = 0;
    let answered = 0;

    questions.forEach(question => {
      if (question.isRepeatingInstance) {
        return;
      }

      // HIDE DERIVED FIELDS: Skip questions with MethodOID only when explicitly marked as not visible
      if (isHiddenDerived(question)) {
        // Don't count derived fields, but still process their children
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
        // answer is always an object {value, Value, CodeList,...} — check actual value
        const actualValue = typeof answer === 'object' && answer.value !== undefined ? answer.value : answer;
        const hasCodeList = typeof answer === 'object' && answer.CodeList !== null && answer.CodeList !== undefined && answer.CodeList.length > 0;
        const hasSelectedItems = hasCodeList && answer.CodeList[0]?.CodeListItem?.some((item: any) => item.IsEnabled);
        if ((actualValue !== '' && actualValue !== null && actualValue !== undefined) || hasSelectedItems) {
          answered++;
        }
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

  return (
    <Box>
      {steps.map((step, stepIndex) => {
        if (step.id === 'summary') return null;

        const stepQuestions = allQuestionsData[step.id] || [];
        // `|| []` for the same reason as the line above: a shaping pass that returns
        // nothing must render an empty section, not crash countQuestions on null.
        const hierarchicalQuestions = organizeQuestionsHierarchically(stepQuestions, step.id) || [];


        const counts = countQuestions(hierarchicalQuestions);
        const totalQuestions = counts.total;
        const answeredQuestions = counts.answered;

        return (
          <Accordion
            key={step.id}
            expanded={expandedAccordions[step.id] !== undefined ? expandedAccordions[step.id] : stepIndex === activeStep}
            onChange={(event, isExpanded) => {
              setExpandedAccordions(prev => ({
                ...prev,
                [step.id]: isExpanded
              }));
            }}
            sx={{
              mb: 1,
              boxShadow: 'none',
              border: '1px solid #e0e0e0',
              '&:before': { display: 'none' }
            }}
          >
            <AccordionSummary
              expandIcon={<ExpandMoreIcon />}
              onClick={() => onNavigateToSection?.(stepIndex)}
              sx={{
                backgroundColor: stepIndex === activeStep ? '#e8f0f8' : '#fafafa',
                minHeight: '48px',
                '& .MuiAccordionSummary-content': { margin: '12px 0' },
                border: stepIndex === activeStep ? '1px solid #1a365d' : 'none',
                borderRadius: '8px 8px 0 0',
                '&:hover': {
                  backgroundColor: stepIndex === activeStep ? '#e8f0f8' : '#f0f0f0'
                }
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', width: '100%', gap: 2 }}>
                {/* Step Status Icon */}
                <Box sx={{ flexShrink: 0 }}>
                  {completed[stepIndex] ? (
                    <Box sx={{
                      width: 24,
                      height: 24,
                      borderRadius: '50%',
                      backgroundColor: '#1a365d',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      boxShadow: '0 2px 4px rgba(26, 54, 93, 0.3)'
                    }}>
                      <CheckIcon sx={{ fontSize: '14px', color: 'white', fontWeight: 'bold' }} />
                    </Box>
                  ) : (
                    <Box sx={{
                      width: 24,
                      height: 24,
                      borderRadius: '50%',
                      border: stepIndex === activeStep ? '2px solid #1a365d' : '2px solid #ccc',
                      backgroundColor: stepIndex === activeStep ? '#e8f0f8' : 'transparent',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center'
                    }}>
                      <Typography variant="caption" sx={{
                        fontWeight: 'bold',
                        fontSize: '0.7rem',
                        color: stepIndex === activeStep ? '#1a365d' : '#999'
                      }}>
                        {stepIndex + 1}
                      </Typography>
                    </Box>
                  )}
                </Box>

                {/* Step Title and Progress */}
                <Box sx={{ flex: 1 }}>
                  <Typography variant="body2" sx={{
                    fontWeight: 600,
                    fontSize: '0.875rem',
                    color: stepIndex === activeStep ? '#1a365d' : '#1a1a1a',
                    mb: 0.25
                  }}>
                    {step.title.toUpperCase()}
                  </Typography>

                  <Typography variant="caption" sx={{
                    color: stepIndex === activeStep ? '#1a365d' : '#666',
                    fontSize: '0.75rem'
                  }}>
                    {answeredQuestions} of {totalQuestions} completed
                  </Typography>
                </Box>

                {/* Progress Badge */}
                <Box sx={{ flexShrink: 0 }}>
                  <Chip
                    label={`${Math.round((answeredQuestions / totalQuestions) * 100)}%`}
                    size="small"
                    sx={{
                      height: '22px',
                      fontSize: '0.7rem',
                      fontWeight: 'bold',
                      backgroundColor: stepIndex === activeStep ? '#1a365d' : (completed[stepIndex] ? '#1a365d' : '#f5f5f5'),
                      color: stepIndex === activeStep || completed[stepIndex] ? 'white' : '#666'
                    }}
                  />
                </Box>
              </Box>
            </AccordionSummary>

            <AccordionDetails sx={{ p: 1, backgroundColor: '#fafafa' }}>
              <Box>
                {hierarchicalQuestions.length === 0 ? (
                  <Typography variant="caption" sx={{ color: '#999', fontStyle: 'italic' }}>
                    No questions in this section
                  </Typography>
                ) : (
                  hierarchicalQuestions.map(question => renderQuestionItem(question, step.id))
                )}
              </Box>
            </AccordionDetails>
          </Accordion>
        );
      })}
    </Box>
  );
};
