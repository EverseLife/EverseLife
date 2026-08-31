"""The world speaks ids: every stored vault name becomes its D-251 key

D-251 gave every piece of content a stable English id; the engine, the wire
and the client switch to them in one release, and this carries the live world
over. Touched: every column that stores a goods name by value (the
`e1c8f3a24b70` list plus everything it missed), the quality tiers of the
market book, gear slots, building kinds (with the column default), the keys
and two worded values of `node.properties`, `work_order.payload.type_key` and
the goods suffixes of `daily_metric.key`.

Event history is left alone on purpose, as every rename before this one did:
it says what was true when it happened. Old flavors of meals and items also
stay: a flavor is compared, not resolved, and rewriting a comparison key
would fake a variety the player did not eat.

The maps below are generated from `vault/renames.json` and embedded verbatim:
a migration must not depend on a file that will keep changing after it ran.

Revision ID: b7d251aa10c4
Revises: a8d2480f11ce
Create Date: 2026-08-30 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b7d251aa10c4'
down_revision: str | None = 'a8d2480f11ce'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Russian goods name -> id, straight from the vault's renames.json.
GOODS = {
    "Автоматическая станция": "auto_station",
    "Администрация": "administration",
    "Аккумулятор": "battery",
    "Аффинированное золото": "refined_gold",
    "Аффинированное серебро": "refined_silver",
    "Баллон высокого давления": "high_pressure_tank",
    "Библиотека": "library",
    "Бинт": "bandage",
    "Био-камера": "bio_chamber",
    "Биопринтер": "bioprinter",
    "Биопринтер Предтеч": "precursor_bioprinter",
    "Бобы": "beans",
    "Болванка рецепта": "recipe_blank",
    "Бродильный чан": "fermentation_vat",
    "Бронза": "bronze",
    "Буровая установка": "drilling_rig",
    "Верстак": "workbench",
    "Верфь Предтеч": "precursor_shipyard",
    "Верёвка": "rope",
    "Ветряк": "windmill",
    "Вода": "water",
    "Водяное колесо": "water_wheel",
    "Волокно": "fiber",
    "Вольфрам": "tungsten",
    "Вольфрамовый резец": "tungsten_cutter",
    "Высокая печь": "blast_furnace",
    "Гвозди": "nails",
    "Гидропонная установка": "hydroponic_unit",
    "Глина": "clay",
    "Глиняный горшок": "clay_pot",
    "Грелка": "warmer",
    "Датчик": "sensor",
    "Двигатель I класса": "engine_class_1",
    "Дерево": "wood",
    "Дорожное полотно": "road_paving",
    "Жаркое": "roast",
    "Жаровня": "brazier",
    "Жаростойкий скафандр": "heatproof_suit",
    "Железная кирка": "iron_pickaxe",
    "Железная руда": "iron_ore",
    "Жила": "vein",
    "Зерно": "grain",
    "Золотая монета": "gold_coin",
    "Золотоносная порода": "gold_ore",
    "Известняк": "limestone",
    "Известь": "lime",
    "Изотопный реактор Предтеч": "precursor_isotope_reactor",
    "Каменная кирка": "stone_pickaxe",
    "Каменный блок": "stone_block",
    "Каменный топор": "stone_axe",
    "Камень": "stone",
    "Камера сгорания": "combustion_chamber",
    "Канистра": "canister",
    "Каторга": "prison",
    "Кварцевый песок": "quartz_sand",
    "Кирпич": "brick",
    "Кислород": "oxygen",
    "Кислородный баллон": "oxygen_tank",
    "Колесо": "wheel",
    "Компост": "compost",
    "Консоль управления кораблём": "ship_console",
    "Корзина": "basket",
    "Корпус": "hull",
    "Корпус на пироксите": "pyroxite_hull",
    "Космическая верфь": "space_shipyard",
    "Космическая мастерская": "space_workshop",
    "Котёл": "cauldron",
    "Кремниевая пластина": "silicon_wafer",
    "Кремний": "silicon",
    "Кровать": "bed",
    "Крошка": "stone_chips",
    "Кузница": "forge",
    "Лечебница": "infirmary",
    "Лён": "flax",
    "Магнит": "magnet",
    "Масличные семена": "oil_seeds",
    "Масло": "oil",
    "Мастерская": "workshop",
    "Медная руда": "copper_ore",
    "Медная фольга": "copper_foil",
    "Медный слиток": "copper_ingot",
    "Мельница": "mill",
    "Меха": "bellows",
    "Мешок": "sack",
    "Микросхема": "microchip",
    "Минеральное удобрение": "mineral_fertilizer",
    "Молот": "hammer",
    "Монетная станция": "coin_station",
    "Мука": "flour",
    "Мясо": "meat",
    "Наземная консоль управления": "ground_console",
    "Насос": "pump",
    "Никель": "nickel",
    "Обогреватель": "heater",
    "Обшивка": "plating",
    "Овощи": "vegetables",
    "Окислитель": "oxidizer",
    "Олово": "tin",
    "Оловянная руда": "tin_ore",
    "Органические отходы": "organic_waste",
    "Основа узла корабля": "ship_node_foundation",
    "Отвар": "decoction",
    "Очаг": "hearth",
    "Песок": "sand",
    "Печатная плата": "circuit_board",
    "Пироксисовая плита": "pyroxite_slab",
    "Пироксит": "pyroxite",
    "Питающий контур": "feed_circuit",
    "Плавильная печь": "smelting_furnace",
    "Повозка": "cart",
    "Полевой автомат": "field_automaton",
    "Похлёбка": "soup",
    "Привод": "drive",
    "Провод": "wire",
    "Прокатный стан": "rolling_mill",
    "Простой рюкзак": "simple_backpack",
    "Процессорный блок": "processor_unit",
    "Ракетное топливо": "rocket_fuel",
    "Раствор": "mortar",
    "Реле": "relay",
    "Рецепт": "recorded_recipe",
    "Рукоять": "handle",
    "Сахар": "sugar",
    "Сахарный стебель": "sugar_cane",
    "Свинец": "lead",
    "Свинцовая руда": "lead_ore",
    "Селекционный питомник": "breeding_nursery",
    "Селитра": "saltpeter",
    "Семена бобов": "beans_seeds",
    "Семена зверобоя": "stjohnswort_seeds",
    "Семена костреца": "brome_seeds",
    "Семена льна": "flax_seeds",
    "Семена полбы": "spelt_seeds",
    "Семена репы": "turnip_seeds",
    "Семена рыжика": "camelina_seeds",
    "Семена сахарника": "sugarcane_seeds",
    "Сено": "hay",
    "Сера": "sulfur",
    "Серебряная монета": "silver_coin",
    "Серебряная порода": "silver_ore",
    "Серная кислота": "sulfuric_acid",
    "Система жизнеобеспечения": "life_support_system",
    "Скафандр на пироксите": "pyroxite_suit",
    "Слиток железа": "iron_ingot",
    "Смола": "resin",
    "Солома": "straw",
    "Солонина": "salted_meat",
    "Соль": "salt",
    "Спирт": "alcohol",
    "Сталь": "steel",
    "Стальная кирка": "steel_pickaxe",
    "Стальная рама": "steel_frame",
    "Стекло": "glass",
    "Стеллаж": "rack",
    "Сундук": "chest",
    "Сушёные овощи": "dried_vegetables",
    "ТЭЦ": "heat_plant",
    "ТЭЦ Предтеч": "precursor_heat_plant",
    "Тачка": "wheelbarrow",
    "Текстолит": "textolite",
    "Теплозащитные плиты": "heat_shield_tiles",
    "Терминал маркетплейса": "market_terminal",
    "Ткань": "cloth",
    "Токарный станок": "lathe",
    "Топливный бак": "fuel_tank",
    "Топор": "axe",
    "Точная деталь": "precision_part",
    "Труба": "pipe",
    "Уголь": "coal",
    "Угольная станция": "coal_plant",
    "Утеплённый костюм": "insulated_suit",
    "Хлеб": "bread",
    "Целебные травы": "healing_herbs",
    "Чистая мастерская": "clean_workshop",
    "Шахтная крепь": "shaft_support",
    "Щебень": "crushed_stone",
    "Экзоскелет": "exoskeleton",
    "Электродвигатель": "electric_motor",
    "Электролизёр": "electrolyzer",
    "Электросхема": "electric_circuit",
    "Энергия": "energy",
}

#: Names older migrations already retired, mapped in case a world kept a
#: stray (the same belt-and-braces as seed_catchup's own rename table).
#: Upgrade only: reversing them would have two spellings fight over one id.
GOODS_ALIASES = {
    "Автоматический станок": "auto_station",
    "Верфь": "space_workshop",
    "Космодром": "space_shipyard",
    "Монетный двор": "coin_station",
    "Монетный станок": "coin_station",
    "Навигационный блок": "ship_console",
}

TIERS = {
    "обычное": "common",
    "отличное": "fine",
    "плохое": "poor",
    "скверное": "awful",
    "хорошее": "good",
}

SLOTS = {
    "каркас": "frame",
    "спина": "back",
    "тело": "body",
}

BUILDING_KINDS = {
    "бетонный": "concrete",
    "деревянный": "wooden",
    "железобетонный": "reinforced_concrete",
    "каменный": "stone",
    "цельнометаллический": "metal",
}

#: Property words of node.properties -- the vault's and the engine's alike.
NODE_PROPERTIES = {
    "без воздуха": "airless",
    "борт": "aboard",
    "вода": "water",
    "выход": "exit",
    "глубина": "depth",
    "город": "city",
    "даль": "distance",
    "дикий": "wild",
    "жидкость": "liquid",
    "значок": "emblem",
    "камни": "stones",
    "карта": "map",
    "кольцо": "ring",
    "лес": "woods",
    "луг": "meadow",
    "мерзлота": "frost",
    "наковальня": "anvil",
    "описание": "description",
    "орбита": "orbit",
    "орбита узел": "orbit_node",
    "осадки": "precipitation",
    "отложена": "deferred",
    "пекло": "heat",
    "период": "period",
    "плодородие": "fertility",
    "помещение": "indoors",
    "посадка везде": "open_landing",
    "предтечи": "precursors",
    "радиус": "radius",
    "разведано": "surveyed",
    "раскрыто": "revealed",
    "реактор": "reactor",
    "река": "river",
    "температура": "temperature",
    "тюрьма": "prison",
    "участок": "plot",
    "фаза": "phase",
    "этаж": "storey",
}

#: The two worded VALUES inside properties: the owner's emblem and water.
EMBLEMS = {
    "вода": "water",
    "дом": "house",
    "еда": "food",
    "камни": "stones",
    "лес": "woods",
    "луг": "meadow",
    "мастерская": "workshop",
    "поле": "field",
    "разметка": "markup",
    "рынок": "market",
    "склад": "warehouse",
}

WATER = {
    "нет": "none",
    "река": "river",
}

#: table -> column holding a goods name by value. The e1c8f3a24b70 list plus
#: what it missed: recipe keys, the station of a batch, veins, the library,
#: learned knowledge.
GOODS_COLUMNS = (
    ("item", "type_key"),
    ("item", "recipe_key"),
    ("market_order", "type_key"),
    ("market_reservation", "type_key"),
    ("market_trade", "type_key"),
    ("craft_batch", "output"),
    ("craft_batch", "station"),
    ("craft_batch", "recipe_key"),
    ("forage", "found"),
    ("vein", "resource"),
    ("library_entry", "recipe"),
    ("knowledge", "key"),
)

TIER_COLUMNS = (
    ("market_order", "tier"),
    ("market_reservation", "tier"),
    ("market_trade", "tier"),
)


def _map_table(name: str, mapping: dict[str, str], *, reverse: bool) -> None:
    """One temp table with the whole map: one UPDATE per column, not per name."""
    op.execute(sa.text(f"CREATE TEMP TABLE {name} (was text PRIMARY KEY, now text NOT NULL) ON COMMIT DROP"))
    rows = [(v, k) if reverse else (k, v) for k, v in mapping.items()]
    for was, now in rows:
        op.execute(
            sa.text(f"INSERT INTO {name} (was, now) VALUES (:was, :now) ON CONFLICT (was) DO NOTHING")
            .bindparams(sa.bindparam("was", was), sa.bindparam("now", now))
        )


def _column(table: str, column: str, mapping: str) -> None:
    op.execute(sa.text(
        f"UPDATE {table} SET {column} = m.now FROM {mapping} m WHERE {table}.{column} = m.was"
    ))


def _drop_collisions(table: str, column: str, mapping: str, *unique_with: str) -> None:
    """Delete rows the rename would turn into duplicates of a row already there.

    Two columns carry a goods name **inside a unique key**: what a library
    holds (`node_id, recipe`) and what a person has learned (`identity_id,
    kind, key`). A world that lived through an earlier rename can hold both
    spellings at once -- the dev world holds «Навигационный блок» and
    «Консоль управления кораблём» on one shelf, because `f7a3c2e91b04` moved
    the items and not the shelf -- and mapping both onto `ship_console` would
    violate the constraint and abort the whole upgrade.

    The row already spelled the way we are heading is the one kept: it is the
    one the engine has been reading. The other says the same thing in a word
    nobody uses any more, and it goes. Nothing is lost that was not already a
    duplicate of its neighbour.
    """
    keys = ", ".join(unique_with)
    op.execute(sa.text(
        f"DELETE FROM {table} WHERE id IN ("
        f"  SELECT id FROM ("
        f"    SELECT t.id, row_number() OVER ("
        f"      PARTITION BY {keys}, COALESCE(m.now, t.{column})"
        #: The already-correct row sorts first and survives; among equals the
        #: oldest wins, so a re-run of the migration keeps the same one.
        f"      ORDER BY (m.now IS NULL) DESC, t.id"
        f"    ) AS seat"
        f"    FROM {table} t LEFT JOIN {mapping} m ON m.was = t.{column}"
        f"  ) ranked WHERE seat > 1"
        f")"
    ))


def _jsonb_keys(table: str, column: str, mapping: str) -> None:
    """Re-key a JSONB map through the temp mapping; values travel unchanged."""
    op.execute(sa.text(
        f"UPDATE {table} SET {column} = "
        f"(SELECT coalesce(jsonb_object_agg(coalesce(m.now, e.key), e.value), '{{}}'::jsonb) "
        f" FROM jsonb_each({column}) e LEFT JOIN {mapping} m ON m.was = e.key) "
        f"WHERE EXISTS (SELECT 1 FROM jsonb_each({column}) e JOIN {mapping} m ON m.was = e.key)"
    ))


def _property_value(prop: str, was: str, now: str) -> None:
    op.execute(sa.text(
        "UPDATE node SET properties = "
        "jsonb_set(properties, ARRAY[CAST(:prop AS text)], to_jsonb(CAST(:now AS text))) "
        "WHERE properties->>CAST(:prop AS text) = :was"
    ).bindparams(
        sa.bindparam("now", now),
        sa.bindparam("prop", prop),
        sa.bindparam("was", was),
    ))


def _run(*, reverse: bool) -> None:
    goods = dict(GOODS)
    if not reverse:
        goods.update(GOODS_ALIASES)
    _map_table("d251_goods", goods, reverse=reverse)
    _map_table("d251_tiers", TIERS, reverse=reverse)
    _map_table("d251_slots", SLOTS, reverse=reverse)
    _map_table("d251_kinds", BUILDING_KINDS, reverse=reverse)
    _map_table("d251_props", NODE_PROPERTIES, reverse=reverse)

    #: Before the rename, not after: a goods name inside a unique key can
    #: arrive at a spelling the row next to it already has.
    _drop_collisions("library_entry", "recipe", "d251_goods", "node_id")
    _drop_collisions("knowledge", "key", "d251_goods", "identity_id", "kind")

    for table, column in GOODS_COLUMNS:
        _column(table, column, "d251_goods")
    for table, column in TIER_COLUMNS:
        _column(table, column, "d251_tiers")
    _column("equipped", "slot", "d251_slots")
    _column("building", "kind", "d251_kinds")

    _jsonb_keys("craft_batch", "spent", "d251_goods")
    _jsonb_keys("node", "properties", "d251_props")

    #: The worded property values, after the keys moved: forward the key is
    #: already English, backward it is Russian again -- and the value swaps.
    if reverse:
        for was, now in WATER.items():
            _property_value("вода", now, was)
        for was, now in EMBLEMS.items():
            _property_value("значок", now, was)
    else:
        for was, now in WATER.items():
            _property_value("water", was, now)
        for was, now in EMBLEMS.items():
            _property_value("emblem", was, now)

    #: The goods suffixes of the daily metrics: continuity of the price index
    #: and stock series across the release.
    for prefix in ("stock.", "price."):
        op.execute(sa.text(
            "UPDATE daily_metric SET key = :prefix || m.now FROM d251_goods m "
            "WHERE key = :prefix || m.was"
        ).bindparams(sa.bindparam("prefix", prefix)))

    #: work_order.payload carries a goods name and a building kind of its own:
    #: an open state order placed before the release must stay payable, and its
    #: build licence must keep matching the (now id-spelled) building.kind.
    op.execute(sa.text(
        "UPDATE work_order SET payload = jsonb_set(payload, '{type_key}', to_jsonb(m.now)) "
        "FROM d251_goods m WHERE payload->>'type_key' = m.was"
    ))
    op.execute(sa.text(
        "UPDATE work_order SET payload = jsonb_set(payload, '{building_kind}', to_jsonb(m.now)) "
        "FROM d251_kinds m WHERE payload->>'building_kind' = m.was"
    ))

    default = "wooden" if not reverse else "деревянный"
    op.execute(sa.text(f"ALTER TABLE building ALTER COLUMN kind SET DEFAULT '{default}'"))

    for name in ("d251_goods", "d251_tiers", "d251_slots", "d251_kinds", "d251_props"):
        op.execute(sa.text(f"DROP TABLE {name}"))


def upgrade() -> None:
    _run(reverse=False)


def downgrade() -> None:
    _run(reverse=True)
