/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 2-Nov-2024
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Stepper Header Component for Requirements v2.0
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

import React from 'react';
import { Box } from '@mui/material';
import CustomStepper from '../../../common/CustomStepper';
import { StepData } from '../types';

interface RequirementsStepperHeaderProps {
  steps: StepData[];
  activeStep: number;
  completed: { [k: number]: boolean };
  onNext: () => void;
  onBack: () => void;
  onStepClick: (stepIndex: number) => void;
  onComplete: () => void;
  canProceed: boolean;
  allStepsCompleted: boolean;
  savingDraft?: boolean;
  savingText?: string;
}

export const RequirementsStepperHeader: React.FC<RequirementsStepperHeaderProps> = ({
  steps,
  activeStep,
  completed,
  onNext,
  onBack,
  onStepClick,
  onComplete,
  canProceed,
  allStepsCompleted,
  savingDraft = false,
  savingText = 'Saving Draft...'
}) => {
  const isLastStep = activeStep === steps.length - 1;

  // Merge completed status into steps array
  const stepsWithCompletion = steps.map((step, index) => ({
    ...step,
    completed: completed[index] || false
  }));

  return (
    <>
      {/* Top Stepper Section */}
      <Box sx={{ marginBottom: 0 }}>
        <CustomStepper
          steps={stepsWithCompletion}
          activeStep={activeStep}
          onNext={isLastStep ? onComplete : onNext}
          onBack={onBack}
          onStepClick={onStepClick}
          nextButtonText={isLastStep ? "Complete" : "Next"}
          nextButtonDisabled={!canProceed}
          showNavigation={false}
          savingDraft={savingDraft}
          savingText={savingText}
          compact={true}
        />
      </Box>

      {/* Horizontal HR Divider */}
      <Box sx={{ 
        width: '100%', 
        height: '1px', 
        backgroundColor: '#e0e0e0', 
        margin: '0px 0px 8px 0px' 
      }} />
    </>
  );
};
