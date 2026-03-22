# spotify-mcp-poke

Spotify MCP server for Poke — check what's playing, search tracks, and queue up vibes.

## Setup

1. Create a Spotify app at https://developer.spotify.com/dashboard
2. Set redirect URI to `http://localhost:8888/callback`
3. Run the auth flow locally:

```bash
export SPOTIFY_CLIENT_ID=your_client_id
export SPOTIFY_CLIENT_SECRET=your_client_secret
python -m spotify_mcp.auth
```

4. Deploy to Render with the `.spotify_cache` file

## Tools

- **now_playing** — what's currently playing
- **recently_played** — recent listening history
- **search_tracks** — find songs by name, artist, or vibe
- **add_to_queue** — queue a specific track by URI
- **queue_vibes** — describe a mood and queue matching tracks
- **skip_track** — skip current song
- **pause_playback** / **resume_playback** — playback control
- **get_my_playlists** — list your playlists
