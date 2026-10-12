"""
AKS CronJob entrypoint for REST API and database connectors.

Loads every non-example YAML config from EXTERNAL_CONFIG_DIR,
instantiates the appropriate connector, runs it, and exits.
File connectors are event-driven (Azure Event Grid) and are skipped here.

Usage:
    python -m external.scheduler.poll_runner
    python -m external.scheduler.poll_runner --source quickbooks
    python -m external.scheduler.poll_runner --type rest_api
"""
import argparse
import concurrent.futures
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG_DIR = os.environ.get("EXTERNAL_CONFIG_DIR", "external/config/sources")

# Wall-clock cap on a single connector's poll call. Sources run strictly
# sequentially (see main()), so with no per-connector bound a single slow or
# degraded partner API can consume the whole CronJob time budget before other
# sources even start. Override via PER_CONNECTOR_TIMEOUT_SECONDS.
PER_CONNECTOR_TIMEOUT_SECONDS = int(os.environ.get("PER_CONNECTOR_TIMEOUT_SECONDS", "300"))


def _run_connector(config) -> dict:
    from external.connectors.rest_api import RestApiConnector
    from external.connectors.db_connector import DbConnector

    if config.connector_type == "rest_api":
        return RestApiConnector(config).run()
    if config.connector_type == "db":
        return DbConnector(config).run()
    log.warning(
        "poll_runner: skipping %s (type=%s — file connectors are event-driven)",
        config.source_name, config.connector_type,
    )
    return {}


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )
    parser = argparse.ArgumentParser(description="FIELDCRAFT External Data Poll Runner")
    parser.add_argument("--source", help="Run only this source_name")
    parser.add_argument("--type",   help="Run only connectors of this type (rest_api|db)")
    args = parser.parse_args()

    from external.config.schema import load_configs_dir
    configs = load_configs_dir(CONFIG_DIR)

    if not configs:
        log.error("No connector configs found in %s", CONFIG_DIR)
        return 1

    if args.source:
        configs = [c for c in configs if c.source_name == args.source]
    if args.type:
        configs = [c for c in configs if c.connector_type == args.type]

    if not configs:
        log.warning("No connectors matched filters source=%s type=%s", args.source, args.type)
        return 0

    results, errors = [], 0
    for cfg in configs:
        log.info("Running %s/%s (%s)", cfg.source_name, cfg.source_entity, cfg.connector_type)
        # Fresh single-worker executor per source: on timeout we abandon (not
        # join) the hung worker thread via shutdown(wait=False) so a stuck
        # connector can't block the next source's turn from ever starting.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(_run_connector, cfg)
            result = future.result(timeout=PER_CONNECTOR_TIMEOUT_SECONDS)
            results.append(result)
            log.info("Completed %s: %s", cfg.source_name, result)
        except concurrent.futures.TimeoutError:
            log.error(
                "Connector %s timed out after %ds — skipping to next source",
                cfg.source_name, PER_CONNECTOR_TIMEOUT_SECONDS,
            )
            errors += 1
        except Exception as exc:
            log.exception("Connector %s failed: %s", cfg.source_name, exc)
            errors += 1
        finally:
            executor.shutdown(wait=False)

    log.info("Poll run complete — %d connectors, %d errors", len(configs), errors)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
