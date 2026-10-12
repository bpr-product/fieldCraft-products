/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 09-Feb-2026
* @author      : Vaisnave I M
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Image Display on clicking zoom icon in bid 2.0
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------

*/

import React from 'react';
import { Box, Dialog, DialogContent, IconButton, Typography } from '@mui/material';
import './ImageZoomDialog.scss';

interface ImageZoomDialogProps {
    open: boolean;
    onClose: () => void;
    imageSrc: string | null;
    imageAlt: string;
}

const ImageZoomDialog: React.FC<ImageZoomDialogProps> = ({ open, onClose, imageSrc, imageAlt }) => {
    return (
        <Dialog
            open={open}
            onClose={onClose}
            maxWidth="sm"
            PaperProps={{
                className: 'image-zoom-dialog-paper'
            }}
        >
            <DialogContent className="image-zoom-dialog-content">
                <IconButton
                    onClick={onClose}
                    className="image-zoom-dialog-close-btn"
                >
                </IconButton>
                {imageSrc && (
                    <Box className="image-zoom-dialog-image-container">
                        <img
                            src={imageSrc}
                            alt={imageAlt}
                            className="image-zoom-dialog-image"
                        />

                    </Box>
                )}
            </DialogContent>
        </Dialog>
    );
};

export default React.memo(ImageZoomDialog);
