from __future__ import annotations
import asyncio, logging, json
from .const import DOMAIN, ACTIONS
from homeassistant.core import HomeAssistant
from homeassistant.const import EVENT_HOMEASSISTANT_START
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.components import mqtt

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    cfg = config.get(DOMAIN) or {}
    devices = cfg.get("devices", [])
    hass.data.setdefault(DOMAIN, {})["devices"] = devices

    # Support configuration reload
    hass.async_create_task(async_load_platform(hass, "event", DOMAIN, {}, config))

    async def _on_start(_):
        # Publish MQTT device discovery
        for device in devices:
            await _publish_discovery(hass, device)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _on_start)

    return True

async def _publish_discovery(hass: HomeAssistant, config: dict):
    """Publish MQTT device discovery for our devices."""

    node_id = f"dimmer_from_switches_{config['id']}"
    base = f"homeassistant/device_automation/{node_id}"
    topic = f"dimmer_from_switches/{config['id']}/action"
    device = {
        "identifiers": [node_id],
        "manufacturer": "Dimmer from Switches HACS Plugin",
        "model": "Dimmer from Switches",
        "name": config["id"]
    }

    for subtype in ACTIONS:
        discovery = {
            "automation_type": "trigger",
            # "platform": "device_automation",
            "type": "action",
            "subtype": subtype,
            "payload": subtype,
            "topic": topic,
            "device": device,
        }

        # Create a typic per trigger.
        discovery_topic = f"{base}/action_{subtype}/config"

        await mqtt.async_publish(hass, discovery_topic, json.dumps(discovery), retain=True)
