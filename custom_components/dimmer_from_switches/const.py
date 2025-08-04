import logging

DOMAIN = "dimmer_from_switches"
ACTIONS = ["on","off","brightness_move_up","brightness_move_down","brightness_stop"]
LOGGER = logging.getLogger(__package__)
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_data"
PLATFORMS = ["event"]
