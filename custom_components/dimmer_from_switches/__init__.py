from __future__ import annotations
import asyncio, logging, json
from .const import DOMAIN, ACTIONS, LOGGER, STORAGE_VERSION, STORAGE_KEY, PLATFORMS
from homeassistant.core import HomeAssistant
from homeassistant.const import EVENT_HOMEASSISTANT_START
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.components import mqtt
from homeassistant.helpers.storage import Store
from homeassistant.helpers.reload import (
    async_setup_reload_service,
    async_integration_yaml_config,
    async_reload_integration_platforms,
)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    LOGGER.info("Starting up Dimmer from Switches")

    if hass.is_running:
        await _load_and_sync_devices(hass, config)
    else:
        async def _on_start(_):
            await _load_and_sync_devices(hass, config)

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _on_start)

    async def handle_reload_service(call):
        LOGGER.info("Reloading Dimmer from Switches integration")

        config = await async_integration_yaml_config(hass, DOMAIN)
        if config is not None:
            await _load_and_sync_devices(hass, config)

    hass.services.async_register(DOMAIN, "reload", handle_reload_service)

    return True

async def _load_and_sync_devices(hass: HomeAssistant, config: dict):
    LOGGER.info("Loading configuration")

    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    stored = await store.async_load() or {}

    cfg = config.get(DOMAIN) or {}
    devices = cfg.get("devices", [])
    hass.data.setdefault(DOMAIN, {})["devices"] = devices

    # Get previous known IDs.

    known_ids = set(stored.get("known_ids", []))
    current_ids = set([d["id"] for d in devices])

    # Delete MQTT discovery for old devices.
    for old_id in known_ids - current_ids:
        await _clear_discovery(hass, old_id)

    # Publish MQTT device discovery.
    for device in devices:
        await _publish_discovery(hass, device)

    await store.async_save({
        "known_ids": list(current_ids)
    })

    hass.async_create_task(async_load_platform(hass, "event", DOMAIN, {}, config))

async def _publish_discovery(hass: HomeAssistant, config: dict):
    """Publish MQTT device discovery for our devices."""

    LOGGER.info("Publishing MQTT discovery for device %s", config['id'])

    node_id = f"dimmer_from_switches_{config['id']}"
    base = f"homeassistant/device_automation/{node_id}"
    topic = f"dimmer_from_switches/{config['id']}/action"
    device = {
        "identifiers": [node_id],
        "manufacturer": "Dimmer from Switches HACS Plugin",
        "model": "Dimmer from Switches",
        "name": config["name"]
    }

    for subtype in ACTIONS:
        discovery = {
            "automation_type": "trigger",
            "type": "action",
            "subtype": subtype,
            "payload": subtype,
            "topic": topic,
            "device": device,
        }

        # Create a typic per trigger.
        discovery_topic = f"{base}/action_{subtype}/config"

        await mqtt.async_publish(hass, discovery_topic, json.dumps(discovery), retain=True)

async def _clear_discovery(hass: HomeAssistant, device_id: str):
    LOGGER.info("Deleting MQTT discovery for device %s", device_id)

    node_id = f"dimmer_from_switches_{device_id}"
    base = f"homeassistant/device_automation/{node_id}"

    for subtype in ACTIONS:
        discovery_topic = f"{base}/action_{subtype}/config"
        await mqtt.async_publish(hass, discovery_topic, "", retain=True)
