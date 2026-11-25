from flask import Blueprint, jsonify
from autosync_service import get_autosync_status

autosync_bp = Blueprint("autosync_status", __name__)

@autosync_bp.get("/api/auto-sync/status")
def auto_sync_status():
    return jsonify(get_autosync_status())
