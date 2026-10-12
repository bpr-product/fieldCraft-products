import { executeGetData, executePostImage, executePutImage } from "./ApiBase";
import { ConfigUrl } from "../auth/OidcConfig";


export const saveAssetDocument = async (imageFile: any, assetId: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/assetDocument/upload/assetId/${assetId}`;
  return await executePostImage(url, imageFile, accessToken);
};


export const ImportImage = async (accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/assetDocument/image`;
  return await executeGetData(url, accessToken);
};

export const editAssetDocument = async( payload:any, id:any, accessToken:any) =>
{
  const url = `${ConfigUrl.apiUrl}/v2/assetDocument/editAssetDocument/id/${id}`;
  return await executePutImage(url, payload, accessToken);
}

export const getAllAssetDocument = async( accessToken:any) =>
{
  const url = `${ConfigUrl.apiUrl}/v2/assetDocument`;
  return await executeGetData(url, accessToken);
}

export const getAssetDocumentById = async( assetId:any,accessToken:any) =>
{
  const url = `${ConfigUrl.apiUrl}/v2/assetDocument/assetId/${assetId}`;
  return await executeGetData(url, accessToken);
}
