import { PagingInfo } from "../queryparams/PagingInfo";
import { ProjectQueryParam } from "../queryparams/ProjectQueryParam";
import { executeGetData, executePostData } from "./ApiBase";
import { ConfigUrl } from "../auth/OidcConfig";

export const postChatBot = async (message: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/chatApi`;
  return await executePostData(url, message, accessToken);
};

export const getProjectInfoByQueryParam = async (
  projectQueryParam: ProjectQueryParam,
  pagingInfo: PagingInfo,
  accessToken: any
) => {
  const url = `${ConfigUrl.apiUrl}/v2/bids/getProjectInfoByQueryParam?page=${pagingInfo.page}&size=${pagingInfo.size}`;
  JSON.stringify(projectQueryParam);
  return await executePostData(url, projectQueryParam, accessToken);
}

export const getProjectInfoByProjectId = async (orderNo: any, accessToken: any) => {
  const url = `${ConfigUrl.apiUrl}/v2/bids/getProjectByOrderNo/orderNo/${orderNo}`;
  return await executeGetData(url, accessToken);
}

