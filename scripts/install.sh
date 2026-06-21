#!/usr/bin/env bash
# nazca — team install for the Claude Desktop MCP server.
#
# Installs nazca (with the MCP extra), runs the one-time Google auth setup, and
# prints the claude_desktop_config.json snippet to register the server.
#
# Usage:  ./scripts/install.sh
# Requires: uv (https://docs.astral.sh/uv/).

set -euo pipefail

REPO="git+https://github.com/Mysios-Labs-inc/nazca.git"
SPEC="nazca[mcp] @ ${REPO}"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }

if ! command -v uv >/dev/null 2>&1; then
  echo "✗ uv not found. Install it first: https://docs.astral.sh/uv/getting-started/installation/"
  echo "  (macOS: brew install uv)"
  exit 1
fi

bold "1/3  Installing nazca[mcp] via uv…"
uv tool install --force "${SPEC}"

# Resolve the installed entry point for the config snippet.
BIN="$(command -v nazca-mcp || true)"
[ -z "${BIN}" ] && BIN="${HOME}/.local/bin/nazca-mcp"

bold "2/3  Authenticating Google / Vertex (one-time)…"
echo "    Running 'nazca setup' — installs gcloud if missing, then logs you in."
nazca setup || {
  echo "⚠  Setup didn't complete. Re-run 'nazca setup' later; install itself is fine."
}

bold "3/3  Register the MCP server in Claude Desktop"
CFG="${HOME}/Library/Application Support/Claude/claude_desktop_config.json"
cat <<EOF

Add this to your claude_desktop_config.json, then restart Claude Desktop:
  ${CFG}

  {
    "mcpServers": {
      "nazca": { "command": "${BIN}" }
    }
  }

Done. After restarting Desktop, ask it to "generate an image of …".
Updates later:  uv tool upgrade nazca
EOF
