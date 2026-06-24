# CLAUDE.md

Project context for Claude Code. Read this before making changes.

## What this project is

A personal automation tool for an audio producer. It watches a local folder and, when a
new mix finishes exporting, uploads it to SoundCloud as a **private** track and adds it
to a playlist of test mixes. The goal is a zero-friction "save a file → it's on my phone"
workflow. Single-user, non-commercial.

## Architecture

Everything is in one file: `music_cicd.py`. It is intentionally a single script with no
framework. Three subcommands dispatched from `main()`:

- `cmd_auth` — OAuth 2.1 authorization-code flow **with PKCE** (required by SoundCloud).
  Interactive: prints the authorize URL, user pastes back the redirected URL, the `code`
  is exchanged for tokens. Tokens are stored in `~/.music-cicd/tokens.json`.
- `cmd_playlists` — `GET /me/playlists`, prints id/title/track_count.
- `cmd_watch` — polls `SC_WATCH_DIR`, detects new files of allowed extensions, waits for
  the file size to be stable (`SC_SETTLE_SECONDS`) to avoid uploading mid-export, then
  `upload_track` → `add_to_playlist`. Processed paths are persisted in
  `~/.music-cicd/processed.json` so restarts don't re-upload.

Token handling: `get_access_token()` refreshes automatically using the stored refresh
token when the access token is near expiry. Refresh tokens may rotate; whatever comes
back is persisted.

## Key external API facts (SoundCloud)

- Auth base: `https://secure.soundcloud.com` (`/authorize`, `/oauth/token`)
- API base: `https://api.soundcloud.com`
- Auth header format is `Authorization: OAuth <token>` (not `Bearer`).
- Upload: `POST /tracks`, multipart, fields `track[title]`, `track[sharing]=private`,
  `track[asset_data]` = file. WAV/AIFF/FLAC/MP3 accepted; 4 GB / 24 h limit.
- Private tracks return a `secret_token`; the share link is `permalink_url/secret_token`.
- **No append-one-track endpoint for playlists.** Must `GET /playlists/:id`, append the
  new track id to the existing list, and `PUT` the whole list back as
  `{"playlist": {"tracks": [{"id": ...}, ...]}}`.

## Known risks / things to verify when touching them

- The exact PUT body shape for playlist updates and a couple of response field names can
  vary by account/API version. The code prints raw status + response text on any failure
  to make this easy to debug. Verify against a real account before trusting changes.
- The watcher uses polling, not filesystem events. This is deliberate (dependency-free,
  robust to network drives). Don't swap to `watchdog` without a reason.
- Auth is interactive by design (one-time). Don't make it headless without preserving the
  ability to do the first authorization by hand.

## Conventions

- Standard library only, except `requests`. Keep new dependencies to a minimum and add
  them to `requirements.txt` if truly needed.
- No secrets in code or git. Config comes from env vars / `.env` (gitignored). Tokens
  live under `~/.music-cicd/`, never in the repo.
- Keep it a small, readable single file unless a feature genuinely warrants splitting.

## Likely next tasks

- Read a sidecar `.txt`/`.json` next to a mix for title/description/tags.
- Notify the user of the share link (email/Telegram/Slack) after upload.
- Run as a background service (launchd on macOS, systemd on Linux).
- Optional: create the target playlist automatically if `SC_PLAYLIST_ID` is unset.

## Testing notes

Pure helpers (`make_pkce`, `_extract_code`, `share_link`, `_load_dotenv`) are unit-testable
without network. The API paths require a live authorized account, so test those manually.
