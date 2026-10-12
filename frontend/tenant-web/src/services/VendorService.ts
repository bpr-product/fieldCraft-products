/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 05-Mar-2024
* @author      :Vijay kumar
* @since       : 0.0.1
* Requirement# :
* Purpose      : Vendor Service Layer
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
import { executeGetData, executePostData, executePutData } from './ApiBase';
import { ConfigUrl } from '../auth/OidcConfig';

export const saveVendor = async (vendor: any, accessToken:any) => {
    const url = `${ConfigUrl.apiUrl}/v2/vendors`;
    return await executePostData(url, vendor, accessToken);
};

export const getVendorcount = async (status:any, searchParms:any, accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/vendors/getVendorCount/status/${status}/searchParams/${searchParms}`;
  return await executeGetData(url, accessToken);
};

export const getVendors = async (status:any, searchParms:any, page:any, size:any, accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/vendors/search/status/${status}/searchParams/${searchParms}?page=${page}&size=${size}`;
  return await executeGetData(url, accessToken);
};
export const sortVendors = async (status:any,sortField:any,sortOrder:any, searchParms:any, page:any, size:any, accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/vendors/search/status/${status}/sortField/${sortField}/sortOrder/${sortOrder}/searchParams/${searchParms}?page=${page}&size=${size}`;
  return await executeGetData(url, accessToken);
};
export const deleteVendor = async(vendorId:any, status:any, accessToken:any) =>
{
  const url = `${ConfigUrl.apiUrl}/v2/vendors/changeStatus/id/${vendorId}/status/${status}`;
  return await executePutData(url,null, accessToken);
}

export const editVendor = async(payload:any, accessToken:any) =>
{
  const url = `${ConfigUrl.apiUrl}/v2/vendors/updateVendor`;
  return await executePutData(url, payload, accessToken);
}

export const getVendorsById = async (id:any, accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/vendors/getVendorsById/id/${id}`;
  return await executeGetData(url, accessToken);
};

export const getAllVendors = async (accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/vendors`;
  return await executeGetData(url, accessToken);
};

export const getMaterialsByVendorId = async (vendorId:any, accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/vendors/getVendorMaterials/vendorId/${vendorId}`;
  return await executeGetData(url, accessToken);
};

export const getMaterialsByAddress = async (vendorAddress:any, accessToken:any) => {
  const encodedAddress = encodeURIComponent(vendorAddress);
  const url = `${ConfigUrl.apiUrl}/v2/vendors/getVendorMaterials/vendorAddress?address=${encodedAddress}`;
  return await executeGetData(url, accessToken);
};

export const getToolsByVendorId = async (vendorId:any, accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/vendors/getVendorTools/vendorId/${vendorId}`;
  return await executeGetData(url, accessToken);
};

export const getToolsByAddress = async (vendorAddress:any, accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/vendors/getVendorTools/vendorAddress/${vendorAddress}`;
  return await executeGetData(url, accessToken);
};

export const getVendorNames = async (accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/vendors/getVendorNames`;
  return await executeGetData(url, accessToken);
};

export const getVendorsAddressByName = async (vendorName:any,accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/vendors/getVendorsByName/name/${vendorName}`;
  return await executeGetData(url, accessToken);
};

export const updateVendorPaymentDetails = async (payload: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/vendors/updateVendorPayment`;
  return await executePutData(url, payload, accessToken);
};