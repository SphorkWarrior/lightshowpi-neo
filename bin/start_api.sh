#!/bin/bash
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
export SYNCHRONIZED_LIGHTS_HOME="$PROJECT_DIR"
source "$PROJECT_DIR/venv/bin/activate"
cd "$PROJECT_DIR"
exec python3 -m api.main
