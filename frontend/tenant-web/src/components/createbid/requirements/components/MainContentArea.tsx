/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Main Content Area Component - Primary content rendering area for Requirements
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Card, CardContent, Typography, Button } from '@mui/material';
import QuestionRendererCard from '../../../../pages/QuestionRenderer/components/QuestionRendererCard_v2';
import { SummaryPage } from './SummaryPage';
import { StepData } from '../types';
import { ItemDef, CodeList, ItemGroupData } from '../../../../interface/SdmInterfaces';

interface MainContentAreaProps {
  // Step and state
  steps: StepData[];
  activeStep: number;
  completed: { [k: number]: boolean };
  loading: boolean;
  allStepsCompleted: boolean;

  // Data
  answersData: { [questionId: string]: any };
  allQuestionsData: { [stepId: string]: any[] };
  itemGroupData: ItemGroupData[];
  itemDef: ItemDef[];
  codeList: CodeList[];
  mandatoryQuestions: { [questionId: string]: boolean };
  template: any;
  measureUnit: any;
  mappings: any[];
  formDef: any[];
  conditionDef: any[];
  methodDef: any[];
  tradeData: any[];
  currentQuestionIndex: { [stepId: string]: number };
  /** Sidebar "jump to question" target, resolved by the card against its own list. */
  navigateToMainOID?: { stepId: string; oid: string; timestamp: number } | null;
  imageBaseUrl?: string;

  // READONLY flag (when true, inner questions should be non-editable)
  readonly?: boolean;

  // Handlers
  handleItemGroupDataChange: (data: any) => void;
  handleShowFromCard: (item: any) => void;
  handleShowRepeat: (data: any) => void;
  handleShowGroupQus: (data: any) => void;
  handleQuestionChange: (questionIndex: number) => void;
  handleQuestionComplete: (questionData: any) => void;
  handleBack: () => void;
  handleNext: () => void;
  handleComplete: () => void;
  handleReset: () => void;
  navigateToQuestionWithFeedback: (stepId: string, questionId: string) => void;

  // Functions
  organizeQuestionsHierarchically: (questions: any[], stepId: string) => any[];
  getFilteredItemGroupData: (itemGroupData: ItemGroupData[], repeatingChildren: any[]) => ItemGroupData[];
  getFilteredRepeatingChildren: (repeatingChildren: any[], itemGroupData: ItemGroupData[]) => any[];
  getItemsWithValidationErrors: () => ItemDef[];
  getCurrentQuestionIndex: (stepId: string) => number;
  getRepeatingInstanceCount: (controlItemOID: string) => number;
  updateRepeatingInstanceCount: (controlItemOID: string, newCount: number, repeatingGroupOID: string, parentSuffix?: string) => void;
  getMethodDefRepeatCount: (methodOIDOrQuestionId: string) => number;
  multiInstanceParser: any; // NEW: Pass parser from parent
  dependencyParser: any; // TASK 0: Pass dependency parser from parent
  canProceed: () => boolean;
  // FIX FOR PROBLEM A: Flag to defer conditional rendering until answersData is stable
  isAnswersDataStable?: boolean;
  // Loading state for draft save
  savingDraft?: boolean;
  summarizedGroupsRef?: React.MutableRefObject<{ [groupOID: string]: boolean }>;
  suppressDeletionConfirmRef?: React.MutableRefObject<boolean>;
  onSummarizeGroupClear?: (controlItemOID: string, repeatingGroupOID: string, groupItemOIDs: string[]) => void;
}

export const MainContentArea: React.FC<MainContentAreaProps> = React.memo(({
  steps,
  activeStep,
  completed,
  loading,
  allStepsCompleted,
  answersData,
  allQuestionsData,
  itemGroupData,
  itemDef,
  codeList,
  mandatoryQuestions,
  template,
  measureUnit,
  mappings,
  formDef,
  conditionDef,
  methodDef,
  tradeData,
  currentQuestionIndex,
  navigateToMainOID,
  handleItemGroupDataChange,
  handleShowFromCard,
  handleShowRepeat,
  handleShowGroupQus,
  handleQuestionChange,
  handleQuestionComplete,
  handleBack,
  handleNext,
  handleComplete,
  handleReset,
  navigateToQuestionWithFeedback,
  organizeQuestionsHierarchically,
  getFilteredItemGroupData,
  getFilteredRepeatingChildren,
  getItemsWithValidationErrors,
  getCurrentQuestionIndex,
  getRepeatingInstanceCount,
  updateRepeatingInstanceCount,
  getMethodDefRepeatCount,
  multiInstanceParser,
  dependencyParser,
  canProceed,
  readonly = false,
  savingDraft = false,
  isAnswersDataStable = true,
  imageBaseUrl = '',
  summarizedGroupsRef,
  suppressDeletionConfirmRef,
  onSummarizeGroupClear
}) => {
  const { t } = useTranslation();
  const isLastStep = activeStep === steps.length - 1;
  const currentStep = steps[activeStep];

  return (
    <Box sx={{
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      minHeight: 0,
      width: isLastStep ? '100%' : 'auto',
      overflow: 'hidden' // LAYOUT FIX: Prevent outer scrollbar
    }}>
      {allStepsCompleted ? (
        <Card sx={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          <CardContent sx={{
            textAlign: 'center',
            padding: '20px',
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center'
          }}>
            <Typography variant="h5" gutterBottom color="primary">
              {t('createbid.requirements.mainContent.allCompleted')}
            </Typography>
            <Typography variant="body1" color="textSecondary" sx={{ marginBottom: '16px' }}>
              {t('createbid.requirements.mainContent.allCompletedSub')}
            </Typography>
            <Button
              onClick={handleReset}
              variant="outlined"
              className="p-btn p-btnPrimary"
              sx={{
                marginRight: '12px',
                textTransform: 'none'
              }}
             id="main-content-area-button-review-steps">
              {t('createbid.requirements.mainContent.reviewSteps')}
            </Button>
            <Button
              className="p-btn p-btnPrimary"
             id="main-content-area-button-continue-to-finalize">
              {t('createbid.requirements.mainContent.continueToFinalize')}
            </Button>
          </CardContent>
        </Card>
      ) : (
        <Box sx={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          margin: '0px 0px 0px 5px',
          minHeight: 0,
          overflow: 'hidden',
          height: '100%' // Ensure parent fills available space
        }}>
          {isLastStep ? (
            // Summary Step Content - Dynamic height based on parent container
            <Box sx={{
              height: 'calc(100vh - 370px)',
              overflowY: 'auto', // Enable vertical scrolling
              overflowX: 'hidden', // Prevent horizontal scroll
              p: 2, // Consistent padding
              backgroundColor: '#f8f9fa',
              // Custom scrollbar styling for better visibility
              '&::-webkit-scrollbar': {
                width: '8px',
              },
              '&::-webkit-scrollbar-track': {
                background: '#f1f1f1',
                borderRadius: '4px',
              },
              '&::-webkit-scrollbar-thumb': {
                background: '#888',
                borderRadius: '4px',
              },
              '&::-webkit-scrollbar-thumb:hover': {
                background: '#555',
              }
            }}>
              <SummaryPage
                steps={steps}
                answersData={answersData}
                allQuestionsData={allQuestionsData}
                itemDef={itemDef}
                codeList={codeList}
                mandatoryQuestions={mandatoryQuestions}
                onEdit={(stepId, questionId) => {
                  navigateToQuestionWithFeedback(stepId, questionId);
                }}
                organizeQuestionsHierarchically={organizeQuestionsHierarchically}
              />
            </Box>
          ) : (
            // Regular Step Content
            <>
              {/* Render QuestionRendererCard - Only when data is fully loaded */}
              {!loading && currentStep?.itemGroupDef && template && currentStep && itemDef.length > 0 && (
                <QuestionRendererCard
                  OID={currentStep.itemGroupDef.OID}
                  measureDef={measureUnit}
                  itemDefs={itemDef}
                  itemGroupDefs={[currentStep.itemGroupDef]}
                  mappings={mappings}
                  formDefs={formDef}
                  codeListDefs={codeList}
                  conditionDefs={conditionDef}
                  methodDefs={methodDef}
                  OnItemGrpDataChange={handleItemGroupDataChange}
                  show={handleShowFromCard}
                  showRepeat={handleShowRepeat}
                  showGroupQus={handleShowGroupQus}
                  itemGroupDatas={itemGroupData}
                  validate={getItemsWithValidationErrors()}
                  tradeData={tradeData}
                  currentQuestionIndex={currentQuestionIndex[currentStep?.id || ''] || 0}
                  navigateToMainOID={navigateToMainOID}
                  onQuestionChange={handleQuestionChange}
                  onQuestionComplete={handleQuestionComplete}
                  repeatingChildren={currentStep?.repeatingChildren || []}
                  stepId={currentStep?.id || ''}
                  allQuestionsData={allQuestionsData}
                  getCurrentQuestionIndex={getCurrentQuestionIndex}
                  getRepeatingInstanceCount={getRepeatingInstanceCount}
                  updateRepeatingInstanceCount={updateRepeatingInstanceCount}
                  getMethodDefRepeatCount={getMethodDefRepeatCount}
                  multiInstanceParser={multiInstanceParser}
                  dependencyParser={dependencyParser}
                  activeStep={activeStep}
                  totalSteps={steps.length}
                  handleBack={handleBack}
                  handleNext={handleNext}
                  handleComplete={handleComplete}
                  canProceed={canProceed}
                  parentAnswersData={answersData}
                  isAnswersDataStable={isAnswersDataStable}
                  savingDraft={savingDraft}
                  readonly={readonly}
                  imageBaseUrl={imageBaseUrl}
                  summarizedGroupsRef={summarizedGroupsRef}
                  suppressDeletionConfirmRef={suppressDeletionConfirmRef}
                  onSummarizeGroupClear={onSummarizeGroupClear}
                />
              )}

              {/* Loading state when template data is not ready */}
              {!loading && (!currentStep?.itemGroupDef || !template || !currentStep || itemDef.length === 0) && (
                <Box sx={{
                  display: 'flex',
                  justifyContent: 'center',
                  alignItems: 'center',
                  height: '400px',
                  flexDirection: 'column',
                  p: 3
                }}>
                  <Typography variant="h6" color="text.secondary" sx={{ mb: 2 }}>
                    {t('createbid.requirements.mainContent.preparingQuestions')}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {t('createbid.requirements.mainContent.preparingQuestionsSub')}
                  </Typography>
                </Box>
              )}

              {/* Placeholder for default steps */}
              {!loading && !currentStep?.itemGroupDef && !template && currentStep && (
                <Box sx={{
                  display: 'flex',
                  justifyContent: 'center',
                  alignItems: 'center',
                  height: '400px',
                  flexDirection: 'column',
                  p: 3,
                  backgroundColor: '#f8f9fa',
                  borderRadius: '8px',
                  margin: 2
                }}>
                  <Typography variant="body1" color="textSecondary">
                    {t('createbid.requirements.mainContent.stepPlaceholder', { title: currentStep.title.toLowerCase() })}
                  </Typography>
                  <Typography variant="body2" color="textSecondary" sx={{ marginTop: '12px' }}>
                    {t('createbid.requirements.mainContent.provideTradeType')}
                  </Typography>

                  <Box sx={{ marginTop: '20px' }}>
                    <Button
                      onClick={handleComplete}
                      className="p-btn p-btnPrimary"
                     id="main-content-area-button-mark-as-complete">
                      {t('createbid.requirements.mainContent.markComplete')}
                    </Button>
                  </Box>
                </Box>
              )}

              {/* Loading state */}
              {!currentStep && (
                <Box sx={{
                  display: 'flex',
                  justifyContent: 'center',
                  alignItems: 'center',
                  height: '400px',
                  flexDirection: 'column',
                  p: 3
                }}>
                  <Typography variant="body1" color="textSecondary">
                    {t('createbid.requirements.mainContent.loadingQuestions')}
                  </Typography>
                </Box>
              )}
            </>
          )}
        </Box>
      )}
    </Box>
  );
});
