"""Media player platform for the Denon RS232 integration."""

from __future__ import annotations

from denon_rs232 import (
    MIN_VOLUME_DB,
    VOLUME_DB_RANGE,
    DenonReceiver,
    DenonState,
    InputSource,
    PowerState,
)

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, DenonRS232ConfigEntry

POWER_STATE_DENON_TO_HA: dict[PowerState, MediaPlayerState] = {
    PowerState.ON: MediaPlayerState.ON,
    PowerState.STANDBY: MediaPlayerState.OFF,
}

INPUT_SOURCE_DENON_TO_HA: dict[InputSource, str] = {
    InputSource.PHONO: "phono",
    InputSource.CD: "cd",
    InputSource.TUNER: "tuner",
    InputSource.DVD: "dvd",
    InputSource.VDP: "vdp",
    InputSource.TV: "tv",
    InputSource.DBS_SAT: "dbs_sat",
    InputSource.VCR_1: "vcr_1",
    InputSource.VCR_2: "vcr_2",
    InputSource.VCR_3: "vcr_3",
    InputSource.V_AUX: "v_aux",
    InputSource.CDR_TAPE1: "cdr_tape1",
    InputSource.MD_TAPE2: "md_tape2",
    InputSource.HDP: "hdp",
    InputSource.DVR: "dvr",
    InputSource.TV_CBL: "tv_cbl",
    InputSource.SAT: "sat",
    InputSource.NET_USB: "net_usb",
    InputSource.DOCK: "dock",
    InputSource.IPOD: "ipod",
    InputSource.BD: "bd",
    InputSource.SAT_CBL: "sat_cbl",
    InputSource.MPLAY: "mplay",
    InputSource.GAME: "game",
    InputSource.AUX1: "aux1",
    InputSource.AUX2: "aux2",
    InputSource.NET: "net",
    InputSource.BT: "bt",
    InputSource.USB_IPOD: "usb_ipod",
    InputSource.EIGHT_K: "eight_k",
    InputSource.PANDORA: "pandora",
    InputSource.SIRIUSXM: "siriusxm",
    InputSource.SPOTIFY: "spotify",
    InputSource.FLICKR: "flickr",
    InputSource.IRADIO: "iradio",
    InputSource.SERVER: "server",
    InputSource.FAVORITES: "favorites",
    InputSource.LASTFM: "lastfm",
    InputSource.XM: "xm",
    InputSource.SIRIUS: "sirius",
    InputSource.HDRADIO: "hdradio",
    InputSource.DAB: "dab",
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: DenonRS232ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Denon RS232 media player."""
    receiver = config_entry.runtime_data
    async_add_entities([DenonRS232MediaPlayer(receiver, config_entry)])


class DenonRS232MediaPlayer(MediaPlayerEntity):
    """Representation of a Denon receiver controlled over RS232."""

    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )
    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "receiver"
    _attr_should_poll = False

    def __init__(
        self,
        receiver: DenonReceiver,
        config_entry: DenonRS232ConfigEntry,
    ) -> None:
        """Initialize the media player."""
        self._receiver = receiver
        self._attr_unique_id = config_entry.entry_id

        model = receiver.model
        model_name = model.name if model else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            manufacturer="Denon",
            model=model_name,
            name=config_entry.title,
        )

        if model:
            self._attr_source_list = sorted(
                INPUT_SOURCE_DENON_TO_HA[source] for source in model.input_sources
            )
        else:
            self._attr_source_list = sorted(
                INPUT_SOURCE_DENON_TO_HA[source] for source in InputSource
            )

        self._volume_min = MIN_VOLUME_DB
        self._volume_range = VOLUME_DB_RANGE
        self._async_update_from_state(receiver.state)

    async def async_added_to_hass(self) -> None:
        """Subscribe to receiver state updates."""
        self.async_on_remove(self._receiver.subscribe(self._async_on_state_update))

    @callback
    def _async_on_state_update(self, state: DenonState | None) -> None:
        """Handle a state update from the receiver."""
        if state is None:
            self._attr_available = False
        else:
            self._attr_available = True
            self._async_update_from_state(state)
        self.async_write_ha_state()

    @callback
    def _async_update_from_state(self, state: DenonState) -> None:
        """Update entity attributes from a DenonState snapshot."""
        self._attr_state = POWER_STATE_DENON_TO_HA.get(state.power)
        self._attr_source = INPUT_SOURCE_DENON_TO_HA.get(state.input_source)
        self._attr_is_volume_muted = state.mute

        if state.volume_min is not None:
            self._volume_min = state.volume_min

            if state.volume_max is not None and state.volume_max > state.volume_min:
                self._volume_range = state.volume_max - state.volume_min

        if state.volume is not None:
            self._attr_volume_level = (
                state.volume - self._volume_min
            ) / self._volume_range
        else:
            self._attr_volume_level = None

    async def async_turn_on(self) -> None:
        """Turn the receiver on."""
        await self._receiver.power_on()

    async def async_turn_off(self) -> None:
        """Turn the receiver off."""
        await self._receiver.power_standby()

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1."""
        db = volume * self._volume_range + self._volume_min
        await self._receiver.set_volume(db)

    async def async_volume_up(self) -> None:
        """Volume up."""
        await self._receiver.volume_up()

    async def async_volume_down(self) -> None:
        """Volume down."""
        await self._receiver.volume_down()

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute."""
        if mute:
            await self._receiver.mute_on()
        else:
            await self._receiver.mute_off()

    async def async_select_source(self, source: str) -> None:
        """Select input source."""
        for input_source, ha_source in INPUT_SOURCE_DENON_TO_HA.items():
            if ha_source == source:
                await self._receiver.select_input_source(input_source)
                break
