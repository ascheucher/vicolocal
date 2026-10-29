# Vicolocal

Watch your Vicohome / smartaddx battery cameras **live over your LAN**, and browse their
cloud recordings, from one small self-hosted web page — without the Vicohome app.

The cameras have no local stream (stock firmware opens zero ports). But their live view is
plain WebRTC that's only *signaled* through the vendor cloud — the actual video goes
**peer-to-peer**, so when your browser is on the same network as the cameras it streams
camera → browser directly over the LAN (hardware-decoded, no app, no cloud relay for the
media). This project is a tiny signaling bridge plus a dashboard that does exactly that.

## Features

- **Live LAN video** — click a camera, the browser does WebRTC straight to it over the LAN.
- **Camera controls in live view** — **pan/tilt** arrows (hold to keep moving; arrow keys work
  too), the **white light**, and **talk** through the camera's speaker (tap to start and stop,
  or hold while you speak).
- **MagicPix** — brightens the picture when it's dark (night, shade), like the app's MagicPix.
  It runs in your browser, so it costs the camera nothing; it's remembered per camera.
- **Recordings** — browse the cloud event clips (motion), play them in-page (HLS), and
  **download** them as mp4.
- **Snapshot tiles** refreshed from the cloud (~every 30 min) at **no battery cost**.
- **Battery-aware** — shows charge level and warns when you're streaming a camera that's
  running on battery (not plugged in).
- **Drag to reorder**, pinch / scroll-wheel **zoom + pan** on the video (tap the zoom badge
  to reset), a built-in **debug console** (connection state, LAN-vs-relay path, fps, loss)
  with copy-to-clipboard.
- **Fullscreen** video (zoom and the pan/tilt arrows keep working; on iPhone it opens the
  native player), and a live view that fits a **phone on its side**.
- **4 themes** (incl. a light one) and a **kid/kiosk mode**.
- Fully **env-configurable** and **containerized** (works behind Traefik on 443, no ports
  for users).

## How it works

```
 browser  ──WebSocket signaling──►  vicolocal server  ──wss (A4x protocol)──►  Vicohome cloud ──► camera
    │                                (compress/translate SDP)                                        │
    └──────────────────────  WebRTC media, peer-to-peer over the LAN  ─────────────────────────────┘
```

The server never touches the video. Camera controls don't touch it either: pan/tilt and the
white light are commands the browser sends straight to the camera on the WebRTC data channel
(JSON, wrapped in the camera's small binary "SCP" frame), and talking swaps your microphone into
the audio track the camera already receives. It fetches a per-view ticket, speaks the camera's
compact ("KISS") signaling protocol, and translates the browser's standard WebRTC offer/answer
to and from it. See [`vico_sdp.py`](vico_sdp.py) (the SDP codec),
[`vico_signaling.py`](vico_signaling.py) (the camera link), and
[`proto/media_signaling.proto`](proto/media_signaling.proto) (the recovered schema).

## Use a second, view-only account (recommended)

Don't point this at your main Vicohome account. Make a **separate account that only has
*shared* (view) access to the cameras** — then the dashboard can't change any camera
settings, and running it won't sign your phone out of your main account. A shared account can
still use the live controls (pan/tilt, light, talk), the same as a shared user in the app; set
`VICO_CONTROLS=0` to hide them.

1. Create a second Vicohome account (any email) in the app.
2. From your **main** account, **share each camera** to the second account: in the Vicohome
   app → the camera → *Settings → Share* → add the second account's email. (It gets live
   view + recordings, not admin.)
3. Give **the second account's** credentials to the dashboard (below).

(The included `vico_api.py share all -2` can do the sharing over the API if you prefer, given
both accounts' credentials.)

## Quick start (local)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt            # aiohttp, websockets, requests (+ ffmpeg for clip download)
export VICO_USER2="you@example.com" VICO_PASS2="…"   # the view-only account
python vico_server.py --port 8080
```

Open `http://<this-machine-lan-ip>:8080` from a browser **on the same WiFi** as the cameras.

## Deploy with Docker + Traefik

```bash
cp .env.example .env     # put VICO_USER2 / VICO_PASS2 in it (gitignored)
docker compose up -d --build
```

`docker-compose.yml` has Traefik labels to serve it at e.g. `https://cams.example.com` on 443
(set your host rule, cert resolver, and Traefik network). The container only needs outbound
internet — the browser does the camera media over the LAN, so **the viewer's device must be on
the same network as the cameras** (or you'll fall back to the cloud TURN relay).

### Configuration (environment variables)

| var | default | meaning |
|-----|---------|---------|
| `VICO_USER2` / `VICO_PASS2` | — | view-only account credentials (or mount `.vico.env`) |
| `VICO_ACCOUNT` | `2` | which account suffix in `.vico.env` to use |
| `VICOHOME_API` | `https://api-us.vicohome.io` | cloud API region; EU accounts use `https://api-eu.vicohome.io` |
| `VICO_TITLE` | `Vicolocal` | heading / page title |
| `VICO_TAGLINE` | `LAN camera feeds` | sub-heading |
| `VICO_THEME` | `dark` | default theme (`dark`/`midnight`/`terminal`/`daylight`) |
| `VICO_ACCENT` / `VICO_ACCENT2` | — | override accent colors |
| `VICO_SIMPLE` | off | kid/kiosk mode (big tiles, no theme/debug chrome) |
| `VICO_BATTERY_WARN` | on | show the on-battery streaming warning |
| `VICO_CONTROLS` | on | live-view controls: pan/tilt, white light, talk (MagicPix is always offered) |
| `VICO_HOST` / `VICO_PORT` | `0.0.0.0` / `8080` | bind address/port |

## Notes & limits

- Battery/solar cameras are woken on demand and stop after a short idle — live view is meant
  to be opened per-click, not left running.
- A camera that's in a bad/half-asleep state can refuse a session; just retry.
- Pan/tilt needs a few seconds after the camera wakes before the motor answers; the page tells
  you if you're early. The white light turns off when you close the live view (it drains the
  battery).
- **Talk needs HTTPS** (browsers only give the microphone to secure pages), so use it through
  the Traefik/HTTPS address, not `http://<lan-ip>:8080`.
- Secrets never belong in the repo: `.vico.env`, `.vico-token.json`, `.env`, and the `findings/`
  captures (which contain tokens and snapshots of your home) are gitignored.

## Credits / legal

Built by reverse-engineering the Vicohome Android app for personal interoperability with
cameras the author owns. Not affiliated with, endorsed by, or using any code from Vicohome /
smartaddx / Photon Sail. No vendor APK or decompiled source is included. Use with cameras you
own, at your own risk.

MIT licensed — see [LICENSE](LICENSE).
