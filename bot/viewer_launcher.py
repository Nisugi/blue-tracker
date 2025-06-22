# bot/viewer_launcher.py
# This runs the web viewer in a separate thread

import threading
import logging
from .viewer import app

# Suppress Flask's default logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

def run_viewer(host='0.0.0.0', port=8080):
    """Run the Flask viewer app"""
    print(f"[Viewer] Starting web interface on port {port}")
    try:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    except Exception as e:
        print(f"[Viewer] Failed to start: {e}")

def start_viewer_thread():
    """Start viewer in background thread"""
    viewer_thread = threading.Thread(
        target=run_viewer,
        daemon=True,  # Dies when main program exits
        name="ViewerThread"
    )
    viewer_thread.start()
    print("[Viewer] Web interface thread started")
