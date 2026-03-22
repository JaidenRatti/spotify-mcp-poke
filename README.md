# spotify-mcp-poke

Spotify MCP server for Poke — check what's playing, search tracks, queue up vibes, control playback, and manage playlists.

## Setup

### 1. Create a Spotify App

1. Go to https://developer.spotify.com/dashboard
2. Click **Create App**
3. Add `http://127.0.0.1:8888/callback` as a Redirect URI (`localhost` is not allowed — must use `127.0.0.1`)
4. Save your Client ID and Client Secret

### 2. Authenticate Locally

Create a `.env` file in the project root:

```
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
```

Run the auth flow:

```bash
uv run python -m spotify_mcp.auth
```

This opens your browser to authorize, then saves a `.spotify_cache` token file.

### 3. Run Locally

**Terminal 1** — start the server:

```bash
uv run spotify-mcp-poke
```

**Terminal 2** — connect to Poke:

```bash
npx poke@latest tunnel http://localhost:8247/mcp -n "Spotify"
```

### 4. Deploy to Render

Render's free tier has an ephemeral filesystem, so the token cache gets wiped on every restart. We store it as an env var instead.

1. Get a clean single-line token JSON:

```bash
cat .spotify_cache | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)))"
```

2. Go to https://dashboard.render.com and click **New > Web Service**
3. Connect your `spotify-mcp-poke` GitHub repo
4. Use **Docker** runtime
5. Set these environment variables in the Render dashboard:

| Variable | Value |
|---|---|
| `SPOTIFY_CLIENT_ID` | Your Spotify client ID |
| `SPOTIFY_CLIENT_SECRET` | Your Spotify client secret |
| `SPOTIFY_TOKEN_JSON` | The single-line JSON output from step 1 |
| `SPOTIFY_TOKEN_CACHE` | `/app/.spotify_cache` |
| `TRANSPORT` | `streamable-http` |
| `PORT` | `8247` |

6. Deploy. Your MCP server will be live at `https://spotify-mcp-poke.onrender.com/mcp`

### 5. Connect to Poke

Add the Render URL as a remote MCP integration in Poke Kitchen, or via CLI:

```bash
npx poke@latest mcp add https://spotify-mcp-poke.onrender.com/mcp -n "Spotify"
```

> **Note:** The free Render tier spins down after inactivity and cold-starts take ~30s. If the token expires and can't refresh (unlikely but possible), re-run `uv run python -m spotify_mcp.auth` locally and update `SPOTIFY_TOKEN_JSON` in Render with the new cache contents. Make sure to use the single-line JSON command above — pasting raw multi-line JSON can cause parsing issues.

## Tools (21)

### Playback Info
- **now_playing** — what's currently playing (track, artist, album, progress, device)
- **recently_played** — recent listening history (up to 50 tracks)
- **get_queue** — see what's coming up next

### Discovery
- **search_tracks** — find songs by name, artist, or vibe keywords
- **get_recommendations** — Spotify's recommendation engine with seed tracks/artists/genres and tunable attributes (energy, danceability, valence, tempo)
- **get_track_features** — audio analysis for a track (energy, danceability, tempo, mood, etc.)

### Play Now
- **play_track** — immediately play a specific song (overrides current queue)
- **play_artist** — search for an artist and start playing their music
- **play_playlist** — start playing a playlist (with optional shuffle)
- **queue_vibes** — describe a mood/vibe, finds and plays matching tracks immediately (or optionally appends to queue)

### Queue
- **add_to_queue** — append a single track to the queue

### Playback Control
- **skip_track** — skip to next song
- **pause_playback** — pause music
- **resume_playback** — resume music
- **set_volume** — change volume (0-100)
- **shuffle_toggle** — turn shuffle on/off
- **repeat_mode** — set repeat to off/track/context

### Library & Playlists
- **get_my_playlists** — list your playlists
- **save_current_track** — like/heart the current song
- **create_playlist** — create a new playlist
- **add_tracks_to_playlist** — add tracks to a playlist
