"""Vicohome's "KISS" compressed-SDP codec, reimplemented in pure Python.

The camera only starts its media sender when the live-view offer arrives on the compressed
path (signaling "version":"0.0.2", messagePayload a JSON object whose "sdp" is this compact
form), not the plain-SDP path. This module turns a full SDP offer into that compact JSON and
turns the camera's compact answer back into a full SDP for a normal WebRTC stack (aiortc).

Format reverse-engineered from a live capture of the app (SdpTransform.compressOfferToJson /
decompressAnswerToSdp); see findings/samples/ for the reference pairs the unit tests check.

Compact object keys: t, sid, sv, ice=[ufrag,pwd], fp=base64(fingerprint bytes),
  a =[mid, dir, [ssrc, cname]]                      (audio m-line; +[stream,track] in answers)
  acp=[[pt, type, extra?], ...]                     (audio codecs; opus folds its red pt)
  vcp=[[pt, type, fb, rtx?, profile/param?], ...]   (video codecs; rtx folded into primary)
  v  =[[mid, dir]]  (offer) / [[mid, dir, [ssrc,rtx], stream, track, cname]] (answer)
  dc =[mid, sctp-port, max-message-size]
  dir: 1=sendrecv 2=recvonly 3=sendonly 0=inactive
"""
import base64

DIR_TO_CODE = {"sendrecv": 1, "recvonly": 2, "sendonly": 3, "inactive": 0}
CODE_TO_DIR = {v: k for k, v in DIR_TO_CODE.items()}

# audio codec name -> type code (red is not emitted; it is folded into opus)
AUDIO_TYPE = {"opus": 1, "PCMU": 2, "PCMA": 3, "CN": 4, "telephone-event": 5}
AUDIO_TYPE_R = {v: k for k, v in AUDIO_TYPE.items()}
VIDEO_TYPE = {"H264": 1, "H265": 2, "red": 4, "ulpfec": 5, "flexfec-03": 6}
VIDEO_TYPE_R = {v: k for k, v in VIDEO_TYPE.items()}

# video rtcp-fb sets <-> code (offers use 1/2, the camera's answer uses 3)
FB_SETS = {
    0: [],
    1: ["goog-remb", "transport-cc", "ccm fir", "nack", "nack pli"],
    2: ["goog-remb", "transport-cc"],
    3: ["nack", "nack pli", "ccm fir", "goog-remb"],
}
FB_TO_CODE = {frozenset(v): k for k, v in FB_SETS.items()}
AUDIO_FB = {0: [], 1: ["nack"]}  # audio fb is coded separately


# ----------------------------- SDP parsing -----------------------------
class Media:
    def __init__(self, kind):
        self.kind = kind
        self.mid = None
        self.direction = "sendrecv"
        self.fmt = []            # payload type order
        self.rtpmap = {}         # pt -> (name, clock, channels)
        self.fmtp = {}           # pt -> params string
        self.fb = {}             # pt -> [fb strings]
        self.ssrcs = []          # ssrc ints (in order of first appearance)
        self.cname = None
        self.sctp_port = None
        self.max_msg = None


def parse_sdp(sdp: str):
    sess = {"sid": None, "sv": None, "ice_ufrag": None, "ice_pwd": None, "fp": None}
    medias, m = [], None
    for raw in sdp.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("o="):
            f = line[2:].split()
            sess["sid"], sess["sv"] = f[1], int(f[2])
        elif line.startswith("m="):
            f = line[2:].split()
            m = Media(f[0])
            m.fmt = f[3:]
            medias.append(m)
        elif line.startswith("a="):
            attr = line[2:]
            key, _, val = attr.partition(":")
            # ice/fp are shared across m-lines under BUNDLE; keep the first seen
            if key == "ice-ufrag" and not sess.get("_uf"):
                sess["ice_ufrag"], sess["_uf"] = val, True
            elif key == "ice-pwd" and not sess.get("_pw"):
                sess["ice_pwd"], sess["_pw"] = val, True
            elif key == "fingerprint" and not sess.get("_fp"):
                sess["fp"], sess["_fp"] = val.split()[1], True
            elif m is not None:
                if key == "mid":
                    m.mid = int(val)
                elif attr in ("sendrecv", "recvonly", "sendonly", "inactive"):
                    m.direction = attr
                elif key == "rtpmap":
                    pt, desc = val.split(None, 1)
                    parts = desc.split("/")
                    m.rtpmap[int(pt)] = (parts[0], int(parts[1]), int(parts[2]) if len(parts) > 2 else 1)
                elif key == "fmtp":
                    pt, params = val.split(None, 1)
                    m.fmtp[int(pt)] = params
                elif key == "rtcp-fb":
                    pt, fb = val.split(None, 1)
                    m.fb.setdefault(int(pt), []).append(fb)
                elif key == "ssrc":
                    s = int(val.split()[0])
                    if s not in m.ssrcs:
                        m.ssrcs.append(s)
                    if "cname:" in val:
                        m.cname = val.split("cname:", 1)[1].split()[0]
                elif key == "sctp-port":
                    m.sctp_port = int(val)
                elif key == "max-message-size":
                    m.max_msg = int(val)
    return sess, medias


def _rtx_for(m: Media, pt: int):
    for p, params in m.fmtp.items():
        if m.rtpmap.get(p, ("",))[0] == "rtx" and f"apt={pt}" in params:
            return p
    return None


# ----------------------------- compress offer -----------------------------
def compress_offer(sdp: str) -> dict:
    sess, medias = parse_sdp(sdp)
    out = {"t": "offer", "sid": sess["sid"], "sv": 2,
           "ice": [sess["ice_ufrag"], sess["ice_pwd"]],
           "fp": base64.b64encode(bytes(int(x, 16) for x in sess["fp"].split(":"))).decode()}
    audio = next((m for m in medias if m.kind == "audio"), None)
    video = next((m for m in medias if m.kind == "video"), None)
    app = next((m for m in medias if m.kind == "application"), None)

    def sends(d):
        return d in ("sendrecv", "sendonly")

    if audio:
        a = [audio.mid, DIR_TO_CODE[audio.direction]]
        if audio.ssrcs and sends(audio.direction):
            a.append([audio.ssrcs[0], audio.cname])
        out["a"] = a
        out["acp"] = _acp(audio)
    if video:
        out["vcp"] = _vcp(video)
        v = [video.mid, DIR_TO_CODE[video.direction]]
        if video.ssrcs and sends(video.direction):  # only when we send video
            v.append([video.ssrcs[0], video.ssrcs[1] if len(video.ssrcs) > 1 else 0])
        out["v"] = [v]
    if app:
        out["dc"] = [app.mid, app.sctp_port or 5000, app.max_msg or 262144]
    return out


def _acp(m: Media):
    red_pt = next((pt for pt, (n, *_1) in m.rtpmap.items() if n == "red"), None)
    rows = []
    for pt in (int(x) for x in m.fmt):
        name = m.rtpmap.get(pt, (None,))[0]
        if name is None or name == "red":
            continue
        t = AUDIO_TYPE.get(name)
        if t is None:
            continue
        if name == "opus":
            rows.append([pt, t, red_pt] if red_pt is not None else [pt, t])
        elif name == "telephone-event":
            clk = m.rtpmap[pt][1]
            rows.append([pt, t, clk // 1000] if clk != 8000 else [pt, t])
        else:
            rows.append([pt, t])
    return rows


def _vcp(m: Media):
    rows = []
    for pt in (int(x) for x in m.fmt):
        name = m.rtpmap.get(pt, (None,))[0]
        if name is None or name == "rtx":
            continue
        t = VIDEO_TYPE.get(name)
        if t is None:
            continue
        if name in ("H264", "H265"):   # advertise the full fb set the camera expects
            fb = 1 if m.fb.get(pt) else 0
        else:
            fb = FB_TO_CODE.get(frozenset(m.fb.get(pt, [])), 0)
        row = [pt, t, fb]
        if name in ("H264", "H265", "red"):
            row.append(_rtx_for(m, pt) or 0)
        if name in ("H264", "H265"):
            prof = _param(m.fmtp.get(pt, ""), "profile-level-id")
            if prof:
                row.append(prof)
        elif name == "flexfec-03":
            rw = _param(m.fmtp.get(pt, ""), "repair-window")
            row += [0, int(rw) if rw else 0]
        rows.append(row)
    return rows


def _param(fmtp: str, key: str):
    for kv in fmtp.replace(" ", ";").split(";"):
        if kv.startswith(key + "="):
            return kv.split("=", 1)[1]
    return None


# ----------------------------- decompress answer -----------------------------
def decompress_answer(c: dict, our_offer_sdp: str = None) -> str:
    """Rebuild a full answer SDP from the camera's compact answer."""
    fp_hex = ":".join(f"{b:02X}" for b in base64.b64decode(c["fp"]))
    mids = []
    L = [f"v=0", f"o=- {c['sid']} {c.get('sv', 2)} IN IP4 127.0.0.1", "s=-", "t=0 0"]

    def block(mid, kind, payloads):
        mids.append(str(mid))
        return payloads

    audio = c.get("a")
    video = c.get("v", [[None, 0]])[0]
    dc = c.get("dc")
    order = []
    if audio:
        order.append(("audio", audio, c.get("acp", [])))
    if video and video[0] is not None:
        order.append(("video", video, c.get("vcp", [])))
    if dc:
        order.append(("application", dc, None))

    group = " ".join(str(x[1][0] if x[0] != "application" else x[1][0]) for x in order)
    L.append(f"a=group:BUNDLE {group}")
    L.append("a=msid-semantic: WMS Stream-1")

    for kind, desc, cp in order:
        if kind == "application":
            mid, port = desc[0], desc[1]
            L += [f"m=application 9 UDP/DTLS/SCTP webrtc-datachannel", "c=IN IP4 127.0.0.1",
                  "a=rtcp:9 IN IP4 0.0.0.0", f"a=ice-ufrag:{c['ice'][0]}", f"a=ice-pwd:{c['ice'][1]}",
                  "a=ice-options:trickle", f"a=fingerprint:sha-256 {fp_hex}", "a=setup:active",
                  f"a=mid:{mid}", f"a=sctp-port:{port}"]
            continue
        mid, d = desc[0], desc[1]
        pts = [row[0] for row in cp]
        # include rtx payloads in the m= line
        mline_pts = []
        for row in cp:
            mline_pts.append(row[0])
            if kind == "video" and len(row) > 3 and row[1] in (1, 2, 4) and row[3]:
                mline_pts.append(row[3])
        proto = "UDP/TLS/RTP/SAVPF"
        L += [f"m={kind} 9 {proto} {' '.join(str(p) for p in mline_pts)}", "c=IN IP4 127.0.0.1"]
        # msid / ssrc
        ssrc_info = desc[2] if len(desc) > 2 else None
        stream = desc[3] if len(desc) > 3 else "Stream-1"
        track = desc[4] if len(desc) > 4 else None
        cname = desc[5] if len(desc) > 5 else (c.get("a", [None, None, [None, None]])[2][1] if c.get("a") else None)
        if track:
            L.append(f"a=msid:{stream} {track}")
        if ssrc_info:
            if kind == "video" and isinstance(ssrc_info, list) and len(ssrc_info) > 1:
                L.append(f"a=ssrc-group:FID {ssrc_info[0]} {ssrc_info[1]}")
                for s in ssrc_info:
                    L += [f"a=ssrc:{s} cname:{cname}", f"a=ssrc:{s} msid:{stream} {track or ''}".rstrip()]
            else:
                s = ssrc_info[0] if isinstance(ssrc_info, list) else ssrc_info
                L += [f"a=ssrc:{s} cname:{cname}", f"a=ssrc:{s} msid:{stream} {track or ''}".rstrip()]
        L += ["a=rtcp:9 IN IP4 0.0.0.0", f"a=ice-ufrag:{c['ice'][0]}", f"a=ice-pwd:{c['ice'][1]}",
              "a=ice-options:trickle", f"a=fingerprint:sha-256 {fp_hex}", "a=setup:active",
              f"a=mid:{mid}", f"a={CODE_TO_DIR[d]}", "a=rtcp-mux", "a=rtcp-rsize"]
        for row in cp:
            L += _codec_lines(kind, row)
    return "\r\n".join(L) + "\r\n"


def _codec_lines(kind, row):
    pt, t = row[0], row[1]
    lines = []
    if kind == "audio":
        name = AUDIO_TYPE_R.get(t, "PCMU")
        if name == "opus":
            lines.append(f"a=rtpmap:{pt} opus/48000/2")
        elif name == "telephone-event":
            lines.append(f"a=rtpmap:{pt} telephone-event/{row[2] * 1000 if len(row) > 2 else 8000}")
        else:
            lines.append(f"a=rtpmap:{pt} {name}/8000")
            for fb in AUDIO_FB.get(row[2] if len(row) > 2 else 0, []):
                lines.append(f"a=rtcp-fb:{pt} {fb}")
    else:
        name = VIDEO_TYPE_R.get(t, "H264")
        lines.append(f"a=rtpmap:{pt} {name}/90000")
        prof = row[4] if (name in ("H264", "H265") and len(row) > 4) else None
        if prof:
            lines.append(f"a=fmtp:{pt} level-asymmetry-allowed=1;packetization-mode=1;profile-level-id={prof}")
        if len(row) > 3 and row[3]:  # rtx, before the fb lines
            lines += [f"a=rtpmap:{row[3]} rtx/90000", f"a=fmtp:{row[3]} apt={pt}"]
        for fb in FB_SETS.get(row[2] if len(row) > 2 else 0, []):
            lines.append(f"a=rtcp-fb:{pt} {fb}")
    return lines


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path
    S = Path(__file__).resolve().parent / "findings" / "samples"
    rc = 0
    if (S / "app-offer.sdp").exists():
        got = compress_offer((S / "app-offer.sdp").read_text())
        want = json.loads((S / "app-offer-compressed.json").read_text())
        ok = got == want
        print("compress_offer:", "OK" if ok else "MISMATCH")
        if not ok:
            rc = 1
            for k in sorted(set(got) | set(want)):
                if got.get(k) != want.get(k):
                    print(f"  [{k}] got ={got.get(k)}")
                    print(f"  [{k}] want={want.get(k)}")
    if (S / "app-answer.sdp").exists():
        got = decompress_answer(json.loads((S / "app-answer-compressed.json").read_text()))
        want = (S / "app-answer.sdp").read_text()
        norm = lambda s: [l.strip() for l in s.strip().splitlines() if l.strip()]
        ok = norm(got) == norm(want)
        print("decompress_answer:", "OK" if ok else "MISMATCH")
        if not ok:
            rc = 1
            import difflib
            for d in difflib.unified_diff(norm(want), norm(got), "want", "got", lineterm=""):
                print(" ", d)
    sys.exit(rc)
