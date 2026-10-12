"""
Kafka client authentication helpers.

This module exposes one function that returns the security properties needed
to authenticate to Kafka. The same code path works for local PLAINTEXT Kafka
and Azure Event Hubs SASL_SSL — the difference is purely environment-driven.

Env vars consumed (all optional; PLAINTEXT when unset):
    ETL_KAFKA_SECURITY_PROTOCOL   PLAINTEXT | SASL_PLAINTEXT | SASL_SSL | SSL
    ETL_KAFKA_SASL_MECHANISM      PLAIN | SCRAM-SHA-256 | SCRAM-SHA-512 | OAUTHBEARER
    ETL_KAFKA_SASL_USERNAME       SASL username (for Event Hubs use the literal
                                  string "$ConnectionString")
    ETL_KAFKA_SASL_PASSWORD       SASL password (for Event Hubs use the full
                                  namespace connection string starting with
                                  "Endpoint=sb://...;SharedAccessKey=...")

Two helpers are provided because Flink's KafkaSource and confluent-kafka use
different config key shapes:

    flink_kafka_props()       -> dict suitable for KafkaSource.set_property(...)
    confluent_kafka_props()   -> dict suitable for confluent_kafka.{Producer,Consumer,AdminClient}

The Flink form needs `sasl.jaas.config` as a single JAAS string. The
confluent-kafka form takes `sasl.username` and `sasl.password` flat.
"""

from __future__ import annotations

import os


def _protocol() -> str:
    return os.environ.get("ETL_KAFKA_SECURITY_PROTOCOL", "PLAINTEXT").upper()


# ── Connection resilience tuning for Azure Event Hubs ────────────────────────
# Event Hubs over SASL_SSL (especially from outside the Azure region, e.g. local
# dev over the public internet) intermittently drops/slow-starts sockets. With the
# Java-client defaults (socket setup timeout 10s, idle 9min) the Flink consumer
# can get stuck retrying "socket connection setup timeout" and stop fetching,
# stalling the pipeline. These raise the setup/request timeouts, back off
# reconnects, and keep connections below the Event Hubs idle limit. Each value is
# env-overridable. Applied only for SASL_SSL.

def _flink_eventhubs_tuning() -> dict[str, str]:
    """Java Kafka client (Flink KafkaSource/Sink) connection-resilience props."""
    return {
        "socket.connection.setup.timeout.ms":     os.environ.get("ETL_KAFKA_SOCKET_SETUP_TIMEOUT_MS", "30000"),
        "socket.connection.setup.timeout.max.ms": os.environ.get("ETL_KAFKA_SOCKET_SETUP_TIMEOUT_MAX_MS", "120000"),
        "request.timeout.ms":                     os.environ.get("ETL_KAFKA_REQUEST_TIMEOUT_MS", "60000"),
        "default.api.timeout.ms":                 os.environ.get("ETL_KAFKA_DEFAULT_API_TIMEOUT_MS", "60000"),
        "connections.max.idle.ms":                os.environ.get("ETL_KAFKA_CONNECTIONS_MAX_IDLE_MS", "180000"),
        "metadata.max.age.ms":                    os.environ.get("ETL_KAFKA_METADATA_MAX_AGE_MS", "180000"),
        "reconnect.backoff.ms":                   os.environ.get("ETL_KAFKA_RECONNECT_BACKOFF_MS", "1000"),
        "reconnect.backoff.max.ms":               os.environ.get("ETL_KAFKA_RECONNECT_BACKOFF_MAX_MS", "10000"),
        "retry.backoff.ms":                       os.environ.get("ETL_KAFKA_RETRY_BACKOFF_MS", "1000"),
        # Small, fast fetches for higher-latency / lower-bandwidth links (e.g. a
        # local WSL box reaching Azure Event Hubs over the public internet). The
        # Java consumer defaults (fetch.max.bytes≈52 MB, max.partition.fetch.bytes
        # 1 MB) produce large fetch responses that can take longer than
        # request.timeout.ms to transfer over a thin link — the in-flight FETCH is
        # then cancelled and the consumer reconnects from scratch, collapsing
        # throughput. Bounding each fetch keeps responses small enough to complete
        # well within the timeout, so the connection stays warm and the consumer
        # makes steady progress. In-region (AKS) these limits are harmless — the
        # link is fast — so they are safe defaults, not just a local workaround.
        "fetch.max.bytes":                        os.environ.get("ETL_KAFKA_FETCH_MAX_BYTES",            "2097152"),    # 2 MB (was ~52 MB)
        "max.partition.fetch.bytes":              os.environ.get("ETL_KAFKA_MAX_PARTITION_FETCH_BYTES",  "131072"),     # 128 KB (was 1 MB)
        "fetch.max.wait.ms":                      os.environ.get("ETL_KAFKA_FETCH_MAX_WAIT_MS",          "500"),
        "max.poll.records":                       os.environ.get("ETL_KAFKA_MAX_POLL_RECORDS",           "500"),
    }


def _confluent_eventhubs_tuning() -> dict[str, str]:
    """librdkafka (confluent-kafka) connection-resilience props for Event Hubs."""
    return {
        "socket.timeout.ms":                  os.environ.get("ETL_KAFKA_SOCKET_TIMEOUT_MS", "60000"),
        "socket.connection.setup.timeout.ms": os.environ.get("ETL_KAFKA_SOCKET_SETUP_TIMEOUT_MS", "30000"),
        "connections.max.idle.ms":            os.environ.get("ETL_KAFKA_CONNECTIONS_MAX_IDLE_MS", "180000"),
        "metadata.max.age.ms":                os.environ.get("ETL_KAFKA_METADATA_MAX_AGE_MS", "180000"),
        "reconnect.backoff.ms":               os.environ.get("ETL_KAFKA_RECONNECT_BACKOFF_MS", "1000"),
        "reconnect.backoff.max.ms":           os.environ.get("ETL_KAFKA_RECONNECT_BACKOFF_MAX_MS", "10000"),
    }


def _mechanism() -> str:
    return os.environ.get("ETL_KAFKA_SASL_MECHANISM", "PLAIN").upper()


def _is_sasl() -> bool:
    return _protocol() in ("SASL_PLAINTEXT", "SASL_SSL")


def _is_ssl() -> bool:
    return _protocol() in ("SSL", "SASL_SSL")


def _sasl_user_pass() -> tuple[str, str]:
    user = os.environ.get("ETL_KAFKA_SASL_USERNAME", "")
    pwd  = os.environ.get("ETL_KAFKA_SASL_PASSWORD", "")
    if not user or not pwd:
        raise RuntimeError(
            "ETL_KAFKA_SECURITY_PROTOCOL is set to a SASL variant but "
            "ETL_KAFKA_SASL_USERNAME / ETL_KAFKA_SASL_PASSWORD are missing."
        )
    return user, pwd


def flink_kafka_props() -> dict[str, str]:
    """
    Return Kafka properties for Flink's KafkaSource / KafkaSink builder.

    Apply with:
        for k, v in flink_kafka_props().items():
            builder = builder.set_property(k, v)
    """
    props: dict[str, str] = {}

    protocol = _protocol()
    if protocol == "PLAINTEXT":
        return props

    props["security.protocol"] = protocol

    if _is_sasl():
        mech = _mechanism()
        props["sasl.mechanism"] = mech
        user, pwd = _sasl_user_pass()
        # IMPORTANT: flink-sql-connector-kafka is a shaded fat-jar — it relocates
        # the Kafka client classes under org.apache.flink.kafka.shaded.*. The JAAS
        # login module must reference the SHADED class name, or Flink throws
        # "No LoginModule found for org.apache.kafka.common.security.plain.PlainLoginModule".
        # Overridable via env in case a non-shaded connector jar is used.
        _SHADED = "org.apache.flink.kafka.shaded."
        if mech == "PLAIN":
            module = os.environ.get(
                "ETL_KAFKA_FLINK_LOGIN_MODULE",
                f"{_SHADED}org.apache.kafka.common.security.plain.PlainLoginModule",
            )
        elif mech in ("SCRAM-SHA-256", "SCRAM-SHA-512"):
            module = os.environ.get(
                "ETL_KAFKA_FLINK_LOGIN_MODULE",
                f"{_SHADED}org.apache.kafka.common.security.scram.ScramLoginModule",
            )
        else:
            # OAUTHBEARER and others — caller must provide a fully-formed JAAS
            # string via ETL_KAFKA_SASL_JAAS_CONFIG.
            jaas_override = os.environ.get("ETL_KAFKA_SASL_JAAS_CONFIG")
            if not jaas_override:
                raise RuntimeError(
                    f"ETL_KAFKA_SASL_MECHANISM={mech} requires "
                    f"ETL_KAFKA_SASL_JAAS_CONFIG to be set explicitly."
                )
            props["sasl.jaas.config"] = jaas_override
            return _add_ssl_props(props)

        # PLAIN / SCRAM — build JAAS string. Escape any embedded double quotes
        # in the password to avoid breaking the JAAS line.
        safe_pwd = pwd.replace('"', '\\"')
        props["sasl.jaas.config"] = (
            f'{module} required '
            f'username="{user}" password="{safe_pwd}";'
        )

    if _protocol() == "SASL_SSL":
        props.update(_flink_eventhubs_tuning())
    return _add_ssl_props(props)


def _add_ssl_props(props: dict[str, str]) -> dict[str, str]:
    if _is_ssl():
        # Identification algorithm matches Azure Event Hubs requirement.
        props.setdefault(
            "ssl.endpoint.identification.algorithm",
            os.environ.get("ETL_KAFKA_SSL_ENDPOINT_ID_ALG", "https"),
        )
    return props


def confluent_kafka_props() -> dict[str, str]:
    """
    Return security props in the flat-key form that confluent-kafka uses
    (sasl.username / sasl.password rather than JAAS).
    """
    props: dict[str, str] = {}

    protocol = _protocol()
    if protocol == "PLAINTEXT":
        return props

    props["security.protocol"] = protocol

    if _is_sasl():
        mech = _mechanism()
        props["sasl.mechanism"] = mech
        user, pwd = _sasl_user_pass()
        # confluent-kafka uses flat keys, not JAAS — works for PLAIN + SCRAM
        # mechanisms. OAUTHBEARER requires a token-refresh callback (out of
        # scope for this helper).
        props["sasl.username"] = user
        props["sasl.password"] = pwd

    if _is_ssl():
        props.setdefault(
            "ssl.endpoint.identification.algorithm",
            os.environ.get("ETL_KAFKA_SSL_ENDPOINT_ID_ALG", "https"),
        )

    if _protocol() == "SASL_SSL":
        props.update(_confluent_eventhubs_tuning())

    return props


def is_event_hubs() -> bool:
    """True iff Kafka client should connect to Azure Event Hubs (SASL_SSL + cs username)."""
    return (
        _protocol() == "SASL_SSL"
        and os.environ.get("ETL_KAFKA_SASL_USERNAME", "") == "$ConnectionString"
    )
