/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Multi-Instance Parser - Extracts RepeatCount configurations from MethodDef
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

/**
 * Multi-Instance Rendering Utilities
 * Handles logic for rendering multiple instances of repeating groups
 */

// Export interfaces and utilities
export interface MultiInstanceContext {
    questionId: string;
    controllerQuestionId: string;
    controllerValue: any;
    parentPath: string[];
    methodDefs: any[];
    itemGroupData: any[];
    answersData: any;
}

export interface MultiInstanceResult {
    shouldRender: boolean;
    instanceCount: number;
    reason: string;
}

/**
 * Fix multi-instance path to handle nested instances
 */
export function fixMultiInstancePath(
    questionId: string,
    instanceIndex: number,
    parentSuffix?: string
): string {
    const suffix = `_${instanceIndex}`;
    const fullSuffix = parentSuffix ? `${parentSuffix}${suffix}` : suffix;
    return `${questionId}${fullSuffix}`;
}

/**
 * Determine if a multi-instance question should render
 */
export function shouldRenderMultiInstance(context: MultiInstanceContext): MultiInstanceResult {
    const { 
        questionId, 
        controllerQuestionId, 
        controllerValue, 
        parentPath,
        methodDefs,
        itemGroupData,
        answersData 
    } = context;
    
    // Default result
    const defaultResult: MultiInstanceResult = {
        shouldRender: false,
        instanceCount: 0,
        reason: 'No controller value found'
    };
    
    // Check if there's a controller question
    if (!controllerQuestionId) {
        return defaultResult;
    }
    
    // Get controller value from various sources
    let count = 0;
    
    // Source 1: Direct controller value
    if (controllerValue?.value !== undefined && controllerValue?.value !== null && controllerValue?.value !== '') {
        const parsedValue = parseInt(controllerValue.value);
        if (!isNaN(parsedValue) && parsedValue > 0) {
            count = parsedValue;
        }
    }
    
    // Source 2: From answersData
    if (count === 0 && answersData[controllerQuestionId]) {
        const answer = answersData[controllerQuestionId];
        const value = answer?.value !== undefined ? answer.value : answer;
        const parsedValue = parseInt(value);
        if (!isNaN(parsedValue) && parsedValue > 0) {
            count = parsedValue;
        }
    }
    
    // Source 3: From itemGroupData
    if (count === 0 && itemGroupData && itemGroupData.length > 0) {
        for (const group of itemGroupData) {
            const item = group?.ItemData?.find((id: any) => id?.ItemOID === controllerQuestionId);
            if (item && item.Value !== undefined && item.Value !== null && item.Value !== '') {
                const parsedValue = parseInt(item.Value);
                if (!isNaN(parsedValue) && parsedValue > 0) {
                    count = parsedValue;
                    break;
                }
            }
        }
    }
    
    // Cap at reasonable limit
    count = Math.max(0, Math.min(count, 20));
    
    if (count > 0) {
        return {
            shouldRender: true,
            instanceCount: count,
            reason: `Controller ${controllerQuestionId} has value ${count}`
        };
    }
    
    return defaultResult;
}

/**
 * Validate multi-instance rendering decision
 */
export function validateMultiInstanceDecision(
    context: MultiInstanceContext,
    result: MultiInstanceResult
): boolean {
    // Basic validation
    if (result.instanceCount < 0) {
        console.warn('Invalid instance count:', result.instanceCount);
        return false;
    }
    
    if (result.instanceCount > 20) {
        console.warn('Instance count exceeds limit:', result.instanceCount);
        return false;
    }
    
    if (result.shouldRender && result.instanceCount === 0) {
        console.warn('Inconsistent result: shouldRender=true but instanceCount=0');
        return false;
    }
    
    return true;
}

/**
 * Debug multi-instance issues
 */
export function debugMultiInstanceIssues(context: MultiInstanceContext): void {
    
    // Try to get the result and log it
    const result = shouldRenderMultiInstance(context);
    
    // Validate the result
    const isValid = validateMultiInstanceDecision(context, result);
}
