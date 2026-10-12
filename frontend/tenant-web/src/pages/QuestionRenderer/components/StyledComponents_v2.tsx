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

import { TextField, FormControl, styled } from "@mui/material";

export const StyledTextField = styled(TextField)({
  '& .MuiOutlinedInput-root': {
    backgroundColor: '#ffffff',
    borderRadius: '6px',
    '& fieldset': {
      borderColor: '#d1d5db',
      borderWidth: '1px',
    },
    '&:hover fieldset': {
      borderColor: '#9ca3af',
    },
    '&.Mui-focused fieldset': {
      borderColor: '#1a365d',
      borderWidth: '2px',
    },
    '& input': {
      padding: '12px 14px',
      fontSize: '14px',
      color: '#374151',
      '&::placeholder': {
        color: '#9ca3af',
        opacity: 1,
      },
    },
  },
  '& .MuiFormHelperText-root': {
    marginLeft: 0,
    marginTop: '6px',
    fontSize: '12px',
  },
});

export const StyledFormControl = styled(FormControl)({
  '& .MuiFormControlLabel-root': {
    marginRight: '24px',
    '& .MuiFormControlLabel-label': {
      fontSize: '14px',
      color: '#374151',
      fontWeight: 500,
    },
  },
  '& .MuiRadio-root': {
    color: '#9ca3af',
    '&.Mui-checked': {
      color: '#1a365d',
    },
    '& .MuiSvgIcon-root': {
      fontSize: '20px',
    },
  },
  '& .MuiFormHelperText-root': {
    marginLeft: 0,
    marginTop: '6px',
    fontSize: '12px',
  },
});

export const UnitTypography = {
  color: '#6b7280',
  fontSize: '14px',
  minWidth: 'fit-content',
  marginLeft: '8px'
};