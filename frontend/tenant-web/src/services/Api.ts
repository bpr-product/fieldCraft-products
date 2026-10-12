import axios, { AxiosRequestConfig } from "axios";


interface IApiResponse {
  map(arg0: (item: {
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
}

export const execute = async (
  method: any,
  url: any,
  payload: any,
  // accessToken: any
) => {
  const result = {} as IApiResponse;
  result.ok = false;

  try {

    const requestOptions: AxiosRequestConfig = {
      method: method,
      headers: {
        // Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },

      data: payload ? JSON.stringify(payload) : null,
    };
    //call api
    const response = await axios(url, requestOptions);
    //process response
    if (response.status === 200 ||response.status === 201 ) {
      result.data = response.data;
      result.ok = true;
    } else {

      result.message = response.statusText;
    }
    result.status = response.status;
  } catch (error) {
    result.status = 400;
    if (error instanceof Error) {
      result.message = error.message;
    } else {
      result.message = "An error has occured";
    }
  }
  return result;
};
export const executeGet = async (url: any) => {
  return await execute('GET', url, null);
}

export const executePost = async (url: any, payload: any) => {
  return await execute("POST", url, payload);
};

export const executePut = async (url: any, payload: any) => {
  delete payload._id;
  return await execute('PUT', url, payload);
}

export const executeDelete = async (url: any) => {
  return await execute('DELETE', url, null);
}

export const executeImage = async (url: any, payload: any) => {
  return await executeImageData("POST", url, payload);
};

// Generic function for image uploads (supports anonymous users)

export const executeImageData = async (
  method: any,
  url: any,
  formData: any,
) => {
  const result = {} as IApiResponse;
  result.ok = false;

  try {
    // Log the URL and request options before sending the request
// Log form data, be cautious with large data
    for (let [key, value] of formData.entries()) {
    }
    const requestOptions: AxiosRequestConfig = {
      method: method,
      headers: {
        "Content-Type": "multipart/form-data",
        "Accept": "application/json",
        "type": "formData"
      },
      data: formData ? formData : null,
    };

    // Call the API
    const response = await axios(url, requestOptions);

    // Process response
    if (response.status === 200 || response.status === 201 || response.status === 204) {
      result.data = response.data;
      result.ok = true;
    } else {
      result.message = response.statusText;
    }
    result.status = response.status;
  } catch (error) {
    result.status = 400;
    if (error instanceof Error) {
      result.message = error.message;
    } else {
      result.message = "An error has occurred";
    }
  }
  return result;
};


