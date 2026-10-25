"""Pull one Vicohome camera's live view without the app, media straight over the LAN.

The cloud only does signaling; the camera offers its LAN address as an ICE host candidate,
so when we're on the same network the video goes camera -> us directly. Signaling follows
the decompiled player (com.a4x.player.internal: SignalConnection, MediaPlayer, P2PConnection):

  1. POST /device/getWebrtcTicket        -> signal server, accessToken, TURN servers
  2. wss://<signal>/iot-channel/connect    AUTH -> AUTH_RESPONSE
  3. JOIN_LIVE                             -> JOIN_LIVE_RESPONSE (server wakes a sleeping camera)
  4. PEER_IN (camera joined)               -> TRANSMIT SDP_OFFER, plain SDP (sdpMode 0, no
                                              "RTC connect acceleration", so no SDP compression)
  5. TRANSMIT SDP_ANSWER / ICE_CANDIDATE   -> setRemoteDescription / addIceCandidate
  6. video track                           -> findings/live-<serial>.jpg + .mp4

By default we offer no TURN servers, so video can only arrive over the LAN; --relay adds
the ticket's TURN servers as a fallback for viewers off the LAN.

    .venv/bin/python vico_live.py [serial] [--seconds 10] [--relay]
"""
import argparse
import asyncio
import base64
import json
import os
import secrets
import string
import sys
import time
from urllib.parse import urlparse

import websockets
import aioice.ice
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRecorder, MediaRelay
import struct

from aiortc.rtcsctptransport import RTCSctpTransport
from aiortc.rtp import RTCP_PSFB_APP, RTCP_PSFB_FIR, RtcpPsfbPacket, pack_remb_fci
from aiortc.sdp import candidate_from_sdp

from vico_api import FINDINGS, VicoCloud, redact
from vico_sdp import compress_offer, decompress_answer

# The camera always answers DTLS a=setup:active, so it is the DTLS client and (per RFC 8841)
# the SCTP client that sends INIT. aiortc instead picks the SCTP role from the ICE role, so as
# the offerer (ICE controlling) it also acts as SCTP client and both sides send INIT -> deadlock,
# no data channel, no video. Force us to the SCTP server so we answer the camera's INIT.
RTCSctpTransport.is_server = property(lambda self: True)

# The camera sends large unfragmented keyframe RTP packets; pylibsrtp's buffer is a hardcoded
# 1500 bytes, so unprotect() raises "packet is too long" and tears the connection down. Give
# every SRTP session a 64 KiB buffer.
import pylibsrtp  # noqa: E402
_srtp_init = pylibsrtp.Session.__init__
def _srtp_big_buffer(self, *a, **k):  # noqa: E302
    _srtp_init(self, *a, **k)
    self._cdata = pylibsrtp.ffi.new("char[]", 65535)
    self._buffer = pylibsrtp.ffi.buffer(self._cdata)
pylibsrtp.Session.__init__ = _srtp_big_buffer

# The camera streams a high bitrate; give each ICE UDP socket a big receive buffer so bursts
# aren't dropped while the event loop is busy (otherwise we see heavy RTP loss).
import socket as _socket  # noqa: E402
_sp_cm = aioice.ice.StunProtocol.connection_made
def _sp_cm_bigrcv(self, transport):  # noqa: E302
    _sp_cm(self, transport)
    try:
        s = transport.get_extra_info("socket")
        if s is not None:
            s.setsockopt(_socket.SOL_SOCKET, _socket.SO_RCVBUF, 8 * 1024 * 1024)
    except Exception:  # noqa: BLE001
        pass
aioice.ice.StunProtocol.connection_made = _sp_cm_bigrcv

T0 = time.monotonic()


def log(msg: str):
    print(f"[{time.monotonic() - T0:6.2f}s] {msg}", flush=True)


def b64json(obj) -> str:
    return base64.b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode()


def local_candidates(pc: RTCPeerConnection):
    """Our gathered candidates as (candidate_line, sdpMid, sdpMLineIndex). aiortc is
    non-trickle, so they're already in localDescription; the camera wants them as
    explicit ICE_CANDIDATE messages (MediaPlayer caches then sends after SDP_ANSWER)."""
    out, mid, idx = [], None, -1
    for line in pc.localDescription.sdp.splitlines():
        if line.startswith("m="):
            idx += 1
            mid = None
        elif line.startswith("a=mid:"):
            mid = line[6:].strip()
        elif line.startswith("a=candidate:"):
            out.append((line[2:], mid or "0", idx if idx >= 0 else 0))
    return out


def payload_of(msg: dict):
    """messagePayload is base64 JSON (or, with RTC acceleration, a JSON object)."""
    p = msg.get("messagePayload")
    if isinstance(p, dict):          # compressed path: payload is a nested JSON object
        return p
    if isinstance(p, str) and p:
        try:
            return json.loads(base64.b64decode(p + "=" * (-len(p) % 4)))
        except (ValueError, Exception):
            return json.loads(p)
    return p


def video_ssrc_from_sdp(sdp: str):
    """The camera's primary video SSRC, so we can ask it for a keyframe (PLI)."""
    in_video, ssrcs = False, []
    for line in sdp.splitlines():
        if line.startswith("m="):
            in_video = line.startswith("m=video")
        elif in_video and line.startswith("a=ssrc:"):
            n = int(line.split(":", 1)[1].split()[0])
            if n not in ssrcs:
                ssrcs.append(n)
    return ssrcs[0] if ssrcs else None


def selected_pair(pc: RTCPeerConnection) -> str:
    """The nominated ICE pair (aioice internals; best effort)."""
    try:
        conn = pc.getTransceivers()[0].receiver.transport.transport._connection
        pair = conn._nominated.get(1)
        lc, rc = pair.local_candidate, pair.remote_candidate
        return f"local {lc.host}:{lc.port} ({lc.type}) <-> camera {rc.host}:{rc.port} ({rc.type})"
    except Exception as e:  # noqa: BLE001
        return f"(unavailable: {e})"


async def run(serial: str, seconds: float, relay: bool, iface_ip: str = None, talk: bool = False,
              plain: bool = False) -> int:
    if not plain:
        talk = True   # compressed path needs a real audio ssrc (a=[mid,dir,[ssrc,cname]])
    if iface_ip:
        # Only gather candidates on the camera LAN; otherwise aiortc also offers waydroid0
        # a container bridge, a VPN, etc. and ICE can latch onto an unroutable interface.
        orig = aioice.ice.get_host_addresses
        aioice.ice.get_host_addresses = lambda *a, **k: [x for x in orig(*a, **k) if x == iface_ip] or [iface_ip]
    ticket = VicoCloud.from_env_file(account="2").webrtc_ticket(serial)
    data = ticket.get("data") or {}
    if ticket.get("result") != 0 or not data.get("accessToken"):
        log(f"ticket failed: result={ticket.get('result')} msg={ticket.get('msg')}")
        return 1
    uid = data["id"]
    session_id = f"Android-{uid}-{int(time.time() * 1000)}"
    url = f"wss://{urlparse(data['signalServer']).hostname}:{data.get('signalPort') or 443}{data['websocketPath']}"

    ice_servers = [RTCIceServer(urls=s["url"], username=s.get("username"), credential=s.get("credential"))
                   for s in data.get("iceServer", [])] if relay else []
    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
    # aiortc's cname is a 36-char UUID; the app uses 16 alphanumerics, and the camera's
    # embedded SDP parser may not take longer. Set it before any track/transceiver exists.
    pc._RTCPeerConnection__cname = "".join(
        secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
    if talk:
        from aiortc.mediastreams import AudioStreamTrack  # base class emits silence
        pc.addTrack(AudioStreamTrack())          # sendrecv audio (camera wants two-way)
    else:
        pc.addTransceiver("audio", direction="recvonly")
    pc.addTransceiver("video", direction="recvonly")
    channel = pc.createDataChannel("dataChannel")

    @channel.on("open")
    def on_dc_open():
        log("data channel open")

    @channel.on("message")
    def on_dc_message(m):
        log(f"data channel << {m if isinstance(m, str) else m[:80].hex()}")

    @pc.on("connectionstatechange")
    def on_conn():
        log(f"connection {pc.connectionState}")

    async def pli_pump(video_ssrc: int):
        """Repeatedly ask the camera for a keyframe. aiortc only sends a PLI after it has
        received RTP, but this camera seems to wait for a PLI before it sends any -> we
        break the deadlock by sending PLIs ourselves until the first frame arrives."""
        recv = next((r for r in pc.getReceivers()
                     if r.track and r.track.kind == "video"), None)
        if not recv:
            log("pli_pump: no video receiver")
            return
        our_ssrc = getattr(recv, "_RTCRtpReceiver__rtcp_ssrc", None)
        log(f"pli_pump: our rtcp ssrc={our_ssrc}, camera video ssrc={video_ssrc}")
        seq = 0
        for i in range(30):
            if first_frame.done():
                return
            try:
                await recv._send_rtcp_pli(video_ssrc)  # PLI
                if our_ssrc is not None:                 # FIR (camera advertised ccm fir)
                    seq = (seq + 1) & 0xFF
                    fir = RtcpPsfbPacket(fmt=RTCP_PSFB_FIR, ssrc=our_ssrc, media_ssrc=0,
                                         fci=struct.pack("!LB3x", video_ssrc, seq))
                    await recv._send_rtcp(fir)
                    # REMB: tell the camera there's plenty of bandwidth (10 Mbps) so its
                    # encoder starts sending (goog-remb was negotiated).
                    remb = RtcpPsfbPacket(fmt=RTCP_PSFB_APP, ssrc=our_ssrc, media_ssrc=0,
                                          fci=pack_remb_fci(10_000_000, [video_ssrc]))
                    await recv._send_rtcp(remb)
                if i == 0:
                    log("sent PLI + FIR + REMB (repeating 1/s)")
            except Exception as e:  # noqa: BLE001
                log(f"keyframe req error: {e!r}")
                return
            await asyncio.sleep(1)

    async def rtp_stats():
        while True:
            await asyncio.sleep(2)
            for s in (await pc.getStats()).values():
                if s.type == "inbound-rtp":
                    log(f"rtp {s.kind}: packets={s.packetsReceived} lost={s.packetsLost}")

    FINDINGS.mkdir(exist_ok=True)
    jpg = FINDINGS / f"live-{serial}.jpg"
    media_relay = MediaRelay()
    first_frame = asyncio.get_running_loop().create_future()
    done = asyncio.Event()
    frame_count = [0]

    @pc.on("track")
    def on_track(track):
        log(f"remote {track.kind} track")
        if track.kind == "video":
            async def grab():
                sub = media_relay.subscribe(track)
                while not done.is_set():
                    try:
                        frame = await sub.recv()
                    except Exception as e:  # noqa: BLE001 - track ended
                        log(f"video track ended: {e!r}")
                        done.set()
                        return
                    frame_count[0] += 1
                    if frame_count[0] == 1 or frame_count[0] % 30 == 0:
                        frame.to_image().save(jpg)   # latest frame for the dashboard
                    if not first_frame.done():
                        first_frame.set_result(frame)
            asyncio.ensure_future(grab())

    @pc.on("iceconnectionstatechange")
    def on_ice():
        log(f"ICE {pc.iceConnectionState}")
        if pc.iceConnectionState == "completed" or pc.iceConnectionState == "connected":
            log(f"path: {selected_pair(pc)}")
        if pc.iceConnectionState == "failed":
            done.set()

    pending_cands, offered = [], False
    async with websockets.connect(url, open_timeout=10) as ws:
        async def send(obj):
            await ws.send(json.dumps(obj, separators=(",", ":")))

        async def on_message(msg: dict):
            nonlocal offered
            method, mtype = msg.get("method"), msg.get("messageType")
            if method == "AUTH_RESPONSE":
                log(f"AUTH code={msg.get('code')}")
                if msg.get("code") != 0:
                    done.set(); return
                await send({"method": "JOIN_LIVE", "group": data.get("groupId") or serial,
                            "role": data.get("role") or "viewer", "name": uid,
                            "traceId": data.get("traceId") or f"trace-{int(time.time() * 1000)}",
                            "traceparent": f"00-{os.urandom(16).hex()}-{os.urandom(8).hex()}-01",
                            "recipientClientId": serial,
                            "supportRtcConnectAcceleration": int(
                                os.environ.get("VICO_ACCEL", "0" if plain else "1")),
                            "accessToken": data["accessToken"]})
            elif method == "JOIN_LIVE_RESPONSE":
                log(f"JOIN_LIVE code={msg.get('code')} {msg.get('message')}")
                # code 0: the camera is already awake, so with RTC connect acceleration offer now;
                # the newer (kissTarget "v2") signal servers never send PEER_IN for it
                if msg.get("code") == 0 and not plain:
                    await on_message({"method": "PEER_IN"})
            elif method == "PEER_IN" and not offered:
                offered = True
                log("camera joined (PEER_IN); creating offer")
                await pc.setLocalDescription(await pc.createOffer())
                if plain:   # legacy uncompressed path (camera answers but never streams)
                    payload = b64json({"type": "offer", "sdp": pc.localDescription.sdp})
                    ver = "0.0.1"
                else:       # compressed path (version 0.0.2) - the one the camera streams on
                    comp = compress_offer(pc.localDescription.sdp)
                    tmpl = os.environ.get("VICO_TEMPLATE")
                    if tmpl:   # replay app's captured compressed structure with our dynamic fields
                        base = json.loads(open(tmpl).read())
                        base["sid"], base["ice"], base["fp"] = comp["sid"], comp["ice"], comp["fp"]
                        if comp.get("a") and len(comp["a"]) > 2 and len(base.get("a", [])) > 2:
                            base["a"][2] = comp["a"][2]
                        comp = base
                    log("compressed offer: " + json.dumps(comp))
                    payload = {"type": "offer", "sdp": comp}
                    ver = "0.0.2"
                await send({"method": "TRANSMIT", "messageType": "SDP_OFFER",
                            "messagePayload": payload,
                            "recipientClientId": serial, "senderClientId": uid, "sessionId": session_id,
                            "viewerType": "a4x_sdk", "resolution": "auto", "version": ver, "sdpMode": 0})
                log(f"offer sent ({'plain' if plain else 'compressed 0.0.2'})")
            elif method == "TRANSMIT" and mtype in ("SDP_ANSWER", "SDP_OFFER_RESPONSE"):
                p = payload_of(msg)
                sdp_field = p.get("sdp") if isinstance(p, dict) else None
                if isinstance(sdp_field, dict):          # compressed answer
                    answer_sdp = decompress_answer(sdp_field)
                elif isinstance(sdp_field, str):         # plain answer
                    answer_sdp = sdp_field
                else:
                    answer_sdp = None
                if answer_sdp:
                    log(f"{mtype}: got answer ({len(answer_sdp)} bytes SDP, "
                        f"{'compressed' if isinstance(sdp_field, dict) else 'plain'})")
                    (FINDINGS / f"answer-{serial}.sdp").write_text(answer_sdp)
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer_sdp, type="answer"))
                    vssrc = video_ssrc_from_sdp(answer_sdp)
                    if vssrc:
                        log(f"pumping PLI at camera video ssrc {vssrc}")
                        asyncio.ensure_future(pli_pump(vssrc))
                    for c in pending_cands:
                        await pc.addIceCandidate(c)
                    pending_cands.clear()
                    cands = [(c, m, i) for c, m, i in local_candidates(pc) if m == "0"]
                    log(f"sending {len(cands)} local ICE candidates to camera")
                    for line, sdp_mid, sdp_idx in cands:
                        await send({"method": "TRANSMIT", "messageType": "ICE_CANDIDATE",
                                    "messagePayload": b64json({"candidate": line, "sdpMid": sdp_mid,
                                                               "sdpMLineIndex": sdp_idx}),
                                    "recipientClientId": serial, "senderClientId": uid,
                                    "sessionId": session_id, "viewerType": "a4x_sdk", "version": "0.0.1"})
                else:
                    log(f"{mtype}: unexpected payload {str(p)[:300]}")
            elif method == "TRANSMIT" and mtype == "ICE_CANDIDATE":
                p = payload_of(msg)
                line = p.get("candidate", "")
                log(f"camera candidate: {line}")
                if not line:
                    return
                cand = candidate_from_sdp(line.split(":", 1)[1] if line.startswith("candidate:") else line)
                cand.sdpMid, cand.sdpMLineIndex = p.get("sdpMid"), p.get("sdpMLineIndex")
                if pc.remoteDescription:
                    await pc.addIceCandidate(cand)
                else:
                    pending_cands.append(cand)
            elif method in ("PEER_OUT", "NEED_REAUTH"):
                log(f"{method} reason={msg.get('reason')}")
                done.set()
            else:
                log(f"<< {method}/{mtype or ''} {json.dumps(redact(msg))[:200]}")

        async def reader():
            async for raw in ws:
                try:
                    await on_message(json.loads(raw))
                except Exception as e:  # noqa: BLE001
                    log(f"handler error: {e!r}")

        await send({"method": "AUTH", "accessToken": data["accessToken"], "version": "0.0.1",
                    "id": uid, "clientType": "app", "status": "normal"})
        reader_task = asyncio.ensure_future(reader())
        stats_task = asyncio.ensure_future(rtp_stats())
        try:
            frame = await asyncio.wait_for(asyncio.shield(first_frame), timeout=45)
            log(f"FIRST FRAME {frame.width}x{frame.height} -> {jpg}")
            log(f"streaming {seconds:.0f}s ...")
            try:
                await asyncio.wait_for(done.wait(), timeout=seconds)
            except asyncio.TimeoutError:
                pass
            log(f"received {frame_count[0]} video frames; path: {selected_pair(pc)}")
            rc = 0
        except asyncio.TimeoutError:
            log("no video within 45s")
            rc = 2
        finally:
            done.set()
            stats_task.cancel()
            await pc.close()
            reader_task.cancel()
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("serial", help="camera serial / live-group id (from `vico_api.py devices`)")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--relay", action="store_true", help="also offer the ticket's TURN servers")
    ap.add_argument("--debug", action="store_true", help="aiortc DTLS/SCTP/RTP debug logging")
    ap.add_argument("--iface", default=None,
                    help="only gather ICE on this local IP (avoids VPN/virtual interfaces)")
    ap.add_argument("--talk", action="store_true", help="offer a silent audio track (sendrecv)")
    ap.add_argument("--plain", action="store_true", help="use the legacy uncompressed offer path")
    a = ap.parse_args()
    if a.debug:
        import logging
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(message)s")
        for noisy in ("aioice", "websockets", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.INFO)
    sys.exit(asyncio.run(run(a.serial, a.seconds, a.relay, a.iface, a.talk, a.plain)))
