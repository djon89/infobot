"""Импорт подписок YouTube-аккаунта через официальный YouTube Data API.

Вход — OAuth «для устройств»: бот показывает код, пользователь вводит его на google.com/device.
"""
import httpx

SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
DEVICE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SUBS_URL = "https://www.googleapis.com/youtube/v3/subscriptions"


class GoogleAuthError(Exception):
    pass


class Pending(Exception):
    def __init__(self, slow_down: bool = False):
        self.slow_down = slow_down


class GoogleYouTube:
    def __init__(self, client_id: str, client_secret: str, http: httpx.AsyncClient | None = None):
        self.client_id, self.client_secret = client_id, client_secret
        self._http = http

    def _c(self):
        return self._http or httpx.AsyncClient(timeout=20)

    async def start_device_flow(self) -> dict:
        async with self._c() as c:
            r = await c.post(DEVICE_URL, data={"client_id": self.client_id, "scope": SCOPE})
        if r.status_code != 200:
            raise GoogleAuthError(f"Google не выдал код: {r.text[:200]}")
        return r.json()  # device_code, user_code, verification_url, expires_in, interval

    async def poll_token(self, device_code: str) -> dict:
        async with self._c() as c:
            r = await c.post(TOKEN_URL, data={
                "client_id": self.client_id, "client_secret": self.client_secret, "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        d = r.json()
        err = d.get("error")
        if err == "authorization_pending":
            raise Pending()
        if err == "slow_down":
            raise Pending(slow_down=True)
        if err == "access_denied":
            raise GoogleAuthError("Доступ не разрешён.")
        if err == "expired_token":
            raise GoogleAuthError("Код истёк — запустите импорт ещё раз.")
        if err:
            raise GoogleAuthError(f"Ошибка Google: {err}")
        return d  # access_token, refresh_token, expires_in

    async def refresh(self, refresh_token: str) -> str:
        async with self._c() as c:
            r = await c.post(TOKEN_URL, data={"client_id": self.client_id, "client_secret": self.client_secret,
                                              "refresh_token": refresh_token, "grant_type": "refresh_token"})
        d = r.json()
        if "access_token" not in d:
            raise GoogleAuthError("Доступ к YouTube отозван или истёк — сделайте /import заново.")
        return d["access_token"]

    async def subscriptions(self, access_token: str) -> list[tuple[str, str]]:
        """[(channel_id, title), ...] — все подписки аккаунта."""
        out, page = [], None
        async with self._c() as c:
            while True:
                params = {"part": "snippet", "mine": "true", "maxResults": 50}
                if page:
                    params["pageToken"] = page
                r = await c.get(SUBS_URL, params=params, headers={"Authorization": f"Bearer {access_token}"})
                if r.status_code != 200:
                    raise GoogleAuthError(f"YouTube API: {r.status_code} {r.text[:200]}")
                d = r.json()
                for it in d.get("items", []):
                    sn = it["snippet"]
                    out.append((sn["resourceId"]["channelId"], sn.get("title") or sn["resourceId"]["channelId"]))
                page = d.get("nextPageToken")
                if not page:
                    return out
