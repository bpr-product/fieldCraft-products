/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 2-Nov-2023
* @author      : Asish
* @since       : 0.0.1
* Requirement# :
* Purpose      : 
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

import { executeGetData, executePostData, executePutData } from "./ApiBase";
import { ConfigUrl } from "../auth/OidcConfig";


export const getpersonalInfo = async (email: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/personalInfo/hasEmployeeDetails/email/${email}`;
  return await executeGetData(url, accessToken);
};
export const getAdminInfo = async (email: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/personalInfo/registrationDetails/email/${email}`;
  return await executeGetData(url, accessToken);
};
export const getcustomerInfo = async (email: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/personalInfo/hasCustomer/email/${email}`;
  return await executeGetData(url, accessToken);
};


export const createPersonalInfo = async (personalDetails: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/personalInfo/createPersonalInfo`;
  return await executePostData(url, personalDetails, accessToken);
};

export const getUserDetails = async (userId: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/personalInfo/hasEmployeeDetails/userId/${userId}`;
  return await executeGetData(url, accessToken);
};
export const updateEmployeeDetail = async (personalDetails: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/personalInfo/updateEmployeeDetails`;
  return await executePutData(url, personalDetails, accessToken);
};

export const roleRemoval = async (id: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/projects/roleRemoval/userRoleMapId/${id}`;
  return await executePutData(url, null, accessToken);
};

export const updateRole = async (data: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/userRoleMap/update`;
  return await executePostData(url, data, accessToken);
};

export const swapRole = async (userId:any, data: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/userRoleMap/roleSwap/userId/${userId}`;
  return await executePostData(url, data, accessToken);
};

export const getPersonalDetails = async (userId: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/personalInfo/hasPersonalDetails/userId/${userId}`;
  return await executeGetData(url, accessToken);
};

export const newRole = async (userId:any, data: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/userRoleMap/roleAssign/userId/${userId}`;
  return await executePostData(url, data, accessToken);
};