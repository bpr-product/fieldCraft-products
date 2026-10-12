/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Purpose : ABC Supply external-service client for material import into FIELDCRAFT.
*/
import {
  executeGetData,
  executeGetDataWithHeaders,
  executePostDataWithHeaders,
  type ApiResponseWithHeaders,
} from "../ApiBase";
import { ConfigUrl } from "../../auth/OidcConfig";

export type ABCSupplyImportSource =
  | "recent"
  | "frequent"
  | "favorite"
  | "search"
  | "item_numbers"
  | "all";

export interface ABCSupplyImportToFIELDCRAFTRequest {
  tenant_id?: string | null;
  service_id?: string | null;
  source: ABCSupplyImportSource;
  query?: string | null;
  item_numbers?: string[] | null;
  bill_to_number?: string | null;
  branch_number?: string | null;
  ship_to_number?: string | null;
  items_per_page: number;
  page_number: number;
  max_pages: number;
  max_items: number;
  fieldcraft_vendor_id: number;
  fieldcraft_vendor_name: string;
  default_trade: string;
  default_grade: string;
  default_base_unit: string;
  default_conversion_factor: string;
  default_cost: string;
  default_manufacturer: string;
  allow_decimal: boolean;
  in_stock: boolean;
  lead_time?: number | null;
  fetch_prices: boolean;
  require_pricing?: boolean;
  dry_run: boolean;
  batch_size: number;
}

export interface ABCSupplyImportSelectedToFIELDCRAFTRequest {
  tenant_id?: string | null;
  service_id?: string | null;
  materials: Array<Record<string, any>>;
  branch_number?: string | null;
  ship_to_number?: string | null;
  fieldcraft_vendor_id: number;
  fieldcraft_vendor_name: string;
  default_trade: string;
  default_grade: string;
  default_base_unit: string;
  default_conversion_factor: string;
  default_cost: string;
  default_manufacturer: string;
  allow_decimal: boolean;
  in_stock: boolean;
  lead_time?: number | null;
  fetch_prices: boolean;
  require_pricing?: boolean;
  dry_run: boolean;
  batch_size: number;
}

export interface ABCSupplyImportCounts {
  fetched?: number;
  duplicateFromSource?: number;
  prepared?: number;
  created?: number;
  linkedExisting?: number;
  skippedExisting?: number;
  skippedMissingPrice?: number;
  skippedInvalid?: number;
  failed?: number;
  priceErrors?: number;
}

export interface ABCSupplyImportResult {
  ok?: boolean;
  source?: string;
  dryRun?: boolean;
  counts?: ABCSupplyImportCounts;
  created?: Array<Record<string, any>>;
  skipped?: Array<Record<string, any>>;
  failed?: Array<Record<string, any>>;
  priceErrors?: Array<Record<string, any>>;
  materials?: Array<Record<string, any>>;
}



export interface ABCSupplyImportedMaterialsRequest {
  tenant_id?: string | null;
  service_id?: string | null;
  external_item_ids?: string[] | null;
  branch_number?: string | null;
  ship_to_number?: string | null;
  bill_to_number?: string | null;
  fieldcraft_vendor_id?: number | null;
  search?: string | null;
  page?: number;
  limit?: number;
}

export interface ABCSupplyImportedMaterial {
  materialExternalMapId?: number | string;
  serviceId?: number | string;
  status?: string;
  externalItemId?: string;
  externalAccountId?: string;
  externalDisplayName?: string;
  materialId?: number | string;
  materialCostId?: number | string;
  vendorMaterialMapId?: number | string;
  vendorId?: number | string;
  vendorName?: string;
  name?: string;
  itemNumber?: string;
  itemCode?: string;
  manufacturer?: string;
  category?: string;
  trade?: string;
  unit?: string;
  grade?: string;
  cost?: number | string;
  branchNumber?: string;
  shipToNumber?: string;
  createdBy?: string;
  createdAt?: string;
  updatedAt?: string;
  reason?: string;
  additionalInfo?: Record<string, any>;
  [key: string]: any;
}

export interface ABCSupplyImportedMaterialsResult {
  items?: ABCSupplyImportedMaterial[];
  page?: number;
  limit?: number;
  total?: number;
  totalPages?: number;
  serviceId?: string | number;
}

export interface ABCSupplyConnectionSaveRequest {
  tenant_id: string;
  env: "sandbox" | "production" | string;
  client_id: string;
  client_secret: string;
  branch_number?: string | null;
  ship_to_number?: string | null;
  bill_to_number?: string | null;
  scopes?: string | null;
}

export interface ABCSupplyConnectionStatusRequest {
  tenant_id: string;
}

export interface ABCSupplyConnectionDisconnectRequest {
  tenant_id: string;
  clear_credentials?: boolean;
}

export interface ABCSupplyConnectionStatus {
  connected?: boolean;
  status?: string;
  env?: string | null;
  serviceId?: string | null;
  branchNumber?: string | null;
  shipToNumber?: string | null;
  clientIdPresent?: boolean;
  clientSecretPresent?: boolean;
  billToNumberPresent?: boolean;
  connectedAt?: string | null;
  validatedAt?: string | null;
  updatedAt?: string | null;
  disconnectedAt?: string | null;
  reconnectRequired?: boolean;
  authError?: string | null;
}

export interface ABCSupplyConfigStatus extends ABCSupplyConnectionStatus {
  api_base_url?: string;
  auth_base_url?: string;
  missing?: string[];
}

export interface ABCSearchRequest {
  tenant_id?: string | null;
  query?: string | null;
  item_number?: string | null;
  branch_number?: string | null;
  items_per_page: number;
  page_number: number;
  include_family_items?: boolean;
}

export interface ABCItemDetailRequest {
  tenant_id?: string | null;
  item_number: string;
  include_price?: boolean;
  quantity?: number;
  uom?: string | null;
  branch_number?: string | null;
  ship_to_number?: string | null;
}

export interface ABCPriceLine {
  id: string;
  itemNumber: string;
  quantity: number;
  uom?: string;
}

export interface ABCPriceRequest {
  tenant_id?: string | null;
  branch_number?: string | null;
  ship_to_number?: string | null;
  purpose?: string;
  request_id?: string;
  lines: ABCPriceLine[];
}

// Generic credential endpoint for the external-service gateway.
// All integrations (ABC Supply, and others in the future) share the same
// API_SECRET on the gateway. Currently served at /v1/quickbooks/credential
// because that endpoint already exists — once /v1/external-service/credential
// is added to the Java backend, update the URL below and retire the QB alias.
let externalServiceSecretHeaderPromise: Promise<Record<string, string>> | null = null;

const getExternalServiceSecretHeader = async (accessToken: string): Promise<Record<string, string>> => {
  if (!externalServiceSecretHeaderPromise) {
    externalServiceSecretHeaderPromise = executeGetData(
      `${ConfigUrl.apiUrl}/v1/quickbooks/credential`,
      accessToken
    )
      .then((credential: any) => {
        const secretKey =
          credential?.data?.secret_key ??
          credential?.data?.secretKey ??
          credential?.data?.secret ??
          credential?.secret_key ??
          credential?.secretKey ??
          "";

        if (!secretKey) {
          throw new Error("External service api-secret not found");
        }

        return { "api-secret": secretKey };
      })
      .finally(() => {
        externalServiceSecretHeaderPromise = null;
      });
  }

  return externalServiceSecretHeaderPromise;
};

const abcPost = async <T>(
  path: string,
  payload: any,
  accessToken: string
): Promise<ApiResponseWithHeaders<T>> => {
  const secretHeader = await getExternalServiceSecretHeader(accessToken);
  return await executePostDataWithHeaders<T>(
    `${ConfigUrl.extservice_base_url}${path}`,
    payload,
    accessToken,
    secretHeader
  );
};

const abcGet = async <T>(
  path: string,
  accessToken: string
): Promise<ApiResponseWithHeaders<T>> => {
  const secretHeader = await getExternalServiceSecretHeader(accessToken);
  return await executeGetDataWithHeaders<T>(
    `${ConfigUrl.extservice_base_url}${path}`,
    accessToken,
    secretHeader
  );
};

export const getABCSupplyConfigStatus = async (
  accessToken: string,
  payload: Partial<ABCSupplyConnectionStatusRequest> = {}
) => {
  return await abcPost<ABCSupplyConfigStatus>(
    "/extsvc/api/v1/abc-supply/config/status",
    payload,
    accessToken
  );
};

export const saveABCSupplyConnection = async (
  payload: ABCSupplyConnectionSaveRequest,
  accessToken: string
) => {
  return await abcPost<ABCSupplyConnectionStatus>(
    "/extsvc/api/v1/abc-supply/connection/save",
    payload,
    accessToken
  );
};

export const getABCSupplyConnectionStatus = async (
  payload: ABCSupplyConnectionStatusRequest,
  accessToken: string
) => {
  return await abcPost<ABCSupplyConnectionStatus>(
    "/extsvc/api/v1/abc-supply/connection/status",
    payload,
    accessToken
  );
};

export const disconnectABCSupplyConnection = async (
  payload: ABCSupplyConnectionDisconnectRequest,
  accessToken: string
) => {
  return await abcPost<ABCSupplyConnectionStatus>(
    "/extsvc/api/v1/abc-supply/connection/disconnect",
    payload,
    accessToken
  );
};

export const importABCSupplyMaterialsToFIELDCRAFT = async (
  payload: ABCSupplyImportToFIELDCRAFTRequest,
  accessToken: string
) => {
  return await abcPost<ABCSupplyImportResult>(
    "/extsvc/api/v1/abc-supply/materials/import-to-fieldcraft",
    payload,
    accessToken
  );
};

export const importSelectedABCSupplyMaterialsToFIELDCRAFT = async (
  payload: ABCSupplyImportSelectedToFIELDCRAFTRequest,
  accessToken: string
) => {
  return await abcPost<ABCSupplyImportResult>(
    "/extsvc/api/v1/abc-supply/materials/import-selected-to-fieldcraft",
    payload,
    accessToken
  );
};


export const getImportedABCSupplyMaterials = async (
  payload: ABCSupplyImportedMaterialsRequest,
  accessToken: string
) => {
  return await abcPost<ABCSupplyImportedMaterialsResult>(
    "/extsvc/api/v1/abc-supply/materials/imported",
    payload,
    accessToken
  );
};

export const resetABCSupplySecretHeaderCache = () => {
  externalServiceSecretHeaderPromise = null;
};

export const searchABCSupplyItems = async (
  payload: ABCSearchRequest,
  accessToken: string
) => {
  return await abcPost<any>(
    "/extsvc/api/v1/abc-supply/items/search",
    payload,
    accessToken
  );
};

export const getABCSupplyItem = async (
  payload: ABCItemDetailRequest,
  accessToken: string
) => {
  return await abcPost<any>(
    "/extsvc/api/v1/abc-supply/items/get",
    payload,
    accessToken
  );
};

export const priceABCSupplyItems = async (
  payload: ABCPriceRequest,
  accessToken: string
) => {
  return await abcPost<any>(
    "/extsvc/api/v1/abc-supply/items/price",
    payload,
    accessToken
  );
};

export const getValidTradeNames = async (accessToken: string) => {
  return await executeGetData(
    `${ConfigUrl.apiUrl}/v2/trades/getValidTradeName`,
    accessToken
  );
};

export const healthCheckABCSupplyService = async (accessToken: string) => {
  return await abcGet<Record<string, any>>("/", accessToken);
};
