/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 2026-06-22
 * @author      : Abirami R
 * @since       : 1.2.8.0
 * Requirement# : ADO 22357 — C9 cross-project proximity
 * Purpose      : Google Places autocomplete + draggable map-pin picker for an
 *                asset's (property's) exact job-site location. Captures precise
 *                lat/lng that the C9 proximity gate later acts on — more reliable
 *                than zip-centroid geocoding. Best-effort: if Maps is not loaded
 *                the form still works (the picker simply does not render).
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE         |   AUTHOR     |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * 22357          | 2026-06-22     | Abirami R    | Initial version
 * -----------------------------------------------------------------------------
 */

import * as React from 'react';
import { useTranslation } from 'react-i18next';
import { Box, InputAdornment, TextField, Typography } from '@mui/material';
import MapOutlinedIcon from '@mui/icons-material/MapOutlined';
import PlaceIcon from '@mui/icons-material/Place';
import { GoogleMap, MarkerF, StandaloneSearchBox } from '@react-google-maps/api';
import { useGoogleMaps } from '../../context/GoogleMapsContext';

import type { ParsedAddress } from '../../interface/AddressInterfaces';
// Kept as a re-export so the Create Asset pages and RoofLens map keep importing it from here.
export type { ParsedAddress };

interface AssetLocationPickerProps {
  /** Current location as a "lat,lng" string (may be empty). */
  value?: string;
  /** Fired whenever the user picks/moves the pin. locStr is "lat,lng". */
  onChange: (locStr: string, parsed?: ParsedAddress) => void;
}

const containerStyle = { width: '100%', height: '320px', borderRadius: '8px' };

// Continental-US centroid — used only until the user picks a location.
const DEFAULT_CENTER = { lat: 39.8283, lng: -98.5795 };

function parseLatLng(value?: string): { lat: number; lng: number } | null {
  if (!value || !value.includes(',')) return null;
  const [latStr, lngStr] = value.split(',');
  const lat = Number(latStr);
  const lng = Number(lngStr);
  if (Number.isNaN(lat) || Number.isNaN(lng)) return null;
  if (Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
  return { lat, lng };
}

function parseAddressComponents(addressComponents: any[] = []): ParsedAddress {
  const fullAddress: string[] = [];
  const result: ParsedAddress = { address: '', city: '', state: '', country: '', zipCode: '' };

  addressComponents.forEach((component: any) => {
    const types: string[] = component.types || [];
    if (
      types.includes('street_number') || types.includes('route') ||
      types.includes('premise') || types.includes('subpremise') ||
      types.includes('neighborhood') || types.includes('sublocality') ||
      types.includes('sublocality_level_1') || types.includes('sublocality_level_2')
    ) {
      fullAddress.push(component.long_name);
    }
    if (!result.city && (types.includes('locality') || types.includes('postal_town') ||
        types.includes('administrative_area_level_2'))) {
      result.city = component.long_name;
    }
    if (!result.state && types.includes('administrative_area_level_1')) {
      result.state = component.long_name;
    }
    if (!result.country && types.includes('country')) {
      result.country = component.long_name;
    }
    if (!result.zipCode && types.includes('postal_code')) {
      result.zipCode = component.long_name;
    }
  });

  result.address = fullAddress.join(' ').trim();
  return result;
}

const AssetLocationPicker: React.FC<AssetLocationPickerProps> = ({ value, onChange }) => {
  const { t } = useTranslation();
  const { isLoaded } = useGoogleMaps();
  const initial = parseLatLng(value);

  const [marker, setMarker] = React.useState<{ lat: number; lng: number } | null>(initial);
  // Text shown in the search box — kept in sync with the resolved address so it
  // always reflects the current pin (not just what the user first typed).
  const [searchText, setSearchText] = React.useState<string>('');
  const mapRef = React.useRef<google.maps.Map | null>(null);
  const searchBoxRef = React.useRef<google.maps.places.SearchBox | null>(null);

  // Keep the marker in sync if a value arrives after mount (e.g. edit flow), and
  // reverse-geocode it once so the search box shows the saved address.
  React.useEffect(() => {
    const parsed = parseLatLng(value);
    if (!parsed) return;
    setMarker(parsed);
    if (window.google?.maps) {
      new google.maps.Geocoder().geocode({ location: parsed }, (results, status) => {
        if (status === 'OK' && results?.[0]) {
          setSearchText(results[0].formatted_address || '');
        }
      });
    }
  }, [value]);

  const applyCoords = (lat: number, lng: number, parsed?: ParsedAddress) => {
    setMarker({ lat, lng });
    mapRef.current?.panTo({ lat, lng });
    onChange(`${lat},${lng}`, parsed);
  };

  // Reverse-geocode a dropped/dragged pin to surface the matched address —
  // and update the search box so it always reflects the current pin.
  const reverseGeocode = (lat: number, lng: number) => {
    const geocoder = new google.maps.Geocoder();
    geocoder.geocode({ location: { lat, lng } }, (results, status) => {
      if (status === 'OK' && results?.[0]) {
        setSearchText(results[0].formatted_address || '');
        applyCoords(lat, lng, parseAddressComponents(results[0].address_components || []));
      } else {
        applyCoords(lat, lng);
      }
    });
  };

  const handlePlacesChanged = () => {
    const places = searchBoxRef.current?.getPlaces();
    const place = places?.[0];
    if (!place?.geometry?.location) return;
    const lat = place.geometry.location.lat();
    const lng = place.geometry.location.lng();
    if (mapRef.current) {
      mapRef.current.setZoom(17);
    }
    setSearchText(place.formatted_address || '');
    applyCoords(lat, lng, parseAddressComponents(place.address_components || []));
  };

  const handleMapClick = (e: google.maps.MapMouseEvent) => {
    if (!e.latLng) return;
    reverseGeocode(e.latLng.lat(), e.latLng.lng());
  };

  const handleMarkerDragEnd = (e: google.maps.MapMouseEvent) => {
    if (!e.latLng) return;
    reverseGeocode(e.latLng.lat(), e.latLng.lng());
  };

  if (!isLoaded) {
    return (
      <Box sx={{ my: 2 }}>
        <Typography variant="subtitle2" fontWeight={600} sx={{ mb: 1, display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <PlaceIcon fontSize="small" color="primary" />
          {t('asset.locationPicker.jobSiteLocation')}
        </Typography>
        <Box
          sx={{
            height: 120,
            borderRadius: '8px',
            border: '1px dashed',
            borderColor: 'grey.300',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Typography variant="caption" color="text.secondary">
            {t('asset.rooflens.loadingMap')}
          </Typography>
        </Box>
      </Box>
    );
  }

  return (
    <Box sx={{ my: 2 }}>
      <Typography variant="subtitle2" fontWeight={600} sx={{ mb: 1, display: 'flex', alignItems: 'center', gap: 0.5 }}>
        <PlaceIcon fontSize="small" color="primary" />
        {t('asset.locationPicker.jobSiteLocation')}
      </Typography>

      <StandaloneSearchBox
        onLoad={(ref) => { searchBoxRef.current = ref; }}
        onPlacesChanged={handlePlacesChanged}
      >
        <TextField
          fullWidth
          size="small"
          placeholder={t('asset.rooflens.searchBar.placeholder')}
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <MapOutlinedIcon fontSize="small" />
              </InputAdornment>
            ),
          }}
          sx={{ mb: 1 }}
        />
      </StandaloneSearchBox>

      <GoogleMap
        mapContainerStyle={containerStyle}
        center={marker ?? DEFAULT_CENTER}
        zoom={marker ? 17 : 4}
        onLoad={(map) => { mapRef.current = map; }}
        onUnmount={() => { mapRef.current = null; }}
        onClick={handleMapClick}
        options={{ streetViewControl: false, mapTypeControl: false }}
      >
        {marker && (
          <MarkerF position={marker} draggable onDragEnd={handleMarkerDragEnd} />
        )}
      </GoogleMap>

      <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
        {marker
          ? t('asset.locationPicker.pinned', { lat: marker.lat.toFixed(5), lng: marker.lng.toFixed(5) })
          : t('asset.locationPicker.searchHint')}
      </Typography>
    </Box>
  );
};

export default AssetLocationPicker;
