from flask import Flask, render_template, jsonify, request, send_file
import subprocess
import os
import sys
from pathlib import Path

# Customer database – path is resolved relative to repo root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from core.customer_db import CustomerDatabase

app = Flask(__name__)
_customer_db = CustomerDatabase(Path(__file__).parent.parent / "data" / "customers")

# Flash Commands Definition
FLASH_COMMANDS = {
    "build": {
        "name": "Build Distro",
        "command": [sys.executable, "scripts/build_distro.py"],
        "description": "Packages the NetworkBuster application into a distribution."
    },
    "train": {
        "name": "Run AI Training",
        "command": ["mvn", "exec:exec@run-neural-network"],
        "description": "Executes the neural network training pipeline."
    },
    "status": {
        "name": "Check Status",
        "command": [sys.executable, "token_cli.py", "list"],
        "description": "Lists active tokens and system health."
    },
    "web3": {
        "name": "Verify Web3",
        "command": [sys.executable, "scripts/web3_verifier.py", "--demo"],
        "description": "Verifies Ethereum connection and demonstrates ownership proof."
    }
}

@app.route('/')
def index():
    return render_template('index.html', commands=FLASH_COMMANDS)

@app.route('/execute/<cmd_id>', methods=['POST'])
def execute(cmd_id):
    if cmd_id not in FLASH_COMMANDS:
        return jsonify({"status": "error", "message": "Command not found"}), 404
    
    cmd_info = FLASH_COMMANDS[cmd_id]
    try:
        # Run command and capture output
        result = subprocess.run(
            cmd_info["command"],
            capture_output=True,
            text=True,
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        )
        return jsonify({
            "status": "success" if result.returncode == 0 else "error",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/download', methods=['GET'])
def download_distro():
    """Download the latest NetworkBuster distribution."""
    dist_dir = Path(__file__).parent.parent / "dist"
    
    if not dist_dir.exists():
        return jsonify({"status": "error", "message": "Distribution not found. Run build first."}), 404
    
    # Find the latest zip file
    zip_files = list(dist_dir.glob("*.zip"))
    if not zip_files:
        return jsonify({"status": "error", "message": "No distribution archive found."}), 404
    
    latest_file = max(zip_files, key=lambda p: p.stat().st_mtime)
    return send_file(latest_file, as_attachment=True, download_name=latest_file.name)

@app.route('/distro-info', methods=['GET'])
def distro_info():
    """Get info about available distributions."""
    dist_dir = Path(__file__).parent.parent / "dist"
    
    if not dist_dir.exists():
        return jsonify({"status": "no_distro", "message": "No distributions built yet."})
    
    zip_files = list(dist_dir.glob("*.zip"))
    distros = []
    for f in zip_files:
        distros.append({
            "name": f.name,
            "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
            "created": f.stat().st_mtime
        })
    
    return jsonify({"status": "success", "distros": sorted(distros, key=lambda x: x['created'], reverse=True)})

@app.route('/customers', methods=['GET'])
def list_customers():
    """Return all customer records as JSON."""
    limit = int(request.args.get('limit', 100))
    customers = _customer_db.list_all(limit=limit)
    return jsonify({
        "status": "success",
        "total": _customer_db.count(),
        "customers": [c.to_dict() for c in customers],
    })


@app.route('/customers/add', methods=['POST'])
def add_customer():
    """
    Manually add a customer record.
    Expected JSON body: { "name": "...", "source": "...", "log_content": "...", "metadata": {} }
    """
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"status": "error", "message": "name is required"}), 400
    customer = _customer_db.add(
        name=name,
        source=body.get("source", "manual"),
        log_content=body.get("log_content", ""),
        metadata=body.get("metadata", {}),
    )
    return jsonify({"status": "success", "customer": customer.to_dict()}), 201


@app.route('/customers/ingest', methods=['POST'])
def ingest_log():
    """
    Ingest a raw log line as a new customer record.
    Expected JSON body: { "filepath": "...", "line_number": 1, "content": "...", "level": "info", "timestamp": "..." }
    """
    body = request.get_json(force=True, silent=True) or {}
    content = (body.get("content") or "").strip()
    if not content:
        return jsonify({"status": "error", "message": "content is required"}), 400

    from datetime import datetime as _dt
    from core.log_monitor import LogEntry

    ts_raw = body.get("timestamp")
    try:
        ts = _dt.fromisoformat(ts_raw) if ts_raw else _dt.now()
    except (ValueError, TypeError):
        ts = _dt.now()

    entry = LogEntry(
        filepath=body.get("filepath", "manual"),
        line_number=int(body.get("line_number", 0)),
        content=content,
        timestamp=ts,
        level=body.get("level"),
    )
    customer = _customer_db.ingest_log_entry(entry)
    return jsonify({"status": "success", "customer": customer.to_dict()}), 201


if __name__ == "__main__":
    # Unified Certificate Configuration
    cert_path = os.path.join(os.path.dirname(__file__), "..", "certs", "unified_certificate.pem")
    key_path = os.path.join(os.path.dirname(__file__), "..", "certs", "unified_key.pem")

    if os.path.exists(cert_path) and os.path.exists(key_path):
        print(f"🔒 Starting WebApp with Unified Certificate")
        app.run(host='0.0.0.0', port=5000, debug=True, ssl_context=(cert_path, key_path))
    else:
        print("⚠️ Unified Certificate not found. Starting in HTTP mode.")
        app.run(host='0.0.0.0', port=5000, debug=True)
