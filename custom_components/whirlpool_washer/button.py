"""Button entities for Whirlpool Washer."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import WhirlpoolConfigEntry, WhirlpoolDataUpdateCoordinator
from .entity import WhirlpoolEntity


@dataclass(frozen=True, kw_only=True)
class WhirlpoolButtonEntityDescription(ButtonEntityDescription):
    """Button entity description for a laundry cycle command."""

    # Wire verb, from the Android app's CycleCommandName enum
    command: str
    # applianceState values the command applies to; the button is
    # unavailable otherwise. Sending pause to an idle washer is accepted
    # and wakes it into "programming", so don't offer it there.
    valid_states: frozenset[str]


BUTTONS: tuple[WhirlpoolButtonEntityDescription, ...] = (
    WhirlpoolButtonEntityDescription(
        key="pause_cycle",
        translation_key="pause_cycle",
        command="pause",
        valid_states=frozenset({"running"}),
    ),
    WhirlpoolButtonEntityDescription(
        key="resume_cycle",
        translation_key="resume_cycle",
        command="resume",
        valid_states=frozenset({"pause"}),
    ),
    WhirlpoolButtonEntityDescription(
        key="cancel_cycle",
        translation_key="cancel_cycle",
        command="cancel",
        valid_states=frozenset({"running", "pause", "delayed"}),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WhirlpoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Whirlpool Washer button entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        WhirlpoolButton(coordinator, description) for description in BUTTONS
    )


class WhirlpoolButton(WhirlpoolEntity, ButtonEntity):
    """Whirlpool Washer button entity."""

    entity_description: WhirlpoolButtonEntityDescription

    def __init__(
        self,
        coordinator: WhirlpoolDataUpdateCoordinator,
        description: WhirlpoolButtonEntityDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def _washer(self) -> dict:
        return (self.coordinator.data or {}).get("washer") or {}

    @property
    def available(self) -> bool:
        return (
            super().available
            and self._washer.get("applianceState")
            in self.entity_description.valid_states
        )

    async def async_press(self) -> None:
        # The app addresses cycle commands to the "washer" cavity and echoes
        # the cavity's current sessionId; without it the washer answers L006.
        await self.coordinator.async_send_command(
            self.entity_description.command,
            {"addressee": "washer", "sessionId": self._washer.get("sessionId", "")},
        )
