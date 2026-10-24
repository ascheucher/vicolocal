"""Minimal Vicohome cloud API client: login, device list, and WebRTC live-view tickets.

Endpoints come from dydx/vico-cli (login, device list) and the decompiled Android app 4.6.0
(`/device/getWebrtcTicket`, see com.a4x.player.internal RestApiClient.getWebRTCTicket).

Credentials are read from `.vico.env` next to this file (user="..." / pass="..."; a second
account as user2/pass2). Auth tokens are cached per account in `.vico-token.json` so we don't
log in on every run: each fresh login may kick other sessions of the same account offline
(error -1024).

CLI (add -2 to act as the second account):
    python vico_api.py devices          # list cameras (serial, name, model, ip, battery)
    python vico_api.py ticket <serial>  # fetch a live-view ticket (saved in full to findings/)
    python vico_api.py thumbs           # latest cloud cover image per camera (no wake)
    python vico_api.py share <serial|all>  # share camera(s) from account 1 to account 2
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ENV_FILE = Path(os.environ.get("VICO_ENV_FILE", HERE / ".vico.env"))
TOKEN_FILE = Path(os.environ.get("VICO_TOKEN_FILE", HERE / ".vico-token.json"))
FINDINGS = HERE / "findings"

API = os.environ.get("VICOHOME_API", "https://api-us.vicohome.io")
TOKEN_TTL_S = 24 * 3600

# What the app sends as `app` in ticket requests (A4xServiceContext.AppInfo.toJson).
APP_INFO = {
    "appName": "VicoHome",
    "appType": "Android",
    "bundle": "com.smartaddx.vicohome",
    "channelId": 1000,
    "tenantId": os.environ.get("VICOHOME_TENANT", "vicoo"),
    "version": 406000877,
    "versionName": "4.6.0",
}

# Result codes that mean "log in again".
AUTH_ERRORS = {-1024, -1025, -1026, -1027}


class VicoError(Exception):
    def __init__(self, result, msg="", path=""):
        super().__init__(f"{path}: result={result} msg={msg}")
        self.result = result
        self.msg = msg


def load_creds(path: Path = ENV_FILE, account: str = "") -> tuple[str, str]:
    """`account` is the key suffix: "" for user/pass, "2" for user2/pass2.

    Env vars VICO_USER{account}/VICO_PASS{account} win (container-friendly); otherwise read
    the `.vico.env` file (user{account}="..." / pass{account}="...")."""
    u, p = os.environ.get("VICO_USER" + account), os.environ.get("VICO_PASS" + account)
    if u and p:
        return u, p
    if not path.exists():
        raise FileNotFoundError(
            f"no credentials: set VICO_USER{account}/VICO_PASS{account} or create {path}")
    vals = {}
    for line in path.read_text().splitlines():
        m = re.match(r'\s*(\w+)\s*=\s*"?(.*?)"?\s*$', line)
        if m:
            vals[m.group(1)] = m.group(2)
    return vals["user" + account], vals["pass" + account]


class VicoCloud:
    def __init__(self, email: str, password: str, api: str = API, timeout: float = 15.0):
        self.email = email
        self.password = password
        self.api = api
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})
        self.token = self._load_cached_token()

    @classmethod
    def from_env_file(cls, path: Path = ENV_FILE, account: str = "", **kw) -> "VicoCloud":
        return cls(*load_creds(path, account), **kw)

    # --- auth ---
    @staticmethod
    def _read_token_cache() -> dict:
        try:
            cache = json.loads(TOKEN_FILE.read_text())
        except (OSError, ValueError):
            return {}
        return cache if "email" not in cache else {}  # drop the old single-account format

    def _load_cached_token(self):
        entry = self._read_token_cache().get(self.email, {})
        if entry.get("expires", 0) > time.time():
            return entry.get("token")
        return None

    def login(self) -> str:
        body = {"email": self.email, "password": self.password, "loginType": 0}
        data = self._post("/account/login", body, auth=False)
        self.token = data["token"]["token"]
        cache = self._read_token_cache()
        cache[self.email] = {"token": self.token, "expires": time.time() + TOKEN_TTL_S}
        TOKEN_FILE.write_text(json.dumps(cache))
        TOKEN_FILE.chmod(0o600)
        return self.token

    # --- transport ---
    def _post(self, path: str, body: dict, auth: bool = True, retry: bool = True):
        headers = {}
        if auth:
            if not self.token:
                self.login()
            headers["Authorization"] = self.token
        r = self.session.post(self.api + path, json=body, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        resp = r.json()
        result = resp.get("result", 0)
        if result in AUTH_ERRORS and auth and retry:
            self.login()
            return self._post(path, body, auth, retry=False)
        if result != 0:
            raise VicoError(result, resp.get("msg", ""), path)
        return resp.get("data")

    def _post_raw(self, path: str, body: dict) -> dict:
        """Like _post but returns the whole envelope (the ticket parser reads top-level `iot`)."""
        if not self.token:
            self.login()
        r = self.session.post(self.api + path, json=body, headers={"Authorization": self.token},
                              timeout=self.timeout)
        r.raise_for_status()
        resp = r.json()
        if resp.get("result") in AUTH_ERRORS:
            self.login()
            return self._post_raw(path, body)
        return resp

    # --- reads ---
    def devices(self) -> list[dict]:
        data = self._post("/device/listuserdevices", {"language": "en", "countryNo": "US"})
        return data.get("list", []) if isinstance(data, dict) else (data or [])

    def webrtc_ticket(self, serial: str) -> dict:
        body = {
            "app": APP_INFO,
            "countryNo": "US",
            "language": "en",
            "serialNumber": serial,
            "supportUnlimitedWebsocket": True,
            "verifyDormancyStatus": True,
            "verify4GVIPStatus": True,
            "needWakeupDevice": False,
        }
        return self._post_raw("/device/getWebrtcTicket", body)

    def library(self, serials=None, start=None, end=None, offset=0, limit=40, tags=None) -> list[dict]:
        """Cloud event recordings (the app's Library). Each item has imageUrl (thumb),
        videoUrl (HLS .m3u8, token-authorized), timestamp, period, deviceAiEventList, traceId.
        Timestamps are Unix seconds. (POST /library/newselectlibrary, FilterEntry.)"""
        now = int(time.time())
        body = {"app": APP_INFO, "countryNo": "US", "language": "en",
                "serialNumbers": serials or [],
                "startTimestamp": start if start is not None else now - 7 * 86400,
                "endTimestamp": end if end is not None else now,
                "from": offset, "to": offset + limit}
        if tags:
            body["objectNames"] = tags
        data = self._post("/library/newselectlibrary", body)
        return data.get("list", []) if isinstance(data, dict) else (data or [])

    def cover_images(self) -> list[dict]:
        """Latest cover image per camera, as the app's home screen shows it, without waking
        anything: [{serialNumber, lastPushImageUrl, lastPushTime}] (DeviceSettingApiClient.deviceThumbImage)."""
        body = {"app": APP_INFO, "countryNo": "US", "language": "en", "tenantId": APP_INFO["tenantId"]}
        return self._post("/device/devicePushImage", body) or []

    # --- device sharing (DeviceShareCore) ---
    def share_id(self, serial: str) -> str:
        """Owner side: mint a share ID for one camera (what the app's share QR code carries)."""
        return self._post("/device/getshareid", {"serialNumber": serial})["shareId"]

    def require_share(self, share_id: str):
        """Invitee side: request access using the owner's share ID."""
        return self._post("/device/requireshare", {"shareId": share_id})

    def recent_approvals(self) -> list[dict]:
        """Owner side: pending share requests."""
        data = self._post("/device/recentapprovals", {})
        return data.get("list", []) if isinstance(data, dict) else (data or [])

    def handle_approval(self, req: dict, status: int = 0):
        """Owner side: 0 approves, -1 rejects, -2 removes (HandlerShareRequestDialog)."""
        body = {"id": req["id"], "targetId": req["targetId"], "shareId": req["shareId"], "status": status}
        return self._post("/device/handleapproval", body)


def share_to_second_account(owner: "VicoCloud", invitee: "VicoCloud", serial: str):
    sid = owner.share_id(serial)
    invitee.require_share(sid)
    pending = [r for r in owner.recent_approvals() if r.get("shareId") == sid]
    if not pending:
        raise VicoError(-1, "share request not found in recentapprovals", "/device/recentapprovals")
    for req in pending:
        owner.handle_approval(req, 0)
    return len(pending)


def redact(obj):
    """Mask secrets for terminal output; full values only go to findings/."""
    secret = {"accessToken", "sign", "credential", "password", "token", "linkToken"}
    if isinstance(obj, dict):
        return {k: ("<redacted>" if k in secret and v else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def main(argv):
    account = ""
    if "-2" in argv:
        argv = [a for a in argv if a != "-2"]
        account = "2"
    vc = VicoCloud.from_env_file(account=account)
    cmd = argv[1] if len(argv) > 1 else "devices"
    if cmd == "devices":
        devs = vc.devices()
        FINDINGS.mkdir(exist_ok=True)
        (FINDINGS / "devices.json").write_text(json.dumps(devs, indent=1))
        for d in devs:
            print(f'{d.get("serialNumber", "?"):<34} {d.get("deviceName", "?"):<30} '
                  f'{d.get("modelNo", "?"):<12} {d.get("ip", "?"):<15} batt={d.get("batteryLevel")}')
    elif cmd == "ticket":
        t = vc.webrtc_ticket(argv[2])
        FINDINGS.mkdir(exist_ok=True)
        (FINDINGS / f"ticket-{argv[2]}.json").write_text(json.dumps(t, indent=1))
        print(json.dumps(redact(t), indent=1))
    elif cmd == "thumbs":
        imgs = vc.cover_images()
        FINDINGS.mkdir(exist_ok=True)
        (FINDINGS / "thumbs.json").write_text(json.dumps(imgs, indent=1))
        now = time.time()
        for t in imgs:
            ts = t.get("lastPushTime") or 0
            ts = ts / 1000 if ts > 1e12 else ts
            age = f"{(now - ts) / 60:8.0f} min ago" if ts else "   (no time)"
            url = t.get("lastPushImageUrl") or ""
            print(f'{t.get("serialNumber", "?"):<34} {age}  {url.split("?")[0][-48:]}')
    elif cmd == "share":
        invitee = VicoCloud.from_env_file(account="2")
        serials = [d["serialNumber"] for d in vc.devices()] if argv[2] == "all" else [argv[2]]
        for sn in serials:
            print(sn, "approved", share_to_second_account(vc, invitee, sn))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
