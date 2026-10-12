/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 6-Nov-2023
* @author      :Asish P Saji
* @since       : 0.0.1
* Requirement# :
* Purpose      : Base class for REST Controller used for preparing the results and validating the parameters etc
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE    |   AUTHOR  |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 1231     dec-11,2023     xyz buildGetResult function implementation
* AB#31383 | 28-Sep-2026 | Vaisnave | Added resolveBillingAddressForCustomer — extracted from
*          |             |          | TaskSelector_v2/v3.tsx's identical inline fetch+mapping so
*          |             |          | a new caller (CreateAsset_v3.tsx) can reuse it instead of a
*          |             |          | third copy
*
*/

import { executeGetData, executePostData, executePutData } from './ApiBase';
import { ConfigUrl } from '../auth/OidcConfig';

export interface CustomerBillingAddress {
  address: string;
  city: string;
  state: string;
  country: string;
  zipCode: string;
}

export const createCustomer = async (payload:any,tenantId :any,accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/personalInfo/createCustomer/tenantId/${tenantId}`;
  return await executePostData(url,payload,accessToken);
};

export const getCustomer = async (userId:any,accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/personalInfo/getCustomer/userId/${userId}`;
  return await executeGetData(url,accessToken);
};

export const updateCustomer = async (payload:any,accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/personalInfo/updateCustomer`;
  return await executePutData(url,payload,accessToken);
};

export const getCustomerUidByEmail = async (email:any,accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/personalInfo/getCustomerUId/email/${email}`;
  return await executeGetData(url,accessToken);
};

export const getPersonalInfoByCustomerUid = async (customerUid: string, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/personalInfo/getPersonalInfo/customerUid/${customerUid}`;
  return await executeGetData(url, accessToken);
};

/**
 * The customer's billing address, for callers that want to offer "use customer billing address
 * as asset address" without duplicating the fetch+field-mapping themselves (see
 * TaskSelector_v2.tsx/TaskSelector_v3.tsx's onAddAssetPage, which had this same block inline
 * twice before this was extracted). Returns null when the customer has no address on file, or the
 * lookup fails — callers treat that as "no billing address available", never an error to surface.
 */
export const resolveBillingAddressForCustomer = async (
  customerUid: string, accessToken: any,
): Promise<CustomerBillingAddress | null> => {
  try {
    const res = await getPersonalInfoByCustomerUid(customerUid, accessToken);
    if (res?.status !== 200 || !res?.data) return null;
    const info = res.data;
    const billingAddress: CustomerBillingAddress = {
      address: info.address || info.billingAddress || '',
      city: info.city || info.billingCity || '',
      state: info.state || info.billingState || '',
      country: info.country || info.billingCountry || '',
      zipCode: info.zipCode || info.billingZipCode || info.postalCode || '',
    };
    const hasAnyField = Object.values(billingAddress).some(v => v !== '');
    return hasAnyField ? billingAddress : null;
  } catch (err) {
    console.warn('Could not fetch customer personal info for billing address:', err);
    return null;
  }
};
