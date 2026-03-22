import os

import spotipy
import uvicorn
from fastmcp import FastMCP
from poke.mcp import PokeCallbackMiddleware
from spotipy.oauth2 import SpotifyOAuth

mcp = FastMCP("spotify")

# ---------------------------------------------------------------------------
# Spotify client (singleton)
# ---------------------------------------------------------------------------

_sp: spotipy.Spotify | None = None

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


def _get_spotify() -> spotipy.Spotify:
    """Return an authenticated Spotify client, refreshing tokens as needed."""
    global _sp
    if _sp is not None:
        return _sp

    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
    token_cache = os.environ.get("SPOTIFY_TOKEN_CACHE", ".spotify_cache")

    if not client_id or not client_secret:
        raise RuntimeError(
            "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET environment variables are required"
        )

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_path=token_cache,
        open_browser=False,
    )

    # Check for a pre-seeded auth code (first-time setup)
    auth_code = os.environ.get("SPOTIFY_AUTH_CODE")
    if auth_code:
        auth_manager.get_access_token(auth_code, as_dict=False)

    token_info = auth_manager.cache_handler.get_cached_token()
    if not token_info:
        raise RuntimeError(
            "No cached Spotify token found. Run the auth flow locally first:\n"
            "  1) Set SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI\n"
            "  2) python -m spotify_mcp.auth\n"
            "  3) Copy the resulting .spotify_cache file to your deployment"
        )

    _sp = spotipy.Spotify(auth_manager=auth_manager)
    return _sp


# ---------------------------------------------------------------------------
# Helper formatters
# ---------------------------------------------------------------------------


def _format_track(track: dict, prefix: str = "") -> str:
    """Format a track into a readable string."""
    name = track.get("name", "Unknown")
    artists = ", ".join(a["name"] for a in track.get("artists", []))
    album = track.get("album", {}).get("name", "")
    duration_ms = track.get("duration_ms", 0)
    minutes, seconds = divmod(duration_ms // 1000, 60)
    uri = track.get("uri", "")

    parts = [f"{prefix}**{name}** by {artists}"]
    if album:
        parts.append(f"  Album: {album}")
    parts.append(f"  Duration: {minutes}:{seconds:02d}")
    if uri:
        parts.append(f"  URI: `{uri}`")
    return "\n".join(parts)


def _format_playback(playback: dict) -> str:
    """Format current playback state into a readable string."""
    if not playback or not playback.get("item"):
        return "Nothing is currently playing."

    track = playback["item"]
    is_playing = playback.get("is_playing", False)
    progress_ms = playback.get("progress_ms", 0)
    duration_ms = track.get("duration_ms", 0)

    prog_min, prog_sec = divmod(progress_ms // 1000, 60)
    dur_min, dur_sec = divmod(duration_ms // 1000, 60)

    status = "Playing" if is_playing else "Paused"
    device = playback.get("device", {}).get("name", "Unknown device")

    lines = [
        f"**Status:** {status}",
        _format_track(track, prefix="**Now playing:** "),
        f"  Progress: {prog_min}:{prog_sec:02d} / {dur_min}:{dur_sec:02d}",
        f"  Device: {device}",
    ]

    context = playback.get("context")
    if context:
        ctx_type = context.get("type", "")
        if ctx_type:
            lines.append(f"  Context: {ctx_type}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def now_playing() -> str:
    """Get what's currently playing on Spotify.

    Returns the track name, artist, album, progress, device, and playback status.
    Call this when the user asks what they're listening to or what's playing.
    """
    sp = _get_spotify()
    playback = sp.current_playback()
    if not playback:
        return "Nothing is currently playing. Make sure Spotify is open and playing on a device."
    return _format_playback(playback)


@mcp.tool()
def recently_played(limit: int = 10) -> str:
    """Get recently played tracks.

    Args:
        limit: Number of recent tracks to return (1-50, default 10).

    Call this when the user asks what they were listening to or their recent history.
    """
    sp = _get_spotify()
    limit = max(1, min(50, limit))
    results = sp.current_user_recently_played(limit=limit)
    items = results.get("items", [])
    if not items:
        return "No recently played tracks found."

    lines = [f"**Recently played ({len(items)} tracks):**\n"]
    for i, item in enumerate(items, 1):
        track = item["track"]
        played_at = item.get("played_at", "")
        lines.append(f"{i}. {_format_track(track)}")
        if played_at:
            lines.append(f"  Played at: {played_at}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def search_tracks(query: str, limit: int = 10) -> str:
    """Search Spotify for tracks matching a query.

    Args:
        query: Search query — can be a song name, artist, genre, mood, or vibe description.
               For best results with vibes, include genre or mood keywords
               (e.g. "chill lo-fi", "upbeat dance", "sad acoustic").
        limit: Number of results to return (1-20, default 10).

    Use this to find specific songs or discover tracks matching a mood/vibe.
    Returns track name, artist, album, duration, and Spotify URI for each result.
    The URI can be passed to add_to_queue to queue the track.
    """
    sp = _get_spotify()
    limit = max(1, min(20, limit))
    results = sp.search(q=query, type="track", limit=limit)
    tracks = results.get("tracks", {}).get("items", [])
    if not tracks:
        return f'No tracks found for "{query}".'

    lines = [f'**Search results for "{query}" ({len(tracks)} tracks):**\n']
    for i, track in enumerate(tracks, 1):
        lines.append(f"{i}. {_format_track(track)}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def add_to_queue(uri: str) -> str:
    """Add a track to the Spotify playback queue.

    Args:
        uri: The Spotify URI of the track to queue (e.g. "spotify:track:4iV5W9uYEdYUVa79Axb7Rh").
             Get this from search_tracks results.

    Call this when the user wants to queue a specific song.
    Requires an active Spotify playback session on some device.
    """
    sp = _get_spotify()
    try:
        sp.add_to_queue(uri)
    except spotipy.exceptions.SpotifyException as e:
        if "NO_ACTIVE_DEVICE" in str(e) or "Not found" in str(e):
            return (
                "No active Spotify device found. "
                "Open Spotify on your phone, desktop, or web player and start playing something first."
            )
        raise
    return f"Added to queue: `{uri}`"


@mcp.tool()
def queue_vibes(description: str, count: int = 5) -> str:
    """Search for tracks matching a vibe and add them all to the queue.

    Args:
        description: A description of the vibe, mood, or genre
                     (e.g. "chill lo-fi beats", "90s hip hop", "energetic workout music",
                      "sad indie songs", "jazz cafe background music").
        count: Number of tracks to queue (1-10, default 5).

    This is the power tool — describe a vibe and it finds and queues multiple matching tracks.
    Call this when the user says things like "play some chill vibes" or "queue up workout music".
    """
    sp = _get_spotify()
    count = max(1, min(10, count))
    results = sp.search(q=description, type="track", limit=count)
    tracks = results.get("tracks", {}).get("items", [])
    if not tracks:
        return f'No tracks found matching "{description}".'

    queued = []
    errors = []
    for track in tracks:
        uri = track["uri"]
        try:
            sp.add_to_queue(uri)
            name = track["name"]
            artist = track["artists"][0]["name"] if track.get("artists") else "Unknown"
            queued.append(f"  - **{name}** by {artist}")
        except spotipy.exceptions.SpotifyException as e:
            if "NO_ACTIVE_DEVICE" in str(e) or "Not found" in str(e):
                return (
                    "No active Spotify device found. "
                    "Open Spotify and start playing something first, then try again."
                )
            errors.append(f"  - Failed to queue {uri}: {e}")

    lines = [f'**Queued {len(queued)} tracks for "{description}":**\n']
    lines.extend(queued)
    if errors:
        lines.append(f"\n**Errors ({len(errors)}):**")
        lines.extend(errors)
    return "\n".join(lines)


@mcp.tool()
def skip_track() -> str:
    """Skip to the next track in the queue.

    Call this when the user wants to skip the current song.
    """
    sp = _get_spotify()
    try:
        sp.next_track()
    except spotipy.exceptions.SpotifyException as e:
        if "NO_ACTIVE_DEVICE" in str(e) or "Not found" in str(e):
            return "No active Spotify device found. Open Spotify and start playing something first."
        raise
    return "Skipped to next track."


@mcp.tool()
def pause_playback() -> str:
    """Pause the current Spotify playback.

    Call this when the user wants to pause their music.
    """
    sp = _get_spotify()
    try:
        sp.pause_playback()
    except spotipy.exceptions.SpotifyException as e:
        if "NO_ACTIVE_DEVICE" in str(e) or "Not found" in str(e):
            return "No active Spotify device found."
        raise
    return "Playback paused."


@mcp.tool()
def resume_playback() -> str:
    """Resume Spotify playback.

    Call this when the user wants to resume or unpause their music.
    """
    sp = _get_spotify()
    try:
        sp.start_playback()
    except spotipy.exceptions.SpotifyException as e:
        if "NO_ACTIVE_DEVICE" in str(e) or "Not found" in str(e):
            return "No active Spotify device found."
        raise
    return "Playback resumed."


@mcp.tool()
def get_my_playlists(limit: int = 20) -> str:
    """List the user's Spotify playlists.

    Args:
        limit: Number of playlists to return (1-50, default 20).

    Call this when the user asks about their playlists or wants to see what playlists they have.
    """
    sp = _get_spotify()
    limit = max(1, min(50, limit))
    results = sp.current_user_playlists(limit=limit)
    playlists = results.get("items", [])
    if not playlists:
        return "No playlists found."

    lines = [f"**Your playlists ({len(playlists)}):**\n"]
    for i, pl in enumerate(playlists, 1):
        name = pl.get("name", "Untitled")
        total = pl.get("tracks", {}).get("total", 0)
        uri = pl.get("uri", "")
        lines.append(f"{i}. **{name}** — {total} tracks")
        if uri:
            lines.append(f"   URI: `{uri}`")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Entry point for the spotify-mcp-poke command."""
    _get_spotify()

    transport = os.environ.get("TRANSPORT", "streamable-http").lower()
    port = int(os.environ.get("PORT", "8247"))

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        app = PokeCallbackMiddleware(
            mcp.http_app(
                transport="streamable-http",
            )
        )
        uvicorn.run(app, host="0.0.0.0", port=port)
