import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from source.dal.QboBlobConfigStore import QboBlobConfigStore


@dataclass(frozen=True)
class CoaMappedTypes:
    qbo_account_type: str
    qbo_detail_type: Optional[str]
    used_swap: bool
    raw_account_type: str
    raw_detail_type: str


class QuickBooksMappingService:
    """Generic QuickBooks mapping/config service.

    Today this handles COA mapping, and it is intentionally placed in Utils so
    future QuickBooks mapping types can reuse the same service file.
    """

    CACHE_TTL_SECONDS = 300

    def __init__(self) -> None:
        self._store = QboBlobConfigStore()
        self._cached_at_by_key: Dict[str, float] = {}
        self._cached_cfg_by_key: Dict[str, Dict[str, Any]] = {}
        self._last_good_cfg_by_key: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _normalize(value: Any) -> str:
        return str(value or "").strip().lower()

    def _get_cached_config(self, cache_key: str, *, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        now = time.time()
        cached_cfg = self._cached_cfg_by_key.get(cache_key)
        cached_at = self._cached_at_by_key.get(cache_key, 0.0)

        if (not force_refresh) and cached_cfg and (now - cached_at) < self.CACHE_TTL_SECONDS:
            return cached_cfg

        return None

    def _set_cached_config(self, cache_key: str, config: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        self._cached_cfg_by_key[cache_key] = config
        self._cached_at_by_key[cache_key] = now
        self._last_good_cfg_by_key[cache_key] = config
        return config

    def _get_last_good_config(self, cache_key: str) -> Optional[Dict[str, Any]]:
        return self._last_good_cfg_by_key.get(cache_key)

    def _validate_and_prepare_coa_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(config, dict):
            raise ValueError("COA mapping JSON must be an object")

        account_type_map = config.get("accountTypeMap")
        detail_type_map = config.get("detailTypeMap")
        if not isinstance(account_type_map, dict) or not isinstance(detail_type_map, dict):
            raise ValueError("COA mapping JSON must include accountTypeMap and detailTypeMap objects")

        normalized_account_type_map: Dict[str, str] = {}
        for key, value in account_type_map.items():
            normalized_key = self._normalize(key)
            if normalized_key:
                normalized_account_type_map[normalized_key] = str(value or "").strip()

        normalized_detail_type_map: Dict[str, str] = {}
        for key, value in detail_type_map.items():
            normalized_key = self._normalize(key)
            if normalized_key:
                normalized_detail_type_map[normalized_key] = str(value or "").strip() if value is not None else ""

        return {
            "swapFields": bool(config.get("swapFields", True)),
            "accountTypeMap": normalized_account_type_map,
            "detailTypeMap": normalized_detail_type_map,
        }

    def _load_local_coa_config(self) -> Optional[Dict[str, Any]]:
        candidates = []
        env_path = os.getenv("QBO_COA_MAPPING_PATH")
        if env_path:
            candidates.append(Path(env_path))
        here = Path(__file__).resolve()
        candidates.extend([
            here.parents[3] / "cao-mapping.json",
            here.parents[2] / "cao-mapping.json",
            Path.cwd() / "cao-mapping.json",
        ])
        seen = set()
        for path in candidates:
            try:
                resolved = str(path.resolve())
            except Exception:
                resolved = str(path)
            if resolved in seen:
                continue
            seen.add(resolved)
            if not path.exists() or not path.is_file():
                continue
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        return None

    def get_coa_config(self, *, force_refresh: bool = False) -> Dict[str, Any]:
        cache_key = "coa"
        cached = self._get_cached_config(cache_key, force_refresh=force_refresh)
        if cached:
            return cached

        raw_config = self._load_local_coa_config()
        if not raw_config:
            raw_config = self._store.try_get_json()
        if not raw_config:
            raise FileNotFoundError("COA mapping config not found in local file or blob storage")

        prepared = self._validate_and_prepare_coa_config(raw_config)
        return self._set_cached_config(cache_key, prepared)

    def try_get_coa_config(self, *, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        cache_key = "coa"
        try:
            return self.get_coa_config(force_refresh=force_refresh)
        except Exception:
            print("[QuickBooksMappingService] WARN: COA mapping config unavailable", flush=True)
            return self._get_last_good_config(cache_key)

    def map_fieldcraft_coa_to_qbo(self, *, account_type: str, detail_type: str) -> Optional[CoaMappedTypes]:
        config = self.try_get_coa_config()
        if not config:
            return None

        raw_account_type = str(account_type or "").strip()
        raw_detail_type = str(detail_type or "").strip()

        account_type_key = self._normalize(raw_account_type)
        detail_type_key = self._normalize(raw_detail_type)

        account_type_map: Dict[str, str] = config["accountTypeMap"]
        detail_type_map: Dict[str, str] = config["detailTypeMap"]
        swap_first = bool(config.get("swapFields", True))

        def _try_map(local_account_type_key: str, local_detail_type_key: str) -> Optional[Tuple[str, Optional[str]]]:
            qbo_account_type = (account_type_map.get(local_account_type_key) or "").strip()
            qbo_detail_type = (detail_type_map.get(local_detail_type_key) or "").strip()
            if not qbo_account_type:
                return None
            return qbo_account_type, (qbo_detail_type or None)

        if swap_first:
            mapped = _try_map(self._normalize(raw_detail_type), self._normalize(raw_account_type))
            if mapped:
                return CoaMappedTypes(
                    qbo_account_type=mapped[0],
                    qbo_detail_type=mapped[1],
                    used_swap=True,
                    raw_account_type=raw_account_type,
                    raw_detail_type=raw_detail_type,
                )

            mapped = _try_map(account_type_key, detail_type_key)
            if mapped:
                return CoaMappedTypes(
                    qbo_account_type=mapped[0],
                    qbo_detail_type=mapped[1],
                    used_swap=False,
                    raw_account_type=raw_account_type,
                    raw_detail_type=raw_detail_type,
                )
        else:
            mapped = _try_map(account_type_key, detail_type_key)
            if mapped:
                return CoaMappedTypes(
                    qbo_account_type=mapped[0],
                    qbo_detail_type=mapped[1],
                    used_swap=False,
                    raw_account_type=raw_account_type,
                    raw_detail_type=raw_detail_type,
                )

            mapped = _try_map(self._normalize(raw_detail_type), self._normalize(raw_account_type))
            if mapped:
                return CoaMappedTypes(
                    qbo_account_type=mapped[0],
                    qbo_detail_type=mapped[1],
                    used_swap=True,
                    raw_account_type=raw_account_type,
                    raw_detail_type=raw_detail_type,
                )

        return None

    @staticmethod
    def qbo_coa_pair_equals(
        *,
        mapped: CoaMappedTypes,
        qbo_account_type: str,
        qbo_detail_type: Optional[str],
    ) -> bool:
        account_type = (qbo_account_type or "").strip()
        detail_type = (qbo_detail_type or "").strip() or None
        return (mapped.qbo_account_type == account_type) and ((mapped.qbo_detail_type or None) == detail_type)