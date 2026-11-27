"""Long-lived, unguessable map links for notifications.

The image entity's own URL carries a token that rotates every 5 minutes. The
iOS app re-downloads attachments whenever a notification is expanded, so those
links break minutes later. Each notification instead gets its own link that
ends in .png (so the phone knows the file type), works without a login,
and keeps serving the same snapshot for LINK_TTL.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import secrets

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.http import KEY_HASS
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .renderer import MapSpec, async_render_spec

LINK_TTL = timedelta(days=7)
MAX_LINKS = 30  # snapshots are ~150-300 kB each
URL_PATH = "/api/ergon_outages/map/{token}.png"
_DATA_LINKS = f"{DOMAIN}_map_links"


@dataclass
class MapLink:
    spec: MapSpec
    created: datetime
    png: bytes | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@callback
def async_setup_map_links(hass: HomeAssistant) -> None:
    """Register the view once; called from async_setup_entry."""
    if _DATA_LINKS not in hass.data:
        hass.data[_DATA_LINKS] = OrderedDict()
        hass.http.register_view(ErgonMapLinkView())


def _links(hass: HomeAssistant) -> OrderedDict[str, MapLink]:
    async_setup_map_links(hass)
    return hass.data[_DATA_LINKS]


@callback
def async_create_map_link(hass: HomeAssistant, spec: MapSpec) -> str:
    """Return a new relative URL serving the map described by `spec`.

    The spec carries its own geometry, so the link still works after the
    outage has cleared or the location has been reconfigured.
    """
    links = _links(hass)
    cutoff = dt_util.utcnow() - LINK_TTL
    for token in [t for t, link in links.items() if link.created < cutoff]:
        del links[token]
    while len(links) >= MAX_LINKS:
        links.popitem(last=False)
    token = secrets.token_urlsafe(24)
    links[token] = MapLink(spec=spec, created=dt_util.utcnow())
    return URL_PATH.format(token=token)


class ErgonMapLinkView(HomeAssistantView):
    """Serves map snapshots by token (no login: the token is the secret)."""

    url = "/api/ergon_outages/map/{token:[A-Za-z0-9_-]{16,64}}.png"
    name = "api:ergon_outages:map"
    requires_auth = False

    async def get(self, request: web.Request, token: str) -> web.Response:
        hass = request.app[KEY_HASS]
        link = hass.data.get(_DATA_LINKS, {}).get(token)
        if link is None or link.created < dt_util.utcnow() - LINK_TTL:
            raise web.HTTPNotFound

        async with link.lock:
            if link.png is None:
                # Rendered on first download; later downloads (e.g. iOS
                # re-fetching on expand) get the same picture.
                link.png = await async_render_spec(hass, link.spec)

        return web.Response(
            body=link.png,
            content_type="image/png",
            headers={"Cache-Control": "private, max-age=604800"},
        )
