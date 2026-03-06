"""Test fixtures for the Denon RS232 integration."""

from unittest.mock import AsyncMock, MagicMock, patch

from denon_rs232 import (
    DenonReceiver,
    DenonState,
    DigitalInputMode,
    InputSource,
    PowerState,
    TunerBand,
    TunerMode,
)
from denon_rs232.models import MODELS
import pytest

from homeassistant.components.denon_rs232.const import DOMAIN
from homeassistant.const import CONF_DEVICE, CONF_MODEL
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from tests.common import MockConfigEntry

from . import MOCK_DEVICE, MOCK_MODEL


def _default_state() -> DenonState:
    """Return a DenonState with typical defaults."""
    return DenonState(
        power=PowerState.ON,
        main_zone=True,
        volume=-30.0,
        volume_min=-80,
        volume_max=10,
        mute=False,
        input_source=InputSource.CD,
        surround_mode="STEREO",
        digital_input=DigitalInputMode.AUTO,
        tuner_band=TunerBand.FM,
        tuner_mode=TunerMode.AUTO,
    )


@pytest.fixture
def mock_receiver() -> MagicMock:
    """Create a mock DenonReceiver."""
    receiver = MagicMock(spec=DenonReceiver)
    receiver.connect = AsyncMock()
    receiver.disconnect = AsyncMock()
    receiver.power_on = AsyncMock()
    receiver.power_standby = AsyncMock()
    receiver.set_volume = AsyncMock()
    receiver.volume_up = AsyncMock()
    receiver.volume_down = AsyncMock()
    receiver.mute_on = AsyncMock()
    receiver.mute_off = AsyncMock()
    receiver.select_input_source = AsyncMock()
    receiver.set_surround_mode = AsyncMock()
    receiver.connected = True
    receiver.state = _default_state()
    receiver.model = MODELS[MOCK_MODEL]

    subscribers = receiver._subscribers = []

    def subscribe(callback):
        subscribers.append(callback)
        return lambda: subscribers.remove(callback)

    receiver.subscribe = subscribe

    def mock_state(state: DenonState | None) -> None:
        receiver.state = state
        for sub in list(subscribers):
            sub(state)

    receiver.mock_state = mock_state

    return receiver


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Create a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: MOCK_DEVICE, CONF_MODEL: MOCK_MODEL},
        title=MODELS[MOCK_MODEL].name,
    )


@pytest.fixture
async def init_components(
    hass: HomeAssistant, mock_receiver: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Initialize the Denon component."""
    hass.config.components.add("usb")
    mock_config_entry.add_to_hass(hass)
    with patch(
        "homeassistant.components.denon_rs232.DenonReceiver",
        return_value=mock_receiver,
    ):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
        await hass.async_block_till_done()
        await hass.async_block_till_done()
