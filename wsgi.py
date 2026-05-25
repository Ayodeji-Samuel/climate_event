"""
wsgi.py – PythonAnywhere WSGI entry point.
In PythonAnywhere Web tab, set:
  Source code:  /home/<username>/climate_event
  Working dir:  /home/<username>/climate_event
  WSGI file:    /home/<username>/climate_event/wsgi.py
"""

import sys
import os

# Add project root to path
project_home = os.path.dirname(os.path.abspath(__file__))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

os.environ.setdefault("FLASK_ENV", "production")

from app import create_app

application = create_app("production")
