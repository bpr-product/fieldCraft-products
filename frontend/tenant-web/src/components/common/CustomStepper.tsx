/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 2-Nov-2024
* @author      : Vaisnave I M
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Custom Stepper Component for multi-step forms
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

/**

 */
import React from 'react';
import { Typography, Button, Paper, CircularProgress } from '@mui/material';
import './CustomStepper.scss';

interface StepData {
  id: string;
  title: string;
  description?: string;
  completed?: boolean;
}

interface CustomStepperProps {
  steps: StepData[];
  activeStep: number;
  onNext?: () => void;
  onBack?: () => void;
  onStepClick?: (stepIndex: number) => void;
  showNavigation?: boolean;
  showProgress?: boolean;
  nextButtonText?: string;
  backButtonText?: string;
  nextButtonDisabled?: boolean;
  backButtonDisabled?: boolean;
  savingDraft?: boolean;
  savingText?: string;
  children?: React.ReactNode;
  compact?: boolean;
}

const CustomStepper: React.FC<CustomStepperProps> = ({
  steps,
  activeStep,
  onNext,
  onBack,
  onStepClick,
  showNavigation = true,
  showProgress = true,
  nextButtonText = "Next",
  backButtonText = "Back",
  nextButtonDisabled = false,
  backButtonDisabled = false,
  savingDraft = false,
  savingText = 'Saving Draft...',
  children,
  compact = false
}) => {
  const handleStepClick = (stepIndex: number) => {
    if (onStepClick) {
      onStepClick(stepIndex);
    }
  };

  return (
        <div className={`custom-stepper ${compact ? 'custom-stepper--compact' : ''}`}>
      {/* Stepper Implementation */}
      <div className="custom-stepper__steps">
        <div className="custom-stepper__step-row">
          {steps.map((step, index) => {
            const stepClass = [
              'custom-stepper__step',
              !onStepClick ? 'custom-stepper__step--no-click' : ''
            ].join(' ');
            const circleClass = [
              'custom-stepper__circle',
              step.completed ? 'custom-stepper__circle--completed' : '',
              index === activeStep ? 'custom-stepper__circle--active' : ''
            ].join(' ');
            const lineClass = [
              'custom-stepper__line',
              index < activeStep ? 'custom-stepper__line--active' : ''
            ].join(' ');
            const titleClass = [
              'custom-stepper__title',
              index === activeStep ? 'custom-stepper__title--active' : ''
            ].join(' ');
            const statusClass = [
              'custom-stepper__status',
              step.completed ? 'custom-stepper__status--completed' : '',
              index === activeStep ? 'custom-stepper__status--active' : ''
            ].join(' ');
            return (
              <div
                key={step.id}
                className={stepClass}
                onClick={() => handleStepClick(index)}
              >
                {/* Connecting Line */}
                {index < steps.length - 1 && <div className={lineClass} />}
                {/* Step Circle */}
                <div className={circleClass}>
                  {step.completed ? '✓' : index + 1}
                </div>
                {/* Text Container */}
                <div className="custom-stepper__text-container">
                  {/* Item Group Name with Number - Fixed height container */}
                  <div className="custom-stepper__title-container">
                    <Typography variant="body2" className={titleClass}>
                      {index + 1}. {step.title}
                    </Typography>
                  </div>
                  {/* Status Text - Fixed height container for perfect alignment */}
                  <div className="custom-stepper__status-container">
                    <Typography variant="caption" className={statusClass}>
                      {step.completed ? 'Completed' : index === activeStep ? 'In Progress' : 'Pending'}
                    </Typography>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
      {/* Step Content */}
      {children && (
        <Paper elevation={1} className="custom-stepper__content">
          {children}
        </Paper>
      )}
      {/* Navigation Buttons */}
      {showNavigation && (
        <div className="custom-stepper__nav">
          <Button
            disabled={activeStep === 0 || backButtonDisabled}
            onClick={onBack}
            variant="outlined"
            size="small"
            className="p-btn p-btnPrimary"
           id="custom-stepper-button-button">
            {backButtonText}
          </Button>
          {/* Progress indicator */}
          {showProgress && (
            <div className="custom-stepper__progress">
              <Typography variant="body2" className="custom-stepper__progress-text">
                Step {activeStep + 1} of {steps.length}
              </Typography>
              <div className="custom-stepper__progress-bar">
                <div
                  className="custom-stepper__progress-bar-inner"
                  style={{ width: `${((activeStep + 1) / steps.length) * 100}%` }}
                />
              </div>
              <Typography variant="body2" className="custom-stepper__progress-percent">
                {Math.round(((activeStep + 1) / steps.length) * 100)}%
              </Typography>
            </div>
          )}
          <Button
            size="small"
            onClick={onNext}
            disabled={nextButtonDisabled || savingDraft}
            startIcon={savingDraft ? <CircularProgress size={20} /> : null}
            className="p-btn p-btnPrimary"
           id="custom-stepper-button-button-2">
            {savingDraft ? savingText : nextButtonText}
          </Button>
        </div>
      )}
    </div>
  );
};

export default CustomStepper;