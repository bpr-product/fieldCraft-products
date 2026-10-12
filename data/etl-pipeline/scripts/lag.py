"""Show Kafka consumer lag for Bronze and Silver — run anytime."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Load .env.local when running on the host; inside the container env vars are already set.
_env = os.path.join(os.path.dirname(__file__), "..", ".env.local")
if os.path.exists(_env):
    for _ln in open(_env).read().splitlines():
        _ln = _ln.strip()
        if not _ln or _ln.startswith("#") or "=" not in _ln:
            continue
        _k, _v = _ln.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip("\"'"))

from confluent_kafka import Consumer, TopicPartition  # noqa: E402
from shared.kafka_utils.auth import confluent_kafka_props  # noqa: E402

BOOTSTRAP = os.environ.get("ETL_KAFKA_BOOTSTRAP_SERVERS", "")
ETL_ENV = os.environ.get("ETL_ENV", "local")
topic = f"{ETL_ENV}.cdc.events"

props = confluent_kafka_props()
c = Consumer({"bootstrap.servers": BOOTSTRAP, "group.id": "_lag_check", "enable.auto.commit": "false", **props})
md = c.list_topics(topic, timeout=10)
partitions = sorted(md.topics[topic].partitions.keys())
hi_total = 0
for p in partitions:
    _, hi = c.get_watermark_offsets(TopicPartition(topic, p), timeout=10)
    hi_total += hi
c.close()

print(f"Hub  {topic:<40} {hi_total:>12,} events  ({len(partitions)} partition(s))")
print()

for group, label in [("flink-bronze-sql-v1", "Bronze"), ("silver-universal-consumer", "Silver")]:
    c2 = Consumer({"bootstrap.servers": BOOTSTRAP, "group.id": group, "enable.auto.commit": "false", **props})
    committed_list = c2.committed([TopicPartition(topic, p) for p in partitions], timeout=10)
    c2.close()
    comm = sum(tp.offset for tp in committed_list if tp.offset >= 0)
    lag = max(0, hi_total - comm)
    pct = int(comm / hi_total * 100) if hi_total > 0 else 0
    status = "caught up" if lag == 0 else "snapshot in progress" if lag > 1000 else "processing"
    print(f"  {label:<8} committed={comm:>12,}  lag={lag:>12,}  {pct:>3}% done  [{status}]")
