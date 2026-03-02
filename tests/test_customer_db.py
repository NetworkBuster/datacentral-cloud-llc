"""
Tests for core/customer_db.py – CustomerDatabase and log ingestion.
"""
import sys
import os
from datetime import datetime
from pathlib import Path

# Ensure repo root is on the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.customer_db import Customer, CustomerDatabase, _extract_name_from_log
from core.log_monitor import LogEntry


# ---------------------------------------------------------------------------
# _extract_name_from_log
# ---------------------------------------------------------------------------

def test_extract_name_user_equals():
    assert _extract_name_from_log("user=alice logged in") == "alice"


def test_extract_name_customer_colon():
    assert _extract_name_from_log("Customer: bob connected") == "bob"


def test_extract_name_fallback():
    line = "some random log line without a name"
    result = _extract_name_from_log(line)
    assert result == line[:60].strip()


def test_extract_name_empty():
    assert _extract_name_from_log("") == "unknown"


# ---------------------------------------------------------------------------
# CustomerDatabase – basic CRUD
# ---------------------------------------------------------------------------

def test_add_and_get(tmp_path):
    db = CustomerDatabase(tmp_path / "db")
    c = db.add(name="Alice", source="/var/log/app.log", log_content="user=Alice joined")
    assert c.id.startswith("cust_")
    assert c.name == "Alice"
    fetched = db.get(c.id)
    assert fetched is not None
    assert fetched.name == "Alice"


def test_list_all(tmp_path):
    db = CustomerDatabase(tmp_path / "db")
    db.add(name="Alice", source="log1.log", log_content="user=Alice")
    db.add(name="Bob",   source="log2.log", log_content="user=Bob")
    records = db.list_all()
    assert len(records) == 2


def test_count(tmp_path):
    db = CustomerDatabase(tmp_path / "db")
    assert db.count() == 0
    db.add(name="Alice", source="x.log", log_content="user=Alice")
    assert db.count() == 1


def test_delete(tmp_path):
    db = CustomerDatabase(tmp_path / "db")
    c = db.add(name="Alice", source="x.log", log_content="user=Alice")
    assert db.delete(c.id) is True
    assert db.get(c.id) is None
    assert db.delete(c.id) is False  # already gone


def test_clear(tmp_path):
    db = CustomerDatabase(tmp_path / "db")
    db.add(name="Alice", source="x.log", log_content="user=Alice")
    db.clear()
    assert db.count() == 0


# ---------------------------------------------------------------------------
# CustomerDatabase – persistence across instances
# ---------------------------------------------------------------------------

def test_persist_to_disk(tmp_path):
    storage = tmp_path / "db"
    db1 = CustomerDatabase(storage)
    db1.add(name="Alice", source="x.log", log_content="user=Alice")

    db2 = CustomerDatabase(storage)   # second instance reads from disk
    assert db2.count() == 1
    records = db2.list_all()
    assert records[0].name == "Alice"


# ---------------------------------------------------------------------------
# CustomerDatabase – ingest_log_entry
# ---------------------------------------------------------------------------

def test_ingest_log_entry(tmp_path):
    db = CustomerDatabase(tmp_path / "db")
    entry = LogEntry(
        filepath="/var/log/app.log",
        line_number=42,
        content="user=charlie connected successfully",
        timestamp=datetime.now(),
        level="info",
    )
    customer = db.ingest_log_entry(entry)
    assert customer.name == "charlie"
    assert customer.source == "/var/log/app.log"
    assert customer.log_content == "user=charlie connected successfully"
    assert customer.metadata["level"] == "info"
    assert customer.metadata["line_number"] == 42
    assert db.count() == 1


def test_ingest_log_entry_no_name(tmp_path):
    db = CustomerDatabase(tmp_path / "db")
    entry = LogEntry(
        filepath="app.log",
        line_number=1,
        content="generic startup message",
        timestamp=datetime.now(),
        level=None,
    )
    customer = db.ingest_log_entry(entry)
    assert customer.name == "generic startup message"


# ---------------------------------------------------------------------------
# Customer.to_dict / from_dict roundtrip
# ---------------------------------------------------------------------------

def test_customer_serialisation():
    c = Customer(
        id="cust_test_001",
        name="TestUser",
        source="test.log",
        log_content="user=TestUser",
        metadata={"level": "info"},
    )
    d = c.to_dict()
    c2 = Customer.from_dict(d)
    assert c2.id == c.id
    assert c2.name == c.name
    assert c2.metadata == c.metadata


# ---------------------------------------------------------------------------
# Webapp routes (Flask test client)
# ---------------------------------------------------------------------------

def _make_app(tmp_path):
    """Return a Flask test client with a fresh database."""
    import importlib, types

    # Import the app module with a patched storage path
    import webapp.app as app_module
    # Override the singleton DB with a fresh one pointing at tmp_path
    app_module._customer_db = CustomerDatabase(tmp_path / "customers")
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client(), app_module._customer_db


def test_route_list_customers_empty(tmp_path):
    client, _ = _make_app(tmp_path)
    resp = client.get("/customers")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 0
    assert data["customers"] == []


def test_route_add_customer(tmp_path):
    client, db = _make_app(tmp_path)
    resp = client.post(
        "/customers/add",
        json={"name": "Alice", "source": "test.log", "log_content": "user=Alice"},
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["status"] == "success"
    assert data["customer"]["name"] == "Alice"


def test_route_add_customer_missing_name(tmp_path):
    client, _ = _make_app(tmp_path)
    resp = client.post("/customers/add", json={"source": "test.log"})
    assert resp.status_code == 400


def test_route_ingest_log(tmp_path):
    client, db = _make_app(tmp_path)
    resp = client.post(
        "/customers/ingest",
        json={"content": "user=bob joined the platform", "filepath": "app.log", "level": "info"},
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["status"] == "success"
    assert data["customer"]["name"] == "bob"
    assert db.count() == 1


def test_route_ingest_log_missing_content(tmp_path):
    client, _ = _make_app(tmp_path)
    resp = client.post("/customers/ingest", json={"filepath": "app.log"})
    assert resp.status_code == 400


def test_route_customers_after_ingest(tmp_path):
    client, _ = _make_app(tmp_path)
    client.post("/customers/ingest", json={"content": "user=dave signup", "filepath": "a.log"})
    resp = client.get("/customers")
    data = resp.get_json()
    assert data["total"] == 1
    assert data["customers"][0]["name"] == "dave"
