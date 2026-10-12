/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 9-Feb-2026
* @author      : Vaisnave I M
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Fetching the image url from SDM for Bid 2.0 
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*
* 
* 
* 
* 
*/
export const getImageUrl = (imagePath: string | undefined, imageBaseUrl?: string | null) => {
    if (!imagePath) return null;
    // If it's already a data URL or absolute URL, return as-is
    if (imagePath.startsWith('data:') || imagePath.startsWith('http://') || imagePath.startsWith('https://')) {
        return imagePath;
    }
    if (!imageBaseUrl) {
        // Fallback: try to load as relative path
        return imagePath;
    }
    // Clean up the base URL and image path
    const cleanBase = imageBaseUrl.replace(/\/$/, '').replace('/:', '/');
    const cleanPath = imagePath.replace(/^[/\\]+/, '').replace(/\\/g, '/');
    return `${cleanBase}/${cleanPath}`;
};