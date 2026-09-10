"""Run the target app: `python -m target_app`.

PORT is read from the environment so the test harness can bind a random free
port and run tests in parallel with a developer's own instance on :8080.
"""

from __future__ import annotations

import os

from target_app.app import create_app


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    create_app().run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
