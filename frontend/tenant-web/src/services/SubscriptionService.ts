/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 01-09-2025
* @author      : Shabnam Banu H
* @since       : 0.0.1
* Requirement# :
* Purpose      : Subscription service
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE    |   AUTHOR  |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*
*
*
*/

import { executeGetData, executePostData, executePutData, executeDeleteData } from "./ApiBase";
import { ConfigUrl } from "../auth/OidcConfig";

export const getSubscriptions = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v1/subscription/subscriber`;
  return await executeGetData(url, accessToken);
};

export const cancelSubscription = async (requestBody:any, accessToken:any) =>
  await executePostData(`${ConfigUrl.apiUrl}/v1/subscription/cancel`, requestBody, accessToken);

export const holdSubscription = async (requestBody:any, accessToken:any) =>
  await executePutData(`${ConfigUrl.apiUrl}/v1/subscription/pause`, requestBody, accessToken);

export const resumeSubscription = async (requestBody:any, accessToken:any) =>
  await executePutData(`${ConfigUrl.apiUrl}/v1/subscription/resume`, requestBody, accessToken);

 
export const addPaymentMethod = async (requestBody: any, accessToken: any) =>
  await executePostData(`${ConfigUrl.apiUrl}/v1/subscription/payment/addPaymentMethod`, requestBody, accessToken);
 
export const getPaymentProfileInfo = async (pluginName: string, accessToken: any) =>
  await executeGetData(`${ConfigUrl.apiUrl}/v1/subscription/payment/profile/pluginName/${pluginName}`, accessToken);
 
export const getEnvironmentVariable = async (accessToken: any) =>
  await executeGetData(`${ConfigUrl.apiUrl}/v1/subscription/env`, accessToken);

export const deletePaymentMethod = async (paymentId: string, accessToken: any) =>
  await executeDeleteData(`${ConfigUrl.apiUrl}/v1/subscription/payment/payment-methods/${paymentId}`, accessToken);

export const setDefaultPaymentMethod = async (requestBody: any, accessToken: any) =>
  await executePutData(`${ConfigUrl.apiUrl}/v1/subscription/payment/default`, requestBody, accessToken);

export const getSubscriptionPlans = async (trialFlag: boolean, accessToken: any) =>
  await executeGetData(`${ConfigUrl.apiUrl}/v1/subscription/plans?trialFlag=${trialFlag}`, accessToken);

export const createSubscription = async (requestBody: any, accessToken: any) =>
  await executePostData(`${ConfigUrl.apiUrl}/v1/subscription/create`, requestBody, accessToken);

export const getInvoiceBySubscriptionId = async (subscriptionId: string, accessToken: any) => 
  await executeGetData(`${ConfigUrl.apiUrl}/v1/subscription/payment/invoice/${subscriptionId}`, accessToken);

export const requestTrialExtension = async (accessToken: any, bundleId:any) =>
  await executePostData(`${ConfigUrl.apiUrl}/v1/subscription/${bundleId}/trialExtension`, bundleId, accessToken)

export const trialExtensionStatus = async (accessToken: any, bundleId:any) =>
    await executeGetData(`${ConfigUrl.apiUrl}/v1/subscription/${bundleId}/supportRequest/status`, accessToken)

export const updatePaymentMethod = async (requestBody: any, accessToken: any) =>
    await executePutData(`${ConfigUrl.apiUrl}/v1/subscription/payment/update`, requestBody, accessToken);
