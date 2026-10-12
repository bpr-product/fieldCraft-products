/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 30-OCT-2025
* @author      : Shabnam Banu H
* @since       : 0.0.1
* Requirement# :
* Purpose      : Global Subscription Store - Fetch and cache subscription details
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE    |   AUTHOR  |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*
*
*
*/

import { getSubscriptions } from "../services/SubscriptionService";

export interface SubscriberDetails{
    accountId: string;
    name: string;
    subscriberKey: string;
    email: string;
    companyName: string;
    phoneNumber: Number|string;
    startDate: string;
    endDate: string;
    status: string;
    subscriptions: Subscription[];
    subscriptionGracePeriod: SubscriptionGracePeriod[];
}

export interface Subscription{
    subscriptionId: string;
    subscriptionKey: string;
    productName: string;
    productCategory: string;
    planName: string;
    price: number | string;
    phaseType: string;
    startDate: string;
    endDate: string;
    cancelledDate: string;
    chargedThroughDate: string;
    quantity: number | string;
    state: string;
    duration: string;
    renewalDate: string;
    remainingDays: number | string;
    features: Feature[];
    bundleId: string;
}

export interface Feature{
    featureId: string;
    featureKey: string;
    featureName: string;
    status: string;
    hasPermission: boolean;
    permissionStatus: string;
}

export interface SubscriptionGracePeriod {
    inGracePeriod:boolean,
    overdueState:string,
    clearState:boolean,
    blockChanges:boolean,
    externalMessage:string,
    daysPastDue:number
}

let cachedSubscriberDetails: SubscriberDetails | null  = null;
let isFetching = false;
let fetchPromise: Promise<SubscriberDetails | null> | null = null;

export async function getSubscriptionData(accessToken,forceRefresh=false): Promise<SubscriberDetails | null> {
    if (cachedSubscriberDetails && !forceRefresh) {
        return Promise.resolve(cachedSubscriberDetails);
    }
    if (isFetching && fetchPromise) { 
        return fetchPromise;
    }
    isFetching = true;

    fetchPromise = getSubscriptions(accessToken).then((response:any) => {
        if (response && response.data) {
            cachedSubscriberDetails = response.data as SubscriberDetails;

        } else { 
            cachedSubscriberDetails = null;
        }
        return cachedSubscriberDetails!;
    }).catch((error) => {
        cachedSubscriberDetails = null;
        return null;
    }).finally(() => {
        isFetching = false;
        fetchPromise = null;
    });
    return fetchPromise;
}

export function getCachedSubscription() : SubscriberDetails | null {
    return cachedSubscriberDetails;
}

export function setSubscriptionCache(data: SubscriberDetails) {
    cachedSubscriberDetails = data;
}

export function clearSubscriptionCache() {
    cachedSubscriberDetails = null;
}
