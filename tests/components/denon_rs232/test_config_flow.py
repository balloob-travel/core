"""Tests for the Denon RS232 config flow."""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.components.denon_rs232.config_flow import OPTION_PICK_MANUAL
from homeassistant.components.denon_rs232.const import DOMAIN
from homeassistant.components.usb import USBDevice
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_DEVICE, CONF_MODEL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from tests.common import MockConfigEntry

from . import MOCK_DEVICE, MOCK_MODEL


@pytest.fixture(autouse=True)
def mock_list_serial_ports() -> Generator[list[USBDevice]]:
    """Mock discovered serial ports."""
    ports = [
        USBDevice(
            device=MOCK_DEVICE,
            vid="123",
            pid="456",
            serial_number="mock-serial",
            manufacturer="mock-manuf",
            description=None,
        )
    ]

    with patch(
        "homeassistant.components.denon_rs232.config_flow.scan_serial_ports",
        return_value=ports,
    ):
        yield ports


async def test_user_form(hass: HomeAssistant) -> None:
    """Test we show the user form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_user_form_creates_entry(hass: HomeAssistant) -> None:
    """Test successful config flow creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    mock_receiver = AsyncMock()
    mock_receiver.connect = AsyncMock()
    mock_receiver.disconnect = AsyncMock()

    with patch(
        "homeassistant.components.denon_rs232.config_flow.DenonReceiver",
        return_value=mock_receiver,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_DEVICE: MOCK_DEVICE, CONF_MODEL: MOCK_MODEL},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "AVR-3805 / AVC-3890"
    assert result["data"] == {CONF_DEVICE: MOCK_DEVICE, CONF_MODEL: MOCK_MODEL}
    mock_receiver.connect.assert_awaited_once()
    mock_receiver.disconnect.assert_awaited_once()


@pytest.mark.parametrize(
    ("exception", "error"),
    (
        (ConnectionError("No response"), "cannot_connect"),
        (OSError("No such device"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ),
)
async def test_user_form_error(
    hass: HomeAssistant, exception: Exception, error: str
) -> None:
    """Test we handle connection errors."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    mock_receiver = AsyncMock()
    mock_receiver.connect = AsyncMock(side_effect=exception)

    with patch(
        "homeassistant.components.denon_rs232.config_flow.DenonReceiver",
        return_value=mock_receiver,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_DEVICE: MOCK_DEVICE, CONF_MODEL: MOCK_MODEL},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}


async def test_user_duplicate_port_aborts(hass: HomeAssistant) -> None:
    """Test we abort if the same port is already configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: MOCK_DEVICE, CONF_MODEL: MOCK_MODEL},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE: MOCK_DEVICE, CONF_MODEL: MOCK_MODEL},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_manual_form_creates_entry(hass: HomeAssistant) -> None:
    """Test creating entry with manual user input."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE: OPTION_PICK_MANUAL, CONF_MODEL: MOCK_MODEL},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"

    mock_receiver = AsyncMock()
    mock_receiver.connect = AsyncMock()
    mock_receiver.disconnect = AsyncMock()

    with patch(
        "homeassistant.components.denon_rs232.config_flow.DenonReceiver",
        return_value=mock_receiver,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_DEVICE: MOCK_DEVICE},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "AVR-3805 / AVC-3890"
    assert result["data"] == {CONF_DEVICE: MOCK_DEVICE, CONF_MODEL: MOCK_MODEL}
    mock_receiver.connect.assert_awaited_once()
    mock_receiver.disconnect.assert_awaited_once()


@pytest.mark.parametrize(
    ("exception", "error"),
    (
        (ConnectionError("No response"), "cannot_connect"),
        (OSError("No such device"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ),
)
async def test_manual_form_error_handling(
    hass: HomeAssistant, exception: Exception, error: str
) -> None:
    """Test creating entry with manual user input."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE: OPTION_PICK_MANUAL, CONF_MODEL: MOCK_MODEL},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"

    mock_receiver = AsyncMock()
    mock_receiver.connect = AsyncMock(side_effect=exception)

    with patch(
        "homeassistant.components.denon_rs232.config_flow.DenonReceiver",
        return_value=mock_receiver,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_DEVICE: MOCK_DEVICE},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": error}


async def test_manual_duplicate_port_aborts(hass: HomeAssistant) -> None:
    """Test we abort if the same port is already configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: MOCK_DEVICE, CONF_MODEL: MOCK_MODEL},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE: OPTION_PICK_MANUAL, CONF_MODEL: MOCK_MODEL},
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE: MOCK_DEVICE},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
