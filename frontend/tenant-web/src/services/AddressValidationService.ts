/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 01-Oct-2026
* @author      : Steeve
* @since       : 1.5.4.0
* Requirement# :
* Purpose      : Client for the Java address-only validation endpoint (no rooftop verdict).
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* AB#32813         | 01-Oct-2026 |     Steeve      | Initial creation
*/
import axios from "axios";
import { ConfigUrl } from "../auth/OidcConfig";
import { PropertyValidationResult } from "../interface/AddressInterfaces";

export type AddressValidationResponse = {
  ok: boolean;
  data?: PropertyValidationResult;
  status?: number;
  message?: string;
};

/** The only endpoints this client may call: address-only (customer/billing) and property (Create Asset). */
export type AddressValidationPath = "/v2/address/validate" | "/v2/assets/validate-address";

type HttpErrorShape = { response?: { status?: number; data?: { message?: string } }; message?: string };

/**
 * The one HTTP client for address-validation endpoints (shared by the address-only and the
 * property check). POST with a JSON body so the address never lands in a URL or access log;
 * explicit 15s timeout so a stalled backend cannot hang a Save button. Never throws — failures
 * resolve to `{ ok: false }`.
 */
export const postAddressValidation = async (
  path: AddressValidationPath,
  address: string | undefined,
  city: string | undefined,
  state: string | undefined,
  zipCode: string | undefined,
  accessToken: string | null | undefined,
): Promise<AddressValidationResponse> => {
  // No token: do not send "Bearer undefined" and wait for an opaque 401 — fail locally, same shape.
  if (!accessToken) return { ok: false, status: 401, message: "Missing access token" };
  try {
    const response = await axios.post(`${ConfigUrl.apiUrl}${path}`, { address, city, state, zipCode }, {
      timeout: 15000,
      headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    });
    return { ok: true, data: response.data, status: response.status };
  } catch (error: unknown) {
    const e = (error ?? {}) as HttpErrorShape;
    return {
      ok: false,
      status: e.response?.status ?? 500,
      message: e.response?.data?.message || e.message || "Request failed",
    };
  }
};

/** Address-only validation (no rooftop verdict) for customer/billing addresses. */
export const validateAddress = (
  address: string | undefined,
  city: string | undefined,
  state: string | undefined,
  zipCode: string | undefined,
  accessToken: string | null | undefined,
): Promise<AddressValidationResponse> =>
  postAddressValidation("/v2/address/validate", address, city, state, zipCode, accessToken);
