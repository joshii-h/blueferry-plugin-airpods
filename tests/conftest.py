"""Keep tests away from the user's configuration, cache and buses."""
from __future__ import annotations

import os

from blueferry_plugin_kit.testing import isolate_environment

# No test may reach a real bus: BlueZ and the session bus are faked.
isolate_environment("blueferry-airpods-tests-", bus_name="blueferry-airpods-tests")
os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = "unix:path=/nonexistent/blueferry-airpods-tests-system"

# The card assertions are in English; one test switches to German.
for _variable in ("LC_ALL", "LC_MESSAGES", "LANGUAGE"):
    os.environ.pop(_variable, None)
os.environ["LANG"] = "C.UTF-8"
