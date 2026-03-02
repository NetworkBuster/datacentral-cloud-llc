"""
NetworkBuster Software - Customer Database
Stores new customer records ingested from log entries.
"""

import json
import uuid
import threading
from pathlib import Path
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Customer:
    """Represents a customer record ingested from logs."""
    id: str
    name: str
    source: str                        # log file path or manual
    log_content: str                   # original log line that triggered creation
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Customer":
        return cls(
            id=d["id"],
            name=d["name"],
            source=d["source"],
            log_content=d["log_content"],
            created_at=d.get("created_at", datetime.now().isoformat()),
            metadata=d.get("metadata", {}),
        )


class CustomerDatabase:
    """
    Lightweight JSON-backed database for customer records.
    Customers are created from log entries produced by LogMonitor.
    """

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or Path("data/customers")
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._db_file = self.storage_path / "customers.json"
        self._records: Dict[str, Customer] = {}
        self._lock = threading.Lock()
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(
        self,
        name: str,
        source: str,
        log_content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Customer:
        """Add a new customer record and persist it."""
        customer_id = self._generate_id(name, source)
        customer = Customer(
            id=customer_id,
            name=name,
            source=source,
            log_content=log_content,
            metadata=metadata or {},
        )
        with self._lock:
            self._records[customer_id] = customer
        self._save()
        return customer

    def ingest_log_entry(self, entry: Any) -> Customer:
        """
        Convert a LogEntry (from core.log_monitor) into a Customer record
        and store it in the database.

        The log line is used as the customer name when no explicit name can be
        parsed; callers can pass a richer *entry* by sub-classing or simply by
        populating ``entry.content``.
        """
        name = _extract_name_from_log(entry.content)
        metadata = {
            "line_number": entry.line_number,
            "level": entry.level,
            "log_timestamp": entry.timestamp.isoformat() if hasattr(entry.timestamp, "isoformat") else str(entry.timestamp),
        }
        return self.add(
            name=name,
            source=entry.filepath,
            log_content=entry.content,
            metadata=metadata,
        )

    def get(self, customer_id: str) -> Optional[Customer]:
        """Return a customer by ID, or None if not found."""
        with self._lock:
            return self._records.get(customer_id)

    def list_all(self, limit: int = 100) -> List[Customer]:
        """Return all customers, newest first."""
        with self._lock:
            records = list(self._records.values())
        records.sort(key=lambda c: c.created_at, reverse=True)
        return records[:limit]

    def count(self) -> int:
        """Return total number of stored customers."""
        with self._lock:
            return len(self._records)

    def delete(self, customer_id: str) -> bool:
        """Remove a customer record. Returns True if deleted."""
        with self._lock:
            if customer_id not in self._records:
                return False
            del self._records[customer_id]
        self._save()
        return True

    def clear(self):
        """Remove all customer records."""
        with self._lock:
            self._records.clear()
        self._save()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_id(name: str, source: str) -> str:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        unique = uuid.uuid4().hex[:12]
        return f"cust_{ts}_{unique}"

    def _save(self):
        """Persist all records to disk."""
        try:
            with self._lock:
                data = [r.to_dict() for r in self._records.values()]
            self._db_file.write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            pass

    def _load(self):
        """Load records from disk on startup."""
        try:
            if self._db_file.exists():
                raw = json.loads(self._db_file.read_text(encoding="utf-8"))
                for item in raw:
                    c = Customer.from_dict(item)
                    self._records[c.id] = c
        except Exception:
            pass


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _extract_name_from_log(content: str) -> str:
    """
    Best-effort extraction of a customer identifier from a log line.

    Looks for common patterns such as:
      - ``user=alice``  / ``customer=alice``  / ``name=alice``
      - ``User: alice`` / ``Customer: alice``

    Falls back to a truncated version of the log line itself.
    """
    import re
    patterns = [
        r"(?:user|customer|client|name)[=:\s]+([A-Za-z0-9_@.\-]+)",
    ]
    for pat in patterns:
        m = re.search(pat, content, re.IGNORECASE)
        if m:
            return m.group(1)
    # Fallback: first 60 chars of the log line
    return content[:60].strip() or "unknown"
