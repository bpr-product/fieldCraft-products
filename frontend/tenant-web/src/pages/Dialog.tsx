/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 
* @author      :
* @since       : 0.0.1
* Requirement# :
* Purpose      : Create Bid Wizard Form
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE    |   AUTHOR  |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 1513          |  25/3/24    |   Nandha  |   Custom dialog usestate problem
*
*/

import React from 'react';
import CancelOutlinedIcon from '@mui/icons-material/CancelOutlined';
import CheckCircleOutlinedIcon from '@mui/icons-material/CheckCircleOutlined';
import { DialogTitle, Typography, DialogContent, DialogContentText, DialogActions, Button, Dialog } from '@mui/material';
import { Box } from '@mui/system';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import { useTranslation } from 'react-i18next';

const CustomDialog = ({ open, onClose, reload, conflictWarning, success, error, Content, errorCode, debugMessage, detailedMessage, timestamp }) => {
    const [show, setShow] = React.useState<any>(false);
    const [isHovered, setIsHovered] = React.useState<any>(false);
    const [copied, setCopied] = React.useState(false);
    const { t, i18n } = useTranslation();
    const handleClose = () => {
        onClose();
    };
    const handleReload = () => {
        reload();
    };
    const handleCopy = () => {
        if (debugMessage) {
            navigator.clipboard.writeText(debugMessage);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        }
    };
    const isoString = "2025-07-10T14:30:23.4499241";
    const date = new Date(isoString);


    const options: Intl.DateTimeFormatOptions = {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        hour12: true,
    };

    const humanReadable = date.toLocaleString('en-US', options);


    return (
        <>
            {success && (
                <Dialog open={open} >
                    <DialogTitle className='success'>
                        <Box paddingLeft={8} paddingRight={8}>
                            <Typography textAlign={'center'}>
                                <CheckCircleOutlinedIcon sx={{ fontSize: "50px", color: "white" }} />
                            </Typography>
                            <Typography textAlign={'center'} color='white' fontSize={'20px'} fontWeight={'400'} >
                                Success
                            </Typography>
                        </Box>
                    </DialogTitle>
                    <Box paddingLeft={8} paddingRight={8}>
                        <DialogContent>
                            <DialogContentText textAlign={'center'} fontSize={'18px'} fontWeight={'500'}>
                                {Content}
                            </DialogContentText>
                        </DialogContent>
                        <DialogActions style={{ justifyContent: "center" }}>
                            <Button id="dialog-success-ok-button" className="p-btn p-btnSuccess" onClick={handleClose}>OK</Button>
                        </DialogActions>
                    </Box>
                </Dialog>
            )}

            {error && (
                <Box sx={{ p: 3 }}>
                    <Dialog
                        open={open}
                        onClose={handleClose}
                        fullWidth
                        maxWidth="sm"
                        PaperProps={{
                            sx: {
                                height: '400px',
                                maxHeight: '400px',
                                display: 'flex',
                                flexDirection: 'column',
                                borderRadius: 3,
                                boxShadow: 3,
                                mx: 'auto',
                            },
                        }}
                    >
                        <DialogTitle
                            sx={{
                                backgroundColor: '#d32f2f',
                                color: 'white',
                                display: 'flex',
                                alignItems: 'center',
                                gap: 2,
                                pl: 4,
                                pr: 4,
                                py: 1,
                                minHeight: 45,
                                borderTopLeftRadius: 12,
                                borderTopRightRadius: 12,
                                fontWeight: 'bold',
                                fontSize: '1.25rem',
                                letterSpacing: 1,
                            }}
                        >
                            <CancelOutlinedIcon sx={{ fontSize: 40 }} />
                            Error
                        </DialogTitle>

                        <DialogContent
                            sx={{
                                flexGrow: 1,
                                overflow: 'hidden',
                                display: 'flex',
                                flexDirection: 'column',
                                alignItems: 'center',
                                justifyContent: 'flex-start',
                                px: 4,
                                py: 2,
                                mt: 4,
                            }}
                        >
                            <Typography
                                marginLeft={"15px"}
                                fontSize="16px"
                                fontWeight="bold"
                                sx={{ color: 'black', mb: 1, width: '100%' }}
                            >
                                {t(errorCode) || Content}
                            </Typography>
                            <Typography
                                marginLeft={"15px"}
                                fontSize="15px"
                                fontWeight="400"
                                sx={{ color: 'black', mb: 1, width: '100%' }}
                            >
                                <strong>Server Message : </strong>{detailedMessage}
                            </Typography>

                            {timestamp && (
                                <DialogContentText
                                    marginLeft={"15px"}
                                    fontSize="11px"
                                    fontWeight="400x"
                                    sx={{ color: 'black', mb: 1, width: '100%' }}
                                >
                                    <Typography>
                                        <strong>Event & Time:</strong> {new Date(timestamp).toLocaleString('en-US', {
                                            year: 'numeric',
                                            month: 'long',
                                            day: 'numeric',
                                            hour: 'numeric',
                                            minute: '2-digit',
                                            hour12: true,
                                        })}
                                    </Typography>

                                </DialogContentText>
                            )}

                            {show && (
                                <Box
                                    sx={{
                                        width: '100%',
                                        maxWidth: 520,
                                        mt: 1,
                                        maxHeight: 150,
                                        minHeight: 90,
                                        overflowY: 'auto',
                                        background: '#f0f0f0',
                                        borderRadius: 1,
                                        p: 2,
                                        fontFamily: 'monospace',
                                        fontSize: '12px',
                                        whiteSpace: 'pre-wrap',
                                        wordBreak: 'break-all',
                                        color: '#333',
                                        textAlign: 'left',
                                    }}
                                >
                                    {debugMessage || Content}
                                </Box>
                            )}
                        </DialogContent>

                        <DialogActions sx={{ justifyContent: 'flex-end', mb: 3, gap: 2, pr: 6 }}>

                            {show && (
                                <Button
                                    variant="outlined"
                                    className="p-btn p-btnPrimary"
                                    onClick={handleCopy}
                                    disabled={!debugMessage}
                                 id="dialog-button-copy">
                                    Copy
                                </Button>
                            )}
                            {(debugMessage || Content) && (
                                <Button
                                    variant="outlined"
                                    className="p-btn p-btnPrimary"
                                    onClick={() => setShow(!show)}
                                 id="dialog-button-button">
                                    {show ? 'Less' : 'Details'}
                                </Button>
                            )}

                            <Button
                                className="p-btn p-btnCancel"
                                onClick={handleClose}
                             id="dialog-button-close">
                                Close
                            </Button>
                        </DialogActions>

                    </Dialog>
                </Box>
            )}


            {conflictWarning && (
                <Dialog open={conflictWarning} >
                    <DialogTitle className='warning'>
                        <Box paddingLeft={8} paddingRight={8}>
                            <Typography textAlign={'center'}>
                                <WarningAmberIcon sx={{ fontSize: "50px", color: "white" }} /></Typography>
                            <Typography textAlign={'center'} color='white' fontSize={'20px'} fontWeight={'400'} >
                                Conflict Detected
                            </Typography>
                        </Box>
                    </DialogTitle>
                    <Box paddingLeft={8} paddingRight={8}>
                        <DialogContent >
                            <DialogContentText textAlign={'center'} fontSize={'18px'} fontWeight={'500'}>{Content}</DialogContentText>
                        </DialogContent>
                        <DialogActions style={{ justifyContent: "center" }}>
                            <Button className="p-btn p-btnCancel" onClick={handleClose} id="dialog-button-cancel">Cancel</Button>
                            <Button className="p-btn p-btnPrimary" onClick={handleReload} id="dialog-button-reload-and-get-latest-changes">Reload and get Latest Changes</Button>
                        </DialogActions>
                    </Box>
                </Dialog>
            )}
        </>
    );
};

export default CustomDialog;