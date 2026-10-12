import { executePostData } from "./ApiBase";
import { ConfigUrl } from "../auth/OidcConfig";

/**
 * Given a list of image filenames from SDM CodeListItem (ImageUrl / ThumbnailUrl),
 * returns a map of filename → fresh SAS URL fetched from the tenant API proxy.
 */
export const getCatalogueSasUrls = async (
  filenames: string[],
  accessToken: string
): Promise<Record<string, string>> => {
  if (!filenames || filenames.length === 0) return {};
  const url = `${ConfigUrl.apiUrl}/v1/catalogue/sas-urls`;
  const response = await executePostData(url, filenames, accessToken);
  if (response?.ok && response.data) {
    return response.data as Record<string, string>;
  }
  return {};
};
