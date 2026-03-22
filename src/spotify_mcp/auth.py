"""One-time Spotify OAuth setup.

Run this locally to get a cached token, then copy .spotify_cache to your deployment.

Usage:
    python -m spotify_mcp.auth

Requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET env vars.
Sets redirect URI to http://localhost:8888/callback by default.
"""

import os

import spotipy
from spotipy.oauth2 import SpotifyOAuth

SCOPES = " ".join(
    [
        "user-read-playback-state",
        "user-read-currently-playing",
        "user-modify-playback-state",
        "user-read-recently-played",
        "playlist-read-private",
        "playlist-read-collaborative",
    ]
)


def main():
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")

    if not client_id or not client_secret:
        print("Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET environment variables first.")
        raise SystemExit(1)

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_path=".spotify_cache",
    )

    sp = spotipy.Spotify(auth_manager=auth_manager)
    user = sp.current_user()
    print(f"Authenticated as: {user['display_name']} ({user['id']})")
    print("Token cached to .spotify_cache — copy this file to your deployment.")


if __name__ == "__main__":
    main()
