import { executeGetData, executePostData } from "./ApiBase";
import { ConfigUrl } from "../auth/OidcConfig";

export const orderRequest= async (payload: any, accessToken: any) => {
    const url = `${ConfigUrl.apiUrl}/v1/request/placeOrderRequest`;
    return await executePostData(url, payload, accessToken);
  };

export const statusTrack= async (payload: any, accessToken: any) => {
    const url = `${ConfigUrl.apiUrl}/v1/request/statusTrack`;
    return await executePostData(url, payload, accessToken);
  };

  export const getRequest= async (assetId:any,accessToken: any) => {
    const url = `${ConfigUrl.apiUrl}/v1/request/assetId/${assetId}`;
    return await executeGetData(url, accessToken);
  };

  export const getServiceRequest= async (payload: any, accessToken: any) => {
    const url = `${ConfigUrl.apiUrl}/v1/request/getServiceRequest`;
    return await executePostData(url, payload, accessToken);
  };
