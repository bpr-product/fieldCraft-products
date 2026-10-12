/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 2-Nov-2023
* @author      : karthikeyan
* @since       : 0.0.1
* Requirement# :
* Purpose      : Create Bid Wizard Form
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR     |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 1557           |  10/04/24   | shiyam Kumar  |  Service Layer For Template Settings
* 1622           |  16/04/24   | shiyam Kumar  |  Implemnet a Function For Bom Template
*/

import { executeDeleteData, executeGetData, executePostData, executePutData } from "./ApiBase";
import { ConfigUrl } from "../auth/OidcConfig";
import { executeGet } from "./Api";

export const addTemplate = async (payload: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/tradeTemplates`;
  return await executePostData(url, payload, accessToken);
}

export const updateTradeTemplate = async (id: any, payload: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/tradeTemplates/${id}`;
  return await executePutData(url, payload, accessToken);
}

export const deleteTemplate = async (id: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/tradeTemplates/${id}`;
  return await executeDeleteData(url, accessToken);
}

export const getTemplates = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/tradeTemplates`;
  return await executeGetData(url, accessToken);
}

export const getTemplatesByTradeId = async (id: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/tradeTemplates/getByTradeId/id/${id}`;
  return await executeGetData(url, accessToken);
}

export const getTemplatesByTradeName = async (tradeOid: any,status:any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/questionTemplate/getQuestionTemplate/tradeOid/${tradeOid}/status/${status}`;
  return await executeGetData(url, accessToken);
}

export const createQuestionTemplate = async (templateDTO: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/questionTemplate/addQuestionTemplate`;
  return await executePostData(url, templateDTO,accessToken);
}


export const getAllQuestionTemplate = async ( accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/questionTemplate/getAllQuestionTemplate`;
  return await executeGetData(url,accessToken);
}
export const getselectedTemplatesByVersion = async (tradeOid: any,version:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/questionTemplate/getQuestionTemplate/tradeOid/${tradeOid}/version/${version}`;
    return await executeGet(url);
}

