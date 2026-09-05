"""Optional simultaneity layer.

Importing from this module is the explicit opt-in boundary. Ordinary foliot
actions, stores, and drivers require none of these names.
"""

from foliot.events._api import (
    BaseEvent,
    DecisionContext,
    EntityIdTemplate,
    EventAction,
    EventConfigurationError,
    EventId,
    EventIdTemplate,
    Events,
    EventStore,
    EventTxn,
    IntentRecord,
    Outcome,
    ResolutionContext,
    end_event,
    open_event,
)
from foliot.events.memory import EventMemoryStore

__all__ = [
    "BaseEvent",
    "DecisionContext",
    "EntityIdTemplate",
    "EventAction",
    "EventConfigurationError",
    "EventId",
    "EventIdTemplate",
    "EventMemoryStore",
    "EventStore",
    "EventTxn",
    "Events",
    "IntentRecord",
    "Outcome",
    "ResolutionContext",
    "end_event",
    "open_event",
]
