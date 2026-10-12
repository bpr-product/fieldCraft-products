import { ConfigUrl } from "../auth/OidcConfig";
import { executeDeleteData, executeGetData, executePostData, executePutData } from "./ApiBase";

export const getUserRolePermissions = async (accessToken: any) => {
    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/GetMyUserRolePermissions`;
    return await executeGetData(url, accessToken);
}

export const saveCreateUserAsset = async (user: any, accessToken:any) => {
    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/createUser`;
    return await executePostData(url, user, accessToken);
  };

  
export const getEmployeeAll = async (page:any,size:any,sortDir:any, accessToken:any) => {
    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/all?page=${page}&size=${size}&sortDir=${sortDir}`;
    return await executeGetData(url,  accessToken);
  };

  export const getAllTenantUser = async ( accessToken:any) => {
    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/getAllTenantUser`;
    return await executeGetData(url,  accessToken);
  };


  export const getEmployeeDetails = async (searchParams:any, page:any, size:any, accessToken:any) => {
    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/getEmployeeDetails/searchParams/${searchParams}?page=${page}&size=${size}`;
    return await executeGetData(url,  accessToken);
  };
  export const sortEmployeeDetails = async (searchParams:any,sortField:any,sortOrder:any, page:any, size:any, accessToken:any) => {
    const searchValue = searchParams?.trim() || 'null'; // or 'null' or 'none' if your backend expects that
    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/getEmployeeDetails/searchParams/${searchValue}/sortField/${sortField}/sortOrder/${sortOrder}?page=${page}&size=${size}`;
    return await executeGetData(url,  accessToken);
  };
  export const getEmployeeCount = async (searchParams:any, accessToken:any) => {
    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/getEmployeeCount/searchParams/${searchParams}`;
    return await executeGetData(url,  accessToken);
  }; 

  export const updateUser = async (id:any,user: any, accessToken:any) => {
    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/${id}`;
    
    return await executePutData(url, user, accessToken);
  };

  export const deleteUser = async (id:any ,accessToken:any) => {
    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/${id}`;
    
    return await executeDeleteData(url, accessToken);
  };

  export const getCustomerDetails = async (searchParams:any, page: any, size: any, accessToken:any) => {
    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/getCustomerDetails/searchParams/${searchParams}?page=${page}&size=${size}`;
    return await executeGetData(url,  accessToken);
  }; 
  export const sortCustomerDetails = async (searchParams:any, sortField:any,sortOrder:any,page: any, size: any, accessToken:any) => {
    const searchValue = searchParams?.trim() || 'null'; // or 'null' or 'none' if your backend expects that

    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/getCustomerDetails/searchParams/${searchValue}/sortField/${sortField}/sortOrder/${sortOrder}?page=${page}&size=${size}`;
    return await executeGetData(url,  accessToken);
  }; 

  export const getCustomerDetailsCount = async (searchParams:any, accessToken:any) => {
    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/getCustomerCount/searchParams/${searchParams}`;
    return await executeGetData(url,  accessToken);
  }

  export const getCustomers = async (accessToken:any) => {
    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/getCustomers`
    return await executeGetData(url,  accessToken);
  }; 

  export const findUserDetails = async ( email:any,accessToken:any) => {
    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/hasUserInAd/email/${email}`;
    return await executeGetData(url,  accessToken);
  };
  export const findUserByMobileNo = async ( mobileNo:any,accessToken:any) => {
    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/hasUserInAd/mobile/${mobileNo}`;
    return await executeGetData(url,  accessToken);
  };
  export const addJiraContent = async (payload:any,accessToken:any) => {
    const url = `${ConfigUrl.apiUrl}/v2/support/sendTicket`;
    return await executePostData(url, payload, accessToken);
  }; 
  export const getUser = async (accessToken:any) => {
    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/getUser`
    return await executeGetData(url,  accessToken);
  };

  export const changePassword = async (
    payload: { newPassword: string; confirmPassword: string },
    accessToken: any
  ) => {
    const url = `${ConfigUrl.apiUrl}/v2/userAccounts/changePassword`;
    return await executePostData(url, payload, accessToken);
  };