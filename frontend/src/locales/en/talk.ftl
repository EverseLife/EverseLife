# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Words about what people say to one another, and about the account screen:
# the talk of a location, circles, the letters and channels of the Net, a
# person's card, the account (D-251, wave IV).
#
# The rules are the same as in `ui.ftl`: a value is one line, however long it
# runs (a break would land in the text); the variants of a select go one per
# line, and those breaks do not land in the text.
#
# Numbers arrive here as strings wherever the screen printed them as they
# stood: `{ $n }` with a real number would be formatted by Fluent to the rules
# of the language — “1,234” instead of “1234” — and the line would change in
# silence.

## The line of the character. One pair for the card and for the account: one word.

ui-line-human = cyborg human
ui-line-nymph = nymph

## The account (D-187, D-238): payment and the device, not the game.

ui-account-rule = The account is payment and the device; the identity is the game. The name cannot be changed: reputation rests on it.
ui-account-who = { $line }{ $aged ->
        [true] { " " }· { $age }
       *[false] {""}
    } · in the world since { $since }

## The tabs of the account.

ui-account-tab-who = character
ui-account-tab-password = password
ui-account-tab-email = email
ui-account-tab-view = view

## What is said after a save goes through.

ui-account-saved = saved
ui-account-password-saved = password changed; other sessions are signed out
ui-account-email-saved = email changed

## The character: what a person shows of themselves.

ui-account-name = name
ui-account-name-fixed = the name does not change
ui-account-name-rule = The name cannot be changed: reputation rests on it.
ui-account-surname = surname
ui-account-age = age
ui-account-about = description
ui-account-save = Save

## The password.

ui-account-password-old = old password
ui-account-password-new = new password
ui-account-password-hint = no shorter than { $min } characters
ui-account-password-again = once more
ui-account-password-repeat = repeat it
ui-account-password-submit = Change password

## The email.

ui-account-email = email
ui-account-password = password
ui-account-email-rule = A change of email is confirmed with the password.
ui-account-email-submit = Change email

## Signing out of the account.

ui-account-logout = Sign out of the account
ui-account-logout-note = The token of this session will be revoked.

## View: how this person reads the screen.

ui-account-density = density
ui-account-density-rule = Density changes the height of a line and the padding. The size of the font and the placement of the elements do not change: the dense mode is more data on the screen, not smaller text. It switches freely, and in either direction.
ui-account-density-label = screen density
ui-account-language = language
ui-account-language-rule = The languages are equal: the world is written in each of them, not translated out of one. The choice is remembered on the account — on another device the world opens in the same one. The engine's refusals come in this language too.

## A person's card (D-222): who they are and where their citizenship is.

ui-profile-label = Profile
ui-profile-close = close
ui-profile-who = { $line }{ $aged ->
        [true] { " " }· { $age }
       *[false] {""}
    }{ $citizen ->
        [true] { " " }· citizenship: { $city }
       *[false] { " " }· no citizenship
    } · in the world since { $since }
ui-profile-write = Write a message

## The Net (D-044, D-069, D-222): the list of letters and channels.

ui-net-write = Write
ui-net-channels = Channels
ui-net-threads = Letters
ui-net-threads-none = No letters yet.
ui-net-thread-empty = not a word yet
ui-net-city = city
ui-net-official = official
ui-net-official-title = the official channel of the city
ui-net-back = back
ui-net-back-title = to the list

## Whom to write to: a name and the Net's hints.

ui-net-to-whom = to whom — a name

## One exchange of letters: the letters and the road they travel by.

ui-net-profile = profile
ui-net-letters-none = Not a word yet.
ui-net-you = you:
ui-net-on-way = on the way · arrives { $when }
ui-net-letter-hint = write…
ui-net-send = Send

## A channel: what has reached this reader.

ui-net-unsubscribe = Unsubscribe
ui-net-subscribe = Subscribe
ui-net-posts-none = Nothing has been posted yet.
ui-net-post-hint = what to tell the readers…
ui-net-publish = Post

## Finding a channel, and one of your own.

ui-net-find-channel = find a channel
ui-net-new-channel = New channel
ui-net-channel-name = name
ui-net-channel-about = what it is about
ui-net-channel-create = Create
ui-net-unsubscribe-quiet = unsubscribe
ui-net-subscribe-quiet = subscribe
ui-net-nothing-found = Nothing found.

## The talk of a location (D-043, D-050): the kind of line is required.

ui-chat-kind-speech = speech
ui-chat-kind-action = action
ui-chat-kind-ooc = out of character
ui-chat-head = talk
ui-chat-head-circle = { " " }· circle “{ $named ->
        [true] { $name }
       *[false] unnamed
    }”
ui-chat-unfold = unfold ▸
ui-chat-fold = fold ▾
ui-chat-silent = Quiet. The talk lives while you are in the room.
ui-chat-quiet-toggle = under one's breath
ui-chat-say-speech = say…
ui-chat-say-action = what the character does…
ui-chat-say-ooc = not in the world…
ui-chat-say = Say
ui-chat-note-circle = You are in the circle “{ $named ->
        [true] { $name }
       *[false] unnamed
    }”: the members hear you, and scraps carry to the rest.
ui-chat-note-all = Everyone here hears you. To gather somewhere quieter, use the “circles” button on the left.

## Circles (D-238): who hears what is said.

ui-chat-chip-title = who hears what is said; click to step up to a circle or gather your own
ui-chat-chip-circle = circle “{ $named ->
        [true] { $name }
       *[false] unnamed
    }”
ui-chat-chip-none = circles
ui-chat-circles-label = Circles
ui-chat-circles-none = No one is whispering: the whole talk of the location is shared.
ui-chat-circle-title = { $named ->
        [true] { $name }
       *[false] an unnamed circle
    }
ui-chat-leave = step away
ui-chat-join = step up
ui-chat-gather-name = name of the circle (optional)
ui-chat-gather = Gather
ui-chat-circles-rule = Whoever steps up to a circle is seen by everyone; there are no closed circles. While you are in a circle, the members hear your lines — with a chance of a leak to other ears.

## A line: a scrap out of someone else's circle, an action, out of character, under one's breath.
# The name in the middle of the sentence is clickable, so the scrap is cut in
# two: the words before the name and the words after it.

ui-chat-overheard = overheard, out of the circle “{ $source }”:
ui-chat-overheard-said = — “{ $text }”
ui-chat-ooc = [out of character]
ui-chat-quiet-line = (under one's breath) { $text }
