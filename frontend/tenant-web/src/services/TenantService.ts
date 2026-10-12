/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 
* @author      : Asish
* @since       : 0.0.1
* Requirement# :
* Purpose      : Tenant service
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE    |   AUTHOR  |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 26909          | 26-08-2026 | Shiyam Kumar G | getVersionDetails back on the tenant
*                |            |                | API: it is the only endpoint that
*                |            |                | carries tenantApiVersion/BuildDate,
*                |            |                | so the ASE-direct call left both
*                |            |                | About-dialog Tenant rows at N/A.
*
*/
import { executeGetData, executePostData, executePostImage, executePutData } from "./ApiBase";
import { ConfigUrl } from "../auth/OidcConfig";

export const generalConfig = async (payload: any,accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/tenants/generalConfiguration`;
  return await executePostData(url,payload, accessToken);
};
export const getTenantDetails = async (accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/tenants/getTenantDetails`;
  return await executeGetData(url, accessToken);
};
/**
 * All four About-dialog rows in one call. The tenant API stamps
 * tenantApiVersion/tenantApiBuildDate from its own AppConstants and proxies
 * aseVersion/aseBuildDate from the running ASE service, so this is the only
 * endpoint that can fill the dialog. Calling ASE directly instead (which this
 * used to do) can never populate the two Tenant rows.
 */
export const getVersionDetails = async (accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/version/getVersions`;
  return await executeGetData(url, accessToken);
};

export const upadateTenantDetails = async (payload:any,id:any,accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/tenants/updateGeneralConfig/id/${id}`;
  return await executePutData(url,payload, accessToken);
};
export const createSignatureContract = async (payload: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/tenants/signatureUpload`; // Corrected URL
  return await executePostImage(url, payload, accessToken);
};
  
export const getSignature= async(accessToken:any) =>{
    const url = `${ConfigUrl.apiUrl}/v2/tenants/getSignature`;
    return await executeGetData(url, accessToken);
}

export const getTenantReport = async (accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/getTenantProjectReport`;
  return await executeGetData(url, accessToken);
}