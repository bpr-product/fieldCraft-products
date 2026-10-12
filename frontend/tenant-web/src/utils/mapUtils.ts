export interface AssetAddress {
    address?: string;
    city?: string;
    state?: string;
    country?: string;
    zipCode?: string;
}

export interface GeocodeResult {
    location: string;
    geometry: google.maps.LatLng;
    fullAddressQuery: string;
}

export const manualGeocode = (
    assetData: AssetAddress
): Promise<GeocodeResult> => {
    return new Promise((resolve, reject) => {
        // 1. Validate input
        if (!assetData?.address && !assetData?.city) {
            return reject("Insufficient address data for geocoding");
        }

        // 2. Build full address
        const queryParts = [
            assetData.address,
            assetData.city,
            assetData.state,
            assetData.country,
            assetData.zipCode
        ].filter(part => part && part.trim() !== "");

        const fullAddressQuery = queryParts.join(", ");

        // 3. Call Google API
        const geocoder = new google.maps.Geocoder();

        geocoder.geocode({ address: fullAddressQuery }, (results, status) => {
            if (status === "OK" && results?.[0]) {
                const geometry = results[0].geometry.location;

                resolve({
                    location: `${geometry.lat()},${geometry.lng()}`,
                    geometry,
                    fullAddressQuery
                });
            } else {
                reject(`Geocoding failed: ${status}`);
            }
        });
    });
};

export const convertLocation = (locationString: any) => {
    if (!locationString) {
      return { lat: 0, lng: 0 };
    }

    let locationObject: any = {};
    let string = locationString;
    let st = string.split(",");
    locationObject["lat"] = Number(st[0]);
    locationObject["lng"] = Number(st[1]);
    return locationObject;
};

export const parseAddressComponents = (addressComponents: any[]) => {
    const fullAddress: string[] = [];
    const locationObject: any = {
        address: '',
        city: '',
        state: '',
        country: '',
        zipCode: '',
    };

    addressComponents.forEach((address: any) => {
        const types = address.types || [];

        if (
            types.includes("street_number") ||
            types.includes("route") ||
            types.includes("premise") ||
            types.includes("subpremise") ||
            types.includes("neighborhood") ||
            types.includes("sublocality") ||
            types.includes("sublocality_level_1") ||
            types.includes("sublocality_level_2") ||
            types.includes("plus_code")
        ) {
            fullAddress.push(address.long_name);
        }

        if (!locationObject.city && (
            types.includes("locality") ||
            types.includes("postal_town") ||
            types.includes("administrative_area_level_2")
        )) {
            locationObject.city = address.long_name;
        }

        if (!locationObject.state && types.includes("administrative_area_level_1")) {
            locationObject.state = address.long_name;
        }

        if (!locationObject.country && types.includes("country")) {
            locationObject.country = address.long_name;
        }

        if (!locationObject.zipCode && types.includes("postal_code")) {
            locationObject.zipCode = address.long_name;
        }
    });

    locationObject.address = fullAddress.join(" ").trim();
    return locationObject;
};

export const isGooglePlacesAvailable = () => {
    return (
      typeof window !== "undefined" &&
      typeof window.google !== "undefined" &&
      typeof google.maps !== "undefined" &&
      typeof google.maps.places !== "undefined"
    );
};

// True once the Google Maps JS API is loaded and the Geocoder constructor is available.
// Guard `new google.maps.Geocoder()` with this to avoid "Geocoder is not a constructor"
// when a handler runs before the Maps API has finished loading (shared by every
// CreateAsset* page's handleGeocode).
export const isGoogleMapsGeocoderReady = (): boolean => {
    return typeof window !== "undefined" && !!window.google?.maps?.Geocoder;
};

/**
 * Resolves a postal code the picked place did not carry, the way Create Asset does (RoofLensSearchBar):
 * a forward geocode of the place's formatted address, then a reverse geocode of its point. Autocomplete
 * often returns a street-level place with no `postal_code`, while the Geocoder's results do. Never
 * rejects: any failure resolves to '' so callers simply leave the field alone.
 */
export const lookupPostalCode = (
    formattedAddress: string | undefined,
    location: { lat: number; lng: number } | undefined,
): Promise<string> => {
    const zipFrom = (results: google.maps.GeocoderResult[] | null): string =>
        (results ?? []).map((r) => parseAddressComponents(r.address_components || []).zipCode).find(Boolean) ?? '';
    const geocode = (request: google.maps.GeocoderRequest): Promise<string> =>
        new Promise((resolve) => {
            try {
                new google.maps.Geocoder().geocode(request, (results, status) =>
                    resolve(status === 'OK' ? zipFrom(results) : ''));
            } catch {
                resolve('');
            }
        });
    return (async () => {
        if (!isGoogleMapsGeocoderReady()) return '';
        const forward = formattedAddress?.trim() ? await geocode({ address: formattedAddress }) : '';
        if (forward || !location) return forward;
        return geocode({ location });
    })();
};

const locationPair = (value: unknown): [number, number] | null => {
    let lat: number;
    let lng: number;
    if (typeof value === 'string') {
        const [a, b] = value.split(',');
        if (a === undefined || b === undefined || !a.trim() || !b.trim()) return null;
        lat = Number(a);
        lng = Number(b);
    } else if (value && typeof value === 'object') {
        const o = value as { lat?: unknown; lng?: unknown };
        lat = Number(o.lat);
        lng = Number(o.lng);
    } else {
        return null;
    }
    return Number.isFinite(lat) && Number.isFinite(lng) ? [lat, lng] : null;
};

/**
 * True when two stored locations ("lat,lng" strings or {lat,lng}) are the same point. Compared
 * numerically so spacing/format differences are not a change, while a dragged pin is.
 */
export const sameLocation = (a: unknown, b: unknown): boolean => {
    const x = locationPair(a);
    const y = locationPair(b);
    if (!x || !y) return !x && !y;
    return x[0] === y[0] && x[1] === y[1];
};
