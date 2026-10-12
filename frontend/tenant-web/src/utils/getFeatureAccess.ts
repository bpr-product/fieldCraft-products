/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 10-OCT-2025
 * @author      : Shabnam Banu H
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Provides feature access flags and subscription status
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE      |    AUTHOR     |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 */

import { getAllServices } from "../services/FeaturesService";
import { trialExtensionStatus } from "../services/SubscriptionService";
import { getCachedSubscription, getSubscriptionData } from "../store/SubscriptionStore";
import {
  ACTIVE,
  CANCELLED_STATE,
  EAGLE_VIEW,
  PROJECT_HUB,
  BLOCKED,
  QUICK_BOOKS,
} from "./SubscriptionConstants";

let cachedServiceList: any[] | null = null;

export const getFeatureAccess = async (accessToken: string) => {
  // const subscription = sessionStorage.getItem("subscription");
   let cachedSubscription = getCachedSubscription();
   let cancelledDate;
   let planName;
   let bundleId;
   let hasSubscription;
   let subscriberEmail;
   let trialEndDate;
   let accountId;
   let remainingDays;
   let phaseType;
   let gracePeriod;
   let trialExtensionRequestStatus: string | null = null;

  try {
    let serviceRes = cachedServiceList;
    if (!serviceRes) {
      const response = await getAllServices(accessToken);
      serviceRes = Array.isArray(response.data) ? response.data : [];
      cachedServiceList = serviceRes; // save in cache
    }
    if (!cachedSubscription) {
      await getSubscriptionData(accessToken);
      cachedSubscription = getCachedSubscription();
    }
    accountId = cachedSubscription?.accountId;
    subscriberEmail = cachedSubscription?.email;
    const subscriptions = cachedSubscription?.subscriptions || [];
    gracePeriod = cachedSubscription?.subscriptionGracePeriod || [];
    if (subscriptions.length === 0) {
      hasSubscription = false;
      return {
        hasSubscription,
      };
    }
    hasSubscription = true;
    const activeSubscriptions = subscriptions.filter(
      (sub: any) => sub?.state === ACTIVE
    );

    if(activeSubscriptions.length > 0) {
      trialEndDate = activeSubscriptions[0]?.endDate;
      planName = activeSubscriptions[0]?.planName;
      remainingDays = activeSubscriptions[0]?.remainingDays ?? calculateRemainingDays(activeSubscriptions[0]?.endDate);
      phaseType = activeSubscriptions[0]?.phaseType;
    }
    const holdSubscriptions = subscriptions.filter(
      (sub: any) => sub?.state === BLOCKED
    );
    const isSubscriptionCancelled = activeSubscriptions.length === 0  &&
      subscriptions.some((sub: any) => sub?.state === CANCELLED_STATE) && holdSubscriptions.length === 0; 

    const isSubscriptionOnHold = activeSubscriptions.length === 0 &&
      subscriptions.some((sub: any) => sub?.state === BLOCKED);

    if (isSubscriptionCancelled) {
      const cancelledSubscriptions = subscriptions.filter(
        (sub: any) => sub?.state === CANCELLED_STATE && sub?.cancelledDate
      );

      const latest = cancelledSubscriptions.reduce((latest, current) =>
        new Date(current.cancelledDate).getTime() >
        new Date(latest.cancelledDate).getTime()
          ? current
          : latest
      );

      planName = latest.planName || null;
      cancelledDate = latest.cancelledDate || null;
      bundleId = latest.bundleId || null;
      phaseType = latest.phaseType || null;

      if (isSubscriptionCancelled && bundleId) {
        trialExtensionRequestStatus = await getTrialExtensionStatus(
          accessToken,
          bundleId
        );
      }
    }

    if (isSubscriptionOnHold) {
      const onHoldSubscriptions = subscriptions.filter(
        (sub: any) => sub?.state === BLOCKED
      );
      bundleId = onHoldSubscriptions[0]?.bundleId || null;
      planName = onHoldSubscriptions[0]?.planName || null;
    }   
    const activeFeatures = activeSubscriptions
      .flatMap((sub: any) => sub?.features || [])
      .filter((f: any) => f?.status === ACTIVE && f?.hasPermission === true);
    const normalize = (name: string) =>
      name?.replace(/[^a-zA-Z]/g, "").toLowerCase();

    const activeFeatureNames = new Set(
      activeFeatures.map((f: any) => normalize(f.featureName))
    );

    const projectHubService = serviceRes.find(
      (s: any) => normalize(s.name) === PROJECT_HUB
    );
    const eagleViewService = serviceRes.find(
      (s: any) => normalize(s.name) === EAGLE_VIEW
    );

    const projectHubAllowed = projectHubService
      ? activeFeatureNames.has(normalize(projectHubService.name))
      : false;

    const eagleViewAllowed = eagleViewService
      ? activeFeatureNames.has(normalize(eagleViewService.name))
      : false;

    const quickBooksService = serviceRes.find(
      (s: any) => normalize(s.name) === QUICK_BOOKS
    );

    const quickBooksAllowed = quickBooksService
      ? activeFeatureNames.has(normalize(quickBooksService.name))
      : false;
  
    
    return {
      projectHubAllowed,
      eagleViewAllowed,
      isSubscriptionCancelled,
      planName,
      cancelledDate,
      isSubscriptionOnHold,
      bundleId,
      subscriberEmail,
      trialEndDate,
      hasSubscription,
      accountId,
      phaseType,
      remainingDays,
      gracePeriod,
      trialExtensionRequestStatus,
      quickBooksAllowed
    };
  } catch (error) {
    console.error("Error fetching feature access flags:", error);
    return {
      projectHubAllowed: false,
      eagleViewAllowed: false,
      isSubscriptionCancelled: false,
    };
  }
};


const calculateRemainingDays = (endDate: string | null): number | null => {
  try {
    if (!endDate) return null;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const end = new Date(endDate);
    if (isNaN(end.getTime())) return null;
    end.setHours(0, 0, 0, 0);
    const diffMs = end.getTime() - today.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    if (diffDays < 0) {
      return 0; 
    }
    if (diffDays > 365) {
    }
    return Math.max(0, diffDays);
  } 
  catch (error) {
    console.error('[calculateRemainingDays] Error:', error);
    return null;
  }
};

const trialExtensionCache: {
  [bundleId: string]: {
    cachedStatus: string | null;
    isFetching: boolean;
    fetchPromise: Promise<string | null> | null;
  };
} = {};

const getTrialExtensionStatus = async (
  accessToken: string,
  bundleId: string,
  forceRefresh: boolean = false
): Promise<string | null> => {
  if (!trialExtensionCache[bundleId]) {
    trialExtensionCache[bundleId] = {
      cachedStatus: null,
      isFetching: false,
      fetchPromise: null
    };
  }

  const cache = trialExtensionCache[bundleId];
  if (cache.cachedStatus !== null && !forceRefresh) {
    return Promise.resolve(cache.cachedStatus);
  }

  if (cache.isFetching && cache.fetchPromise) {
    return cache.fetchPromise;
  }

  cache.isFetching = true;

  cache.fetchPromise = (async () => {
    try {
      const response = await trialExtensionStatus(accessToken, bundleId);
      const status = response?.data ?? null;
      cache.cachedStatus = status;
      return status;
    } catch (err) {
      cache.cachedStatus = null;
      return null;
    } finally {
      cache.isFetching = false;
      cache.fetchPromise = null;
    }
  })();

  return cache.fetchPromise;
};