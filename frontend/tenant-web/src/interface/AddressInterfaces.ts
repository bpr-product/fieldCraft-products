/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-10-05
 * @author      : Steeve
 * @since       : 1.5.4.0
 * Requirement# : AB#32816
 * Purpose      : Shared address shapes, so features (asset, customer, vendor, subcontractor, ...) and
 *                shared utils depend on a neutral module instead of on each other's components.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE      |   AUTHOR  |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * AB#32816       | 2026-10-05  |  Steeve   | ParsedAddress moved here from AssetLocationPicker (re-exported there)
 * AB#32816       | 2026-10-05  |  Steeve   | LatLng moved here from rooflens/types (re-exported there)
 * AB#32813       | 2026-10-05  |  Steeve   | PropertyValidationResult moved here from rooflens/types (re-exported there)
 * -----------------------------------------------------------------------------
 */

/** The address parts a map pick / Places result is reduced to. */
export interface ParsedAddress {
  address: string;
  city: string;
  state: string;
  country: string;
  zipCode: string;
}

/**
 * AB#28854 — result of GET /api/v2/assets/validate-address (AssetApi.java / PropertyValidationResult
 * on the backend). `addressStatus`/`roofStatus` are "VALID"/"INVALID"/"UNVERIFIED" and
 * "ROOF_FOUND"/"NO_ROOF"/"UNVERIFIED" respectively — kept as plain strings (not a TS union) to
 * mirror the backend's own String-status-constant convention, not an enum.
 */
export interface PropertyValidationResult {
  addressStatus: string;
  roofStatus: string | null;
  accepted: boolean;
  message: string | null;
}

/** A WGS84 point; shared by the 3D view and the address pickers. */
export interface LatLng {
  lat: number;
  lng: number;
}
