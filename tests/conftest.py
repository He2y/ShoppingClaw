"""Pin test environment so the suite is hermetic w.r.t. the developer's .env.

The repo .env sets AMSG_CONFIG=sava (production preset). Most unit tests were
written against the legacy preset; pinning it here keeps assertions stable.
Tests that exercise other presets construct AMSGOptimConfig explicitly.

This must run before any phone_agent import calls load_dotenv() - dotenv does
not override variables that already exist in the process environment.
"""

import os

os.environ["AMSG_CONFIG"] = "legacy"
