/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Sidebar Section Component - Wrapper for Requirements Sidebar
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

import React from 'react';
import { Box } from '@mui/material';
import { RequirementsSidebar } from './RequirementsSidebar';
import { StepData } from '../types';
import { ItemDef, CodeList } from '../../../../interface/SdmInterfaces';

interface SidebarSectionProps {
  steps: StepData[];
  activeStep: number;
  completed: { [k: number]: boolean };
  answersData: { [questionId: string]: any };
  allQuestionsData: { [stepId: string]: any[] };
  expandedAccordions: { [stepId: string]: boolean };
  setExpandedAccordions: React.Dispatch<React.SetStateAction<{ [stepId: string]: boolean }>>;
  onNavigateToQuestion: (stepId: string, questionId: string) => void;
  /** Optional: clicking a section header navigates to that step (indices match `steps` prop). */
  onNavigateToSection?: (stepIndex: number) => void;
  organizeQuestionsHierarchically: (questions: any[], stepId: string) => any[];
  itemDef: ItemDef[];
  codeList: CodeList[];
  mandatoryQuestions: { [questionId: string]: boolean };
  show: boolean; // Whether to show sidebar (hide on summary step)
}

export const SidebarSection: React.FC<SidebarSectionProps> = ({
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
  mandatoryQuestions,
  show
}) => {
  if (!show) {
    return null;
  }

  return (
    <>
      {/* Right Sidebar - Dynamic Navigation */}
      <Box sx={{ 
        width: '260px',
        padding: '8px',
        maxHeight: 'calc(100vh - 370px)',
        overflowY: 'auto' 
      }}>
        <RequirementsSidebar
          steps={steps}
          activeStep={activeStep}
          completed={completed}
          answersData={answersData}
          allQuestionsData={allQuestionsData}
          expandedAccordions={expandedAccordions}
          setExpandedAccordions={setExpandedAccordions}
          onNavigateToQuestion={onNavigateToQuestion}
          onNavigateToSection={onNavigateToSection}
          organizeQuestionsHierarchically={organizeQuestionsHierarchically}
          itemDef={itemDef}
          codeList={codeList}
          mandatoryQuestions={mandatoryQuestions}
        />
      </Box>
    </>
  );
};
