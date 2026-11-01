#!/bin/sh
# Launch the Offline Task Organizer. Extra arguments are passed through
# (for example: ./run.sh --port 9000 --open).
cd "$(dirname "$0")" || exit 1
exec python3 -m organizer "$@"
