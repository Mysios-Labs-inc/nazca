"""`nazca setup` — one-time interactive bootstrap of Vertex (Google) auth.

This runs in the user's terminal (never inside the MCP server, which has no TTY
or browser). It:

  1. ensures the Google Cloud SDK (`gcloud`) is installed — offering to install
     it via Homebrew or the official script,
  2. runs the interactive `gcloud auth application-default login` browser flow,
  3. verifies a token can be minted.

Runtime token minting prefers Application Default Credentials via google-auth
(see `nazca.backends.vertex.access_token`), so once ADC is set the MCP server
works even without `gcloud` on its PATH.
"""

from __future__ import annotations

import platform
import shutil
import subprocess

import click

from nazca.backends.vertex import VertexError, _adc_token, _find_gcloud


def _have_gcloud() -> str | None:
    try:
        return _find_gcloud()
    except VertexError:
        return None


def _install_gcloud(assume_yes: bool) -> str | None:
    """Offer to install the Cloud SDK. Returns the gcloud path on success, else None."""
    system = platform.system()
    brew = shutil.which("brew")

    if system == "Darwin" and brew:
        cmd = [brew, "install", "--cask", "google-cloud-sdk"]
        how = "Homebrew (brew install --cask google-cloud-sdk)"
    elif system in ("Darwin", "Linux"):
        # Official installer → ~/google-cloud-sdk (found by _find_gcloud's fallback).
        cmd = ["bash", "-c",
               'curl -sSL https://sdk.cloud.google.com | bash -s -- '
               '--disable-prompts --install-dir="$HOME"']
        how = "official install script → ~/google-cloud-sdk"
    else:
        click.echo(
            f"  ✗ Automatic install isn't supported on {system}. "
            "Install the Google Cloud SDK manually: https://cloud.google.com/sdk/docs/install"
        )
        return None

    click.echo(f"  Google Cloud SDK not found. Install via {how}?")
    if not assume_yes and not click.confirm("  Proceed with install", default=True):
        click.echo("  Skipped install. Re-run `nazca setup` when ready.")
        return None

    click.echo("  Installing… (this downloads ~150MB and may take a minute)")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        click.echo(f"  ✗ Install failed (exit {e.returncode}). Install manually: "
                   "https://cloud.google.com/sdk/docs/install")
        return None
    return _have_gcloud()


def _adc_login(gcloud: str) -> bool:
    """Run the interactive ADC browser login. Returns True on success."""
    click.echo("  Launching `gcloud auth application-default login` (opens a browser)…")
    try:
        subprocess.run([gcloud, "auth", "application-default", "login"], check=True)
    except subprocess.CalledProcessError as e:
        click.echo(f"  ✗ Login failed (exit {e.returncode}).")
        return False
    return True


def run_setup(assume_yes: bool = False) -> int:
    """Execute the setup flow. Returns a process exit code (0 = ready)."""
    click.echo("nazca setup — configuring Vertex AI (Google) authentication\n")

    # 1. gcloud binary
    gcloud = _have_gcloud()
    if gcloud:
        click.echo(f"  ✓ gcloud found: {gcloud}")
    else:
        gcloud = _install_gcloud(assume_yes)
        if not gcloud:
            return 1
        click.echo(f"  ✓ gcloud installed: {gcloud}")

    # 2. credentials — already authed?
    if _adc_token():
        click.echo("  ✓ Application Default Credentials already present.")
    else:
        if not assume_yes and not click.confirm(
            "  No credentials found. Log in now", default=True
        ):
            click.echo("  Skipped login. Re-run `nazca setup` when ready.")
            return 1
        if not _adc_login(gcloud):
            return 1

    # 3. verify a token can actually be minted
    try:
        from nazca.backends.vertex import access_token

        access_token()
        click.echo("  ✓ Token minted successfully.\n")
    except VertexError as e:
        click.echo(f"  ✗ Could not mint a token: {e}")
        return 1

    from nazca import config

    click.echo("Ready. Vertex models will work via your own credentials.")
    click.echo(f"  Project (VERTEX_PROJECT): {config.VERTEX_PROJECT}")
    click.echo("  Override with: export VERTEX_PROJECT=<your-gcp-project>")
    click.echo("\nFor Claude Desktop, register the MCP server (see README → "
               "'Use with Claude Desktop').")
    return 0
