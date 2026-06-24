#!/usr/bin/env python3
"""
music-cicd  -  Auto-upload finished mixes to SoundCloud as private tracks
               and drop them into a playlist.

WHAT IT DOES
  Watches a folder. When a new audio file finishes exporting, it:
    1. uploads it to SoundCloud as PRIVATE (never public, not even briefly)
    2. adds it to a playlist you choose
    3. prints the private/secret share link
  If a previously-uploaded file is later overwritten (e.g. you re-exported
  the same mix), the new version is uploaded as a new track, swapped into
  the same playlist slot, and the old track is deleted - SoundCloud has no
  endpoint to replace a track's audio in place.

COMMANDS
  python music_cicd.py auth        # run ONCE: authorize + store refresh token
  python music_cicd.py playlists   # list your playlists (to find a playlist id)
  python music_cicd.py watch       # run continuously: watch folder + upload

CONFIG (environment variables)
  SC_CLIENT_ID       (required)  your app's Client ID
  SC_CLIENT_SECRET   (required)  your app's Client Secret
  SC_REDIRECT_URI    (required)  must EXACTLY match the URI registered on the app
                                 e.g. https://www.jpcarrascal.com/music-cicd/redirect
  SC_WATCH_DIR       (watch)     folder to watch, e.g. /Users/jp/Mixes
  SC_PLAYLIST_ID     (watch)     numeric id of the playlist to add tracks to
                                 (run `playlists` to find it; leave empty to skip)
  SC_EXTENSIONS      (optional)  comma list, default: wav,aiff,flac,mp3
  SC_POLL_SECONDS    (optional)  how often to scan the folder, default 5
  SC_SETTLE_SECONDS  (optional)  file must be unchanged this long before upload,
                                 default 10 (guards against uploading mid-export)

NOTE
  This talks to SoundCloud's live API. A couple of response field names or the
  playlist-update body shape can vary by account/API version, so the script
  prints the raw status + response on any failure to make tweaks trivial.
"""

import os
import sys
import json
import time
import base64
import hashlib
import secrets as _secrets
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run:  pip install requests")

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
AUTHORIZE_URL = "https://secure.soundcloud.com/authorize"
TOKEN_URL     = "https://secure.soundcloud.com/oauth/token"
API           = "https://api.soundcloud.com"

STATE_DIR   = Path.home() / ".music-cicd"
TOKEN_FILE  = STATE_DIR / "tokens.json"
SEEN_FILE   = STATE_DIR / "processed.json"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def _load_dotenv(path=".env"):
    """Load KEY=VALUE lines from a local .env file (real env vars win)."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def cfg(name, required=False, default=None):
    val = os.environ.get(name, default)
    if required and not val:
        sys.exit(f"Missing required environment variable: {name}")
    return val


def load_json(path, fallback):
    try:
        return json.loads(path.read_text())
    except Exception:
        return fallback


def save_json(path, data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# PKCE (required by SoundCloud's OAuth 2.1 authorization-code flow)
# ---------------------------------------------------------------------------
def make_pkce():
    verifier = base64.urlsafe_b64encode(os.urandom(40)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ---------------------------------------------------------------------------
# Auth: one-time authorization to obtain a refresh token
# ---------------------------------------------------------------------------
def cmd_auth():
    client_id     = cfg("SC_CLIENT_ID", required=True)
    client_secret = cfg("SC_CLIENT_SECRET", required=True)
    redirect_uri  = cfg("SC_REDIRECT_URI", required=True)

    verifier, challenge = make_pkce()
    state = _secrets.token_urlsafe(16)

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    url = AUTHORIZE_URL + "?" + urlencode(params)

    print("\n1) Open this URL in your browser and click 'Connect':\n")
    print("   " + url + "\n")
    print("2) After you approve, your browser will land on your redirect URI")
    print("   (the page can be blank or 404 - that's fine). Copy the FULL URL")
    print("   from the address bar, OR just the value of the ?code= part.\n")

    pasted = input("Paste the redirected URL (or the code) here:\n> ").strip()
    code = _extract_code(pasted)
    if not code:
        sys.exit("Could not find a 'code' in what you pasted.")

    resp = requests.post(
        TOKEN_URL,
        headers={"accept": "application/json; charset=utf-8"},
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "code": code,
        },
        timeout=60,
    )
    if resp.status_code >= 400:
        sys.exit(f"Token exchange failed ({resp.status_code}):\n{resp.text}")

    tok = resp.json()
    _store_tokens(tok)
    print("\nAuthorized. Tokens saved to", TOKEN_FILE)

    # Confirm by hitting /me
    me = requests.get(
        f"{API}/me",
        headers=_auth_header(tok["access_token"]),
        timeout=30,
    )
    if me.ok:
        u = me.json()
        print(f"Signed in as: {u.get('username') or u.get('full_name')} (id {u.get('id')})")
    else:
        print("(Could not fetch /me, but tokens were saved.)")


def _extract_code(pasted):
    if "code=" in pasted:
        qs = parse_qs(urlparse(pasted).query)
        if "code" in qs:
            return qs["code"][0]
        # in case they pasted "code=XYZ" with no full URL
        return parse_qs(pasted).get("code", [None])[0]
    return pasted or None


# ---------------------------------------------------------------------------
# Token storage + refresh
# ---------------------------------------------------------------------------
def _store_tokens(tok):
    data = load_json(TOKEN_FILE, {})
    data["access_token"] = tok["access_token"]
    if tok.get("refresh_token"):
        data["refresh_token"] = tok["refresh_token"]
    expires_in = int(tok.get("expires_in", 3600))
    data["expires_at"] = int(time.time()) + expires_in - 60  # refresh a bit early
    save_json(TOKEN_FILE, data)
    return data


def get_access_token():
    data = load_json(TOKEN_FILE, None)
    if not data or "refresh_token" not in data:
        sys.exit("No tokens found. Run:  python music_cicd.py auth")

    if int(time.time()) < data.get("expires_at", 0):
        return data["access_token"]

    # refresh
    client_id     = cfg("SC_CLIENT_ID", required=True)
    client_secret = cfg("SC_CLIENT_SECRET", required=True)
    resp = requests.post(
        TOKEN_URL,
        headers={"accept": "application/json; charset=utf-8"},
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": data["refresh_token"],
        },
        timeout=60,
    )
    if resp.status_code >= 400:
        sys.exit(f"Token refresh failed ({resp.status_code}):\n{resp.text}\n"
                 f"You may need to run `auth` again.")
    return _store_tokens(resp.json())["access_token"]


def _auth_header(token):
    return {
        "accept": "application/json; charset=utf-8",
        "Authorization": f"OAuth {token}",
    }


# ---------------------------------------------------------------------------
# Playlists
# ---------------------------------------------------------------------------
def cmd_playlists():
    token = get_access_token()
    resp = requests.get(
        f"{API}/me/playlists",
        headers=_auth_header(token),
        params={"limit": 200, "linked_partitioning": "true"},
        timeout=60,
    )
    if not resp.ok:
        sys.exit(f"Failed to list playlists ({resp.status_code}):\n{resp.text}")
    body = resp.json()
    items = body.get("collection", body) if isinstance(body, dict) else body
    if not items:
        print("No playlists found. Create one in the SoundCloud app first,")
        print("or the watcher can create one automatically if you leave SC_PLAYLIST_ID unset.")
        return
    print(f"\n{'ID':>12}  {'tracks':>6}  sharing   title")
    print("-" * 60)
    for p in items:
        print(f"{p.get('id'):>12}  {p.get('track_count', '?'):>6}  "
              f"{p.get('sharing', '?'):<8}  {p.get('title')}")
    print("\nSet SC_PLAYLIST_ID to the ID of the playlist you want.\n")


def _get_playlist_track_ids(token, playlist_id):
    g = requests.get(
        f"{API}/playlists/{playlist_id}",
        headers=_auth_header(token),
        timeout=60,
    )
    if not g.ok:
        print(f"  ! Could not read playlist {playlist_id} ({g.status_code}): {g.text}")
        return None
    return [t["id"] for t in g.json().get("tracks", []) if "id" in t]


def _put_playlist_track_ids(token, playlist_id, track_ids):
    # The API rejects a JSON body here ("Could not parse JSON request body")
    # despite docs suggesting otherwise; it wants classic form-encoded
    # Rails-style array params instead.
    form_data = [("playlist[tracks][][id]", str(tid)) for tid in track_ids]
    p = requests.put(
        f"{API}/playlists/{playlist_id}",
        headers=_auth_header(token),
        data=form_data,
        timeout=120,
    )
    if not p.ok:
        print(f"  ! Could not update playlist ({p.status_code}): {p.text}")
        return False
    return True


def add_to_playlist(token, playlist_id, track_id):
    # SoundCloud has no "append one track" endpoint: GET the playlist,
    # then PUT back the full track list with the new id appended.
    existing = _get_playlist_track_ids(token, playlist_id)
    if existing is None:
        return False
    if track_id in existing:
        return True
    return _put_playlist_track_ids(token, playlist_id, existing + [track_id])


def replace_in_playlist(token, playlist_id, old_track_id, new_track_id):
    existing = _get_playlist_track_ids(token, playlist_id)
    if existing is None:
        return False
    updated = [new_track_id if tid == old_track_id else tid for tid in existing]
    if old_track_id not in existing:
        updated.append(new_track_id)  # old track wasn't in there; just add the new one
    return _put_playlist_track_ids(token, playlist_id, updated)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
def upload_track(token, path: Path):
    title = path.stem
    with open(path, "rb") as fh:
        resp = requests.post(
            f"{API}/tracks",
            headers=_auth_header(token),
            data={
                "track[title]": title,
                "track[sharing]": "private",
            },
            files={
                "track[asset_data]": (path.name, fh, "application/octet-stream"),
            },
            timeout=None,  # large files
        )
    if not resp.ok:
        print(f"  ! Upload failed ({resp.status_code}): {resp.text}")
        return None
    return resp.json()


def delete_track(token, track_id):
    resp = requests.delete(
        f"{API}/tracks/{track_id}",
        headers=_auth_header(token),
        timeout=60,
    )
    if not resp.ok:
        print(f"  ! Could not delete old track {track_id} ({resp.status_code}): {resp.text}")
        return False
    return True


def share_link(track):
    permalink = track.get("permalink_url", "")
    secret = track.get("secret_token")
    if permalink and secret:
        return f"{permalink}/{secret}"
    return permalink or "(no permalink in response)"


# ---------------------------------------------------------------------------
# Watch loop
# ---------------------------------------------------------------------------
def cmd_watch():
    watch_dir = Path(cfg("SC_WATCH_DIR", required=True)).expanduser()
    if not watch_dir.is_dir():
        sys.exit(f"SC_WATCH_DIR is not a directory: {watch_dir}")
    playlist_id = cfg("SC_PLAYLIST_ID")  # optional
    exts = {("." + e.strip().lstrip(".")).lower()
            for e in cfg("SC_EXTENSIONS", default="wav,aiff,flac,mp3").split(",")}
    poll = int(cfg("SC_POLL_SECONDS", default="5"))
    settle = int(cfg("SC_SETTLE_SECONDS", default="10"))

    # path -> {track_id, size, mtime} for files already uploaded at least once
    processed = load_json(SEEN_FILE, {})
    if isinstance(processed, list):
        # upgrading from the old format (a list of paths, no track ids):
        # snapshot current size/mtime so these are NOT re-uploaded just
        # because the format changed. track_id is unknown, so if one of
        # these files is later overwritten it'll be uploaded as a new
        # track (can't replace what we don't have the id for) rather
        # than silently re-uploading everything right now.
        migrated = {}
        for key in processed:
            try:
                st = Path(key).stat()
                migrated[key] = {"track_id": None, "size": st.st_size, "mtime": st.st_mtime}
            except OSError:
                pass
        processed = migrated
    settling = {}  # path -> (size, mtime, first_seen_at) for settle tracking

    print(f"Watching {watch_dir} for {sorted(exts)}")
    print(f"Playlist: {playlist_id or '(none - just uploading)'}")
    print("Ctrl-C to stop.\n")

    while True:
        try:
            for path in sorted(watch_dir.iterdir()):
                if not path.is_file() or path.suffix.lower() not in exts:
                    continue
                key = str(path.resolve())
                try:
                    st = path.stat()
                except OSError:
                    continue
                size, mtime = st.st_size, st.st_mtime

                done = processed.get(key)
                if done and done.get("size") == size and done.get("mtime") == mtime:
                    continue  # already uploaded, unchanged since

                prev = settling.get(key)
                if prev is None or prev[0] != size or prev[1] != mtime:
                    # changed (new file, or an overwrite of a processed one):
                    # (re)start the settle timer
                    settling[key] = (size, mtime, time.time())
                    continue
                if time.time() - prev[2] < settle:
                    continue  # still settling

                # stable long enough -> process it
                is_reupload = bool(done) and done.get("track_id") is not None
                print(f"-> {path.name}" + (" (re-export, replacing)" if is_reupload else ""))
                token = get_access_token()
                # SoundCloud has no "replace audio" endpoint: PUT /tracks/:id
                # silently ignores a new asset_data on an existing track. The
                # only way to update a mix is to upload it as a new track and
                # delete the old one.
                track = upload_track(token, path)
                if not track:
                    # leave it un-settled so you can retry; back off
                    settling.pop(key, None)
                    continue
                tid = track.get("id")
                print(f"   uploaded as private (track id {tid})")
                print(f"   private link: {share_link(track)}")

                if playlist_id:
                    if is_reupload:
                        ok = replace_in_playlist(token, playlist_id, done["track_id"], tid)
                        print("   replaced in playlist" if ok else "   (playlist replace failed)")
                    else:
                        ok = add_to_playlist(token, playlist_id, tid)
                        print("   added to playlist" if ok else "   (playlist add failed)")

                if is_reupload:
                    old_tid = done["track_id"]
                    if delete_track(token, old_tid):
                        print(f"   deleted old track {old_tid}")

                processed[key] = {"track_id": tid, "size": size, "mtime": mtime}
                save_json(SEEN_FILE, processed)
                settling.pop(key, None)

            time.sleep(poll)
        except KeyboardInterrupt:
            print("\nStopped.")
            return


# ---------------------------------------------------------------------------
def main():
    _load_dotenv()
    cmds = {"auth": cmd_auth, "playlists": cmd_playlists, "watch": cmd_watch}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[sys.argv[1]]()


if __name__ == "__main__":
    main()
