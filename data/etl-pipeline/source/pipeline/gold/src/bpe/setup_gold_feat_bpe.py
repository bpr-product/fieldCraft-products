"""Setup script for the gold_bpe database — Ball Park Estimate feature.

Creates the runtime collections for the ``gold_bpe`` database on the existing
Azure Cosmos DB for MongoDB vCore cluster.
No new cluster is provisioned.

Final approved collections:
  - model_config     — ML configuration singleton (OID mapping, features, weights)
  - encoder_catalog  — feature names and count for numpy array encoding
  - feature_store    — per-project engineered features + cost actuals
  - model_registry   — versioned model artifact registry

Idempotent — safe to re-run.

Env:
  MONGO_URI / KEY_VAULT_URL   resolved by shared.secret_resolver
  GOLD_DB_NAME / GOLD_BPE_DB_NAME   default "gold_bpe"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import OperationFailure

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHARED_SRC = str(_REPO_ROOT / "shared")
if _SHARED_SRC not in sys.path:
    sys.path.insert(0, _SHARED_SRC)
from secret_resolver import get_mongo_uri  # noqa: E402

# ---------------------------------------------------------------------------
# Final approved collections
# ---------------------------------------------------------------------------

FINAL_COLLECTIONS = {
    "model_config",
    "encoder_catalog",
    "feature_store",
    "model_registry",
}

# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _ensure_collection(db, name: str, validator: dict) -> None:
    """Create or update a collection with a $jsonSchema validator."""
    try:
        if name in db.list_collection_names():
            db.command({
                "collMod": name,
                "validator": validator,
                "validationLevel": "strict",
                "validationAction": "error",
            })
            print(f"[ok] updated validator on {db.name}.{name}")
        else:
            db.create_collection(
                name,
                validator=validator,
                validationLevel="strict",
                validationAction="error",
            )
            print(f"[ok] created {db.name}.{name}")
        _ensure_shard_key(db, name)
    except OperationFailure as exc:
        errmsg = exc.details.get("errmsg", str(exc)) if exc.details else str(exc)
        print(
            f"[warn] schema validator not applied on {name} ({exc.code}): {errmsg}",
            file=sys.stderr,
        )
        if name not in db.list_collection_names():
            db.create_collection(name)
            print(f"[ok] created {db.name}.{name} without server-side validator")


def _ensure_shard_key(db, collection_name: str, shard_key: str = "tenantId") -> None:
    # Default matches the FIELDCRAFT Gold document standard (camelCase tenantId) used
    # by every collection that holds per-tenant data, including feature_store
    # -- the previous "tenant_id" default targeted a field that doesn't exist
    # in any feature_store document, so the shard key silently applied to
    # nothing. model_config/encoder_catalog are tenant-agnostic singletons and
    # have neither field; the shardCollection call still no-ops for them.
    try:
        db.command({
            "shardCollection": f"{db.name}.{collection_name}",
            "key": {shard_key: "hashed"},
        })
        print(f"[ok] shard key '{shard_key}' set on {db.name}.{collection_name}")
    except Exception as exc:
        msg = str(exc)
        if "already sharded" in msg.lower() or "already exists" in msg.lower():
            print(f"[ok] shard key already set on {db.name}.{collection_name}")
        else:
            print(f"[warn] could not set shard key on {collection_name}: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# model_config — ML configuration singleton
# Schema matches Final.model_config
# ---------------------------------------------------------------------------

def setup_model_config(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_grain"],
            "properties": {
                "_grain":                  {"bsonType": "string"},
                "oidMapping":              {"bsonType": "object"},
                "features":                {"bsonType": "array"},
                "qa_weightage":            {"bsonType": "object"},
                "material_catalog":        {"bsonType": ["object", "null"]},
                "serviceDefinitions":      {"bsonType": "object"},
                "templateVersionSupport":  {"bsonType": "object"},
                "modelArtifact":           {"bsonType": "object"},
                "xgboostHyperparams":      {"bsonType": "object"},
                "tritonConfig":            {"bsonType": "object"},
                "ordinalEncoding":         {"bsonType": "object"},
                "blob_manifest":           {"bsonType": ["object", "null"]},
                "createdAt":               {"bsonType": ["string", "date", "null"]},
                "updatedAt":               {"bsonType": ["string", "date", "null"]},
            },
        }
    }
    _ensure_collection(db, "model_config", validator)
    coll = db["model_config"]
    coll.create_index([("_grain", ASCENDING)], unique=True, name="uq_grain")
    print("[ok] indexes ensured on model_config")


# ---------------------------------------------------------------------------
# encoder_catalog — feature names for numpy array encoding
# Schema matches Final.encoder_catalog
# ---------------------------------------------------------------------------

def setup_encoder_catalog(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["feature_names", "n_features"],
            "properties": {
                "feature_names": {"bsonType": "array"},
                "n_features":    {"bsonType": ["int", "long"]},
            },
        }
    }
    _ensure_collection(db, "encoder_catalog", validator)
    print("[ok] encoder_catalog ready")


# ---------------------------------------------------------------------------
# feature_store — per-project engineered features + cost actuals
# Schema matches Final.feature_store
# ---------------------------------------------------------------------------

def setup_feature_store(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["project_id"],
            "properties": {
                "project_id":           {"bsonType": ["int", "long", "string"]},
                "service":              {"bsonType": ["string", "null"]},
                "trade_oid":            {"bsonType": ["string", "null"]},
                "area":                 {"bsonType": ["int", "long", "double", "null"]},
                "stories":              {"bsonType": ["int", "long", "double", "null"]},
                "asset_type":           {"bsonType": ["string", "null"]},
                "zipcode":              {"bsonType": ["string", "null"]},
                "city":                 {"bsonType": ["string", "null"]},
                "state":                {"bsonType": ["string", "null"]},
                "services":             {"bsonType": ["array", "null"]},
                "n_services":           {"bsonType": ["int", "long", "null"]},
                "is_single_service":    {"bsonType": ["bool", "null"]},
                "q_template_version":   {"bsonType": ["int", "long", "null"]},
                "features":             {"bsonType": ["object", "null"]},
                "material_cost":        {"bsonType": ["double", "int", "long", "null"]},
                "worker_cost":          {"bsonType": ["double", "int", "long", "null"]},
                "tool_cost":            {"bsonType": ["double", "int", "long", "null"]},
                "overhead_cost":        {"bsonType": ["double", "int", "long", "null"]},
                "margin":               {"bsonType": ["double", "int", "long", "null"]},
                "total_cost":           {"bsonType": ["double", "int", "long", "null"]},
                "bom_items":            {"bsonType": ["array", "null"]},
                "actual_material_spend":{"bsonType": ["double", "int", "long", "null"]},
                "actual_labor_spend":   {"bsonType": ["double", "int", "long", "null"]},
                "data_quality":         {"bsonType": ["string", "null"]},
                "source_flags":         {"bsonType": ["object", "null"]},
                "refreshed_at":         {"bsonType": ["string", "date", "null"]},
                "sourceEnv":            {"bsonType": ["string", "null"]},
            },
        }
    }
    _ensure_collection(db, "feature_store", validator)
    coll = db["feature_store"]
    # Tenant-scoped unique key: project_id is only unique within a tenant.
    # Migrate the legacy single-field unique index (uq_project_id) so existing
    # collections drop the project-only constraint that blocked cross-tenant ids.
    try:
        coll.drop_index("uq_project_id")
        print("[ok] dropped legacy uq_project_id index on feature_store")
    except OperationFailure:
        pass  # absent on a fresh collection — nothing to migrate
    coll.create_index(
        [("tenantId", ASCENDING), ("project_id", ASCENDING), ("sourceEnv", ASCENDING)],
        unique=True,
        name="uq_tenant_project",
    )
    coll.create_index(
        [("trade_oid", ASCENDING)],
        name="ix_trade_oid",
    )
    coll.create_index(
        [("refreshed_at", DESCENDING)],
        name="ix_refreshed_at",
    )
    print("[ok] indexes ensured on feature_store")


# ---------------------------------------------------------------------------
# model_registry — versioned model artifact registry
# Schema matches Final.model_registry
# ---------------------------------------------------------------------------

def setup_model_registry(db) -> None:
    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["version", "status"],
            "properties": {
                "version":        {"bsonType": "string"},
                "status":         {"bsonType": "string"},
                "deployedAt":     {"bsonType": ["string", "date", "null"]},
                "retiredAt":      {"bsonType": ["string", "date", "null"]},
                "trainedAt":      {"bsonType": ["string", "date", "null"]},
                "trainedBy":      {"bsonType": ["string", "null"]},
                "trainingData":   {"bsonType": ["object", "null"]},
                "features":       {"bsonType": ["array", "null"]},
                "nFeatures":      {"bsonType": ["int", "long", "null"]},
                "targets":        {"bsonType": ["object", "null"]},
                "populationStats":{"bsonType": ["object", "null"]},
                "hyperparams":    {"bsonType": ["object", "null"]},
                "inference":      {"bsonType": ["object", "null"]},
                "encoderVersion": {"bsonType": ["string", "null"]},
                "notes":          {"bsonType": ["string", "null"]},
            },
        }
    }
    _ensure_collection(db, "model_registry", validator)
    coll = db["model_registry"]
    coll.create_index([("version", ASCENDING)], unique=True, name="uq_version")
    coll.create_index([("status", ASCENDING)], name="ix_status")
    print("[ok] indexes ensured on model_registry")


# ---------------------------------------------------------------------------
# Seed functions — model_config and encoder_catalog
# ---------------------------------------------------------------------------

_MODEL_CONFIG_DEFAULTS: dict = {
    "_grain": "bpe_config",
    "oidMapping": {
        "I.34": "area",
        "I.6":  "area",
        "I.2":  "area",
        "I.66": "job_type_raw",
        "I.1":  "roof_material",
        "I.5":  "roof_age",
        "I.7":  "defect_flags",
        "I.8":  "defect_area_pct",
        "I.10": "defect_shingles",
        "I.11": "defect_shingles_flex",
        "I.12": "defect_granules",
        "I.13": "defect_leaks",
        "I.15": "defect_leaks_area",
        "I.16": "defect_flashing",
        "I.17": "defect_flashing_cond",
        "I.18": "moss_level",
        "I.19": "debris_level",
        "I.50": "algae_level",
        "I.65": "maintenance_program",
        "I.24": "skylight_material",
        "I.25": "skylight_condition",
        "I.26": "vent_condition",
        "I.28": "pipe_condition",
        "I.35": "skylight_count",
        "I.37": "vent_count",
        "I.38": "has_pipes",
        "I.55": "pipe_count",
        "I.51": "has_solar",
        "I.52": "solar_pct",
        "I.53": "has_chimneys",
        "I.54": "chimney_count",
        "I.56": "predominant_pitch",
        "I.57": "drip_edge",
        "I.58": "parapet_walls",
        "I.59": "flashing_linear",
        "I.60": "step_flashing",
        "I.39": "LengthRidge",
        "I.40": "LengthEave",
        "I.41": "LengthValley",
        "I.42": "LengthRake",
        "I.43": "LengthHip",
        "I.67": "total_roof_area",
        "I.68": "waste_percentage",
    },
    "features": [
        "I.2", "I.6", "I.66",
        "I.7", "I.8", "I.10", "I.11", "I.12", "I.13", "I.15", "I.16", "I.17",
        "I.19", "I.50", "I.18",
        "I.1", "I.5",
        "Pitch", "TotalRoofFacets",
        "LengthRidge", "LengthEave", "LengthValley", "LengthRake", "LengthHip",
        "I.24", "I.25", "I.35", "I.26", "I.37", "I.28", "I.38", "I.55",
        "I.51", "I.52", "I.53", "I.54",
        "I.56", "I.57", "I.58", "I.59", "I.60", "I.67", "I.68", "I.65",
        "I.20",
    ],
    "ordinalEncoding": {
        "tuneup": 1.0, "cleaning": 2.0, "enhancement": 3.0,
        "none": 0.0, "minor": 1.0, "moderate": 2.0, "major": 3.0,
        "good": 1.0, "fair": 2.0, "poor": 3.0,
        "light": 1.0, "medium": 2.0, "heavy": 3.0,
        "asphalt": 1.0, "wood": 1.5, "metal": 2.0, "tile": 3.0,
        "flat": 0.0, "low": 1.0, "sloped": 2.0, "standard": 2.0, "steep": 3.0,
        "4/12": 2.0, "5/12": 2.5, "6/12": 3.0, "7-8/12": 3.5, "<3/12": 0.5,
        "7–8/12": 3.5,
        "yes": 1.0, "no": 0.0, "true": 1.0, "false": 0.0,
        "present": 1.0, "absent": 0.0,
        "damaged": 1.0, "cracked": 1.0, "broken": 1.0, "curled": 1.0, "leaking": 1.0,
    },
    "qa_weightage": {
        "I.66": {"TuneUp": 0.55, "Cleaning": 0.72, "Enhancement": 1.00, "1": 0.55, "2": 0.72, "3": 1.00},
        "I.1":  {"Asphalt": 1.00, "Metal": 1.22, "Tile": 1.40, "Wood": 1.15, "1": 1.00, "2": 1.22, "3": 1.40, "4": 1.15},
        "I.2":  {"Sloped": 1.00, "Flat": 0.90, "1": 1.00, "2": 0.90},
        "I.5":  {"Less than 10 years": 0.85, "10–20 years ": 1.00, "10-20 years": 1.00, "Over 20 years": 1.15, "Less": 0.85, "Over": 1.15, "1": 0.85, "2": 1.00, "3": 1.15},
        "I.10": {"significantly curled": 1.08, "Slightly Curved": 1.03, "significantly": 1.08, "Slightly": 1.03, "1": 1.08, "2": 1.03},
        "I.11": {"100%": 1.00, "30-75%": 1.05, "0-30%": 1.10, "1": 1.00, "2": 1.05, "3": 1.10},
        "I.12": {"Granule coverage 70–100%": 1.00, "Granule coverage 70-100%": 1.00, "Granule coverage less than 70%": 1.08, "less": 1.08, "1": 1.00, "2": 1.08},
        "I.15": {"Only a Small Area": 1.05, "Multiple areas across the surface": 1.10, "Most or all of the surface": 1.20, "Small": 1.05, "Multiple": 1.10, "Most": 1.20, "1": 1.05, "2": 1.10, "3": 1.20},
        "I.17": {"Minor damage in flashing": 1.03, "Minor damage in flashing Major damage in flashing": 1.08, "Missing sections": 1.12, "Minor": 1.03, "Major": 1.08, "Missing": 1.12, "1": 1.03, "2": 1.08, "3": 1.12},
        "I.8":  {"0-30%": 1.00, "30-70%": 1.06, "70-100%": 1.12, "1": 1.00, "2": 1.06, "3": 1.12},
        "I.18": {"Only a Small Area ": 1.02, "Only a Small Area": 1.02, "Multiple areas across the roof": 1.05, "Most or all of the roof": 1.10, "Small": 1.02, "Multiple": 1.05, "Most": 1.10, "1": 1.02, "2": 1.05, "3": 1.10},
        "I.50": {"Only a Small Area ": 1.02, "Only a Small Area": 1.02, "Multiple areas across the roof": 1.05, "Most or all of the roof": 1.10, "Small": 1.02, "Multiple": 1.05, "Most": 1.10, "1": 1.02, "2": 1.05, "3": 1.10},
        "I.19": {"Only a Small Area": 1.00, "Multiple areas across the roof": 1.02, "Most or all of the roof": 1.05, "Small": 1.00, "Multiple": 1.02, "Most": 1.05, "1": 1.00, "2": 1.02, "3": 1.05},
        "I.38": {"Yes": 1.03, "No": 1.00, "True": 1.03, "False": 1.00, "1": 1.03, "2": 1.00},
        "I.28": {"Good": 1.00, "Fair": 1.02, "Poor": 1.05, "1": 1.00, "2": 1.02, "3": 1.05},
        "I.24": {"Glass": 1.08, "Acrylic": 1.04, "Polycarbonate": 1.00, "1": 1.08, "2": 1.04, "3": 1.00},
        "I.25": {"Good": 1.00, "Fair": 1.04, "Poor": 1.10, "1": 1.00, "2": 1.04, "3": 1.10},
        "I.26": {"Good": 1.00, "Fair": 1.02, "Poor": 1.06, "1": 1.00, "2": 1.02, "3": 1.06},
        "I.35": {"0": 1.00, "1": 1.03, "2": 1.06, "3": 1.09, "4": 1.12, "5": 1.15, "6": 1.15},
        "I.37": {"0": 1.00, "1": 1.01, "2": 1.02, "3": 1.03, "4": 1.04, "5": 1.05, "6": 1.05},
        "I.54": {"0": 1.00, "1": 1.05, "2": 1.10, "3": 1.15, "4": 1.15},
        "I.51": {"Yes": 1.08, "No": 1.00, "True": 1.08, "False": 1.00, "1": 1.08, "2": 1.00, "0": 1.00},
        "I.52": {"_thresholds": [[0, 1.00], [1, 1.02], [25, 1.05], [50, 1.08], [75, 1.12], [100, 1.15]]},
        "I.53": {"Yes": 1.05, "No": 1.00, "True": 1.05, "False": 1.00, "1": 1.05, "2": 1.00, "0": 1.00},
        "I.56": {"Flat": 0.90, "Low": 0.95, "Sloped": 1.05, "Standard": 1.05, "Steep": 1.18, "< 4:12 (moderate)": 1.00, "5:12 - 7:12 (standard)": 1.05, "8:12 - 11:12 (steep)": 1.18, "12:12+ (very steep)": 1.28, "moderate": 1.00, "steep": 1.18, "very": 1.28, "1": 1.00, "2": 1.05, "3": 1.18, "4": 1.28},
    },
    "material_catalog": {},
    "serviceDefinitions": {
        "Enhancement": {
            "priority": 1, "label": "Roof Enhancement",
            "description": "RoofMaxx rejuvenation treatment applied to restore roof flexibility and extend lifespan.",
            "benefits": ["Bio-oil treatment restores shingle flexibility and waterproofing", "Slows thermal cracking and UV degradation", "Extends roof life by up to 5 years per application", "Qualifies roof for RoofMaxx extended warranty"],
            "lifespan_gain_years": 12,
        },
        "Cleaning": {
            "priority": 2, "label": "Roof Cleaning",
            "description": "Soft-wash algae/moss removal — prepares surface for better treatment absorption.",
            "benefits": ["Removes algae and moss that trap moisture", "Improves RoofMaxx penetration depth by clearing surface contaminants", "Restores curb appeal before treatment", "Clears gutters and flushes roof debris"],
            "lifespan_gain_years": 8,
        },
        "TuneUp": {
            "priority": 3, "label": "Roof Tune-Up",
            "description": "Targeted repairs — flashing, fasteners, and pipe boots — to address wear points and unlock extended warranty.",
            "benefits": ["Seals active leak points at penetrations", "Replaces aged pipe boots with thermoplastic boots", "Re-fastens loose shingles and ridge caps", "Required for extended RoofMaxx warranty eligibility"],
            "lifespan_gain_years": 5,
        },
    },
    "templateVersionSupport": {
        "currentVersion": 6,
        "minSupportedVersion": 1,
        "versionFeatureMap": {},
    },
    "modelArtifact": {
        "version": "v0",
        "tritonUrl": "${TRITON_URL}",
        "gpu": True,
        "gpuFallbackToCpu": True,
        "lastTrainedAt": None,
        "features": [],
        "populationMeanTotalCost": 0.0,
    },
    "xgboostHyperparams": {
        "n_estimators": 400,
        "max_depth": 6,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_weight": 3,
        "device": "cuda",
        "tree_method": "hist",
        "random_state": 42,
    },
    "tritonConfig": {
        "maxBatchSize": 256,
        "preferredBatchSizes": [32, 64, 128, 256],
        "pollIntervalSecs": 10,
    },
    "blob_manifest": {
        "files": {},
        "last_sync_at": None,
        "sync_status": None,
    },
    "createdAt": None,
    "updatedAt": None,
}


def seed_model_config(db) -> None:
    """Seed gold_bpe.model_config singleton with ML config defaults.

    Idempotent — only seeds if document does not exist.
    ML team reads this at inference time via their ConfigManager.
    """
    coll = db["model_config"]
    existing = coll.find_one({"_grain": "bpe_config"})
    if existing:
        print("[skip] model_config already seeded — not overwriting")
        return

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    doc = {**_MODEL_CONFIG_DEFAULTS, "createdAt": now, "updatedAt": now}
    coll.insert_one(doc)
    print("[done] model_config seeded with defaults")


# ---------------------------------------------------------------------------
# qa_weightage bounds validation
# Mirrors the production BPE ConfigManager check — a stray multiplier (e.g. a
# fat-fingered 5.0) silently distorts every cost-range band, so we surface it.
# Non-fatal: violations are reported, never block setup.
# ---------------------------------------------------------------------------

QA_WEIGHTAGE_MIN = 0.5
QA_WEIGHTAGE_MAX = 3.0


def _iter_multipliers(value):
    """Yield numeric multipliers from a qa_weightage option value.

    Handles scalars and the I.52 ``_thresholds`` form ``[[pct, mult], ...]``;
    bools and non-numeric values are ignored.
    """
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield float(value)
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                try:
                    yield float(item[1])
                except (ValueError, TypeError):
                    continue


def validate_qa_weightage_bounds(
    qa_weightage: dict,
    lo: float = QA_WEIGHTAGE_MIN,
    hi: float = QA_WEIGHTAGE_MAX,
) -> list[str]:
    """Return human-readable violations for multipliers outside ``[lo, hi]``."""
    violations: list[str] = []
    for oid, options in (qa_weightage or {}).items():
        if not isinstance(options, dict):
            continue
        for label, val in options.items():
            for mult in _iter_multipliers(val):
                if mult < lo or mult > hi:
                    violations.append(f"{oid}.{label}={mult} outside [{lo}, {hi}]")
    return violations


def report_qa_weightage_validation(db) -> int:
    """Validate the live model_config.qa_weightage and print any violations.

    Returns the number of violations found (0 = clean). Never raises.
    """
    try:
        doc = db["model_config"].find_one({"_grain": "bpe_config"}) or {}
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[warn] could not read model_config for qa_weightage validation: {exc}",
              file=sys.stderr)
        return 0
    violations = validate_qa_weightage_bounds(doc.get("qa_weightage") or {})
    if violations:
        print(f"[warn] qa_weightage has {len(violations)} out-of-bounds multiplier(s):",
              file=sys.stderr)
        for v in violations:
            print(f"        - {v}", file=sys.stderr)
    else:
        print("[ok] qa_weightage multipliers within "
              f"[{QA_WEIGHTAGE_MIN}, {QA_WEIGHTAGE_MAX}]")
    return len(violations)


def seed_encoder_catalog(db) -> None:
    """Seed gold_bpe.encoder_catalog with feature metadata.

    ML team loads this to know feature order for numpy array encoding.
    Idempotent — only seeds if document does not exist.
    """
    coll = db["encoder_catalog"]
    existing = coll.find_one({})
    if existing:
        print("[skip] encoder_catalog already seeded — not overwriting")
        return

    feature_names = _MODEL_CONFIG_DEFAULTS["features"]
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    coll.insert_one({
        "feature_names": feature_names,
        "n_features":    len(feature_names),
        "schema_version": "2.0.0",
        "createdAt":     now,
        "updatedAt":     now,
    })
    print(f"[done] encoder_catalog seeded with {len(feature_names)} features")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    import logging
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        uri = get_mongo_uri()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    db_name = os.environ.get("GOLD_DB_NAME") or os.environ.get("GOLD_BPE_DB_NAME", "gold_bpe")

    client = MongoClient(uri)
    db = client[db_name]

    # Create / update all 4 final approved collections
    setup_model_config(db)
    setup_encoder_catalog(db)
    setup_feature_store(db)
    setup_model_registry(db)

    # Seed input collections with defaults (idempotent)
    seed_model_config(db)
    seed_encoder_catalog(db)

    # Validate the qa_weightage multipliers that drive the cost-range bands.
    report_qa_weightage_validation(db)

    print(f"[done] all {len(FINAL_COLLECTIONS)} collections ready in {db_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
