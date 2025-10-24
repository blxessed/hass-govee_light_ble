from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.components.light import (ColorMode, LightEntity, ATTR_BRIGHTNESS, ATTR_RGB_COLOR)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GoveeCoordinator

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up a Lights."""
    # This gets the data update coordinator from hass.data as specified in your __init__.py
    coordinator: GoveeCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ].coordinator

    async_add_entities([
        GoveeBluetoothLight(coordinator)
    ], True)


class GoveeBluetoothLight(CoordinatorEntity, LightEntity):

    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB
    _attr_should_poll = False

    def __init__(self, coordinator: GoveeCoordinator):
        """Initialize."""
        super().__init__(coordinator)
        self._attr_name = coordinator.device_name
        self._attr_unique_id = f"{coordinator.device_address}"
        self._attr_device_info = DeviceInfo(
            #only generate device once!
            manufacturer="GOVEE",
            model=coordinator.device_name,
            serial_number=coordinator.device_address,
            identifiers={(DOMAIN, coordinator.device_address)}
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def brightness(self):
        """Return the current brightness (0-255)."""
        data = self.coordinator.data
        return data.brightness if data else None

    @property
    def is_on(self) -> bool | None:
        """Return true if light is on."""
        data = self.coordinator.data
        return data.state if data else None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the current RGB color."""
        data = self.coordinator.data
        return data.color if data else None

    async def async_turn_on(self, **kwargs):
        """Turn device on."""
        await self.coordinator.setStateBuffered(True)

        was_off = not self.is_on

        brightness: int | None = kwargs.get(ATTR_BRIGHTNESS)
        force_brightness = ATTR_BRIGHTNESS not in kwargs or was_off
        if brightness is None:
            # Use the last known brightness or fall back to full brightness so the strip lights up.
            current = self.brightness
            brightness = current if current not in (None, 0) else 255
        await self.coordinator.setBrightnessBuffered(int(brightness), force=force_brightness)

        if ATTR_RGB_COLOR in kwargs:
            red, green, blue = kwargs.get(ATTR_RGB_COLOR)
            await self.coordinator.setColorBuffered(red, green, blue, force=True)
        
        await self.coordinator.sendPacketBuffer()

    
    async def async_turn_off(self, **kwargs):
        """Turn device off."""
        await self.coordinator.setStateBuffered(False)
        await self.coordinator.sendPacketBuffer()
