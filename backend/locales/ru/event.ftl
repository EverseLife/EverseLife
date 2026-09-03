# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Что случилось, пока вас не было (D-226): строки сводки возвращения.
#
# Ключ выводится из вида события: `craft.finished` -> `event-craft-finished`
# (точка в имени сообщения Fluent недопустима). Список видов задаёт сервер —
# `api/commands/world.TOLD` и `TOLD_OF_THE_PLACE`, — и тест полноты сверяется
# именно с ним: событие, добавленное в сводку без строки, роняет тесты, а не
# показывает игроку `plates.erupted`.
#
# Журнал пишет всё — каждый взмах киркой, каждую проводку. Здесь только концы
# дел: что кончилось, дошло, нашлось, решилось, пропало.

event-craft-finished = партия готова
event-travel-arrived = пришли
event-farm-harvested = урожай собран
event-explore-found = разведка: находка
event-explore-empty = разведка: пусто
event-body-died = тело погибло
event-body-printed = напечатано тело
event-mining-collapsed = обвал в забое
event-market-trade = сделка
event-market-order_expired = ордер снят по сроку
event-market-reservation_lapsed = бронь просрочена
event-city-law_set = город изменил закон
event-city-vote_closed = голосование закрыто
event-justice-case_judged = приговор
event-justice-sanction_applied = наложена санкция
event-bank-debt_withheld = с долга удержано
event-utility-cut_off = узел отключён за неуплату
event-transport-broke = повозка разбилась
# The sky (D-289): the tanks ran dry under way, or the coast ended.
event-ship-adrift = корабль лёг в дрейф
event-ship-lost = корабль погиб
event-ship-sighted = замечен корабль
event-ship-held = корабль на удержании
event-ship-dock_asked = просьба о стыковке
event-ship-docked_ship = стыковка борт к борту
event-ship-undocked_ship = расстыковка
event-road-laid = дорога уложена
event-deed-sold = бумага продана
event-land-reclaimed = город забрал свою локацию
event-city-grant_paid = подъёмные выплачены
event-estate-site_ready = стройка готова: дом ждёт хозяина

# Землетрясение и предупреждение о нём (D-197). Приходят не тому, кто их
# вызвал, а тому, кто здесь стоит: у этих двух нет виновника. До этой волны
# они показывались сырым ключом — единственные два события сводки без строки.
event-plates-warned = землю трясёт: скоро тряхнёт сильнее
event-plates-erupted = землетрясение

# --- на что смотреть: строки списка внимания (`world.digest`) ----------------
#
# Список внимания — не события, а незакрытые дела: то, где ещё можно
# что-то сделать. Сервер называет строку ключом и отдаёт значения, клиент
# рисует; так вид голосования и ключ товара становятся словами того языка,
# на котором читают, а не тем, что случайно лежит в базе.

attention-case = против вас иск: { $claim }
attention-vote-law = голосование: { LAW($law) }
attention-vote-kind = голосование: { $kind ->
        [election] выборы правителя
        [recall] отзыв правителя
        [charter] правка устава
        [council] выборы в совет
       *[law] закон
    }
attention-debt = долг за быт: { $node }{ $cut ->
        [true] { " " }— узел отключён
       *[false] {""}
    }
attention-reservation = забрать бронь: { NAME($goods) }
event-emission-printed = деньги напечатаны в казну
