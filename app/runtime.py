"""Bounded live telemetry; single process, no Mongo persistence."""

import json
from collections import deque
from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

CLUSTERS = ("mongo_1", "mongo_2", "mongo_3")
COLLECTIONS = ("categories", "products", "customers", "orders")
write_lock = RLock()


def cluster_for_position(position):
    if not isinstance(position, int) or isinstance(position, bool) or not 1 <= position <= 9:
        raise ValueError("La posición del bot debe estar entre 1 y 9")
    return CLUSTERS[(position - 1) // 3]


class LiveState:
    def __init__(self):
        self.lock = RLock()
        self.instance = str(uuid4())
        self.sequence = 0
        self.events = deque(maxlen=500)
        self.bots = {}
        self.etl = {"status": "unknown", "message": "Sin sincronización observada", "last_success": None}

    def emit(self, *, bot_id=None, operation, collection=None, cluster_id=None, status, message, **details):
        if status not in {"sent", "success", "deleted", "error"}:
            raise ValueError("Invalid event status")
        with self.lock:
            self.sequence += 1
            event = {
                "id": f"{self.instance}:{self.sequence}",
                "sequence": self.sequence,
                "bot_id": bot_id,
                "operation": operation,
                "collection": collection,
                "cluster_id": cluster_id,
                "status": status,
                "message": message,
                "timestamp": datetime.now(UTC).isoformat(),
                **details,
            }
            self.events.append(event)
            return deepcopy(event)

    def active_count(self):
        with self.lock:
            return sum(b["state"] in {"running", "stopping"} for b in self.bots.values())

    def assignments(self):
        """Pack active bots, ordered by ID, into groups of at most three."""
        with self.lock:
            active = sorted(
                (name for name, bot in self.bots.items() if bot["state"] in {"running", "stopping"}),
                key=lambda name: int(name.split("-")[1]),
            )
            return {name: cluster_for_position(i + 1) for i, name in enumerate(active)}

    def destination(self, bot_id=None):
        # Manual operator writes stay on cluster 1. Unmanaged authenticated bot
        # clients retain a deterministic ID-based route outside the dashboard.
        with self.lock:
            if bot_id is None:
                return "mongo_1"
            return self.assignments().get(bot_id) or cluster_for_position(int(bot_id.split("-")[1]))

    def snapshot(self):
        with self.lock:
            assignments = self.assignments()
            return {
                "instance_id": self.instance,
                "cursor": f"{self.instance}:{self.sequence}",
                "bots": {
                    name: {**deepcopy(bot), "cluster_id": assignments.get(name)}
                    for name, bot in self.bots.items()
                },
                "active_count": self.active_count(),
                "assignments": assignments,
                "cluster_loads": {cluster: list(assignments.values()).count(cluster) for cluster in CLUSTERS},
                "etl": deepcopy(self.etl),
                "recent": deepcopy(list(self.events)[-20:]),
            }

    def since(self, cursor):
        with self.lock:
            try:
                instance, sequence = cursor.rsplit(":", 1)
                sequence = int(sequence)
            except (ValueError, AttributeError):
                return None
            if instance != self.instance or sequence > self.sequence or sequence < 0:
                return None
            if self.events and sequence < self.events[0]["sequence"] - 1:
                return None
            return deepcopy([e for e in self.events if e["sequence"] > sequence])


live = LiveState()


def sse_frame(event, data, event_id=None):
    prefix = f"id: {event_id}\n" if event_id else ""
    return prefix + f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
