from __future__ import annotations
import asyncio, logging
from .const import DOMAIN, ACTIONS
from dataclasses import dataclass
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.event import EventEntity
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers import entity_registry as er
from homeassistant.components import mqtt

_LOGGER = logging.getLogger(__name__)

async def async_setup_platform(hass: HomeAssistant, config, async_add_entities, discovery_info=None):
    devices = hass.data[DOMAIN]["devices"]
    entities = []
    binders = []

    for c in devices:
        ctrl = ControllerEvent(hass, c['name'], c['id'])
        entities.append(ctrl)

        # Hide sources if requested.
        if c.get("hide_sources", False):
            await _hide(hass, c.get("up_switch"))
            await _hide(hass, c.get("down_switch"))

        binder = SwitchPairBinder(
            hass=hass,
            ctrl=ctrl,
            up=c["up_switch"],
            down=c["down_switch"],
            press_window_ms=int(c.get("press_window_ms", 500)),
        )
        binders.append(binder)

    async_add_entities(entities)

    # After adding entities, bind and start.
    for b in binders:
        await b.async_start()

async def _hide(hass: HomeAssistant, entity_id: str | None):
    reg = er.async_get(hass)
    ent = reg.async_get(entity_id)

    if ent and ent.hidden_by is None:
        reg.async_update_entity(entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION)

class ControllerEvent(EventEntity):
    _attr_should_poll = False
    _attr_event_types = ACTIONS

    def __init__(self, hass: HomeAssistant, name: str, cid: str):
        self.hass = hass
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{cid}"
        self._last_type: str | None = None
        self._mqtt_topic = f"dimmer_from_switches/{cid}/action"

    @callback
    def fire(self, event_type: str):
        if event_type not in ACTIONS:
            return

        self._last_type = event_type

        # Also publish an MQTT action payload for device triggers.
        asyncio.create_task(mqtt.async_publish(self.hass, self._mqtt_topic, event_type, retain=False))

        self.async_write_ha_state()

    @property
    def event_type(self) -> str | None:
        return self._last_type

@dataclass
class _PressState:
    task: asyncio.Task | None = None
    active: bool = False
    long_started: bool = False

class SwitchPairBinder:
    """Implements device logic to switch between dimmer and on/off mode."""
    def __init__(self, hass: HomeAssistant, ctrl: ControllerEvent, up: str, down: str, press_window_ms: int):
        self.hass = hass
        self.ctrl = ctrl
        self.up = up
        self.down = down
        self.window = press_window_ms / 1000.0
        self._up = _PressState()
        self._down = _PressState()
        self._unsub = None

    async def async_start(self):
        @callback
        def _handle(evt):
            eid = evt.data["entity_id"]
            new = evt.data["new_state"]
            old = evt.data["old_state"]
            new_s = new.state if new else None
            old_s = old.state if old else None

            # Only respond to explicit 'off' -> 'on' changes. This filters out
            # state changes where the source change is an 'unavailable' change
            # or something like that.

            if new_s == "on" and old_s == "off":
                if eid == self.up:
                    self._start_press(self._up, long_action="brightness_move_up", short_action="on")
                elif eid == self.down:
                    self._start_press(self._down, long_action="brightness_move_down", short_action="off")

            # And the reverse, 'on' -> 'off'.
            if new_s == "off" and old_s == "on":
                if eid == self.up:
                    self._end_press(self._up)
                elif eid == self.down:
                    self._end_press(self._down)

        self._unsub = async_track_state_change_event(self.hass, [self.up, self.down], _handle)

    def _start_press(self, ps: _PressState, long_action: str, short_action: str):
        ps.active = True
        ps.long_started = False
        ps.short_action = short_action
        ps.long_action  = long_action

        async def _timer():
            try:
                await asyncio.sleep(self.window)
            except asyncio.CancelledError:
                return
            
            # Go into long press mode if the timer elapsed without
            # being cancelled.
            if ps.active and not ps.long_started:
                ps.long_started = True
                self.ctrl.fire(long_action)

        ps.task = asyncio.create_task(_timer())

    def _end_press(self, ps: _PressState):
        # Cancel timer if it's still running.
        if ps.task and not ps.task.done():
            ps.task.cancel()

        # If the timer didn't elapsed, this was a short click.
        if ps.active and not ps.long_started:
            self.ctrl.fire(ps.short_action)
        else:
            # Otherwise, the long action was fired already and
            # we just have to fire the stop action.
            self.ctrl.fire("brightness_stop")

        ps.active = False
        ps.long_started = False
        ps.task = None
