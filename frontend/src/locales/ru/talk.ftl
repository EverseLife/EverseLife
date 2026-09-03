# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Слова о том, что люди говорят друг другу, и об экране аккаунта: разговор
# локации, кружки, переписка и каналы Сети, карточка человека, аккаунт
# (D-251, волна IV).
#
# Правила те же, что у `ui.ftl`: значение — одной строкой, каким бы длинным
# оно ни было (перенос попал бы в текст); варианты выбора — каждый на своей
# строке, эти переносы в текст не попадают.
#
# Числа приезжают сюда строками там, где экран печатал их как есть: `{ $n }` с
# настоящим числом Fluent отформатировал бы по правилам языка — «1 234» вместо
# «1234», — и строка изменилась бы молча.

## Линия персонажа. Одна пара на карточку и на аккаунт: слово одно.

ui-line-human = человек-киборг
ui-line-nymph = нимфа

## Аккаунт (D-187, D-238): оплата и устройство, а не игра.

ui-account-rule = Аккаунт — это оплата и устройство, личность — это игра. Имя несменяемо: на нём держится репутация.
ui-account-who = { $line }{ $aged ->
        [true] { " " }· { $age }
       *[false] {""}
    } · в мире с { $since }

## Вкладки аккаунта.

ui-account-tab-who = персонаж
ui-account-tab-password = пароль
ui-account-tab-email = почта
ui-account-tab-view = вид

## Что сказано после удачного сохранения.

ui-account-saved = сохранено
ui-account-password-saved = пароль сменён; другие сессии разлогинены
ui-account-email-saved = почта сменена

## Персонаж: что человек показывает о себе.

ui-account-name = имя
ui-account-name-fixed = имя не меняется
ui-account-name-rule = Имя несменяемо: на нём держится репутация.
ui-account-surname = фамилия
ui-account-age = возраст
ui-account-about = описание
ui-account-save = Сохранить

## Пароль.

ui-account-password-old = старый пароль
ui-account-password-new = новый пароль
ui-account-password-hint = не короче { $min } знаков
ui-account-password-again = ещё раз
ui-account-password-repeat = повторите
ui-account-password-submit = Сменить пароль

## Почта.

ui-account-email = почта
ui-account-password = пароль
ui-account-email-rule = Смена почты подтверждается паролем.
ui-account-email-submit = Сменить почту

## Выход из аккаунта.

ui-account-logout = Выйти из аккаунта
ui-account-logout-note = Жетон этой сессии будет отозван.

## Вид: как этот человек читает экран.

ui-account-density = плотность
ui-account-density-rule = Плотность меняет высоту строк и отступы. Размер шрифта и расположение элементов не меняются: плотный режим — это больше данных на экране, а не более мелкий текст. Переключается свободно и в любую сторону.
ui-account-density-label = плотность экрана
ui-account-language = язык
ui-account-language-rule = Языки равноправны: мир написан на каждом из них, а не переведён с одного. Выбор запоминается на аккаунте — на другом устройстве мир откроется на нём же. Отказы движка тоже приходят на этом языке.

## Карточка человека (D-222): кто он и где его гражданство.

ui-profile-label = Профиль
ui-profile-close = закрыть
ui-profile-who = { $line }{ $aged ->
        [true] { " " }· { $age }
       *[false] {""}
    }{ $citizen ->
        [true] { " " }· гражданство: { $city }
       *[false] { " " }· без гражданства
    } · в мире с { $since }
ui-profile-write = Написать сообщение

## Сеть (D-044, D-069, D-222): список переписок и каналов.

ui-net-votes = Голосования
ui-net-write = Написать
ui-net-channels = Каналы
ui-net-threads = Переписка
ui-net-threads-none = Переписок ещё нет.
ui-net-thread-empty = пока ни слова
ui-net-city = город
ui-net-official = официальный
ui-net-official-title = официальный канал города
ui-net-back = назад
ui-net-back-title = к списку

## Кому писать: имя и подсказки Сети.

ui-net-to-whom = кому — имя

## Одна переписка: письма и дорога, по которой они идут.

ui-net-profile = профиль
ui-net-letters-none = Пока ни слова.
ui-net-you = вы:
ui-net-on-way = в пути · дойдёт { $when }
ui-net-letter-hint = написать…
ui-net-send = Отправить

## Канал: что дошло до этого читателя.

ui-net-unsubscribe = Отписаться
ui-net-subscribe = Подписаться
ui-net-posts-none = Пока ничего не опубликовано.
ui-net-post-hint = что сказать читателям…
ui-net-publish = Опубликовать

## Поиск канала и свой собственный.

ui-net-find-channel = найти канал
ui-net-new-channel = Новый канал
ui-net-channel-name = название
ui-net-channel-about = о чём
ui-net-channel-create = Создать
ui-net-unsubscribe-quiet = отписаться
ui-net-subscribe-quiet = подписаться
ui-net-nothing-found = Ничего не найдено.

## Разговор локации (D-043, D-050): вид речи обязателен.

ui-chat-kind-speech = речь
ui-chat-kind-action = действие
ui-chat-kind-ooc = вне игры
ui-chat-head = Общение
ui-chat-head-circle = { " " }· кружок «{ $named ->
        [true] { $name }
       *[false] без имени
    }»
# Кто ещё стоит в комнате. Имена подставляются как есть, поэтому впереди
# метка с двоеточием, а не предлог (D-258).
ui-chat-here = · здесь:
ui-chat-here-more = и ещё { $rest }
# Когда в строку не влезло ни одного имени: «и ещё» без того, к чему оно
# присоединяется, — не фраза, поэтому счёт встаёт вместо списка.
ui-chat-here-only = · здесь: { $rest }
ui-chat-unfold = развернуть ▸
ui-chat-fold = свернуть ▾
ui-chat-silent = Тихо. Разговор живёт, пока ты в комнате.
ui-chat-quiet-toggle = вполголоса
ui-chat-say-speech = сказать…
ui-chat-say-action = что делает персонаж…
ui-chat-say-ooc = не в мире…
ui-chat-say = Сказать
ui-chat-note-circle = Вы в кружке «{ $named ->
        [true] { $name }
       *[false] без имени
    }»: слышат участники, остальным долетают обрывки.

## Кружки (D-238): кому слышно сказанное.

ui-chat-chip-title = кому слышно сказанное; клик — подойти к кружку или собрать свой
ui-chat-chip-circle = кружок «{ $named ->
        [true] { $name }
       *[false] без имени
    }»
ui-chat-chip-none = кружки
ui-chat-circles-label = Кружки
ui-chat-circles-none = Никто не шепчется: весь разговор локации — общий.
ui-chat-circle-title = { $named ->
        [true] { $name }
       *[false] кружок без имени
    }
ui-chat-leave = отойти
ui-chat-join = подойти
ui-chat-gather-name = имя кружка (можно без)
ui-chat-gather = Собрать
ui-chat-circles-rule = Подошедшего к кружку видно всем; закрытых кружков нет. Пока вы в кружке, реплики слышат участники — с шансом утечки к чужим ушам.

## Реплика: обрывок из чужого кружка, действие, вне игры, вполголоса.
# Имя посреди фразы кликабельно, поэтому обрывок разрезан надвое: слова
# до имени и слова после него.

ui-chat-overheard = краем уха, из кружка «{ $source }»:
ui-chat-overheard-said = — «{ $text }»
ui-chat-ooc = [вне игры]
ui-chat-quiet-line = (вполголоса) { $text }
