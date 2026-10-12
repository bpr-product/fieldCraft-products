/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 
* @author      : Kanimozhi.S
* @since       : 0.0.1
* Requirement# :
* Purpose      : Features Service
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE        |   AUTHOR     |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 4037           |  20-03-2025   |   Kanimozhi  | List the Third party service and , new service  configuration API integration 
*
*
*/


import { executeGetData } from "./ApiBase";
import { ConfigUrl } from "../auth/OidcConfig";

export const getAllServices = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v1/service/getAllService`;
  return await executeGetData(url, accessToken);
};

export const getSubscriberById = async (accessToken: any, subscriberId: any) => {
  const url = `${ConfigUrl.apiUrl}/v1/subscriber/getSubscriber/${subscriberId}`;
  return await executeGetData(url, accessToken);
};
