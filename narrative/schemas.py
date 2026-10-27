from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Certainty = Literal['confirmed', 'hypothesis', 'rumor', 'interpretation', 'refuted', 'unknown', 'contradiction']
Kind = Literal['world', 'campaign', 'character', 'place', 'item', 'relationship', 'knowledge', 'fact', 'detail', 'event', 'rule', 'faction', 'temporal_link']


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class World(Strict):
    calendar_label: str = 'Días locales relativos al inicio'
    current_day: int = Field(default=0, ge=0)
    mode: Literal['defined', 'discover'] = 'discover'
    description: str = ''
    established: bool = False


class Campaign(Strict):
    protagonist_id: str
    premise: str = ''
    mode: Literal['defined', 'discover'] = 'discover'
    current_day: int = Field(default=0, ge=0)
    status: Literal['active', 'paused', 'completed'] = 'active'


class CharacterRole(Strict):
    place_id: str
    label: str = Field(min_length=1, max_length=200)
    exclusive: bool = True


class CharacterChange(Strict):
    mode: Literal['evolution', 'correction']
    reason: str = Field(min_length=1, max_length=10000)
    based_on_revision: int = Field(ge=1)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)


class Character(Strict):
    aliases: list[str] = Field(default_factory=list)
    age: int | None = Field(default=None, ge=0)
    born_day: int | None = None
    origin: str = ''
    description: str = ''
    location_id: str | None = None
    status: str = 'Sin establecer'
    player_controlled: bool = False
    personality: dict[str, Any] = Field(default_factory=dict)
    psychology: dict[str, Any] = Field(default_factory=dict)
    life: dict[str, Any] = Field(default_factory=dict)
    balance: int = Field(default=0, ge=0)
    currency: str = 'Sin establecer'
    capabilities: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    routine: str = ''
    injuries: list[str] = Field(default_factory=list)
    roles: list[CharacterRole] = Field(default_factory=list, max_length=100)
    change_basis: CharacterChange | None = None


class Place(Strict):
    parent_id: str | None = None
    description: str = ''


class Item(Strict):
    owner_id: str | None = None
    quantity: int = Field(default=1, ge=0)
    created_day: int | None = None
    description: str = ''


class Relationship(Strict):
    from_id: str
    to_id: str
    dimensions: dict[str, str] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str = ''


class Knowledge(Strict):
    character_id: str
    fact_id: str
    belief: Literal['knows', 'believes', 'suspects', 'hides'] = 'believes'
    learned_from: str
    acquired_day: int | None = None


class Fact(Strict):
    statement: str = Field(min_length=1)
    certainty: Certainty = 'unknown'
    visibility: Literal['narrator', 'shared'] = 'narrator'
    known_by: list[str] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)
    day: int | None = None
    end_day: int | None = None
    contradicts: list[str] = Field(default_factory=list)


class DetailConnection(Strict):
    target_id: str
    relation: str = Field(min_length=1)
    certainty: Certainty = 'hypothesis'
    evidence_ids: list[str] = Field(default_factory=list)


class Detail(Fact):
    tags: list[str] = Field(default_factory=list)
    features: dict[str, str] = Field(default_factory=dict)
    place_id: str | None = None
    item_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    connections: list[DetailConnection] = Field(default_factory=list)


class Action(Strict):
    type: Literal['move', 'transfer', 'spend', 'reveal', 'mention', 'interaction', 'emotion', 'ability', 'use_item', 'injury', 'protagonist_decision']
    character_id: str | None = None
    from_id: str | None = None
    to_id: str | None = None
    item_id: str | None = None
    fact_id: str | None = None
    amount: int | None = Field(default=None, gt=0)
    quantity: int | None = Field(default=None, gt=0)
    learned_from: str = ''
    as_strangers: bool = False
    changes: dict[str, Any] = Field(default_factory=dict)
    reason: str = ''
    change_basis: CharacterChange | None = None
    ability: str = ''
    claimed_days: int | None = Field(default=None, ge=0)
    injury: str = ''
    decision: str = ''
    authorized_turn_id: str | None = None


class SimulationInterval(Strict):
    character_id: str
    development: Literal['routine', 'undeveloped', 'deferred']
    summary: str


class Simulation(Strict):
    start_day: int = Field(ge=0)
    end_day: int = Field(ge=1)
    intervals: list[SimulationInterval] = Field(default_factory=list, max_length=200)
    policy: Literal['explicit-routines-only-v1'] = 'explicit-routines-only-v1'


class SceneCharacter(Strict):
    character_id: str
    name: str = Field(min_length=1, max_length=200)
    personality: dict[str, Any] = Field(default_factory=dict)


class Event(Strict):
    summary: str = Field(min_length=1)
    certainty: Certainty = 'unknown'
    visibility: Literal['narrator', 'shared'] = 'narrator'
    known_by: list[str] = Field(default_factory=list)
    day: int | None = None
    end_day: int | None = None
    participants: list[str] = Field(default_factory=list)
    predecessors: list[str] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    development: Literal['confirmed', 'routine', 'undeveloped', 'deferred'] = 'confirmed'
    simulation: Simulation | None = None
    cast: list[SceneCharacter] = Field(default_factory=list, max_length=200)


class Rule(Strict):
    statement: str
    certainty: Certainty = 'confirmed'
    forbidden_abilities: list[str] = Field(default_factory=list)
    visibility: Literal['narrator', 'shared'] = 'narrator'
    known_by: list[str] = Field(default_factory=list)


class Faction(Strict):
    description: str = ''
    members: list[str] = Field(default_factory=list)
    resources: dict[str, Any] = Field(default_factory=dict)


class TemporalLink(Strict):
    from_world_id: str
    to_world_id: str
    certainty: Certainty = 'unknown'
    relation: str = 'Equivalencia desconocida'
    evidence_ids: list[str] = Field(default_factory=list)


DATA_MODELS = dict(world=World, campaign=Campaign, character=Character, place=Place, item=Item,
                   relationship=Relationship, knowledge=Knowledge, fact=Fact, detail=Detail, event=Event,
                   rule=Rule, faction=Faction, temporal_link=TemporalLink)


class RecordInput(Strict):
    kind: Kind
    name: str = Field(min_length=1, max_length=200)
    world_id: str | None = None
    campaign_id: str | None = None
    source_id: str | None = None
    source_ref: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any]

    @model_validator(mode='after')
    def validate_data(self):
        self.data = DATA_MODELS[self.kind].model_validate(self.data).model_dump()
        return self


class ProposalInput(Strict):
    title: str = Field(min_length=1, max_length=240)
    operation: Literal['create', 'replace'] = 'create'
    target_id: str | None = None
    expected_version: int | None = Field(default=None, ge=0)
    record: RecordInput


class Wizard(Strict):
    universe_id: str | None = None
    world_id: str | None = None
    protagonist_id: str | None = None
    universe_name: str = 'Mi universo'
    campaign_name: str = Field(min_length=1, max_length=200)
    world_name: str = ''
    mode: Literal['defined', 'discover'] = 'discover'
    premise: str = ''
    protagonist_name: str = Field(min_length=1, max_length=200)
    personality: str = ''
    world_rules: str = ''


class Acceptance(Strict):
    expected_version: int = Field(ge=0)
    decision: str = Field(min_length=1)
    acknowledge_warnings: bool = False
    authorize_character_change: bool = False


class TurnInput(Strict):
    role: Literal['user', 'narrator']
    content: str = Field(min_length=1, max_length=50000)
    cast: list[SceneCharacter] = Field(default_factory=list, max_length=200)
    request_id: str | None = Field(default=None, max_length=36)
    expected_version: int | None = Field(default=None, ge=0)

    @field_validator('request_id')
    @classmethod
    def valid_request_id(cls, value):
        return str(UUID(value)) if value is not None else None

    @model_validator(mode='after')
    def cast_is_narrator_metadata(self):
        if self.role == 'user' and self.cast:
            raise ValueError('El reparto se declara en la narración, no en las decisiones del jugador.')
        return self


class PlayerMessageReview(Strict):
    decision: Literal['confirm', 'reject']
    expected_version: int = Field(ge=0)
    content: str = Field(min_length=1, max_length=50000)


class Advance(Strict):
    world_id: str
    days: int = Field(gt=0, le=36500)
    relevant_character_ids: list[str] = Field(default_factory=list, max_length=200)
