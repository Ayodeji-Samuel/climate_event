"""
wsgi.py – PythonAnywhere WSGI entry point.

In the PythonAnywhere Web tab:
  Source code:        /home/<username>/climate_event
  Working directory:  /home/<username>/climate_event
  Virtualenv:         /home/<username>/climate_event/.venv   (Python 3.11+)
  Static files:       URL /static/  →  /home/<username>/climate_event/static
and make the WSGI configuration file (/var/www/<username>_pythonanywhere_com_wsgi.py)
contain just:

  import sys
  sys.path.insert(0, "/home/<username>/climate_event")
  from wsgi import application
"""

import sys
import os

# Add project root to path
project_home = os.path.dirname(os.path.abspath(__file__))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

os.environ.setdefault("FLASK_ENV", "production")

# app.py builds the app once at import; don't call create_app() a second time.
from app import app as application  # noqa: E402
