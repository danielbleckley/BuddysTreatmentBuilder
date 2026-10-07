#!/bin/bash
# Installs dependencies in Claude Code cloud sessions (web / phone). Does nothing on the Mac.
if [ "$CLAUDE_CODE_REMOTE" != "true" ]; then
  exit 0
fi
cd "$CLAUDE_PROJECT_DIR" || exit 0
pip install --quiet -r requirements.txt >>/tmp/cloud-setup.log 2>&1 || echo "Dependency install failed: see /tmp/cloud-setup.log"
exit 0
