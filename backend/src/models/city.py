"""Город как институт: устав, код-законы, должности, счётчик (D-036, D-130, D-154).

До этого «город» существовал только как узел-представитель на карте: у него
была казна, но не было никого, кто вправе ею распорядиться. Здесь появляется
недостающее, и ровно три вещи:

* **город** — устав (ответы на вопросы `laws.json`) и код-законы (значения);
* **должность** — «личность вправе делать вот это в вот этом городе»;
* **счётчик** — сколько узел должен за быт и до какого момента посчитан.

Ветвлений по названию должности в движке нет и не будет: движок знает
полномочия, а как называется занимающий их пост — дело города (D-154). Иначе
каждая новая форма правления потребовала бы выката версии, и вся идея «игроки
пишут правила» умерла бы вместе с ней.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Power(StrEnum):
    """Крупное полномочие власти (D-154, D-155).

    Полное право — **строка**, и крупными значениями оно не исчерпывается:
    `law:<id>` открывает ровно один код-закон, а `laws` покрывает их все.
    Список конкретных законов движок не держит: он ровно тот, что лежит в
    `data/laws.yaml` вольта. «Министр экономики» — это набор прав, которому
    город дал имя, и ветвлений по названию должности в коде нет.
    """

    #: Править **все** код-законы. Покрывает любое `law:<id>`.
    LAWS = "laws"
    #: Отвечать на вопросы устава.
    CHARTER = "charter"
    #: Распоряжаться казной: платить из неё.
    TREASURY = "treasury"
    #: Назначать и снимать должности.
    OFFICES = "offices"
    #: Раздавать городские участки жителям (D-089).
    LAND = "land"
    #: Полный срез экономической панели: публичный виден всем и так (D-140).
    DASHBOARD = "dashboard"
    #: Суд и санкции. Полномочие объявлено, механика приедет со своей системой.
    JUSTICE = "justice"
    #: Принимать в граждане и отказывать (D-160). Изгнание идёт не отсюда, а по
    #: `justice`: изгнание — санкция, а не кадровое решение.
    CITIZENS = "citizens"


#: Приставка права на один закон: `law:import_duty`. Отделена двоеточием,
#: потому что идентификаторы законов приходят из вольта и точек не содержат.
LAW_SCOPE = "law:"


class City(Base):
    """Город. Живёт на узле-представителе: его территория — дети этого узла."""

    __tablename__ = "city"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: Узел-представитель города на планетном слое (D-045). Один город — один узел.
    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node.id"), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(nullable=False)
    #: Слово города новичку: что он о себе объявляет на карточке двери (D-183).
    #: Обещание, а не договор — движок его не исполняет и не разбирает.
    about: Mapped[str] = mapped_column(nullable=False, default="", server_default="")
    #: Основатель. По умолчанию он же и правитель — так устав и говорит (D-130).
    founder_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )

    #: Ответы устава: `{вопрос: вариант}`. Заполняется умолчаниями `laws.json`
    #: при основании — город возникает работающим, а не пустым (D-130).
    charter: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    #: Числовые параметры вариантов устава: `{вопрос: значение}`.
    charter_params: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    #: Код-законы: `{закон: значение строкой}`. Строкой — потому что закон
    #: бывает и числом, и словом, а ветвлений по типу закона в коде нет (D-094).
    laws: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    created_at: Mapped[datetime] = created_column()


class Office(Base):
    """Должность: личность и то, что ей позволено в этом городе.

    Сложенная должность не удаляется, а помечается сроком: кто чем распоряжался
    в прошлом месяце — вопрос суда, и ответ на него обязан сохраниться.
    """

    __tablename__ = "city_office"
    __table_args__ = (
        Index("ix_city_office_city", "city_id"),
        Index("ix_city_office_identity", "identity_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("city.id"), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)

    #: Как должность называется в этом городе. Движку всё равно: он смотрит
    #: в полномочия, а «президент» или «старейшина» — дело города.
    title: Mapped[str] = mapped_column(nullable=False)
    #: Полномочия списком значений `Power`.
    powers: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=list)

    appointed_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )
    created_at: Mapped[datetime] = created_column()
    #: Пусто — должность действует.
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)


class CityGrant(Base):
    """Подъёмные, выданные городом личности (D-153).

    Отдельная строка, а не флаг на личности: подъёмные платит **город**, и
    один и тот же человек, переехав, вправе получить их в другом городе — но
    не дважды в одном. Запись и есть это правило.
    """

    __tablename__ = "city_grant"
    __table_args__ = (
        UniqueConstraint("city_id", "identity_id", name="uq_city_grant_identity"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("city.id"), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    #: Сколько выдано, минорными единицами.
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = created_column()


class UtilityMeter(Base):
    """Счётчик узла: быт идёт временем, счёт приходит раз в период (D-135, D-149).

    Счётчик заводится на **занятый** узел — свой либо городской. У ничьего узла
    счётчика нет: выставлять счёт некому, а исчезать деньгам некуда (И2).
    """

    __tablename__ = "utility_meter"

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node.id"), nullable=False, unique=True
    )

    #: До какого момента быт уже посчитан. Двигается заданием журнала.
    counted_at: Mapped[datetime] = created_column()
    #: Неоплаченное, минорными единицами. Долг не сгорает и не растёт процентом:
    #: проценты — дело банка (Э4), а не коммунальной службы.
    debt: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Отключён за неуплату: станки узла не работают, пока долг не закрыт.
    #: Отобрать узел за долг движок не вправе — это решение суда (D-149).
    cut_off: Mapped[bool] = mapped_column(nullable=False, default=False)
    #: Сколько энергии ушло на быт за последний период — для показа владельцу.
    last_energy: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)

    created_at: Mapped[datetime] = created_column()


class Citizen(Base):
    """Гражданство: личность состоит в городе (D-160).

    **Одно на человека.** Двойное гражданство запрещено уставом мира, а не
    договорённостью, — поэтому ограничение стоит в базе: вторая запись на ту же
    личность невозможна физически.

    Выход свободен, но не мгновенен: заявление ставит `leaving_at`, и запись
    держится до срока. Задержка существует ровно затем, чтобы нельзя было выйти
    из города прямо перед приговором.
    """

    __tablename__ = "citizen"
    __table_args__ = (
        UniqueConstraint("identity_id", name="uq_citizen_identity"),
        Index("ix_citizen_city", "city_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("city.id"), nullable=False)
    #: С какого момента: по нему считается ценз проживания (`vote_qualification`).
    since: Mapped[datetime] = created_column()
    #: Когда гражданство спадёт по заявлению о выходе. Пусто — не выходит.
    leaving_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: До какого срока гражданство не складывается: условие печати, принятое
    #: выбором двери (D-184). Записывается в момент печати и позже не меняется —
    #: город, поднявший срок задним числом, не удлиняет чужое обязательство.
    #: Пусто — обязательства нет, уйти можно в тот же день.
    bound_until: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = created_column()


class CitizenshipRequest(Base):
    """Заявка в граждане либо приглашение (D-160).

    Одна и та же запись: разница только в том, кто её начал. `application` —
    человек попросил и ждёт власть; `invite` — власть позвала и ждёт человека.
    Второй таблицы для этого не нужно, а два имени в коде разошлись бы.
    """

    __tablename__ = "citizenship_request"
    __table_args__ = (
        UniqueConstraint("identity_id", "city_id", name="uq_request_identity_city"),
        Index("ix_request_city", "city_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("city.id"), nullable=False)
    #: `application` — от человека, `invite` — от власти.
    kind: Mapped[str] = mapped_column(nullable=False)
    #: Кто позвал, если это приглашение.
    by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )
    created_at: Mapped[datetime] = created_column()


class CouncilSeat(Base):
    """Место в совете города (D-164).

    Совет — набор мест, а не звание: звание пришлось бы проверять по названию
    должности, а движок названий не знает и знать не должен (D-154). Место
    либо есть, либо нет.

    Сложенное место не удаляется, а помечается сроком: кто голосовал в совете
    в прошлом месяце — вопрос суда, и ответ обязан сохраниться.
    """

    __tablename__ = "council_seat"
    __table_args__ = (Index("ix_council_city", "city_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("city.id"), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )
    #: Как получено место: выборами либо назначением.
    how: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = created_column()
    #: Пусто — место занято.
    vacated_at: Mapped[datetime | None] = mapped_column(nullable=True)
