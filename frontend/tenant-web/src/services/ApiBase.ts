
import axios, { AxiosRequestConfig } from "axios";
import i18next from "i18next";


interface IApiResponse {
  map(arg0: (item: {
    [x: string]: any;
    quantity: any;
    referenceNo(referenceNo: any): { primaryKey: any; bodyRegion: any; bodyPart: any; observationType: any; code: any; };
    primaryKey: any; //process response
    //process response
    bodyRegion: any; bodyPart: any; observationType: any; code: any;
  }) => { primaryKey: any; bodyRegion: any; bodyPart: any; observationType: any; code: any; }): unknown;
  data: any;
  status: number;
  message: string;
  ok: boolean;
  skipped?: boolean;
  reason?: string;
  errorCode:string;
  debugMessage: string;
  timestamp: string;
}




export const execute = async (
  method: any,
  url: any,
  payload: any,
  accessToken: any
) => {
  const result = {} as IApiResponse;
  result.ok = false;



  try {

    const requestOptions: AxiosRequestConfig = {
      method: method,
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },

      data: payload != null ? JSON.stringify(payload) : null,
    };

    //call api
    const response = await axios(url, requestOptions);

    //process response
    if (response.status === 200 || response.status === 201 || response.status === 202 || response.status === 204) {
      result.data = response.data;
      result.ok = true;
    } else {
      result.message = response.statusText;
    }
    result.status = response.status;
  } catch (error: any) {
    const errorData = error.response?.data;
    result.status = error.response?.status ?? 500;
    result.data = errorData;
    result.message = errorData?.message || errorData?.detail?.message || "Request failed";
    result.errorCode = errorData?.errorCode || errorData?.detail?.errorCode;
    result.debugMessage = errorData?.debugMessage;
    result.timestamp = errorData?.timestamp;
  }
  return result;
};


export const executeGetData = async (url: any, accessToken:any) => {
  return await execute('GET', url, null, accessToken);
}

export const executePostData = async (url: any, payload: any, accessToken:any) => {
  return await execute("POST", url, payload, accessToken);
};
// export const executePutData = async (url: string, accessToken: string) => {
//   try {
//     const response = await axios.put(url, null, {
//       headers: {
//         Authorization: `Bearer ${accessToken}`,
//       },
//     });

//     return response.data;
//   } catch (error) {
//     // Handle error, e.g., log or throw a custom error
//     throw new Error('Failed to execute PUT request');
//   }
// };



export const executePostImage = async (url: any, formData: any, accessToken:any) => {
  return await executeImage("POST", url, formData, accessToken);
};

export const executePutImage = async (url: any, formData: any, accessToken:any) => {
  return await executeImage("PUT", url, formData, accessToken);
};


export const executeImage = async (
  method: any,
  url: any,
  formData: any,
  accessToken: any
) => {
  const result = {} as IApiResponse;
  result.ok = false;
  try {
 
    const requestOptions: AxiosRequestConfig = {
      method: method,
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "multipart/form-data",
        "Accept": "application/json",
        "type": "formData"
      },
 
      data: formData ? formData : null,
    };
 
    //call api
    const response = await axios(url, requestOptions);
    //process response
    if (response.status === 200 || response.status === 201 || response.status === 202 || response.status === 204) {
      result.data = response.data;
      result.ok = true;
    } else {
      result.message = response.statusText;
    }
    result.status = response.status;
  } catch (error: any) {
    const errorData = error.response?.data;
    result.status = error.response?.status ?? 500;
    result.message = errorData?.message || errorData?.detail?.message || "Request failed";
    result.errorCode = errorData?.errorCode || errorData?.detail?.errorCode;
    result.debugMessage = errorData?.debugMessage;
    result.timestamp = errorData?.timestamp;
  }
  return result;
};

export const executePutData = async (url: any, payload: any, accessToken:any) => {
  return await execute('PUT', url, payload, accessToken);
}

export const executePatchData = async (url: any, payload: any, accessToken:any) => {
  return await execute('PATCH', url, payload, accessToken);
}

export const executeDeleteData = async (url: any, accessToken:any) => {
  return await execute('DELETE', url, null, accessToken);
}



export interface PyApiResponse<T = any> {
  ok: boolean;
  status?: number;
  message?: string;
  data?: T;
}

// AI-services calls (BPE inference, recommendations) have no server-side
// timeout guarantee on the frontend's behalf, and `fetch` never times out on
// its own — an unreachable/hung backend previously left callers' `loading`
// state stuck forever (e.g. the Bid Details Recommendation panel showing
// "Analyzing roof specifications..." indefinitely). BPE v2/infer is
// documented as typically 5-15s on CPU; 30s gives real requests a wide
// margin while still bounding the worst case instead of an infinite hang.
const PY_API_TIMEOUT_MS = 30000;

export const PyexecutePostData = async <T>(
  url: string,
  payload: any,
  headers: Record<string, string> = {}
): Promise<PyApiResponse<T>> => {
  const result: PyApiResponse<T> = { ok: false };
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), PY_API_TIMEOUT_MS);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...headers,
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });

    result.status = response.status;

    if (response.ok) {
      result.data = (await response.json().catch(() => ({}))) as T;
      result.ok = true;
    } else {
      result.message = await response.text().catch(() => response.statusText);
    }
  } catch (err: any) {
    result.message = err.name === "AbortError"
      ? i18next.t("common.requestTimedOut")
      : err.message ?? "Oops, looks like I need a quick reboot break..Try again later!";
  } finally {
    clearTimeout(timeoutId);
  }

  return result;
};


export interface ApiResponseWithHeaders<T = any> {
  ok: boolean;
  status?: number;
  message?: string;
  data?: T;
  errorCode?: string;
  debugMessage?: string;
  timestamp?: string;
}

const _getBearerToken = (accessToken: any) => {
  if (!accessToken) return "";
  return (
    accessToken?.access_token ??
    accessToken?.token ??
    accessToken?.id_token ??
    accessToken
  );
};

export const executeGetDataWithHeaders = async <T = any>(
  url: string,
  accessToken: any,
  extraHeaders: Record<string, string> = {}
): Promise<ApiResponseWithHeaders<T>> => {
  const result: ApiResponseWithHeaders<T> = { ok: false };

  try {
    const token = _getBearerToken(accessToken);

    const requestOptions: AxiosRequestConfig = {
      method: "GET",
      headers: {
        Authorization: token ? `Bearer ${token}` : undefined,
        "Content-Type": "application/json",
        ...extraHeaders,
      },
    };

    const response = await axios(url, requestOptions);

    result.status = response.status;
    if (response.status === 200 || response.status === 201 || response.status === 202 || response.status === 204) {
      result.data = response.data as T;
      result.ok = true;
    } else {
      result.message = response.statusText;
    }
  } catch (error: any) {
    result.status = error?.response?.status ?? 500;

    const data = error?.response?.data ?? {};
    result.message = data?.message ?? data?.detail ?? error?.message ?? "Request failed";
    result.errorCode = data?.errorCode;
    result.debugMessage = data?.debugMessage;
    result.timestamp = data?.timestamp;
  }

  return result;
};
export const executePostDataWithHeaders = async <T = any>(
  url: string,
  payload: any,
  accessToken: any,
  extraHeaders: Record<string, string> = {}
): Promise<ApiResponseWithHeaders<T>> => {
  const result: ApiResponseWithHeaders<T> = { ok: false };

  try {
    const token = _getBearerToken(accessToken);

    const requestOptions: AxiosRequestConfig = {
      method: "POST",
      headers: {
        Authorization: token ? `Bearer ${token}` : undefined,
        "Content-Type": "application/json",
        ...extraHeaders,
      },
      data: payload != null ? JSON.stringify(payload) : null,
    };

    const response = await axios(url, requestOptions);

    result.status = response.status;
    if (response.status === 200 || response.status === 201 || response.status === 202 || response.status === 204) {
      result.data = response.data as T;
      result.ok = true;
    } else {
      result.message = response.statusText;
    }
  } catch (error: any) {
    result.status = error?.response?.status ?? 500;

    const data = error?.response?.data ?? {};
    result.message = data?.message ?? data?.detail ?? error?.message ?? "Request failed";
    result.errorCode = data?.errorCode;
    result.debugMessage = data?.debugMessage;
    result.timestamp = data?.timestamp;
  }

  return result;
};

