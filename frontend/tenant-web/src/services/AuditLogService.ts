/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 19-07-2025
* @author      : Shabnam Banu H
* @since       : 0.0.1
* Requirement# :
* Purpose      : Audit Log
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
import { executeGetData, executePostData } from './ApiBase';
import { ConfigUrl } from '../auth/OidcConfig';

export const saveAuditLog = async (auditLog: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v1/auditLog/create`;
  return await executePostData(url, auditLog, accessToken);
};
export const getAuditMetadata = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v1/auditLog/auditLogMetadata`;
  const response = await executeGetData(url, accessToken);
  if (!response.ok) {
    throw new Error('Failed to fetch audit metadata');
  }
  return response;
};

 