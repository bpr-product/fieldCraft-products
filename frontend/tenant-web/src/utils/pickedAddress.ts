/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 30-Sep-2026
* @author      : Steeve
* @since       :
* Requirement# :
* Purpose      : The address parts a RoofLens / location-picker pick carries, in one place, so the asset
*                form writes the SAME values to the questionnaire fields and to assetData.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* AB#32415       | 30-Sep-2026 |     Steeve      | Extracted from CreateAsset_v3 (pick-address revert fix)
*/
import type { ParsedAddress } from '../interface/AddressInterfaces';

/** Address parts a map pick can supply, in the order the form and assetData store them. */
export const PICK_ADDRESS_KEYS = ['address', 'city', 'state', 'country', 'zipCode'] as const;
export type PickAddressKey = typeof PICK_ADDRESS_KEYS[number];

// A pick counts as "an address" only when it resolved at least one of these. A missing, all-empty or
// country-only result (reverse geocode failed, a street-less point) carries no address information.
const ADDRESS_SIGNAL_KEYS: readonly PickAddressKey[] = ['address', 'city', 'zipCode'];

/**
 * The trimmed address parts of a pick, with parts the pick did not supply as '' (so a stale street or
 * zip cannot survive next to the new pin) — or null when the pick carries no address at all, in which
 * case callers leave the form untouched (fail-open).
 */
export const buildPickedAddress = (parsed?: ParsedAddress): Record<PickAddressKey, string> | null => {
    if (!parsed || !ADDRESS_SIGNAL_KEYS.some((k) => !!parsed[k]?.trim())) return null;
    return Object.fromEntries(
        PICK_ADDRESS_KEYS.map((k) => [k, parsed[k]?.trim() ?? '']),
    ) as Record<PickAddressKey, string>;
};
