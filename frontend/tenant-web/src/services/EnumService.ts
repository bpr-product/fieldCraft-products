/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 19-July-2025
 * @author      : Aravind J
 * @since       : 0.9.1.0
 * Requirement# :
 * Purpose      : Enum Service Layer
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |    DATE    |     AUTHOR    |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *
 *
 *
 */
import { executeGetData } from "./ApiBase";
import { ConfigUrl } from "../auth/OidcConfig";

export const getAllEnumCategoriesWithValues = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/enum/enumCategory`;
  return await executeGetData(url, accessToken);
};

export interface EnumOption { value: string; label: string; }

/** Reads one category's values out of getAllEnumCategoriesWithValues' nested response shape
 * (EnumService.getAllCategoriesWithEnumValues: { CATEGORY: { CODE: { VALUE_KEY: "Label" } } })
 * and flattens it to { value: CODE, label: "Label" } options for a dropdown (e.g. the
 * TURNDOWN_REASON category backing the Turn-down confirmation's reason picker, US 25586). */
export const getEnumOptions = async (category: string, accessToken: any): Promise<EnumOption[]> => {
  try {
    const res: any = await getAllEnumCategoriesWithValues(accessToken);
    const body = res?.data ?? res;
    const codeMap = body?.[category] ?? {};
    return Object.entries(codeMap as Record<string, Record<string, string>>).map(([code, labelMap]) => ({
      value: code,
      label: Object.values(labelMap ?? {})[0] ?? code,
    }));
  } catch (e) {
    console.error(`getEnumOptions(${category}) error:`, e);
    return [];
  }
};
