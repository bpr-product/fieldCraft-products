/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Deletion Confirm Dialog Component - Confirmation for deleting repeating instances
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

/**
 * DeletionConfirmDialog Component
 * 
 * Handles confirmation dialog for deleting repeating group instances
 */

import React from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography
} from '@mui/material';

interface DeletionConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  deletionInfo: {
    show: boolean;
    controlItemOID?: string;
    newCount?: number;
    oldCount?: number;
    repeatingGroupOID?: string;
    instancesToDelete?: number[];
  };
}

export const DeletionConfirmDialog: React.FC<DeletionConfirmDialogProps> = ({
  open,
  onClose,
  onConfirm,
  deletionInfo
}) => {
  if (!deletionInfo.show) return null;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
    >
      <DialogTitle>
        Confirm Deletion
      </DialogTitle>
      <DialogContent>
        <Typography variant="body1" sx={{ mb: 2 }}>
          This will permanently delete {deletionInfo.instancesToDelete?.length || 0} {deletionInfo.instancesToDelete?.length === 1 ? 'item' : 'items'} and all associated data.
        </Typography>
        <Typography variant="body2" color="error" sx={{ mt: 2, fontWeight: 500 }}>
          This action cannot be undone. Are you sure you want to continue?
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button
          onClick={onClose}
          className="p-btn p-btnCancel"
         id="deletion-confirm-dialog-button-cancel">
          Cancel
        </Button>
        <Button
          onClick={onConfirm}
          className="p-btn p-btnError"
         id="deletion-confirm-dialog-button-delete">
          Delete
        </Button>
      </DialogActions>
    </Dialog>
  );
};
