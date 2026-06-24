# music-cicd

Auto-upload finished mixes to SoundCloud as **private** tracks and drop them into a
playlist — so you can keep working in the studio and have new mixes show up on your
phone without manually uploading anything.

Drop a `.wav` (or `.aiff`/`.flac`/`.mp3`) into a watched folder → it uploads private,
gets added to a playlist, and prints a secret share link.

## How it works

A single script (`music_cicd.py`) with three commands:

- `auth` — one-time OAuth 2.1 + PKCE authorization; stores a refresh token in `~/.music-cicd/`
- `playlists` — lists your playlists so you can grab a playlist id
- `watch` — polls the watch folder and runs the upload → add-to-playlist pipeline

Tracks are uploaded with `sharing=private`, so they are never public, even briefly.
The watcher waits until a file's size has been stable (default 10s) before uploading,
so it never grabs a half-finished export, and it records processed files so a restart
won't re-upload everything.

## Setup

Requires Python 3.8+.

```bash
pip install -r requirements.txt
cp .env.example .env        # then fill in your values
```

You need a registered SoundCloud app (Artist Pro account) for the Client ID/Secret:
https://soundcloud.com/you/apps

## Usage

```bash
# 1) Authorize once. Opens a URL; approve, then paste the redirected URL back.
python music_cicd.py auth

# 2) Find the playlist id you want to add mixes to, set SC_PLAYLIST_ID in .env
python music_cicd.py playlists

# 3) Run the watcher (Ctrl-C to stop)
python music_cicd.py watch
```

## Configuration

All config is via environment variables (or a local `.env`). See `.env.example` for the
full list: `SC_CLIENT_ID`, `SC_CLIENT_SECRET`, `SC_REDIRECT_URI`, `SC_WATCH_DIR`,
`SC_PLAYLIST_ID`, and optional tuning (`SC_EXTENSIONS`, `SC_POLL_SECONDS`,
`SC_SETTLE_SECONDS`).

## Security

`.env` and the token files are gitignored. Never commit your client secret or tokens.
