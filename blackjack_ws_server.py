#!/usr/bin/env python3
"""
JASLI's Casino - Online Blackjack WebSocket Server
=================================================

Purpose
-------
This is the public/online "casino brain" for JASLI's Blackjack.

Players on different Wi-Fi networks connect OUTWARD to this hosted server.
No player needs to port-forward. No player laptop is the real server.

Install:
    pip install websockets

Run locally:
    python blackjack_ws_server.py

Run on a VPS / cloud host:
    python blackjack_ws_server.py --host 0.0.0.0 --port 8765

Basic protocol
--------------
All messages are JSON.

Client -> Server:
    {"type": "CREATE_ROOM", "name": "Ali"}
    {"type": "JOIN_ROOM", "room_code": "JASLI-1234", "name": "Friend"}
    {"type": "CLAIM_SEAT", "seat": 0}
    {"type": "LEAVE_SEAT"}
    {"type": "START_BETTING"}
    {"type": "BET", "amount": 1000}
    {"type": "SKIP_BET"}
    {"type": "HIT"}
    {"type": "STAND"}
    {"type": "DOUBLE"}
    {"type": "SPLIT"}
    {"type": "ADD_BOT"}
    {"type": "PING"}

Server -> Client:
    {"type": "HELLO", "client_id": "..."}
    {"type": "ROOM_CREATED", "room_code": "...", "client_id": "..."}
    {"type": "JOIN_OK", "room_code": "...", "client_id": "..."}
    {"type": "SEAT_ASSIGNED", "seat": 1}
    {"type": "STATE", "state": {...}}
    {"type": "ERROR", "message": "..."}
    {"type": "PONG"}

Important security notes
------------------------
This is a casual-game prototype server, not a hardened casino backend.
Do not use it for real-money gambling.
For public deployment, put it behind TLS/WSS and add rate limiting/auth later.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import secrets
import signal
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

import websockets
from websockets.asyncio.server import serve, ServerConnection


# =========================
# CONFIG
# =========================

MAX_PLAYERS = 5
STARTING_MONEY = 50_000
MIN_BET = 100

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

BOT_NAMES = [
    "Bot Bruno",
    "Bot Clara",
    "Bot Max",
    "Bot Ruby",
    "Bot Victor",
]

ROOM_PREFIX = "JASLI"
ROOM_IDLE_SECONDS = 60 * 60 * 2  # 2 hours


# =========================
# JSON HELPERS
# =========================

async def send_json(ws: ServerConnection, payload: Dict[str, Any]) -> None:
    await ws.send(json.dumps(payload, separators=(",", ":")))


def make_error(message: str) -> Dict[str, str]:
    return {"type": "ERROR", "message": message}


# =========================
# BLACKJACK HELPERS
# =========================

def new_deck() -> List[List[str]]:
    deck = [[rank, suit] for suit in SUITS for rank in RANKS]
    random.shuffle(deck)
    return deck


def card_value(rank: str) -> int:
    if rank in ["J", "Q", "K"]:
        return 10
    if rank == "A":
        return 11
    return int(rank)


def hand_value(cards: List[List[str]]) -> int:
    total = 0
    aces = 0

    for rank, _suit in cards:
        if rank == "A":
            total += 11
            aces += 1
        else:
            total += card_value(rank)

    while total > 21 and aces > 0:
        total -= 10
        aces -= 1

    return total


def is_blackjack(cards: List[List[str]]) -> bool:
    return len(cards) == 2 and hand_value(cards) == 21


def new_hand(bet: int) -> Dict[str, Any]:
    return {
        "cards": [],
        "bet": bet,
        "finished": False,
        "busted": False,
        "doubled": False,
        "from_split": False,
        "result": "",
        "settled": False,
    }


def public_dealer_hand(dealer_hand: List[Dict[str, Any]], dealer_revealed: bool) -> List[Dict[str, Any]]:
    public_cards = []
    for item in dealer_hand:
        if item.get("hidden") and not dealer_revealed:
            public_cards.append({"card": None, "hidden": True})
        else:
            public_cards.append({"card": item["card"], "hidden": False})
    return public_cards


# =========================
# ROOM MODEL
# =========================

@dataclass
class Room:
    code: str
    clients: Dict[str, ServerConnection] = field(default_factory=dict)
    client_names: Dict[str, str] = field(default_factory=dict)
    client_seats: Dict[str, Optional[int]] = field(default_factory=dict)

    seat_owners: List[Optional[str]] = field(default_factory=lambda: [None] * MAX_PLAYERS)
    seat_types: List[str] = field(default_factory=lambda: ["empty"] * MAX_PLAYERS)
    money: List[int] = field(default_factory=lambda: [0] * MAX_PLAYERS)
    round_bets: List[int] = field(default_factory=lambda: [0] * MAX_PLAYERS)
    round_start_money: List[int] = field(default_factory=lambda: [0] * MAX_PLAYERS)

    player_hands: List[List[Dict[str, Any]]] = field(default_factory=lambda: [[] for _ in range(MAX_PLAYERS)])
    dealer_hand: List[Dict[str, Any]] = field(default_factory=list)
    deck: List[List[str]] = field(default_factory=list)

    mode: str = "lobby"  # lobby, betting, playing, dealer, round_over
    betting_player: Optional[int] = None
    current_player: Optional[int] = None
    current_hand: int = 0
    dealer_revealed: bool = False
    round_active: bool = False
    round_over: bool = True
    message: str = "Waiting for players."

    voice_events: List[Dict[str, Any]] = field(default_factory=list)
    next_voice_id: int = 1

    host_client_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self) -> None:
        self.last_activity = time.time()

    def active_players(self) -> List[int]:
        return [i for i in range(MAX_PLAYERS) if self.seat_types[i] != "empty"]

    def round_participants(self) -> List[int]:
        return [
            i for i in range(MAX_PLAYERS)
            if self.seat_types[i] != "empty" and self.round_bets[i] > 0
        ]

    def player_display_name(self, seat: int) -> str:
        owner = self.seat_owners[seat]
        if self.seat_types[seat] == "bot":
            return BOT_NAMES[seat]
        if owner and owner in self.client_names:
            return self.client_names[owner]
        if self.seat_types[seat] == "human":
            return f"Player {seat + 1}"
        return f"Seat {seat + 1}"

    def draw_card(self) -> List[str]:
        if not self.deck:
            self.deck = new_deck()
        return self.deck.pop()

    def add_voice(self, text: str) -> None:
        self.voice_events.append({
            "id": self.next_voice_id,
            "text": text,
            "time": time.time(),
        })
        self.next_voice_id += 1
        # keep recent events only
        self.voice_events = self.voice_events[-50:]

    def state_for_client(self, client_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            "room_code": self.code,
            "mode": self.mode,
            "message": self.message,
            "seat_types": self.seat_types,
            "seat_owners": self.seat_owners,
            "money": self.money,
            "round_bets": self.round_bets,
            "round_start_money": self.round_start_money,
            "player_hands": self.player_hands,
            "dealer_hand": public_dealer_hand(self.dealer_hand, self.dealer_revealed),
            "dealer_revealed": self.dealer_revealed,
            "round_active": self.round_active,
            "round_over": self.round_over,
            "betting_player": self.betting_player,
            "current_player": self.current_player,
            "current_hand": self.current_hand,
            "active_players": self.active_players(),
            "round_participants": self.round_participants(),
            "client_id": client_id,
            "my_seat": self.client_seats.get(client_id) if client_id else None,
            "host_client_id": self.host_client_id,
            "voice_events": self.voice_events[-20:],
            "server_time": time.time(),
        }


# =========================
# SERVER GLOBALS
# =========================

rooms: Dict[str, Room] = {}
client_to_room: Dict[str, str] = {}
client_to_ws: Dict[str, ServerConnection] = {}


def make_room_code() -> str:
    while True:
        code = f"{ROOM_PREFIX}-{random.randint(1000, 9999)}"
        if code not in rooms:
            return code


async def broadcast_state(room: Room) -> None:
    dead_clients = []

    for cid, ws in list(room.clients.items()):
        try:
            await send_json(ws, {
                "type": "STATE",
                "state": room.state_for_client(cid),
            })
        except Exception:
            dead_clients.append(cid)

    for cid in dead_clients:
        await remove_client_from_room(cid, silent=True)


async def broadcast_event(room: Room, payload: Dict[str, Any]) -> None:
    dead_clients = []

    for cid, ws in list(room.clients.items()):
        try:
            await send_json(ws, payload)
        except Exception:
            dead_clients.append(cid)

    for cid in dead_clients:
        await remove_client_from_room(cid, silent=True)


# =========================
# ROOM / CLIENT MANAGEMENT
# =========================

async def create_room(client_id: str, ws: ServerConnection, name: str) -> Room:
    code = make_room_code()
    room = Room(code=code)
    rooms[code] = room

    room.host_client_id = client_id
    await add_client_to_room(room, client_id, ws, name)

    room.message = f"{name} created room {code}."
    room.add_voice("Room created. Waiting for players.")
    return room


async def add_client_to_room(room: Room, client_id: str, ws: ServerConnection, name: str) -> None:
    room.clients[client_id] = ws
    room.client_names[client_id] = name[:32] if name else f"Guest {client_id[:4]}"
    room.client_seats.setdefault(client_id, None)

    client_to_room[client_id] = room.code
    client_to_ws[client_id] = ws
    room.touch()


async def remove_client_from_room(client_id: str, silent: bool = False) -> None:
    room_code = client_to_room.get(client_id)
    if not room_code:
        return

    room = rooms.get(room_code)
    if not room:
        return

    async with room.lock:
        seat = room.client_seats.get(client_id)

        if seat is not None and 0 <= seat < MAX_PLAYERS:
            # Free the seat if the owner disconnects.
            if room.seat_owners[seat] == client_id:
                name = room.player_display_name(seat)
                room.seat_owners[seat] = None
                room.seat_types[seat] = "empty"
                room.money[seat] = 0
                room.round_bets[seat] = 0
                room.player_hands[seat] = []
                room.message = f"{name} disconnected and left Seat {seat + 1}."

        room.clients.pop(client_id, None)
        room.client_names.pop(client_id, None)
        room.client_seats.pop(client_id, None)
        client_to_room.pop(client_id, None)
        client_to_ws.pop(client_id, None)

        if room.host_client_id == client_id:
            room.host_client_id = next(iter(room.clients.keys()), None)
            if room.host_client_id:
                room.message = "Host disconnected. A new host was assigned."

        if not room.clients:
            # Keep the room briefly? For now delete immediately.
            rooms.pop(room.code, None)
            return

        if not silent:
            await broadcast_state(room)


# =========================
# TURN / BETTING HELPERS
# =========================

def next_active_after(room: Room, current: int) -> Optional[int]:
    for i in range(current + 1, MAX_PLAYERS):
        if room.seat_types[i] != "empty":
            return i
    return None


def next_unsettled_hand(room: Room) -> Optional[tuple[int, int]]:
    for p in range(MAX_PLAYERS):
        if room.seat_types[p] == "empty":
            continue
        for h, hand in enumerate(room.player_hands[p]):
            if not hand.get("finished") and not hand.get("settled"):
                return p, h
    return None


def all_hands_settled(room: Room) -> bool:
    for p in room.round_participants():
        for hand in room.player_hands[p]:
            if not hand.get("settled"):
                return False
    return True


def current_hand(room: Room) -> Optional[Dict[str, Any]]:
    if room.current_player is None:
        return None
    if room.current_player < 0 or room.current_player >= MAX_PLAYERS:
        return None
    hands = room.player_hands[room.current_player]
    if room.current_hand < 0 or room.current_hand >= len(hands):
        return None
    return hands[room.current_hand]


def can_double(room: Room, seat: int, hand: Dict[str, Any]) -> bool:
    if len(hand["cards"]) != 2:
        return False
    if room.money[seat] < hand["bet"]:
        return False
    if hand["bet"] > room.round_start_money[seat] / 2:
        return False
    return True


def can_split(room: Room, seat: int, hand: Dict[str, Any]) -> bool:
    if len(hand["cards"]) != 2:
        return False
    if room.money[seat] < hand["bet"]:
        return False
    if hand["bet"] > room.round_start_money[seat] / 2:
        return False
    return hand["cards"][0][0] == hand["cards"][1][0]


def advance_to_next_betting_player(room: Room) -> None:
    if room.betting_player is None:
        return

    nxt = next_active_after(room, room.betting_player)
    if nxt is not None:
        room.betting_player = nxt
        room.message = f"{room.player_display_name(nxt)}: choose your bet or skip."
        return

    if not room.round_participants():
        room.mode = "round_over"
        room.round_over = True
        room.round_active = False
        room.message = "Everyone skipped. Start betting again when ready."
        return

    start_deal_round(room)


def start_betting(room: Room) -> None:
    active = room.active_players()
    if not active:
        room.message = "No active players. Claim a seat first."
        return

    room.mode = "betting"
    room.round_active = False
    room.round_over = True
    room.dealer_revealed = False
    room.dealer_hand = []
    room.player_hands = [[] for _ in range(MAX_PLAYERS)]
    room.round_bets = [0 for _ in range(MAX_PLAYERS)]
    room.current_player = None
    room.current_hand = 0
    room.betting_player = active[0]
    room.message = f"{room.player_display_name(active[0])}: choose your bet or skip."


def start_deal_round(room: Room) -> None:
    participants = room.round_participants()

    room.deck = new_deck()
    room.dealer_hand = []
    room.player_hands = [[] for _ in range(MAX_PLAYERS)]
    room.round_start_money = room.money.copy()

    for seat in participants:
        bet = room.round_bets[seat]
        if room.money[seat] < bet:
            room.round_bets[seat] = 0
            continue
        room.money[seat] -= bet
        room.player_hands[seat] = [new_hand(bet)]

    participants = room.round_participants()
    if not participants:
        room.mode = "round_over"
        room.message = "No valid bets. Start betting again when ready."
        return

    # Deal player, dealer, player, dealer hidden
    for seat in participants:
        room.player_hands[seat][0]["cards"].append(room.draw_card())

    room.dealer_hand.append({"card": room.draw_card(), "hidden": False})

    for seat in participants:
        room.player_hands[seat][0]["cards"].append(room.draw_card())

    room.dealer_hand.append({"card": room.draw_card(), "hidden": True})

    room.mode = "playing"
    room.round_active = True
    room.round_over = False
    room.dealer_revealed = False
    room.current_player = participants[0]
    room.current_hand = 0
    room.betting_player = None
    room.message = "Cards dealt."
    room.add_voice(random.choice([
        "Bets confirmed. Good luck.",
        "Bets are in. Let's begin.",
        "All bets are down. Let's play.",
        "The table is set. Good luck.",
    ]))

    dealer_cards = [c["card"] for c in room.dealer_hand]
    if is_blackjack(dealer_cards):
        finish_all_hands_due_to_dealer_blackjack(room)
        return

    for seat in participants:
        hand = room.player_hands[seat][0]
        if is_blackjack(hand["cards"]):
            payout = int(hand["bet"] * 2.5)
            room.money[seat] += payout
            hand["finished"] = True
            hand["settled"] = True
            hand["result"] = f"Blackjack! Won ${payout - hand['bet']}"
            room.add_voice(f"{room.player_display_name(seat)} has blackjack.")

    find_next_active_hand(room)


def finish_all_hands_due_to_dealer_blackjack(room: Room) -> None:
    room.dealer_revealed = True
    for seat in room.round_participants():
        for hand in room.player_hands[seat]:
            if is_blackjack(hand["cards"]):
                room.money[seat] += hand["bet"]
                hand["result"] = "Push: both Blackjack"
                room.add_voice(f"{room.player_display_name(seat)} pushes.")
            else:
                hand["result"] = f"Dealer Blackjack. Lost ${hand['bet']}"
                room.add_voice(f"{room.player_display_name(seat)} loses.")
            hand["finished"] = True
            hand["settled"] = True

    room.mode = "round_over"
    room.round_active = False
    room.round_over = True
    room.message = "Dealer has Blackjack. Round over."


def find_next_active_hand(room: Room) -> None:
    nxt = next_unsettled_hand(room)
    if nxt is None:
        if all_hands_settled(room):
            room.mode = "round_over"
            room.round_active = False
            room.round_over = True
            room.message = "Round over. Start betting when ready."
        else:
            start_dealer_turn(room)
        return

    seat, h = nxt
    room.current_player = seat
    room.current_hand = h
    value = hand_value(room.player_hands[seat][h]["cards"])
    room.mode = "playing"
    room.message = f"{room.player_display_name(seat)}, Hand {h + 1}: {value}. Hit, Stand, Double, or Split."


def start_dealer_turn(room: Room) -> None:
    room.mode = "dealer"
    room.dealer_revealed = True
    room.current_player = None
    room.current_hand = 0
    room.message = "Dealer's turn."

    while hand_value([c["card"] for c in room.dealer_hand]) < 17:
        room.dealer_hand.append({"card": room.draw_card(), "hidden": False})

    finish_round(room)


def finish_round(room: Room) -> None:
    dealer_cards = [c["card"] for c in room.dealer_hand]
    dealer_value = hand_value(dealer_cards)

    for seat in room.round_participants():
        for hand in room.player_hands[seat]:
            if hand.get("settled"):
                continue

            player_value = hand_value(hand["cards"])

            if hand.get("busted"):
                hand["result"] = f"Busted: {player_value}. Lost ${hand['bet']}"

            elif dealer_value > 21:
                room.money[seat] += hand["bet"] * 2
                hand["result"] = f"Dealer busts. Won ${hand['bet']}"
                room.add_voice(f"{room.player_display_name(seat)} wins.")

            elif player_value > dealer_value:
                room.money[seat] += hand["bet"] * 2
                hand["result"] = f"Won: {player_value} vs {dealer_value}"
                room.add_voice(f"{room.player_display_name(seat)} wins.")

            elif player_value < dealer_value:
                hand["result"] = f"Lost: {player_value} vs {dealer_value}"
                room.add_voice(f"{room.player_display_name(seat)} loses.")

            else:
                room.money[seat] += hand["bet"]
                hand["result"] = f"Push: {player_value}"
                room.add_voice(f"{room.player_display_name(seat)} pushes.")

            hand["finished"] = True
            hand["settled"] = True

    room.mode = "round_over"
    room.round_active = False
    room.round_over = True
    room.current_player = None
    room.message = "Round over. Start betting when ready."

    # Remove bankrupt players.
    for seat in range(MAX_PLAYERS):
        if room.seat_types[seat] != "empty" and room.money[seat] <= 0:
            room.add_voice(f"{room.player_display_name(seat)} is out of money.")
            room.seat_types[seat] = "empty"
            room.seat_owners[seat] = None
            room.money[seat] = 0
            room.round_bets[seat] = 0
            room.player_hands[seat] = []


# =========================
# BOT LOGIC
# =========================

def bot_choose_bet(room: Room, seat: int) -> int:
    money = room.money[seat]
    if money < MIN_BET:
        return 0
    if money < 1000:
        target = 100
    elif money < 5000:
        target = random.choice([100, 200, 300, 500])
    elif money < 20000:
        target = random.choice([500, 1000, 1500, 2000])
    else:
        target = random.choice([1000, 2000, 3000, 5000])
    return min(target, money)


def dealer_visible_value(room: Room) -> int:
    visible = [c["card"] for c in room.dealer_hand if not c.get("hidden")]
    if not visible:
        return 10
    return card_value(visible[0][0])


def bot_should_split(room: Room, seat: int, hand: Dict[str, Any]) -> bool:
    if not can_split(room, seat, hand):
        return False
    return hand["cards"][0][0] in ["A", "8"]


def bot_should_double(room: Room, seat: int, hand: Dict[str, Any]) -> bool:
    if not can_double(room, seat, hand):
        return False
    value = hand_value(hand["cards"])
    dealer = dealer_visible_value(room)
    if value == 11:
        return True
    if value == 10 and dealer <= 9:
        return True
    if value == 9 and 3 <= dealer <= 6:
        return True
    return False


def bot_action(room: Room, seat: int, hand: Dict[str, Any]) -> str:
    value = hand_value(hand["cards"])
    dealer = dealer_visible_value(room)

    if value >= 21:
        return "STAND"
    if bot_should_split(room, seat, hand):
        return "SPLIT"
    if bot_should_double(room, seat, hand):
        return "DOUBLE"
    if value <= 11:
        return "HIT"
    if value >= 17:
        return "STAND"
    if value == 12:
        return "STAND" if 4 <= dealer <= 6 else "HIT"
    if 13 <= value <= 16:
        return "STAND" if dealer <= 6 else "HIT"
    return "STAND"


def process_bots_until_human_or_done(room: Room) -> None:
    # Safety guard against infinite loops.
    for _ in range(100):
        if room.mode == "betting" and room.betting_player is not None:
            seat = room.betting_player
            if room.seat_types[seat] != "bot":
                return
            bet = bot_choose_bet(room, seat)
            room.round_bets[seat] = bet
            room.message = f"{room.player_display_name(seat)} bets ${bet}." if bet else f"{room.player_display_name(seat)} skips."
            advance_to_next_betting_player(room)
            continue

        if room.mode == "playing" and room.current_player is not None:
            seat = room.current_player
            if room.seat_types[seat] != "bot":
                return
            hand = current_hand(room)
            if not hand:
                find_next_active_hand(room)
                continue
            action = bot_action(room, seat, hand)
            apply_player_action(room, action)
            continue

        return


# =========================
# PLAYER ACTIONS
# =========================

def apply_player_action(room: Room, action: str) -> None:
    hand = current_hand(room)
    if hand is None or room.current_player is None:
        room.message = "No active hand."
        return

    seat = room.current_player
    action = action.upper()

    if action == "HIT":
        hand["cards"].append(room.draw_card())
        value = hand_value(hand["cards"])
        if value > 21:
            hand["busted"] = True
            hand["finished"] = True
            hand["result"] = f"Busted: {value}"
            room.message = f"{room.player_display_name(seat)} busted with {value}."
            room.add_voice(f"{room.player_display_name(seat)} busted with {value}.")
            find_next_active_hand(room)
        elif value == 21:
            hand["finished"] = True
            hand["result"] = "21"
            room.message = f"{room.player_display_name(seat)} gets 21."
            room.add_voice(f"{room.player_display_name(seat)} gets 21.")
            find_next_active_hand(room)
        else:
            room.message = f"{room.player_display_name(seat)}, Hand {room.current_hand + 1}: {value}. Hit or Stand."
        return

    if action == "STAND":
        hand["finished"] = True
        hand["result"] = "Stood"
        room.message = f"{room.player_display_name(seat)} stands."
        find_next_active_hand(room)
        return

    if action == "DOUBLE":
        if not can_double(room, seat, hand):
            room.message = "Cannot double down now."
            return
        room.money[seat] -= hand["bet"]
        hand["bet"] *= 2
        hand["doubled"] = True
        hand["cards"].append(room.draw_card())

        value = hand_value(hand["cards"])
        if value > 21:
            hand["busted"] = True
            hand["result"] = f"Busted after double: {value}"
            room.add_voice(f"{room.player_display_name(seat)} busted with {value}.")
        else:
            hand["result"] = f"Doubled: {value}"
            room.add_voice(f"{room.player_display_name(seat)} gets {value}.")

        hand["finished"] = True
        room.message = f"{room.player_display_name(seat)} doubled and got {value}."
        find_next_active_hand(room)
        return

    if action == "SPLIT":
        if not can_split(room, seat, hand):
            room.message = "Cannot split now. Cards must be exactly the same rank."
            return

        card1 = hand["cards"][0]
        card2 = hand["cards"][1]
        bet = hand["bet"]

        room.money[seat] -= bet

        hand1 = new_hand(bet)
        hand2 = new_hand(bet)
        hand1["cards"] = [card1, room.draw_card()]
        hand2["cards"] = [card2, room.draw_card()]
        hand1["from_split"] = True
        hand2["from_split"] = True

        room.player_hands[seat][room.current_hand] = hand1
        room.player_hands[seat].insert(room.current_hand + 1, hand2)

        room.message = f"{room.player_display_name(seat)} split the hand."
        return

    room.message = f"Unknown action: {action}"


# =========================
# COMMAND HANDLER
# =========================

async def handle_command(client_id: str, ws: ServerConnection, raw: str) -> None:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        await send_json(ws, make_error("Invalid JSON."))
        return

    msg_type = str(msg.get("type", "")).upper()
    name = str(msg.get("name", f"Guest {client_id[:4]}")).strip()[:32] or f"Guest {client_id[:4]}"

    if msg_type == "PING":
        await send_json(ws, {"type": "PONG", "time": time.time()})
        return

    if msg_type == "CREATE_ROOM":
        if client_id in client_to_room:
            await send_json(ws, make_error("You are already in a room."))
            return

        room = await create_room(client_id, ws, name)
        await send_json(ws, {
            "type": "ROOM_CREATED",
            "room_code": room.code,
            "client_id": client_id,
        })
        await broadcast_state(room)
        return

    if msg_type == "JOIN_ROOM":
        if client_id in client_to_room:
            await send_json(ws, make_error("You are already in a room."))
            return

        code = str(msg.get("room_code", "")).strip().upper()
        room = rooms.get(code)
        if not room:
            await send_json(ws, make_error("Room not found. Check the room code."))
            return

        async with room.lock:
            await add_client_to_room(room, client_id, ws, name)
            room.message = f"{name} joined the room."
            room.touch()

        await send_json(ws, {
            "type": "JOIN_OK",
            "room_code": room.code,
            "client_id": client_id,
        })
        await broadcast_state(room)
        return

    room_code = client_to_room.get(client_id)
    if not room_code:
        await send_json(ws, make_error("Create or join a room first."))
        return

    room = rooms.get(room_code)
    if not room:
        await send_json(ws, make_error("Room no longer exists."))
        return

    async with room.lock:
        room.touch()

        if msg_type == "CLAIM_SEAT":
            seat = msg.get("seat")
            if not isinstance(seat, int) or seat < 0 or seat >= MAX_PLAYERS:
                await send_json(ws, make_error("Invalid seat."))
                return

            if room.client_seats.get(client_id) is not None:
                await send_json(ws, make_error("You already control a seat. Leave it first."))
                return

            if room.seat_owners[seat] is not None:
                await send_json(ws, make_error("That seat is already taken."))
                return

            if room.mode not in ["lobby", "round_over", "betting"]:
                await send_json(ws, make_error("You cannot claim a seat during an active hand."))
                return

            room.seat_owners[seat] = client_id
            room.client_seats[client_id] = seat
            room.seat_types[seat] = "human"
            room.money[seat] = STARTING_MONEY
            room.round_bets[seat] = 0
            room.player_hands[seat] = []
            room.message = f"{room.player_display_name(seat)} claimed Seat {seat + 1}."

            await send_json(ws, {"type": "SEAT_ASSIGNED", "seat": seat})
            process_bots_until_human_or_done(room)
            await broadcast_state(room)
            return

        if msg_type == "LEAVE_SEAT":
            seat = room.client_seats.get(client_id)
            if seat is None:
                await send_json(ws, make_error("You do not control a seat."))
                return

            amount = room.money[seat]
            room.seat_owners[seat] = None
            room.client_seats[client_id] = None
            room.seat_types[seat] = "empty"
            room.money[seat] = 0
            room.round_bets[seat] = 0
            room.player_hands[seat] = []
            room.message = f"{room.client_names.get(client_id, 'Player')} left Seat {seat + 1} with ${amount}."

            await send_json(ws, {"type": "SEAT_LEFT", "seat": seat})
            await broadcast_state(room)
            return

        if msg_type == "ADD_BOT":
            if room.mode not in ["lobby", "round_over", "betting"]:
                await send_json(ws, make_error("You cannot add a bot during an active hand."))
                return

            free = [i for i in range(MAX_PLAYERS) if room.seat_types[i] == "empty"]
            if not free:
                await send_json(ws, make_error("No free seats for bots."))
                return

            seat = free[0]
            room.seat_types[seat] = "bot"
            room.seat_owners[seat] = "BOT"
            room.money[seat] = STARTING_MONEY
            room.round_bets[seat] = 0
            room.message = f"{BOT_NAMES[seat]} joined Seat {seat + 1}."
            process_bots_until_human_or_done(room)
            await broadcast_state(room)
            return

        if msg_type == "START_BETTING":
            if room.mode not in ["lobby", "round_over", "betting"]:
                await send_json(ws, make_error("Round already active."))
                return
            start_betting(room)
            process_bots_until_human_or_done(room)
            await broadcast_state(room)
            return

        if msg_type == "BET":
            seat = room.client_seats.get(client_id)
            if seat is None:
                await send_json(ws, make_error("You do not control a seat."))
                return
            if room.mode != "betting":
                await send_json(ws, make_error("It is not betting time."))
                return
            if room.betting_player != seat:
                await send_json(ws, make_error("It is not your betting turn."))
                return

            amount = msg.get("amount")
            if not isinstance(amount, int) or amount < MIN_BET:
                await send_json(ws, make_error(f"Minimum bet is ${MIN_BET}."))
                return
            if amount > room.money[seat]:
                await send_json(ws, make_error("Not enough money."))
                return

            room.round_bets[seat] = amount
            room.message = f"{room.player_display_name(seat)} bets ${amount}."
            advance_to_next_betting_player(room)
            process_bots_until_human_or_done(room)
            await broadcast_state(room)
            return

        if msg_type == "SKIP_BET":
            seat = room.client_seats.get(client_id)
            if seat is None:
                await send_json(ws, make_error("You do not control a seat."))
                return
            if room.mode != "betting":
                await send_json(ws, make_error("It is not betting time."))
                return
            if room.betting_player != seat:
                await send_json(ws, make_error("It is not your betting turn."))
                return

            room.round_bets[seat] = 0
            room.message = f"{room.player_display_name(seat)} skips."
            advance_to_next_betting_player(room)
            process_bots_until_human_or_done(room)
            await broadcast_state(room)
            return

        if msg_type in ["HIT", "STAND", "DOUBLE", "SPLIT"]:
            seat = room.client_seats.get(client_id)
            if seat is None:
                await send_json(ws, make_error("You do not control a seat."))
                return
            if room.mode != "playing":
                await send_json(ws, make_error("It is not playing time."))
                return
            if room.current_player != seat:
                await send_json(ws, make_error("It is not your turn."))
                return

            apply_player_action(room, msg_type)
            process_bots_until_human_or_done(room)
            await broadcast_state(room)
            return

        await send_json(ws, make_error(f"Unknown command: {msg_type}"))


# =========================
# WEBSOCKET CONNECTION HANDLER
# =========================

async def connection_handler(ws: ServerConnection) -> None:
    client_id = secrets.token_hex(8)

    await send_json(ws, {
        "type": "HELLO",
        "client_id": client_id,
        "server": "JASLI_BLACKJACK_WS",
        "message": "Connected. Create or join a room.",
    })

    try:
        async for raw in ws:
            await handle_command(client_id, ws, raw)

    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as exc:
        try:
            await send_json(ws, make_error(str(exc)))
        except Exception:
            pass
    finally:
        await remove_client_from_room(client_id)


# =========================
# HOUSEKEEPING
# =========================

async def room_cleanup_loop() -> None:
    while True:
        await asyncio.sleep(60)
        now = time.time()
        stale = [
            code for code, room in rooms.items()
            if not room.clients or now - room.last_activity > ROOM_IDLE_SECONDS
        ]
        for code in stale:
            rooms.pop(code, None)


async def main_async(host: str, port: int) -> None:
    print("==========================================")
    print("JASLI's Casino - Online Blackjack Server")
    print("==========================================")
    print(f"Listening on: ws://{host}:{port}")
    print("Clients create rooms and join with room codes.")
    print("Do not use this for real-money gambling.")
    print("==========================================")

    stop = asyncio.Future()

    def ask_exit(*_args: Any) -> None:
        if not stop.done():
            stop.set_result(None)

    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, ask_exit)
        loop.add_signal_handler(signal.SIGINT, ask_exit)
    except (NotImplementedError, RuntimeError):
        # Windows event loops may not support signal handlers.
        pass

    cleanup_task = asyncio.create_task(room_cleanup_loop())

    async with serve(connection_handler, host, port):
        await stop

    cleanup_task.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(description="JASLI's Casino Online Blackjack WebSocket Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host/interface to bind. Default: 0.0.0.0")
    parser.add_argument("--port", default=8765, type=int, help="Port to listen on. Default: 8765")
    args = parser.parse_args()

    asyncio.run(main_async(args.host, args.port))


if __name__ == "__main__":
    main()
