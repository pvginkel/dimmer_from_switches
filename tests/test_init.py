"""Test component setup."""
import copy
from unittest.mock import Mock, patch

import paho.mqtt.client as paho
from homeassistant.const import EVENT_HOMEASSISTANT_START
from homeassistant.core import CoreState
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockMqttReasonCode

from custom_components.dimmer_from_switches.const import DOMAIN

CONFIG = {
    DOMAIN: {
        "devices": [
            {
                "name": "Test: Dimmer",
                "id": "test_dimmer",
                "up_switch": "switch.test_l1",
                "down_switch": "switch.test_l2",
            }
        ]
    }
}

DISCOVERY_TOPIC = (
    "homeassistant/device_automation/dimmer_from_switches_test_dimmer/action_on/config"
)
ACTION_TOPIC = "dimmer_from_switches/test_dimmer/action"


def _published(mqtt_client_mock):
    return [(c.args[0], c.args[1]) for c in mqtt_client_mock.publish.call_args_list]


def _topics(mqtt_client_mock):
    return [topic for topic, _ in _published(mqtt_client_mock)]


async def _short_press(hass, entity_id):
    hass.states.async_set(entity_id, "off")
    hass.states.async_set(entity_id, "on")
    hass.states.async_set(entity_id, "off")
    await hass.async_block_till_done()


async def test_async_setup(hass, mqtt_mock):
    """Test the component gets setup."""
    assert await async_setup_component(hass, DOMAIN, {}) is True


async def test_broker_unreachable_at_start(
    hass, mock_hass_config, mqtt_client_mock, mqtt_mock_entry
):
    """After a power outage Home Assistant starts before the MQTT broker is up.

    The dimmers must still load, and the discovery must be published once the
    broker is reachable.
    """
    connected = False
    publish = mqtt_client_mock.publish.side_effect

    def _publish(topic, payload, qos, retain, *args):
        # paho refuses a QoS 0 publish without a socket.
        if not connected:
            return Mock(mid=None, rc=paho.MQTT_ERR_NO_CONN)
        return publish(topic, payload, qos, retain)

    mqtt_client_mock.publish.side_effect = _publish

    hass.set_state(CoreState.not_running)
    mqtt_client = await mqtt_mock_entry()
    # The fixture always connects; take the connection away again.
    mqtt_client.connected = False
    mqtt_client_mock.on_disconnect(None, None, 0, MockMqttReasonCode())
    await hass.async_block_till_done()

    assert await async_setup_component(hass, DOMAIN, CONFIG)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_START)
    await hass.async_block_till_done()

    assert hass.states.get("event.test_dimmer") is not None
    assert DISCOVERY_TOPIC not in _topics(mqtt_client_mock)

    # The broker comes up.
    connected = True
    mqtt_client.connected = True
    mqtt_client_mock.on_connect(None, None, None, MockMqttReasonCode())
    await hass.async_block_till_done()

    assert DISCOVERY_TOPIC in _topics(mqtt_client_mock)

    # A short press on the up switch publishes "on".
    await _short_press(hass, "switch.test_l1")
    assert (ACTION_TOPIC, "on") in _published(mqtt_client_mock)


async def test_reconnect_republishes_discovery(hass, mqtt_client_mock, mqtt_mock):
    """A broker restart republishes the discovery."""
    assert await async_setup_component(hass, DOMAIN, CONFIG)
    await hass.async_block_till_done()

    mqtt_client_mock.publish.reset_mock()
    mqtt_client_mock.on_disconnect(None, None, 0, MockMqttReasonCode())
    await hass.async_block_till_done()
    mqtt_client_mock.on_connect(None, None, None, MockMqttReasonCode())
    await hass.async_block_till_done()

    assert DISCOVERY_TOPIC in _topics(mqtt_client_mock)


async def test_reload_replaces_switch_listeners(hass, mqtt_client_mock, mqtt_mock):
    """A reload moves a dimmer to its new switches and drops the old listeners."""
    assert await async_setup_component(hass, DOMAIN, CONFIG)
    await hass.async_block_till_done()

    config = copy.deepcopy(CONFIG)
    config[DOMAIN]["devices"][0]["up_switch"] = "switch.other_l1"
    with patch("homeassistant.config.load_yaml_config_file", return_value=config):
        await hass.services.async_call(DOMAIN, "reload", blocking=True)
    await hass.async_block_till_done()

    assert hass.states.get("event.test_dimmer") is not None

    mqtt_client_mock.publish.reset_mock()
    await _short_press(hass, "switch.test_l1")
    assert (ACTION_TOPIC, "on") not in _published(mqtt_client_mock)

    await _short_press(hass, "switch.other_l1")
    assert _published(mqtt_client_mock).count((ACTION_TOPIC, "on")) == 1
