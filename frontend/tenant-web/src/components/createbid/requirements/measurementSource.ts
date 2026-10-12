/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-21
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : Pure source-precedence resolver for questionnaire measurement
 *                auto-fill. Decides whether a given MetaKey's pre-populated value
 *                should come from RoofLens (when the RoofLens measurement source
 *                flag is on) or fall back to the existing EagleView trade data.
 *                Extracted so the Requirements_v3 pre-population loop and its unit
 *                tests exercise the same logic. Pure — no I/O, no React.
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * RoofLens-Integration | 2026-07-21 | FIELDCRAFT Web | Initial version
 */

/** Which measurement source supplied a resolved value. */
export type MeasurementSource = 'rooflens' | 'eagleview';

/** A resolved pre-population value plus the source it came from (for tagging). */
export interface ResolvedMeasurement {
  value: string | number;
  source: MeasurementSource;
}

/**
 * Resolve the pre-population value for one questionnaire MetaKey, honoring the
 * RoofLens-measurement-source feature flag.
 *
 * - When `flagOn` is true and `roofLensMeta` carries a non-null value for the
 *   MetaKey, RoofLens wins (source: 'rooflens').
 * - Otherwise fall back to the existing EagleView trade data, checking the root
 *   object then its nested `TotalMeasurements` (source: 'eagleview'), mirroring
 *   the original loop's lookup.
 * - Returns null when neither source has a usable value.
 *
 * User-entered answers are NOT considered here — the caller's merge step keeps
 * manual edits winning over anything this returns.
 */
export function resolveMeasurementValue(
  metaKey: string,
  roofLensMeta: Record<string, string | number> | null | undefined,
  eagleViewData: any,
  flagOn: boolean
): ResolvedMeasurement | null {
  if (flagOn && roofLensMeta) {
    const rlValue = roofLensMeta[metaKey];
    if (rlValue !== undefined && rlValue !== null) {
      return { value: rlValue, source: 'rooflens' };
    }
  }

  if (eagleViewData) {
    const evValue = eagleViewData[metaKey] ?? eagleViewData.TotalMeasurements?.[metaKey];
    if (evValue !== undefined && evValue !== null) {
      return { value: evValue, source: 'eagleview' };
    }
  }

  return null;
}
