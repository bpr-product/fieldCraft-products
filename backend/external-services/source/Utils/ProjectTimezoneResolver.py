from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Iterable, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from timezonefinder import TimezoneFinder
except ImportError:  # pragma: no cover - exercised only by a broken deployment image
    TimezoneFinder = None  # type: ignore[assignment]


_DEFAULT_TIMEZONE_FINDER = (
    TimezoneFinder(in_memory=True) if TimezoneFinder is not None else None
)


@lru_cache(maxsize=4096)
def _cached_timezone_at(latitude: float, longitude: float) -> Optional[str]:
    if _DEFAULT_TIMEZONE_FINDER is None:
        return None
    return _DEFAULT_TIMEZONE_FINDER.timezone_at(
        lat=latitude,
        lng=longitude,
    )


class ProjectTimezoneResolutionError(ValueError):
    """Raised when a payroll-safe project timezone cannot be resolved."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProjectTimezoneResolution:
    timezone_name: str
    source: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)


class ProjectTimezoneResolver:
    """Resolve a project's IANA timezone from existing tenant data.

    Resolution is intentionally deterministic and isolated to the QuickBooks
    daily-entry flow. A stored mapping snapshot wins so retries do not change
    payroll dates after project data is edited. New records use, in order:

    1. project.jobsite_lat / project.jobsite_lng
    2. project.location when it contains latitude/longitude
    3. location/coordinate values already stored with project.customer_asset

    The project and customer-asset address fields are retained as audit context,
    but plain address text is never guessed into a payroll timezone. In the FIELDCRAFT
    project flow, project.location/customer_asset.location is the coordinate
    value produced from that address. No browser, server, UTC, fixed environment
    timezone, or runtime geocoding service is used.
    """

    SNAPSHOT_KEYS = (
        "dailyTimezone",
        "daily_timezone",
    )
    TRUSTED_SNAPSHOT_SOURCES = {
        "PROJECT_JOBSITE_COORDINATES",
        "PROJECT_LOCATION",
        "PROJECT_CUSTOMER_ASSET_LOCATION",
        "MAPPING_SNAPSHOT",
    }

    _LATITUDE_KEYS = (
        "latitude",
        "lat",
        "jobsiteLat",
        "jobsite_lat",
        "projectLatitude",
        "project_latitude",
    )
    _LONGITUDE_KEYS = (
        "longitude",
        "lng",
        "lon",
        "long",
        "jobsiteLng",
        "jobsite_lng",
        "projectLongitude",
        "project_longitude",
    )
    _LOCATION_KEYS = (
        "location",
        "coordinates",
        "coordinate",
        "latLng",
        "lat_lng",
        "geoLocation",
        "geo_location",
        "geolocation",
    )
    _NESTED_CONTAINER_KEYS = (
        "asset",
        "customerAsset",
        "customer_asset",
        "assetAddress",
        "asset_address",
        "addressDetails",
        "address_details",
        "jobsite",
        "site",
        "property",
    )

    # Standard "lat,lng", optional brackets/labels/spaces.
    _COORDINATE_PAIR_RE = re.compile(
        r"^\s*[\[(]?\s*(?:lat(?:itude)?\s*[:=]\s*)?"
        r"(?P<first>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*[,;| ]\s*"
        r"(?:lng|lon|long|longitude)?\s*[:=]?\s*"
        r"(?P<second>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*[\])]?\s*$",
        re.IGNORECASE,
    )
    # WKT stores longitude first: POINT(lng lat).
    _WKT_POINT_RE = re.compile(
        r"^\s*POINT\s*\(\s*"
        r"(?P<lng>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s+"
        r"(?P<lat>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*\)\s*$",
        re.IGNORECASE,
    )

    def __init__(self, finder: Any = None) -> None:
        self._finder = finder

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _json_object(cls, value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        raw = cls._clean(value)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _validated_timezone_name(value: Any) -> Optional[str]:
        name = str(value or "").strip()
        if not name:
            return None
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return None
        return name

    @staticmethod
    def _coordinate(value: Any, *, minimum: float, maximum: float) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if minimum <= number <= maximum else None

    @classmethod
    def _coordinate_pair(
        cls,
        latitude: Any,
        longitude: Any,
    ) -> Optional[Tuple[float, float]]:
        lat = cls._coordinate(latitude, minimum=-90.0, maximum=90.0)
        lng = cls._coordinate(longitude, minimum=-180.0, maximum=180.0)
        if lat is None or lng is None:
            return None
        return lat, lng

    @classmethod
    def _first_value(cls, data: Dict[str, Any], keys: Iterable[str]) -> Any:
        for key in keys:
            if key in data and data.get(key) not in (None, ""):
                return data.get(key)
        return None

    @classmethod
    def _coordinates_from_mapping(
        cls,
        value: Dict[str, Any],
    ) -> Optional[Tuple[float, float]]:
        latitude = cls._first_value(value, cls._LATITUDE_KEYS)
        longitude = cls._first_value(value, cls._LONGITUDE_KEYS)
        direct = cls._coordinate_pair(latitude, longitude)
        if direct:
            return direct

        for key in cls._LOCATION_KEYS:
            if value.get(key) not in (None, ""):
                # GeoJSON coordinates are longitude/latitude, unlike the FIELDCRAFT
                # location strings which are latitude/longitude.
                if (
                    key == "coordinates"
                    and str(value.get("type") or "").strip().lower() == "point"
                    and isinstance(value.get(key), (list, tuple))
                    and len(value.get(key)) >= 2
                ):
                    geojson = cls._coordinate_pair(
                        value.get(key)[1],
                        value.get(key)[0],
                    )
                    if geojson:
                        return geojson
                nested = cls._coordinates_from_value(value.get(key))
                if nested:
                    return nested

        geometry = value.get("geometry")
        if isinstance(geometry, dict):
            if (
                str(geometry.get("type") or "").strip().lower() == "point"
                and isinstance(geometry.get("coordinates"), (list, tuple))
                and len(geometry.get("coordinates")) >= 2
            ):
                geojson = cls._coordinate_pair(
                    geometry.get("coordinates")[1],
                    geometry.get("coordinates")[0],
                )
                if geojson:
                    return geojson
            nested = cls._coordinates_from_value(
                geometry.get("location") or geometry
            )
            if nested:
                return nested

        for key in cls._NESTED_CONTAINER_KEYS:
            nested_value = value.get(key)
            if isinstance(nested_value, (dict, list, tuple)):
                nested = cls._coordinates_from_value(nested_value)
                if nested:
                    return nested
        return None

    @classmethod
    def _coordinates_from_string(cls, value: str) -> Optional[Tuple[float, float]]:
        raw = value.strip()
        if not raw:
            return None

        # JSON text may contain a list, object, or quoted coordinate string.
        if raw[:1] in {"{", "[", '"'}:
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = None
            if parsed is not None:
                nested = cls._coordinates_from_value(parsed)
                if nested:
                    return nested

        wkt_match = cls._WKT_POINT_RE.match(raw)
        if wkt_match:
            return cls._coordinate_pair(
                wkt_match.group("lat"),
                wkt_match.group("lng"),
            )

        pair_match = cls._COORDINATE_PAIR_RE.match(raw)
        if not pair_match:
            return None
        return cls._coordinate_pair(
            pair_match.group("first"),
            pair_match.group("second"),
        )

    @classmethod
    def _coordinates_from_value(cls, value: Any) -> Optional[Tuple[float, float]]:
        if value is None:
            return None
        if isinstance(value, dict):
            return cls._coordinates_from_mapping(value)
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return cls._coordinate_pair(value[0], value[1])
        if isinstance(value, str):
            return cls._coordinates_from_string(value)
        return None

    @classmethod
    def _mapping_snapshot(cls, candidate: Dict[str, Any]) -> Optional[str]:
        reason = cls._json_object(candidate.get("reason"))
        source = cls._clean(
            reason.get("dailyTimezoneSource")
            or reason.get("daily_timezone_source")
        ).upper()
        for key in cls.SNAPSHOT_KEYS:
            raw = reason.get(key)
            if raw in (None, ""):
                continue
            timezone_name = cls._validated_timezone_name(raw)
            if not timezone_name:
                if source in cls.TRUSTED_SNAPSHOT_SOURCES:
                    raise ProjectTimezoneResolutionError(
                        "PROJECT_TIMEZONE_SNAPSHOT_INVALID",
                        f"The stored project timezone '{raw}' is not a valid IANA timezone.",
                    )
                # Unknown or legacy snapshots are not authoritative. Ignore
                # malformed values and recalculate from the live project data.
                continue
            # Older daily entries could contain a timezone derived from the
            # removed deployment environment. Only location-derived snapshots
            # with an explicit source are authoritative. Unknown/legacy values
            # are recalculated from the current existing project location once.
            if source in cls.TRUSTED_SNAPSHOT_SOURCES:
                return timezone_name
        return None

    @classmethod
    def _project_coordinates(
        cls,
        candidate: Dict[str, Any],
    ) -> Optional[Tuple[float, float, str]]:
        latitude = candidate.get("project_jobsite_lat")
        if latitude is None:
            latitude = candidate.get("jobsite_lat")
        longitude = candidate.get("project_jobsite_lng")
        if longitude is None:
            longitude = candidate.get("jobsite_lng")
        direct = cls._coordinate_pair(latitude, longitude)
        if direct:
            return direct[0], direct[1], "PROJECT_JOBSITE_COORDINATES"

        for key in ("project_location", "location"):
            location = cls._coordinates_from_value(candidate.get(key))
            if location:
                return location[0], location[1], "PROJECT_LOCATION"

        for key in ("project_customer_asset", "customer_asset"):
            asset = cls._json_object(candidate.get(key))
            asset_coordinates = cls._coordinates_from_mapping(asset) if asset else None
            if asset_coordinates:
                return (
                    asset_coordinates[0],
                    asset_coordinates[1],
                    "PROJECT_CUSTOMER_ASSET_LOCATION",
                )

        # Some repository/API callers flatten asset values into the row.
        flattened_asset = {
            key: candidate.get(key)
            for key in (
                "asset_location",
                "asset_latitude",
                "asset_longitude",
                "asset_lat",
                "asset_lng",
            )
            if candidate.get(key) not in (None, "")
        }
        if flattened_asset:
            asset_coordinates = cls._coordinates_from_mapping(
                {
                    "location": flattened_asset.get("asset_location"),
                    "latitude": flattened_asset.get("asset_latitude")
                    or flattened_asset.get("asset_lat"),
                    "longitude": flattened_asset.get("asset_longitude")
                    or flattened_asset.get("asset_lng"),
                }
            )
            if asset_coordinates:
                return (
                    asset_coordinates[0],
                    asset_coordinates[1],
                    "PROJECT_CUSTOMER_ASSET_LOCATION",
                )
        return None

    def _timezone_at(self, latitude: float, longitude: float) -> Optional[str]:
        if self._finder is not None:
            timezone_name = self._finder.timezone_at(
                lat=latitude,
                lng=longitude,
            )
        else:
            if _DEFAULT_TIMEZONE_FINDER is None:
                raise ProjectTimezoneResolutionError(
                    "PROJECT_TIMEZONE_RESOLVER_UNAVAILABLE",
                    "Project timezone resolution is unavailable because "
                    "timezonefinder is not installed.",
                )
            timezone_name = _cached_timezone_at(latitude, longitude)
        return self._validated_timezone_name(timezone_name)

    def resolve(self, candidate: Dict[str, Any]) -> ProjectTimezoneResolution:
        snapshot = self._mapping_snapshot(candidate)
        if snapshot:
            return ProjectTimezoneResolution(snapshot, "MAPPING_SNAPSHOT")

        coordinates = self._project_coordinates(candidate)
        if not coordinates:
            project_id = candidate.get("project_id") or "unknown"
            geocode_status = self._clean(candidate.get("project_geocode_status")) or "UNKNOWN"
            zipcode = self._clean(candidate.get("project_zipcode"))
            asset = self._json_object(candidate.get("project_customer_asset"))
            address_present = any(
                self._clean(asset.get(key))
                for key in ("address", "city", "state", "country", "zipCode", "zipcode")
            )
            context = [f"project {project_id}", f"geocode status {geocode_status}"]
            if zipcode:
                context.append(f"zipcode {zipcode}")
            if address_present:
                context.append("project address available but coordinate location missing")
            raise ProjectTimezoneResolutionError(
                "PROJECT_TIMEZONE_UNRESOLVED",
                "The project timezone could not be determined from the existing "
                "project.location, jobsite coordinates, or customer-asset location "
                f"({', '.join(context)}).",
            )

        latitude, longitude, source = coordinates
        timezone_name = self._timezone_at(latitude, longitude)
        if not timezone_name:
            project_id = candidate.get("project_id") or "unknown"
            raise ProjectTimezoneResolutionError(
                "PROJECT_TIMEZONE_UNRESOLVED",
                f"No IANA timezone could be resolved for project {project_id}.",
            )
        return ProjectTimezoneResolution(
            timezone_name=timezone_name,
            source=source,
            latitude=latitude,
            longitude=longitude,
        )
