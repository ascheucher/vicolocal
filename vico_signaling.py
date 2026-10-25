"""Signaling bridge between a standard-WebRTC peer (a browser) and a Vicohome camera.

The browser does the actual WebRTC (DTLS/SRTP/media) directly with the camera over the LAN;
this class only brokers signaling: it gets a live-view ticket, speaks the camera's A4x
websocket protocol, compresses the browser's offer into the camera's "KISS" form, and
decompresses the camera's answer back to standard SDP. No media touches the server.
Cameras whose deviceSupport lacks supportRtcConnectAcceleration (older firmware) ignore the
compressed offer, so for them (accel=False) the offer and answer go as plain SDP, as the app does.

Flow (see vico_live.py / the decompiled player for provenance):
    connect()          ticket -> wss AUTH -> JOIN_LIVE(accessToken, accel) -> PEER_IN, or (accel)
                       JOIN_LIVE_RESPONSE code 0 when the camera is already awake
    negotiate(offer)   compress offer -> TRANSMIT SDP_OFFER(0.0.2) -> wait SDP_ANSWER -> decompress
    add_browser_candidate(...)   -> TRANSMIT ICE_CANDIDATE
    on_camera_candidate(cb)      <- camera's ICE candidates (standard candidate dicts)
"""
import asyncio
import base64
import json
import os
import time
from urllib.parse import urlparse

import websockets

from vico_api import VicoCloud
from vico_sdp import compress_offer, decompress_answer, slim_offer


class CameraError(Exception):
    pass


class CameraLink:
    def __init__(self, serial: str, account: str = "2", on_log=None, accel: bool = True):
        self.serial = serial
        self.account = account
        self.accel = accel             # camera supports RTC connect acceleration (compressed SDP)
        self._log = on_log or (lambda m: None)
        self.ws = None
        self._reader = None
        self._uid = None
        self._session_id = None
        self._answer = None            # future for the SDP answer
        self._peer_in = None           # event: camera joined
        self.on_camera_candidate = None  # cb(dict candidate)
        self.on_closed = None            # cb(reason)
        self.ice_servers = []            # TURN servers from WEBRTC_CONFIG (for the browser)
        self._closed = False

    def log(self, msg):
        self._log(f"[{self.serial[:8]}] {msg}")

    async def connect(self, wake_timeout: float = 30.0):
        ticket = VicoCloud.from_env_file(account=self.account).webrtc_ticket(self.serial)
        data = ticket.get("data") or {}
        if ticket.get("result") != 0 or not data.get("accessToken"):
            raise CameraError(f"ticket failed: result={ticket.get('result')} {ticket.get('msg')}")
        self._data = data
        self._uid = data["id"]
        self._session_id = f"Android-{self._uid}-{int(time.time() * 1000)}"
        self.ice_servers = data.get("iceServer", [])
        url = (f"wss://{urlparse(data['signalServer']).hostname}:"
               f"{data.get('signalPort') or 443}{data['websocketPath']}")
        self.log(f"connecting {url}")
        self.ws = await websockets.connect(url, open_timeout=10)
        self._peer_in = asyncio.Event()
        self._reader = asyncio.ensure_future(self._read_loop())

        await self._send({"method": "AUTH", "accessToken": data["accessToken"], "version": "0.0.1",
                          "id": self._uid, "clientType": "app", "status": "normal"})
        await self._send({"method": "JOIN_LIVE", "group": data.get("groupId") or self.serial,
                          "role": data.get("role") or "viewer", "name": self._uid,
                          "traceId": data.get("traceId") or f"trace-{int(time.time() * 1000)}",
                          "traceparent": f"00-{os.urandom(16).hex()}-{os.urandom(8).hex()}-01",
                          "recipientClientId": self.serial, "supportRtcConnectAcceleration": int(self.accel),
                          "accessToken": data["accessToken"]})
        try:
            await asyncio.wait_for(self._peer_in.wait(), timeout=wake_timeout)
        except asyncio.TimeoutError:
            raise CameraError("camera did not join (PEER_IN) in time - it may be offline or busy")

    async def negotiate(self, offer_sdp: str, timeout: float = 15.0) -> str:
        """Send the browser's offer and return the camera's answer SDP."""
        self._answer = asyncio.get_running_loop().create_future()
        if self.accel:
            payload, version = {"type": "offer", "sdp": compress_offer(offer_sdp)}, "0.0.2"
        else:
            payload = base64.b64encode(json.dumps({"type": "offer", "sdp": slim_offer(offer_sdp)},
                                                  separators=(",", ":")).encode()).decode()
            version = "0.0.1"
        await self._send({"method": "TRANSMIT", "messageType": "SDP_OFFER",
                          "messagePayload": payload,
                          "recipientClientId": self.serial, "senderClientId": self._uid,
                          "sessionId": self._session_id, "viewerType": "a4x_sdk",
                          "resolution": "auto", "version": version, "sdpMode": 0})
        self.log(f"sent {'compressed' if self.accel else 'plain'} offer")
        try:
            return await asyncio.wait_for(self._answer, timeout=timeout)
        except asyncio.TimeoutError:
            raise CameraError("camera did not answer the offer (bad state - try again)")

    async def add_browser_candidate(self, candidate: str, sdp_mid, sdp_mline_index):
        payload = base64.b64encode(json.dumps(
            {"candidate": candidate, "sdpMid": sdp_mid, "sdpMLineIndex": sdp_mline_index},
            separators=(",", ":")).encode()).decode()
        await self._send({"method": "TRANSMIT", "messageType": "ICE_CANDIDATE",
                          "messagePayload": payload, "recipientClientId": self.serial,
                          "senderClientId": self._uid, "sessionId": self._session_id,
                          "viewerType": "a4x_sdk", "version": "0.0.1"})

    async def close(self):
        self._closed = True
        if self._reader:
            self._reader.cancel()
        if self.ws:
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass

    # --- internals ---
    async def _send(self, obj):
        await self.ws.send(json.dumps(obj, separators=(",", ":")))

    @staticmethod
    def _payload(msg):
        p = msg.get("messagePayload")
        if isinstance(p, dict):
            return p
        if isinstance(p, str) and p:
            try:
                return json.loads(base64.b64decode(p + "=" * (-len(p) % 4)))
            except Exception:  # noqa: BLE001
                try:
                    return json.loads(p)
                except Exception:  # noqa: BLE001
                    return p
        return p

    async def _read_loop(self):
        try:
            async for raw in self.ws:
                try:
                    await self._handle(json.loads(raw))
                except Exception as e:  # noqa: BLE001
                    self.log(f"handler error: {e!r}")
        except Exception:  # noqa: BLE001
            pass
        if not self._closed and self.on_closed:
            self.on_closed("signal socket closed")

    async def _handle(self, msg):
        method = msg.get("method")
        mtype = msg.get("messageType")
        if method == "AUTH_RESPONSE":
            self.log(f"AUTH code={msg.get('code')}")
        elif method == "JOIN_LIVE_RESPONSE":
            self.log(f"JOIN_LIVE code={msg.get('code')} {msg.get('message')}")
            # code 0: the camera is already awake. With RTC connect acceleration (which JOIN_LIVE
            # asks for) the app offers right away instead of waiting for PEER_IN, and the newer
            # signal servers (ticket kissTarget "v2") never send PEER_IN for an awake camera.
            if msg.get("code") == 0 and self.accel:
                self._peer_in.set()
        elif method == "WEBRTC_CONFIG":
            self.ice_servers = msg.get("iceServers", self.ice_servers)
        elif method == "PEER_IN":
            self.log("camera joined (PEER_IN)")
            self._peer_in.set()
        elif method == "TRANSMIT" and mtype in ("SDP_ANSWER", "SDP_OFFER_RESPONSE"):
            p = self._payload(msg)
            sdp = p.get("sdp") if isinstance(p, dict) else None
            answer = decompress_answer(sdp) if isinstance(sdp, dict) else (sdp if isinstance(sdp, str) else None)
            if answer and self._answer and not self._answer.done():
                self.log("got camera answer")
                self._answer.set_result(answer)
        elif method == "TRANSMIT" and mtype == "ICE_CANDIDATE":
            p = self._payload(msg)
            if isinstance(p, dict) and p.get("candidate") and self.on_camera_candidate:
                self.on_camera_candidate({"candidate": p["candidate"],
                                          "sdpMid": p.get("sdpMid"),
                                          "sdpMLineIndex": p.get("sdpMLineIndex")})
        elif method in ("PEER_OUT", "NEED_REAUTH"):
            self.log(f"{method} reason={msg.get('reason')}")
            if self.on_closed:
                self.on_closed(f"{method}")
