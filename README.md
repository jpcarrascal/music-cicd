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
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then fill in your values
```

(A virtualenv avoids macOS's "externally managed environment" pip error. If your
system Python doesn't have that restriction, a plain `pip install -r requirements.txt`
works too - just adjust the `python` calls below accordingly.)

You need a registered SoundCloud app (Artist Pro account) for the Client ID/Secret:
https://soundcloud.com/you/apps

## Usage

```bash
# 1) Authorize once. Opens a URL; approve, then paste the redirected URL back.
.venv/bin/python music_cicd.py auth

# 2) Find the playlist id you want to add mixes to, set SC_PLAYLIST_ID in .env
.venv/bin/python music_cicd.py playlists

# 3) Run the watcher in the foreground (Ctrl-C to stop)
.venv/bin/python music_cicd.py watch
```

## Running as a background service (launchd)

For day-to-day use you'll want `watch` running continuously without a terminal open,
starting automatically at login. On macOS this means a launchd **user agent** (not a
system daemon - it only runs while you're logged in, which is what you want for a
personal tool).

1. Create a log directory:

   ```bash
   mkdir -p ~/Library/Logs/music-cicd
   ```

2. Copy the template and fill in your real paths (absolute paths only - launchd does
   not expand `~` or `$HOME`):

   ```bash
   cp launchd/com.music-cicd.watch.plist.example ~/Library/LaunchAgents/com.music-cicd.watch.plist
   ```

   Edit the copy and replace every `/path/to/music-cicd` with this repo's absolute path,
   and every `/path/to/home` with your home directory.

3. Load it:

   ```bash
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.music-cicd.watch.plist
   ```

   It starts immediately, and again automatically every time you log in. If `watch`
   ever crashes, launchd restarts it (`KeepAlive` / `SuccessfulExit: false` - it won't
   loop-restart if you stop it deliberately).

**Useful commands:**

```bash
# tail the live log
tail -f ~/Library/Logs/music-cicd/watch.log

# check whether it's running
launchctl print gui/$(id -u)/com.music-cicd.watch | grep state

# stop it (won't restart until you load it again)
launchctl bootout gui/$(id -u)/com.music-cicd.watch

# after editing the plist, reload it
launchctl bootout gui/$(id -u)/com.music-cicd.watch
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.music-cicd.watch.plist
```

Before loading the agent, make sure no copy of `watch` is already running in a
terminal - two instances polling the same folder would race on uploads.

## Configuration

All config is via environment variables (or a local `.env`). See `.env.example` for the
full list: `SC_CLIENT_ID`, `SC_CLIENT_SECRET`, `SC_REDIRECT_URI`, `SC_WATCH_DIR`,
`SC_PLAYLIST_ID`, and optional tuning (`SC_EXTENSIONS`, `SC_POLL_SECONDS`,
`SC_SETTLE_SECONDS`).

## Security

`.env` and the token files are gitignored. Never commit your client secret or tokens.
