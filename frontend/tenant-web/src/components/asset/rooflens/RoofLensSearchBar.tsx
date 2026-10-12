/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-07-17
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : Address search for the RoofLens map — Google Places autocomplete
 *                (client-side, mirrors AssetLocationPicker) → recenters the map and
 *                surfaces the parsed address components so the Property Details
 *                fields can be auto-filled (replacing the old location picker).
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * RoofLens-Integration | 2026-07-17 | FIELDCRAFT Web | Initial version
 * RoofLens-Integration | 2026-07-17 | FIELDCRAFT Web | Emit ParsedAddress for field auto-fill
 * AB#28854       | 2026-09-02   | FIELDCRAFT Web | Fixed a silent-failure UX bug (reported live testing
 *                                            AB#28854): StandaloneSearchBox only fires
 *                                            onPlacesChanged when the typed text resolves to a
 *                                            usable place — pressing Enter on text that Google
 *                                            can't resolve to anything with a location (a
 *                                            nonexistent/garbo address, or text where the user
 *                                            never clicked/arrow-selected a dropdown suggestion)
 *                                            did NOTHING: no error, no feedback, the map just
 *                                            stayed on its empty state with zero indication why.
 *                                            Now shows a visible hint in that case instead.
 */

import * as React from "react";
import { useTranslation } from "react-i18next";
import { InputAdornment, TextField, Typography } from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import { StandaloneSearchBox } from "@react-google-maps/api";
import { ParsedAddress } from "../AssetLocationPicker";
import { isGoogleMapsGeocoderReady } from "../../../utils/mapUtils";

// How long to wait, after Enter, for Google's own onPlacesChanged to fire before concluding
// nothing resolved. StandaloneSearchBox's places_changed event is effectively synchronous once
// fired, but giving it a tick avoids a false "no match" flash if it's ever delayed.
const NO_MATCH_HINT_DELAY_MS = 300;

interface Props {
  initialText?: string;
  onPick: (lat: number, lng: number, address: string, parsed?: ParsedAddress) => void;
}

/** Map Google Places address_components to the CRM's ParsedAddress shape
 *  (kept in sync with AssetLocationPicker so field auto-fill behaves the same). */
function parseAddressComponents(components: any[] = []): ParsedAddress {
  const fullAddress: string[] = [];
  const result: ParsedAddress = { address: "", city: "", state: "", country: "", zipCode: "" };
  components.forEach((c: any) => {
    const types: string[] = c.types || [];
    if (
      types.includes("street_number") || types.includes("route") ||
      types.includes("premise") || types.includes("subpremise") ||
      types.includes("neighborhood") || types.includes("sublocality") ||
      types.includes("sublocality_level_1") || types.includes("sublocality_level_2")
    ) {
      fullAddress.push(c.long_name);
    }
    if (!result.city && (types.includes("locality") || types.includes("postal_town") || types.includes("administrative_area_level_2"))) {
      result.city = c.long_name;
    }
    if (!result.state && types.includes("administrative_area_level_1")) result.state = c.long_name;
    if (!result.country && types.includes("country")) result.country = c.long_name;
    if (!result.zipCode && types.includes("postal_code")) result.zipCode = c.long_name;
  });
  result.address = fullAddress.join(" ").trim();
  return result;
}

const RoofLensSearchBar: React.FC<Props> = ({ initialText = "", onPick }) => {
  const { t } = useTranslation();
  const [text, setText] = React.useState(initialText);
  const [noMatchHint, setNoMatchHint] = React.useState(false);
  const boxRef = React.useRef<google.maps.places.SearchBox | null>(null);
  // Set true the instant a real pick commits, inside handlePlacesChanged — read back by the Enter
  // handler below to tell "a pick happened" apart from "nothing happened at all."
  const pickedRef = React.useRef(false);
  const hintTimerRef = React.useRef<number | undefined>(undefined);
  // Adversarial-review finding: the Enter-triggered geocode fallback below captures the typed
  // text at keydown time and resolves it asynchronously (300ms delay + a real network round
  // trip). Without a supersede guard, a user who kept typing something ELSE right after
  // pressing Enter could have that stale, now-irrelevant geocode result silently clobber their
  // newer input once it finally resolved. Bumped on every keystroke AND every Enter press —
  // same "only the newest attempt wins" pattern as runSam2's runIdRef elsewhere in this tree.
  const searchGenerationRef = React.useRef(0);

  React.useEffect(() => {
    if (initialText) setText(initialText);
  }, [initialText]);

  React.useEffect(() => () => window.clearTimeout(hintTimerRef.current), []);

  const emitPick = (lat: number, lng: number, address: string, parsed: ParsedAddress) => {
    setText(address);
    onPick(lat, lng, address, parsed);
  };

  const handlePlacesChanged = () => {
    const place = boxRef.current?.getPlaces()?.[0];
    if (!place?.geometry?.location) {
      // A genuine places_changed fired, but with nothing usable (e.g. a query-only result with
      // no resolvable location) — same user-facing outcome as no event firing at all.
      setNoMatchHint(true);
      return;
    }
    pickedRef.current = true;
    setNoMatchHint(false);
    const lat = place.geometry.location.lat();
    const lng = place.geometry.location.lng();
    const address = place.formatted_address || text;
    // This pick is now the newest — supersedes any Enter-key fallback still in flight for older
    // text, AND (adversarial-review finding) any earlier dropdown pick's own missing-components
    // geocode fallback still in flight below, so a late resolution from an ambiguous EARLIER pick
    // can never silently overwrite whatever a newer pick already correctly applied.
    const myGeneration = ++searchGenerationRef.current;

    if (place.address_components && place.address_components.length > 0) {
      emitPick(lat, lng, address, parseAddressComponents(place.address_components));
      return;
    }

    // Real, confirmed live gap (console-logged root cause, not guessed): StandaloneSearchBox's
    // own result can carry a genuine `formatted_address` while OMITTING `address_components`
    // entirely — a Google Places API data-completeness gap, not something this code controls.
    // Previously this silently produced an empty ParsedAddress, and CreateAsset_v3's setField
    // only writes a truthy value — so the questionnaire's Street Address/City/State/etc. fields
    // kept showing the PREVIOUS pick's stale values even though the map/pin/search box had all
    // moved to the new address. Re-resolve via a plain forward geocode of the formatted address
    // (same Geocoder pattern as the Enter-key fallback below) to get real components instead.
    if (isGoogleMapsGeocoderReady()) {
      new google.maps.Geocoder().geocode({ address }, (results, status) => {
        if (searchGenerationRef.current !== myGeneration) return; // superseded by a newer pick
        const better = results?.[0];
        const parsed = status === "OK" && better
          ? parseAddressComponents(better.address_components || [])
          : { address: "", city: "", state: "", country: "", zipCode: "" };
        emitPick(lat, lng, address, parsed);
      });
    } else {
      emitPick(lat, lng, address, { address: "", city: "", state: "", country: "", zipCode: "" });
    }
  };

  // StandaloneSearchBox only fires onPlacesChanged when the user explicitly SELECTS a
  // suggestion (click, or arrow-down-then-Enter onto a highlighted dropdown row) — pressing
  // Enter directly in the text box, with nothing selected, never resolves anything on its own,
  // even when the typed text is a real, geocodable address (e.g. editing "557" -> "533" in an
  // already-resolved address and pressing Enter before picking the new suggestion). Previously
  // this only showed a "no match" warning and left the address genuinely unresolved. Now: when
  // Enter is pressed and no Places pick lands shortly after, fall back to a plain forward
  // geocode of the raw typed text (the same google.maps.Geocoder API this codebase already uses
  // elsewhere, e.g. CreateAsset_v3.tsx's handleGeocode / AssetLocationPicker.tsx's
  // reverseGeocode) — if THAT resolves, treat it exactly like a real dropdown pick; only show
  // the warning when even that fails.
  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter") return;
    pickedRef.current = false;
    window.clearTimeout(hintTimerRef.current);
    const textAtEnter = text; // capture now — `text` may change before the timer fires
    const myGeneration = ++searchGenerationRef.current; // this Enter press is now the newest
    hintTimerRef.current = window.setTimeout(() => {
      if (pickedRef.current) return; // a real Places pick already landed
      if (searchGenerationRef.current !== myGeneration) return; // superseded — user typed since
      if (!textAtEnter.trim() || !isGoogleMapsGeocoderReady()) {
        setNoMatchHint(true);
        return;
      }
      new google.maps.Geocoder().geocode({ address: textAtEnter }, (results, status) => {
        if (pickedRef.current) return; // a real Places pick landed while this was in flight
        if (searchGenerationRef.current !== myGeneration) return; // superseded while in flight
        const place = results?.[0];
        if (status === "OK" && place?.geometry?.location) {
          const lat = place.geometry.location.lat();
          const lng = place.geometry.location.lng();
          const address = place.formatted_address || textAtEnter;
          pickedRef.current = true;
          setNoMatchHint(false);
          emitPick(lat, lng, address, parseAddressComponents(place.address_components || []));
        } else {
          setNoMatchHint(true);
        }
      });
    }, NO_MATCH_HINT_DELAY_MS);
  };

  const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    setText(event.target.value);
    setNoMatchHint(false); // editing again — the stale hint no longer applies
    // Supersede any pending Enter-triggered geocode fallback still in flight for older text —
    // it would otherwise be free to resolve later and silently overwrite what the user has
    // typed since. See searchGenerationRef's own comment for the full rationale.
    searchGenerationRef.current += 1;
  };

  return (
    <>
      <StandaloneSearchBox onLoad={(ref) => { boxRef.current = ref; }} onPlacesChanged={handlePlacesChanged}>
        <TextField
          fullWidth
          size="small"
          placeholder={t("asset.rooflens.searchBar.placeholder")}
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          error={noMatchHint}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" />
              </InputAdornment>
            ),
          }}
        />
      </StandaloneSearchBox>
      {noMatchHint && (
        <Typography variant="caption" color="error" sx={{ display: "block", mt: 0.5 }}>
          {t("asset.rooflens.searchBar.noMatch")}
        </Typography>
      )}
    </>
  );
};

export default RoofLensSearchBar;
