"""The notification link must work without a login, using only its token."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from .conftest import collection, feature
from .test_integration import _entry, _mock


async def test_entity_picture_link_works_without_login(
    hass: HomeAssistant, aioclient_mock, hass_client_no_auth
) -> None:
    await async_setup_component(hass, "http", {})
    _mock(aioclient_mock, collection(feature("26AA1", start=dt_util.utcnow() - timedelta(hours=1))))
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.ergon_outages.image.ErgonOutageMapImage._async_fetch_tile",
        return_value=None,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        url = hass.states.get("image.ergon_outages_home_outage_map").attributes["entity_picture"]
        client = await hass_client_no_auth()
        resp = await client.get(url)
        assert resp.status == 200, await resp.text()
        assert resp.headers["Content-Type"] == "image/png"
        body = await resp.read()
        assert body[:4] == b"\x89PNG" and len(body) > 1000
