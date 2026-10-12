/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-03
 * @author      : Abirami R
 * @since       : 1.2.8.0
 * Requirement# : ADO 22357 — Daily Clock-In Gate (US-1)
 * Purpose      : TaskContext + user-session state for the tenant app. Carries the
 *                day clock-in timestamp (dayClockedInTs / setDayClockedInTs) that
 *                drives the GoPAL "On the clock" live day-timer and gate state.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE         |   AUTHOR     |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * ADO 22357      | 2026-07-03     | Abirami R    | Add dayClockedInTs to TaskContext
 * -----------------------------------------------------------------------------
 */
import { createContext } from "react";
import { getUserRolePermissions } from "../services/UserService";
import { useOidc } from "@axa-fr/react-oidc";
import { getAuditMetadata } from "../services/AuditLogService";
import { getAllEnumCategoriesWithValues } from "../services/EnumService";
import { getTenantDetails } from "../services/TenantService";
import { getRolesTitle } from "../utils/EmployeeRoles";

export type UserModeContext = {
  userSessionDetails: any;
  setUserDetails: (userSessionDetails: any) => void;
};

export type TaskContextType = {
  isTaskCompleted: boolean;
  handleCompleted: () => void;
  refreshGps: boolean;
  triggerGpsRefresh: (updatedProjectId?: number) => void;
  /** R1.6 / ADO 22386 — true when the current user has logged in for the day (C10 gate). */
  isDayClockedIn: boolean;
  setDayClockedIn: (value: boolean) => void;
  /** ISO timestamp of the day clock-in (resource_day_state.day_clocked_in_ts).
   *  Drives the live "On the clock" day timer in GoPAL. Null when not clocked in. */
  dayClockedInTs: string | null;
  setDayClockedInTs: (value: string | null) => void;
  /** True once the day-status API call in MainLayout has resolved (success or error). */
  isDayChecked: boolean;
};

export const TaskContext = createContext<TaskContextType>({
  isTaskCompleted: false,
  handleCompleted: () => { },
  refreshGps: false,
  triggerGpsRefresh: () => {},
  isDayClockedIn: false,
  setDayClockedIn: () => {},
  dayClockedInTs: null,
  setDayClockedInTs: () => {},
  isDayChecked: false,
});

export const UserContext = createContext<UserModeContext>({
  userSessionDetails: getUserSessionDetails(),
  setUserDetails: () => { },
});

// Requirement# 21843 — the tenant-DB numeric project id currently shown in the
// Project Hub sidebar (MainLayout.tsx owns the actual state; this context just
// exposes it and its setter to routed pages so opening a project elsewhere in
// the app — project list, Project Information, Gantt — can select the same
// project in the Hub without a separate/duplicate selection store. `null`
// means no project is selected.
export type ActiveProjectContextType = {
  activeProjectId: number | null;
  setActiveProjectId: (projectId: number | null) => void;
};

export const ActiveProjectContext = createContext<ActiveProjectContextType>({
  activeProjectId: null,
  setActiveProjectId: () => { },
});

// Shiloh (Project Hub chat) on/off control — state lives in MainLayout
// (single source of truth, still initialized from FEATURE_FLAGS.ENABLE_Shiloh
// and still combined with notes.READ permission before it actually gates the
// panel). Exposed via context so the toggle control itself can live on the
// General Configuration page instead of the top bar, without prop-drilling.
export type ShilohFeatureContextType = {
  shilohEnabled: boolean;
  setShilohEnabled: (enabled: boolean) => void;
};

export const ShilohFeatureContext = createContext<ShilohFeatureContextType>({
  shilohEnabled: true,
  setShilohEnabled: () => { },
});

// --- Session and Permissions ---
export var userSession: any;
var permissions: any = [];
var enumCategory: any;
export async function getUserSessionDetails(accessToken?: any) {
  var userSessionDetails: any;
  if (accessToken != null && accessToken.length !== 0) {
    const response = await getUserRolePermissions(accessToken);
    userSessionDetails = response.data;
    userSession = response.data;
    // role is an array of raw role tokens; map each to its US-English display
    // name so the tab reads "Labor" not "labour" (AB#28141 labor i18n migration).
    document.title = "FIELDCRAFT " + getRolesTitle(userSession?.role);
    const rolePermissions = userSession?.userRolePermissions;
    for (const index in rolePermissions) {
      permissions.push(rolePermissions[index].permission);
    }
    enumCategory = await getAllEnumCategoriesWithValues(accessToken);
    await getTenantSessionDetails(accessToken);
  }
  return userSessionDetails;
}

export function isUserHasPermission(permission: string): boolean {
  return userSession && permissions.includes(permission);
}

// AB#26965: userSession.role is an array of role keys, not a single string -- a user can
// hold multiple roles at once (e.g. "sales" and "admin" together). Mirrors
// isUserHasPermission's own module-level userSession read above.
export function getCurrentUserRoles(): string[] {
  return userSession && Array.isArray(userSession.role) ? userSession.role : [];
}

export async function getUserSession(accessToken?: any) {
  if (accessToken && accessToken.length !== 0) {
    const response = await getUserRolePermissions(accessToken);
    return response.data;
  }
  //throw new Error("User OID is invalid or empty");
}

// --- Page Tracking ---
type PageDetails = {
  path: string;
  tabPosition: any;
  tabName: String;
};

var previousPageDetails: PageDetails;

export async function setCurrentPageDetails(
  path: any,
  tabPosition: any,
  tabName: any
) {
  previousPageDetails = {
    path: path,
    tabPosition: tabPosition,
    tabName: tabName,
  };
}

export function getPreviousPageDetails() {
  return previousPageDetails;
}

export function getEnumsData() {
  return enumCategory?.data ?? {};
}

// --- Unauthorized Handler ---
export const useHandleUnauthorized = () => {
  const { logout } = useOidc();
  const url = new URL(globalThis.window.location.href);
  const queryParam = url.search.substring(1);

  const handleUnauthorized = (res: any) => {
    if (
      (res?.status === 400 || res?.status === 401) &&
      res.message === "Request failed with status code 401"
    ) {
      console.error("Unauthorized access - logging out");
      if (!queryParam.toString()) {
        logout("/"); // Call logout function
      }
    }
  };

  return { handleUnauthorized };
};
// export async function getUserEmail(accessToken?: string): Promise<string | null> {
//   try {
//     if (userSession && userSession.email) return userSession.email;
//     if (accessToken && accessToken.length !== 0) {
//       const res = await getUserRolePermissions(accessToken);
//       const session = res?.data ?? null;
//       if (session) {
//         userSession = session;
//         const rolePermissions = userSession?.userRolePermissions ?? [];
//         for (const idx in rolePermissions) {
//           if (!permissions.includes(rolePermissions[idx].permission)) {
//             permissions.push(rolePermissions[idx].permission);
//           }
//         }
//         return userSession.email ?? null;
//       }
//     }
//   } catch (err) {
//     console.error("getUserEmail error:", err);
//   }
//   return null;
// }

// let subscriptionemail: string | null = null;
// export async function getSubscriberEmail(accessToken?: string): Promise<string | null> {
//   try {
//     if (!accessToken) {
//       return null;
//     }
//     if (subscriptionemail) {
//       return subscriptionemail;
//     }
//     // const res = await getSubscriptions(accessToken);

//     let email: string | null = null;
//     if (subscription) {
//       try {
//         const subscriptionObj = JSON.parse(subscription);
//         email = subscriptionObj?.email ?? null;
//       } catch (e) {
//         console.error("Failed to parse subscription from localStorage:", e);
//       }
//     }
//     subscriptionemail = email;
//     return email;
//   } catch (err) {
//     console.error("getEmail error:", err);
//     return null;
//   }
// }

// let cachedAccountId: string | null = null;
// export async function getAccountId(
//   accessToken?: string
// ): Promise<string | null> {
//   if (!accessToken) {
//     return null;
//   }
//   if (cachedAccountId) {
//     return cachedAccountId;
//   }
//   let accountId: string | null = null;
//   if (subscription) {
//     try {
//       const subscriptionObj = JSON.parse(subscription);
//       accountId = subscriptionObj?.accountId ?? null;
//     } catch (e) {
//       console.error("Failed to parse subscription from localStorage:", e);
//     }
//   }

//   cachedAccountId = accountId;
//   return accountId;
// }

let auditActions: string[] = [];
let auditFeatures: string[] = [];
let actionsEnum: Record<string, string> = {};
let auditMetadataLoaded = false;

export async function loadAuditMetadata(accessToken: any): Promise<void> {
  if (auditMetadataLoaded) return;

  try {
    const res = await getAuditMetadata(accessToken);

    if (!res.ok) {
      console.error("Audit metadata fetch failed:", res);
      throw new Error("Failed to fetch audit metadata");
    }

    const data = await res.data;

    auditActions = data.actions;
    auditFeatures = data.features;
    actionsEnum = Object.fromEntries(data.actions.map((a: string) => [a, a]));
    auditMetadataLoaded = true;
  } catch (error) {
    console.error("Error in loadAuditMetadata:", error);
    auditMetadataLoaded = false;
  }
}

export function getAuditActionsEnum(): Record<string, string> {
  return actionsEnum;
}

export function getAuditFeatures(): string[] {
  return auditFeatures;
}

export function getAuditFeaturesEnum(): Record<string, string> {
  return Object.fromEntries(auditFeatures.map((f: string) => [f, f]));
}

export function isAuditMetadataLoaded(): boolean {
  return auditMetadataLoaded;
}

export async function ensureAuditMetadata(accessToken: string) {
  if (!isAuditMetadataLoaded()) {
    await loadAuditMetadata(accessToken);
  }

  return {
    actionsEnum: getAuditActionsEnum(),
    featuresEnum: getAuditFeaturesEnum(),
  };
}

export interface TenantModeContext {
  tenantSessionDetails: any;
  setTenantDetails: (tenantSessionDetails: any) => void;
}

export const TenantContext = createContext<TenantModeContext>({
  tenantSessionDetails: getTenantSessionDetails(),
  setTenantDetails: () => { },
});

// --- Session and Tenant Details ---
export var tenantSession: any;
export async function getTenantSessionDetails(accessToken?: any) {
  var tenantSessionDetails: any;
  if (accessToken != null && accessToken.length !== 0) {
    const response = await getTenantDetails(accessToken);
    if (response.ok) {
      tenantSessionDetails = response.data;
      tenantSession = response.data;
    }
  }
  return tenantSessionDetails;
}
