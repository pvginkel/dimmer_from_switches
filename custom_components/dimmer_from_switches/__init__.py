from __future__ import annotations

import json

from homeassistant.components import mqtt
from homeassistant.const import EVENT_HOMEASSISTANT_START
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.reload import async_integration_yaml_config
from homeassistant.helpers.storage import Store

from .const import ACTIONS, DOMAIN, LOGGER, STORAGE_KEY, STORAGE_VERSION


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    LOGGER.info("Starting up Dimmer from Switches")

    # Publish MQTT discovery on every connect. After a power outage Home
    # Assistant comes up long before the MQTT broker, and a publish without a
    # connection raises.
    @callback
    def _on_mqtt_connection(connected: bool):
        if connected:
            hass.async_create_task(_sync_discovery(hass))

    mqtt.async_subscribe_connection_status(hass, _on_mqtt_connection)

    if hass.is_running:
        await _load_devices(hass, config)
    else:
        async def _on_start(_):
            await _load_devices(hass, config)

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _on_start)

    async def handle_reload_service(call):
        LOGGER.info("Reloading Dimmer from Switches integration")

        config = await async_integration_yaml_config(hass, DOMAIN)
        if config is not None:
            await _load_devices(hass, config)

    hass.services.async_register(DOMAIN, "reload", handle_reload_service)

    return True

async def _load_devices(hass: HomeAssistant, config: dict):
    LOGGER.info("Loading configuration")

    cfg = config.get(DOMAIN) or {}
    hass.data.setdefault(DOMAIN, {})["devices"] = cfg.get("devices", [])

    # The event entities and the switch listeners don't need MQTT, so load
    # them before publishing: a failed publish must not keep them from loading.
    hass.async_create_task(async_load_platform(hass, "event", DOMAIN, {}, config))

    # When MQTT isn't connected yet, the connection callback publishes.
    if mqtt.is_connected(hass):
        await _sync_discovery(hass)

async def _sync_discovery(hass: HomeAssistant):
    devices = hass.data.get(DOMAIN, {}).get("devices")
    if devices is None:
        # Not loaded yet; _load_devices publishes once it has.
        return

    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    stored = await store.async_load() or {}

    # Get previous known IDs.

    known_ids = set(stored.get("known_ids", []))
    current_ids = {d["id"] for d in devices}

    try:
        # Delete MQTT discovery for old devices.
        for old_id in known_ids - current_ids:
            await _clear_discovery(hass, old_id)

        # Publish MQTT device discovery.
        for device in devices:
            await _publish_discovery(hass, device)
    except HomeAssistantError as err:
        LOGGER.warning("Publishing MQTT discovery failed, retrying on the next MQTT connect: %s", err)
        return

    await store.async_save({
        "known_ids": list(current_ids)
    })

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
