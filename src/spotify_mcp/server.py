import json
import os

from dotenv import load_dotenv

load_dotenv()

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
        "playlist-modify-public",
        "playlist-modify-private",
        "user-library-modify",
        "user-library-read",
    ]
)


def _get_spotify() -> spotipy.Spotify:
    """Return an authenticated Spotify client, refreshing tokens as needed."""
    global _sp
    if _sp is not None:
        return _sp

    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
    token_cache = os.environ.get("SPOTIFY_TOKEN_CACHE", ".spotify_cache")

    if not client_id or not client_secret:
        raise RuntimeError(
            "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET environment variables are required"
        )

    # If SPOTIFY_TOKEN_JSON is set (for Render/deployments), write it to the cache file
    # so spotipy can pick it up and handle refresh automatically.
    token_json = os.environ.get("SPOTIFY_TOKEN_JSON")
    if token_json and not os.path.exists(token_cache):
        with open(token_cache, "w") as f:
            f.write(token_json)

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
def play_track(uri: str) -> str:
    """Immediately play a specific track, interrupting whatever is currently playing.

    Args:
        uri: The Spotify URI of the track (e.g. "spotify:track:4iV5W9uYEdYUVa79Axb7Rh").
             Get this from search_tracks results.

    Use this when the user wants to play a specific song RIGHT NOW — not queue it.
    This overrides the current queue and starts the track immediately.
    Use add_to_queue instead if the user wants to add to the queue without interrupting.
    """
    sp = _get_spotify()
    try:
        sp.start_playback(uris=[uri])
    except spotipy.exceptions.SpotifyException as e:
        if "NO_ACTIVE_DEVICE" in str(e) or "Not found" in str(e):
            return (
                "No active Spotify device found. "
                "Open Spotify on your phone, desktop, or web player and start playing something first."
            )
        raise

    track = sp.track(uri.replace("spotify:track:", ""))
    name = track.get("name", "Unknown")
    artists = ", ".join(a["name"] for a in track.get("artists", []))
    return f"Now playing **{name}** by {artists}."


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
def queue_vibes(description: str, count: int = 5, play_now: bool = True) -> str:
    """Search for tracks matching a vibe and play or queue them.

    Args:
        description: A description of the vibe, mood, or genre
                     (e.g. "chill lo-fi beats", "90s hip hop", "energetic workout music",
                      "sad indie songs", "jazz cafe background music").
        count: Number of tracks (1-10, default 5).
        play_now: If True (default), immediately starts playing these tracks, replacing
                  the current queue. If False, appends them to the existing queue.

    This is the power tool — describe a vibe and it finds matching tracks.
    By default it starts playing them immediately (overriding whatever is queued).
    Call this when the user says things like "play some chill vibes" or "queue up workout music".
    Set play_now=False only if the user explicitly says "add to queue" or "queue up" without
    wanting to interrupt what's currently playing.
    """
    sp = _get_spotify()
    count = max(1, min(10, count))
    results = sp.search(q=description, type="track", limit=count)
    tracks = results.get("tracks", {}).get("items", [])
    if not tracks:
        return f'No tracks found matching "{description}".'

    track_uris = [t["uri"] for t in tracks]
    track_lines = []
    for track in tracks:
        name = track["name"]
        artist = track["artists"][0]["name"] if track.get("artists") else "Unknown"
        track_lines.append(f"  - **{name}** by {artist}")

    if play_now:
        try:
            sp.start_playback(uris=track_uris)
        except spotipy.exceptions.SpotifyException as e:
            if "NO_ACTIVE_DEVICE" in str(e) or "Not found" in str(e):
                return (
                    "No active Spotify device found. "
                    "Open Spotify and start playing something first, then try again."
                )
            raise
        lines = [f'**Now playing {len(tracks)} tracks for "{description}":**\n']
    else:
        errors = []
        for uri in track_uris:
            try:
                sp.add_to_queue(uri)
            except spotipy.exceptions.SpotifyException as e:
                if "NO_ACTIVE_DEVICE" in str(e) or "Not found" in str(e):
                    return (
                        "No active Spotify device found. "
                        "Open Spotify and start playing something first, then try again."
                    )
                errors.append(str(e))
        lines = [f'**Queued {len(tracks)} tracks for "{description}":**\n']

    lines.extend(track_lines)
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


@mcp.tool()
def play_playlist(uri: str, shuffle: bool = True) -> str:
    """Start playing a playlist.

    Args:
        uri: The Spotify URI of the playlist (e.g. "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M").
             Get this from get_my_playlists results.
        shuffle: Whether to shuffle the playlist (default True).

    Call this when the user wants to play a specific playlist like "play my Discover Weekly"
    or "put on my workout playlist".
    """
    sp = _get_spotify()
    try:
        sp.shuffle(shuffle)
        sp.start_playback(context_uri=uri)
    except spotipy.exceptions.SpotifyException as e:
        if "NO_ACTIVE_DEVICE" in str(e) or "Not found" in str(e):
            return "No active Spotify device found. Open Spotify and start playing something first."
        raise
    mode = "shuffle on" if shuffle else "shuffle off"
    return f"Now playing playlist `{uri}` ({mode})."


@mcp.tool()
def get_recommendations(
    seed_track_uris: list[str] | None = None,
    seed_artists: list[str] | None = None,
    seed_genres: list[str] | None = None,
    energy: float | None = None,
    danceability: float | None = None,
    valence: float | None = None,
    tempo: float | None = None,
    limit: int = 10,
) -> str:
    """Get track recommendations from Spotify's recommendation engine.

    This is much better than search for discovering music by vibe, since it uses
    Spotify's actual audio analysis and collaborative filtering.

    Args:
        seed_track_uris: Up to 5 Spotify track URIs to use as seeds
                         (e.g. ["spotify:track:4iV5W9uYEdYUVa79Axb7Rh"]).
        seed_artists: Up to 5 Spotify artist URIs to seed from.
        seed_genres: Up to 5 genre names (e.g. ["indie", "lo-fi", "jazz"]).
                     Total seeds (tracks + artists + genres) must be 1-5.
        energy: Target energy 0.0-1.0 (0 = calm, 1 = intense).
        danceability: Target danceability 0.0-1.0.
        valence: Target mood 0.0-1.0 (0 = sad/dark, 1 = happy/cheerful).
        tempo: Target BPM (e.g. 120.0).
        limit: Number of recommendations (1-20, default 10).

    You need at least one seed (track, artist, or genre). Use now_playing or
    recently_played to get seed track URIs, or use genre names directly.
    """
    sp = _get_spotify()
    limit = max(1, min(20, limit))

    seed_tracks = []
    if seed_track_uris:
        seed_tracks = [u.replace("spotify:track:", "") for u in seed_track_uris[:5]]

    seed_artist_ids = []
    if seed_artists:
        seed_artist_ids = [u.replace("spotify:artist:", "") for u in seed_artists[:5]]

    kwargs: dict = {}
    if energy is not None:
        kwargs["target_energy"] = energy
    if danceability is not None:
        kwargs["target_danceability"] = danceability
    if valence is not None:
        kwargs["target_valence"] = valence
    if tempo is not None:
        kwargs["target_tempo"] = tempo

    total_seeds = len(seed_tracks) + len(seed_artist_ids) + len(seed_genres or [])
    if total_seeds == 0:
        return "Need at least one seed (track URI, artist URI, or genre name)."
    if total_seeds > 5:
        return "Total seeds (tracks + artists + genres) must be 5 or fewer."

    results = sp.recommendations(
        seed_tracks=seed_tracks or None,
        seed_artists=seed_artist_ids or None,
        seed_genres=seed_genres or None,
        limit=limit,
        **kwargs,
    )
    tracks = results.get("tracks", [])
    if not tracks:
        return "No recommendations found for those seeds."

    lines = [f"**Recommendations ({len(tracks)} tracks):**\n"]
    for i, track in enumerate(tracks, 1):
        lines.append(f"{i}. {_format_track(track)}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def set_volume(volume_percent: int) -> str:
    """Set the Spotify playback volume.

    Args:
        volume_percent: Volume level from 0 to 100.

    Call this when the user wants to change the volume, turn it up/down, or mute.
    """
    sp = _get_spotify()
    volume_percent = max(0, min(100, volume_percent))
    try:
        sp.volume(volume_percent)
    except spotipy.exceptions.SpotifyException as e:
        if "NO_ACTIVE_DEVICE" in str(e) or "Not found" in str(e):
            return "No active Spotify device found."
        if "VOLUME_CONTROL_DISALLOW" in str(e):
            return "Volume control is not available on this device (common on mobile)."
        raise
    return f"Volume set to {volume_percent}%."


@mcp.tool()
def shuffle_toggle(state: bool) -> str:
    """Turn shuffle on or off.

    Args:
        state: True to enable shuffle, False to disable.

    Call this when the user says "turn on shuffle" or "stop shuffling".
    """
    sp = _get_spotify()
    try:
        sp.shuffle(state)
    except spotipy.exceptions.SpotifyException as e:
        if "NO_ACTIVE_DEVICE" in str(e) or "Not found" in str(e):
            return "No active Spotify device found."
        raise
    return f"Shuffle {'enabled' if state else 'disabled'}."


@mcp.tool()
def repeat_mode(state: str = "off") -> str:
    """Set the repeat mode.

    Args:
        state: One of "off", "track" (repeat current song), or "context" (repeat playlist/album).

    Call this when the user wants to repeat a song or turn off repeat.
    """
    sp = _get_spotify()
    if state not in ("off", "track", "context"):
        return 'Invalid repeat mode. Use "off", "track", or "context".'
    try:
        sp.repeat(state)
    except spotipy.exceptions.SpotifyException as e:
        if "NO_ACTIVE_DEVICE" in str(e) or "Not found" in str(e):
            return "No active Spotify device found."
        raise
    return f"Repeat mode set to: {state}."


@mcp.tool()
def get_queue() -> str:
    """Get the current playback queue.

    Shows what's currently playing and what tracks are coming up next.
    Call this when the user asks "what's in my queue?" or "what's next?".
    """
    sp = _get_spotify()
    queue = sp.queue()
    lines = []

    currently = queue.get("currently_playing")
    if currently:
        lines.append("**Currently playing:**")
        lines.append(_format_track(currently))
        lines.append("")

    upcoming = queue.get("queue", [])
    if upcoming:
        lines.append(f"**Up next ({len(upcoming)} tracks):**\n")
        for i, track in enumerate(upcoming[:20], 1):
            lines.append(f"{i}. {_format_track(track)}")
            lines.append("")
    elif not currently:
        return "Queue is empty. Nothing is playing."

    return "\n".join(lines)


@mcp.tool()
def get_track_features(uri: str) -> str:
    """Get audio features for a track — energy, danceability, tempo, mood, etc.

    Args:
        uri: Spotify track URI (e.g. "spotify:track:4iV5W9uYEdYUVa79Axb7Rh").

    Returns detailed audio analysis including danceability, energy, valence (happiness),
    tempo, acousticness, instrumentalness, and more. Fun for answering
    "what's the vibe of this song?" or comparing tracks.
    """
    sp = _get_spotify()
    track_id = uri.replace("spotify:track:", "")
    features = sp.audio_features([track_id])
    if not features or not features[0]:
        return f"No audio features found for `{uri}`."

    f = features[0]
    track = sp.track(track_id)
    name = track.get("name", "Unknown")
    artists = ", ".join(a["name"] for a in track.get("artists", []))

    lines = [
        f"**Audio features for {name} by {artists}:**\n",
        f"  Danceability: {f.get('danceability', 0):.0%}",
        f"  Energy: {f.get('energy', 0):.0%}",
        f"  Valence (happiness): {f.get('valence', 0):.0%}",
        f"  Tempo: {f.get('tempo', 0):.0f} BPM",
        f"  Acousticness: {f.get('acousticness', 0):.0%}",
        f"  Instrumentalness: {f.get('instrumentalness', 0):.0%}",
        f"  Speechiness: {f.get('speechiness', 0):.0%}",
        f"  Liveness: {f.get('liveness', 0):.0%}",
        f"  Loudness: {f.get('loudness', 0):.1f} dB",
        f"  Key: {f.get('key', 'N/A')} (mode: {'major' if f.get('mode') == 1 else 'minor'})",
        f"  Time signature: {f.get('time_signature', 'N/A')}/4",
    ]
    return "\n".join(lines)


@mcp.tool()
def play_artist(artist_name: str) -> str:
    """Search for an artist and start playing their top tracks.

    Args:
        artist_name: The name of the artist to play.

    Call this when the user says "play Drake" or "put on some Taylor Swift".
    """
    sp = _get_spotify()
    results = sp.search(q=f"artist:{artist_name}", type="artist", limit=1)
    artists = results.get("artists", {}).get("items", [])
    if not artists:
        return f'No artist found for "{artist_name}".'

    artist = artists[0]
    artist_uri = artist["uri"]
    artist_display = artist["name"]

    try:
        sp.start_playback(context_uri=artist_uri)
    except spotipy.exceptions.SpotifyException as e:
        if "NO_ACTIVE_DEVICE" in str(e) or "Not found" in str(e):
            return "No active Spotify device found. Open Spotify and start playing something first."
        raise
    return f"Now playing **{artist_display}**."


@mcp.tool()
def save_current_track() -> str:
    """Save/like/heart the currently playing track to your library.

    Call this when the user says "like this song", "save this", or "heart this track".
    """
    sp = _get_spotify()
    playback = sp.current_playback()
    if not playback or not playback.get("item"):
        return "Nothing is currently playing."

    track = playback["item"]
    track_id = track["id"]
    name = track.get("name", "Unknown")
    artists = ", ".join(a["name"] for a in track.get("artists", []))

    sp.current_user_saved_tracks_add([track_id])
    return f"Saved **{name}** by {artists} to your library."


@mcp.tool()
def create_playlist(name: str, description: str = "", public: bool = False) -> str:
    """Create a new Spotify playlist.

    Args:
        name: Name for the playlist (e.g. "Road Trip", "Study Vibes").
        description: Optional description for the playlist.
        public: Whether the playlist should be public (default False/private).

    Returns the playlist URI which can be used with other tools.
    Call this when the user wants to create a new playlist.
    """
    sp = _get_spotify()
    user = sp.current_user()
    user_id = user["id"]
    playlist = sp.user_playlist_create(
        user=user_id,
        name=name,
        public=public,
        description=description,
    )
    uri = playlist.get("uri", "")
    return f'Created playlist **{name}**.\n  URI: `{uri}`'


@mcp.tool()
def add_tracks_to_playlist(playlist_uri: str, track_uris: list[str]) -> str:
    """Add tracks to an existing playlist.

    Args:
        playlist_uri: The Spotify URI of the playlist (from get_my_playlists or create_playlist).
        track_uris: List of Spotify track URIs to add.

    Call this after create_playlist or to add songs to an existing playlist.
    """
    sp = _get_spotify()
    playlist_id = playlist_uri.replace("spotify:playlist:", "")
    sp.playlist_add_items(playlist_id, track_uris)
    return f"Added {len(track_uris)} tracks to playlist `{playlist_uri}`."


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
