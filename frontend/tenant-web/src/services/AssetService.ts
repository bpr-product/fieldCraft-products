
import { executeDeleteData, executeGetData, executePostData, executePutData } from "./ApiBase";
import { ConfigUrl } from "../auth/OidcConfig";
import { PropertyValidationResult } from "../interface/AddressInterfaces";
import { postAddressValidation } from "./AddressValidationService";



export const saveAsset = async (asset: any, accessToken:any) => {
  const url = `${ConfigUrl.apiUrl}/v2/assets/addasset`;
  return await executePostData(url, asset, accessToken);
};

export const getAsset = async(customerUid:any, accessToken:any) =>
{
  const url = `${ConfigUrl.apiUrl}/v2/assets/getAssetByCustomerUid/customerUid/${customerUid}`;
  return await executeGetData(url, accessToken);
}


export const getAssetUidByAsset = async(assetUid:any, accessToken:any) =>
  {
    const url = `${ConfigUrl.apiUrl}/v2/assets/getAssetByAssetUid/assetUid/${assetUid}`;
    return await executeGetData(url, accessToken);
  }

  

export const deleteAssetDocument = async(assetId:any, accessToken:any) =>
{
  const url = `${ConfigUrl.apiUrl}/v2/assetDocument/deleteAssetDocumentById/assetId/${assetId}`;
  return await executeDeleteData(url, accessToken);
}

export const deleteAsset = async(assetId:any, accessToken:any) =>
{
  const url = `${ConfigUrl.apiUrl}/v2/assets/delete/id/${assetId}`;
  return await executeDeleteData(url, accessToken);
}

export const editAsset = async(id:any, payload:any, accessToken:any) =>
{
  const url = `${ConfigUrl.apiUrl}/v2/assets/editAssetDetails/id/${id}`;
  return await executePutData(url, payload, accessToken);
}
export const checkAsset = async(accessToken:any) =>
  {
    const url = `${ConfigUrl.apiUrl}/v2/assets/getAsset/checkAssetIsEmpty`;
    return await executeGetData(url,accessToken);
  }

/**
 * AB#28854 — confirms an address is real/deliverable AND has an actual rooftop (Google Address
 * Validation + Solar API buildingInsights, orchestrated server-side by
 * AssetAddressValidationService). Fail-open: `result.data.accepted` is true even when a check
 * couldn't complete (see `result.data.message` for a non-blocking warning to show); only a
 * confirmed invalid address or confirmed absence of a rooftop returns `accepted: false`.
 * Gated behind the "rooflens-address-validation" feature flag by callers — always call the flag
 * check before this, never assume this is a no-op when unconfigured.
 *
 * POST with a JSON body (not `executeGetData`, deliberately): a customer's property address is
 * personal data this codebase already has an explicit no-log rule for on the Java side
 * (GeocodingClient, AB#26892) — a GET with the address in the query string would still land it in
 * browser history, `Referer` headers, and ingress access logs outside this code's control. A
 * short 15s timeout is set explicitly: `execute()`/`executeGetData` set none, and this call's
 * backend worst case is three serial 5s upstream Google calls — with no client timeout, a stalled
 * request would leave the Asset Save button (or the RoofLens picker) disabled indefinitely.
 */
export const validatePropertyAddress = (
  address: string | undefined,
  city: string | undefined,
  state: string | undefined,
  zipCode: string | undefined,
  accessToken: any,
): Promise<{ ok: boolean; data?: PropertyValidationResult; status?: number; message?: string }> =>
  postAddressValidation("/v2/assets/validate-address", address, city, state, zipCode, accessToken);

// Asset-triggered "Auto Estimate" button (step 5). Unlike the plain CRUD calls above,
// executePostData/executeGetData (ApiBase.execute) never reject — a 4xx/5xx just comes back as
// { ok: false, status, data }. AutoEstimateButton's HARD/SOFT-block branching needs to
// distinguish 422 (no roof area) from 409 (estimate already exists) from a real failure, so a
// non-ok result here is re-thrown in the same shape axios itself would throw it in
// ({ response: { status, data } }) — the one place in this file where a "get*"/"request*" call
// can reject, and deliberately so.
export const requestAutoEstimate = async (assetUid: any, confirm: boolean, accessToken: any) => {
    const url = `${ConfigUrl.apiUrl}/v2/assets/${assetUid}/autoEstimate`;
    const result = await executePostData(url, { confirm }, accessToken);
    if (!result.ok) {
        const error: any = new Error(result.message || `Auto Estimate request failed (${result.status})`);
        error.response = { status: result.status, data: result.data };
        throw error;
    }
    return result;
};

