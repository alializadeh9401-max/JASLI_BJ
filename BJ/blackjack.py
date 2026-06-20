import pygame
import random
import sys
import socket
import json
import subprocess
import re
import threading
import queue
import math
import time
import array
import os
import asyncio

# =========================
# ONLINE WEBSOCKET SERVER SUPPORT ONLY
# =========================
# This build does NOT use same-Wi-Fi Online hosting in the UI.
# Players connect to a WebSocket server URL, create/join a room code,
# and the WebSocket server owns the real blackjack state.
# =========================

network = None
online_mode = False
my_seat = None
my_client_id = None
server_state = None

# =========================
# APP FLOW / MENU STATE
# =========================
# app_screen controls the high-level application flow.
# intro -> main_menu -> multiplayer_menu / playing
app_screen = "intro"
play_mode = None  # None, "single", "online_host", or "online_guest"

# Online mode uses a public WebSocket server. Host creates a room code; guests join that room code.
current_server_number = ""  # legacy field kept unused for compatibility
menu_status_message = ""

# =========================
# ONLINE WEBSOCKET SUPPORT
# =========================
# HARD-CODED ONLINE SERVER URL
# ------------------------------------------------------------------
# Put your deployed WebSocket server URL here before building the exe.
# Players will NOT type this in the app. They only create/join room codes.
#
# Examples:
#   DEFAULT_ONLINE_SERVER_URL = "wss://jaslis-blackjack.onrender.com"
#   DEFAULT_ONLINE_SERVER_URL = "wss://your-server.fly.dev"
#
# Local developer test only:
#   DEFAULT_ONLINE_SERVER_URL = "ws://127.0.0.1:8765"
# ------------------------------------------------------------------
DEFAULT_ONLINE_SERVER_URL = "wss://jasli-blackjack-server.onrender.com"
online_ws_client = None
online_ws_active = False
online_room_code = ""
online_server_url = DEFAULT_ONLINE_SERVER_URL
online_last_error = ""

try:
    import websockets
except Exception:
    websockets = None

# Voice events are kept in the authoritative host state so guests can hear
# announcements even if a one-off socket message is missed or arrives between frames.
voice_event_counter = 0
server_voice_events = []
seen_voice_event_ids = set()

class OnlineWebSocketClient:
    """Background-thread WebSocket client for online multiplayer.

    The Pygame main loop stays normal. This object owns an asyncio loop in a
    daemon thread, receives server JSON into inbox, and accepts outgoing JSON via
    a thread-safe queue.
    """
    def __init__(self):
        self.url = ""
        self.connected = False
        self.inbox = queue.Queue()
        self.outbox = queue.Queue()
        self.thread = None
        self.stop_event = threading.Event()
        self._ready = threading.Event()
        self._last_error = ""

    def connect(self, url, timeout=7.0):
        self.close()
        self.url = url.strip()
        self.connected = False
        self.stop_event.clear()
        self._ready.clear()
        self._last_error = ""
        self.thread = threading.Thread(target=self._thread_main, daemon=True)
        self.thread.start()
        self._ready.wait(timeout=timeout)
        if not self.connected:
            raise RuntimeError(self._last_error or "Could not connect to online server.")

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self._last_error = str(exc)
            self.connected = False
            self._ready.set()

    async def _run(self):
        if websockets is None:
            self._last_error = "Missing dependency: pip install websockets"
            self._ready.set()
            return

        try:
            async with websockets.connect(self.url, ping_interval=20, ping_timeout=20) as ws:
                self.connected = True
                self._ready.set()

                async def reader():
                    try:
                        async for raw in ws:
                            try:
                                self.inbox.put(json.loads(raw))
                            except json.JSONDecodeError:
                                pass
                    finally:
                        self.connected = False

                async def writer():
                    while self.connected and not self.stop_event.is_set():
                        item = await asyncio.to_thread(self.outbox.get)
                        if item is None:
                            break
                        try:
                            await ws.send(json.dumps(item))
                        except Exception as exc:
                            self._last_error = str(exc)
                            self.connected = False
                            break

                await asyncio.gather(reader(), writer())

        except Exception as exc:
            self._last_error = str(exc)
            self.connected = False
            self._ready.set()

    def send(self, data):
        if not self.connected:
            return
        self.outbox.put(data)

    def get_messages(self):
        messages = []
        while not self.inbox.empty():
            messages.append(self.inbox.get())
        return messages

    def close(self):
        self.stop_event.set()
        self.connected = False
        try:
            self.outbox.put_nowait(None)
        except Exception:
            pass


def is_online_ws_client():
    return online_ws_active and online_ws_client is not None and online_ws_client.connected


def connect_online_server(url):
    global online_ws_client, online_ws_active, online_server_url, online_last_error
    global my_seat, my_client_id, server_state, online_mode

    if online_ws_client is None:
        online_ws_client = OnlineWebSocketClient()

    try:
        if network is not None:
            network.close()
    except Exception:
        pass

    leave_host_mode_before_joining_as_guest()
    my_seat = None
    my_client_id = None
    server_state = None
    online_mode = False  # legacy raw-socket legacy local mode off
    online_server_url = url.strip() or DEFAULT_ONLINE_SERVER_URL

    try:
        online_ws_client.connect(online_server_url)
        online_ws_active = True
        online_last_error = ""
        return True
    except Exception as exc:
        online_ws_active = False
        online_last_error = str(exc)
        return False


def online_send(payload):
    if is_online_ws_client():
        online_ws_client.send(payload)


def create_online_room(player_name="Host"):
    online_send({"type": "CREATE_ROOM", "name": player_name})


def join_online_room(room_code, player_name="Guest"):
    online_send({"type": "JOIN_ROOM", "room_code": room_code.strip().upper(), "name": player_name})


def map_online_state_mode(server_mode):
    if server_mode == "betting":
        return "betting"
    # The local renderer uses "game" for normal table drawing.
    return "game"


def apply_online_state_to_game(state):
    """Mirror authoritative WebSocket server state into the existing Pygame renderer."""
    global my_seat, my_client_id, online_room_code, server_state

    server_state = state
    online_room_code = state.get("room_code", online_room_code)
    my_client_id = state.get("client_id", my_client_id)
    my_seat = state.get("my_seat", my_seat)

    game.mode = map_online_state_mode(state.get("mode", "lobby"))
    game.seat_types = state.get("seat_types", game.seat_types).copy()
    game.money = state.get("money", game.money).copy()
    game.round_bets = state.get("round_bets", game.round_bets).copy()
    game.round_start_money = state.get("round_start_money", game.round_start_money).copy()
    game.betting_player = state.get("betting_player") if state.get("betting_player") is not None else 0
    game.current_player = state.get("current_player") if state.get("current_player") is not None else 0
    game.current_hand = state.get("current_hand", 0) or 0
    game.message = state.get("message", game.message)
    game.dealer_revealed = state.get("dealer_revealed", False)
    game.round_active = state.get("round_active", False)
    game.round_over = state.get("round_over", True)

    # Dealer: hidden cards arrive as {card: null, hidden: true}; renderer needs a placeholder card.
    new_dealer = []
    for card_data in state.get("dealer_hand", []):
        card = card_data.get("card")
        if not card:
            card = ["A", "♠"]
        new_dealer.append({"card": tuple(card), "hidden": card_data.get("hidden", False)})
    game.dealer_hand = new_dealer

    raw_player_hands = state.get("player_hands")
    if raw_player_hands is not None:
        game.player_hands = [
            [deserialize_hand_from_network(hand_data) for hand_data in hands]
            for hands in raw_player_hands
        ]
        while len(game.player_hands) < MAX_PLAYERS:
            game.player_hands.append([])

    # Online server is authoritative; never locally animate/deal/advance.
    game.animations = []
    game.deal_queue = []
    game.processing_deal_queue = False
    game.deal_phase = None
    game.auto_betting_time = None

    if game.mode == "betting" and game.betting_player is not None:
        if 0 <= game.betting_player < MAX_PLAYERS:
            if not game.current_bet_options:
                game.current_bet_options = generate_bet_options(game.money[game.betting_player])
                game.current_bet_index = 0
    else:
        game.current_bet_options = []

    process_voice_events_from_state(state)


def update_online_messages():
    global my_client_id, my_seat, online_room_code, online_ws_active

    if not is_online_ws_client():
        return

    for msg in online_ws_client.get_messages():
        msg_type = msg.get("type")

        if msg_type == "HELLO":
            my_client_id = msg.get("client_id", my_client_id)
            game.message = msg.get("message", "Connected to online server.")

        elif msg_type == "ROOM_CREATED":
            online_room_code = msg.get("room_code", online_room_code)
            my_client_id = msg.get("client_id", my_client_id)
            game.message = f"Online room created: {online_room_code}. Press 1-5 to claim a seat."

        elif msg_type == "JOIN_OK":
            online_room_code = msg.get("room_code", online_room_code)
            my_client_id = msg.get("client_id", my_client_id)
            game.message = f"Joined online room {online_room_code}. Press 1-5 to claim a seat."

        elif msg_type == "SEAT_ASSIGNED":
            my_seat = msg.get("seat")
            game.message = f"You control Seat {my_seat + 1}. Press ENTER to start betting when ready."

        elif msg_type == "STATE":
            state = msg.get("state", {})
            if state:
                apply_online_state_to_game(state)

        elif msg_type == "ERROR":
            game.message = msg.get("message", game.message)

        elif msg_type == "PONG":
            pass


def start_online_host_mode(server_url=None):
    global app_screen, play_mode, menu_status_message, online_room_code
    ok = connect_online_server(server_url or DEFAULT_ONLINE_SERVER_URL)
    if not ok:
        menu_status_message = f"Online connection failed: {online_last_error}"
        return
    play_mode = "online_host"
    app_screen = "playing"
    online_room_code = ""
    if game.mode == "intro":
        game.mode = "game"
    game.message = "Connected. Creating online room..."
    create_online_room("Host")


def start_online_guest_mode(server_url, room_code):
    global app_screen, play_mode, menu_status_message, online_room_code
    ok = connect_online_server(server_url or DEFAULT_ONLINE_SERVER_URL)
    if not ok:
        menu_status_message = f"Online connection failed: {online_last_error}"
        return False
    play_mode = "online_guest"
    app_screen = "playing"
    online_room_code = room_code.strip().upper()
    if game.mode == "intro":
        game.mode = "game"
    game.message = f"Connected. Joining room {online_room_code}..."
    join_online_room(online_room_code, "Guest")
    return True


class NetworkClient:
    def __init__(self):
        self.sock = None
        self.connected = False
        self.inbox = queue.Queue()
        self.reader_thread = None
        self._recv_buffer = ""

    def connect(self, host, port, room_code):
        self.close()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect((host, port))
        self.sock.settimeout(None)
        self.connected = True
        self.reader_thread = threading.Thread(target=self.reader_loop, daemon=True)
        self.reader_thread.start()
        self.send({"type": "JOIN", "room_code": room_code})

    def reader_loop(self):
        while self.connected:
            try:
                data = self.sock.recv(4096)
                if not data:
                    break
                self._recv_buffer += data.decode("utf-8")
                while "\n" in self._recv_buffer:
                    line, self._recv_buffer = self._recv_buffer.split("\n", 1)
                    if not line.strip():
                        continue
                    try:
                        self.inbox.put(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            except Exception:
                break

        self.connected = False

    def send(self, data):
        if not self.connected:
            return
        try:
            message = json.dumps(data) + "\n"
            self.sock.sendall(message.encode("utf-8"))
        except Exception:
            self.connected = False

    def get_messages(self):
        messages = []
        while not self.inbox.empty():
            messages.append(self.inbox.get())
        return messages

    def close(self):
        self.connected = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass


def leave_host_mode_before_joining_as_guest():
    """
    One app instance must never be both HOST and GUEST.
    If this laptop was hosting and the user presses C / Connect, fully drop host mode
    before opening or attempting a guest connection. Otherwise seat keys keep using
    host_claim_or_leave_seat() instead of sending JOIN_SEAT to the real host.
    """
    global server_running, server_game_state, server_room_code, server_clients
    global server_client_ids, server_client_seats, server_seat_owners, server_next_client_id
    global my_seat, my_client_id, server_state, online_mode, seen_voice_event_ids

    if server_running:
        try:
            stop_local_server()
        except Exception:
            server_running = False

    # Clear all local host identity/state. This laptop is about to be a normal guest.
    server_game_state = None
    server_room_code = ""
    server_clients = []
    server_client_ids = {}
    server_client_seats = {}
    server_seat_owners = [None for _ in range(MAX_PLAYERS)]
    server_next_client_id = 1

    my_seat = None
    my_client_id = None
    server_state = None
    seen_voice_event_ids = set()
    online_mode = False


def connect_to_disabled_legacy_socket_server(host_ip, room_code, port=5050):
    global online_mode, network, my_seat, my_client_id, server_state

    # IMPORTANT: an app instance cannot host and join at the same time.
    # This is the bug where Laptop 2 pressed G, then C, but still behaved like a host.
    leave_host_mode_before_joining_as_guest()

    # This laptop's controlled seat is private local state.
    # Never carry it over from a previous session or from the host's broadcast state.
    my_seat = None
    my_client_id = None
    server_state = None

    if network is None:
        network = NetworkClient()
    else:
        try:
            network.close()
        except Exception:
            pass

    try:
        network.connect(host_ip, port, room_code)
        online_mode = True
        return True
    except Exception:
        online_mode = False
        my_seat = None
        my_client_id = None
        return False


def remember_host_voice_event(text):
    """Host-side: add a voice line to the reliable replicated event list."""
    global voice_event_counter, server_voice_events

    voice_event_counter += 1
    event = {"id": voice_event_counter, "text": str(text), "time": time.time()}
    server_voice_events.append(event)

    # Keep the list small. Guests track ids, so old lines are not needed forever.
    if len(server_voice_events) > 40:
        server_voice_events = server_voice_events[-40:]

    return event


def speak_remote_voice_event(event):
    """Guest-side: speak a replicated voice event once only."""
    if not isinstance(event, dict):
        return

    text = event.get("text", "")
    if not text:
        return

    event_id = event.get("id", event.get("voice_id"))
    if event_id is not None:
        key = str(event_id)
        if key in seen_voice_event_ids:
            return
        seen_voice_event_ids.add(key)

    # Remote clients are allowed to use the same local TTS system, but since they
    # are not hosting, this will not re-broadcast anything.
    speak(text)


def process_voice_events_from_state(state):
    """Guest-side reliable voice sync via normal STATE packets."""
    if not is_disabled_legacy_remote_client():
        return

    for event in state.get("voice_events", []):
        speak_remote_voice_event(event)


def update_disabled_legacy_socket_messages():
    global server_state, my_seat, my_client_id, online_mode
    if not online_mode or network is None or not network.connected:
        return

    for msg in network.get_messages():
        msg_type = msg.get("type")

        if msg_type in ["STATE", "FULL_STATE"]:
            state = msg.get("state")
            if state:
                server_state = state
                apply_network_state_to_game(state)
                process_voice_events_from_state(state)

        elif msg_type == "JOIN_OK":
            # This is the only point where this app learns its private client id.
            # The host's seat choice must never become this client's my_seat.
            my_client_id = msg.get("client_id", my_client_id)
            my_seat = None
            game.message = msg.get("message", game.message)

        elif msg_type == "SEAT_ASSIGNED":
            my_seat = msg.get("seat")
            if isinstance(my_seat, int) and 0 <= my_seat < MAX_PLAYERS:
                game.mode = "game"
                game.seat_types[my_seat] = "human"
                if game.money[my_seat] <= 0:
                    game.money[my_seat] = STARTING_MONEY
                game.message = msg.get("message", f"You control Seat {my_seat + 1}.")

        elif msg_type == "SEAT_RELEASED":
            seat = msg.get("seat")
            if seat == my_seat:
                my_seat = None
            game.message = msg.get("message", game.message)

        elif msg_type == "VOICE":
            # Fast path. The reliable path is also embedded in STATE via voice_events.
            # speak_remote_voice_event() prevents duplicates using event ids.
            speak_remote_voice_event({
                "id": msg.get("id", msg.get("voice_id")),
                "text": msg.get("text", ""),
            })

        elif msg_type == "ERROR":
            game.message = msg.get("message", game.message)

        elif msg_type == "SERVER_CLOSED":
            game.message = "Server closed. Returning to local mode."
            online_mode = False
            my_seat = None
            try:
                network.close()
            except Exception:
                pass


def host_claim_or_leave_seat(number):
    global my_seat
    seat = number - 1
    if seat < 0 or seat >= MAX_PLAYERS:
        return

    if server_game_state is None:
        game.message = "Connect to the online WebSocket server first."
        return

    if game.round_active or game.animations or game.deal_queue or game.processing_deal_queue:
        game.message = "You can only change seats between rounds or during betting."
        return

    owner = server_seat_owners[seat]

    if my_seat == seat:
        amount = game.money[seat]
        server_seat_owners[seat] = None
        my_seat = None
        game.seat_types[seat] = "empty"
        game.money[seat] = 0
        game.round_bets[seat] = 0
        game.player_hands[seat] = []
        game.message = f"Player {seat + 1} cashed out with ${amount}."
        if not game.any_active_players():
            game.mode = "game"
            game.round_active = False
            game.round_over = True
        else:
            game.schedule_auto_betting(0.6)
        sync_host_game_to_server()
        return

    if my_seat is not None:
        game.message = f"You already control Seat {my_seat + 1}. Press that number to leave first."
        return

    if owner is not None or not game.seat_is_empty(seat):
        game.message = "That seat is already taken."
        return

    server_seat_owners[seat] = "HOST"
    my_seat = seat
    game.seat_types[seat] = "human"
    game.money[seat] = STARTING_MONEY
    game.round_bets[seat] = 0
    game.player_hands[seat] = []
    game.message = f"Host claimed Seat {seat + 1} with ${STARTING_MONEY}."
    if game.mode == "betting":
        game.begin_betting()
    else:
        game.schedule_auto_betting(0.6)
    sync_host_game_to_server()


def network_or_local_join_seat(number):
    if number < 1 or number > MAX_PLAYERS:
        return

    if is_online_ws_client():
        seat_index = number - 1
        if my_seat == seat_index:
            game.message = f"Leaving Seat {number}..."
            online_send({"type": "LEAVE_SEAT"})
        else:
            game.message = f"Requesting Seat {number}..."
            online_send({"type": "CLAIM_SEAT", "seat": seat_index})
        return

    if server_running:
        host_claim_or_leave_seat(number)
        return

    if online_mode and network is not None and network.connected:
        seat_index = number - 1
        # Safety: if a bad/stale local my_seat points at a HOST-owned seat, do not
        # treat it as ours. This was the bug where guests inherited Seat 1.
        if server_state is not None:
            reconcile_my_seat_from_owner_list(server_state)

        if my_seat == seat_index:
            game.message = f"Leaving Seat {number}..."
            network.send({"type": "LEAVE_SEAT"})
        else:
            game.message = f"Requesting Seat {number}..."
            network.send({"type": "JOIN_SEAT", "seat": seat_index})
    else:
        game.add_human_by_number(number)


def network_or_local_confirm_bet():
    if is_online_ws_client():
        if server_state and server_state.get("mode") in ["lobby", "round_over"]:
            online_send({"type": "START_BETTING"})
            game.message = "Starting online betting..."
            return
        if my_seat == game.betting_player:
            online_send({"type": "BET", "amount": game.current_selected_bet()})
        else:
            game.message = "It is not your betting turn."
        return

    if server_running:
        if my_seat == game.betting_player:
            game.confirm_bet()
        else:
            game.message = "It is not your betting turn."
        return

    if online_mode and network is not None and network.connected and my_seat is not None:
        if my_seat == game.betting_player:
            network.send({"type": "BET", "amount": game.current_selected_bet()})
        else:
            game.message = "It is not your betting turn."
    else:
        game.confirm_bet()


def network_or_local_skip_bet():
    if is_online_ws_client():
        if my_seat == game.betting_player:
            online_send({"type": "SKIP_BET"})
        else:
            game.message = "It is not your betting turn."
        return

    if server_running:
        if my_seat == game.betting_player:
            game.skip_bet()
        else:
            game.message = "It is not your betting turn."
        return

    if online_mode and network is not None and network.connected and my_seat is not None:
        if my_seat == game.betting_player:
            network.send({"type": "SKIP_BET"})
        else:
            game.message = "It is not your betting turn."
    else:
        game.skip_bet()


def network_or_local_hit():
    if is_online_ws_client():
        online_send({"type": "HIT"})
        return

    if server_running:
        if my_seat == game.current_player:
            game.hit()
        else:
            game.message = "It is not your turn."
        return

    if online_mode and network is not None and network.connected and my_seat is not None:
        network.send({"type": "HIT"})
    else:
        game.hit()


def network_or_local_stand():
    if is_online_ws_client():
        online_send({"type": "STAND"})
        return

    if server_running:
        if my_seat == game.current_player:
            game.stand()
        else:
            game.message = "It is not your turn."
        return

    if online_mode and network is not None and network.connected and my_seat is not None:
        network.send({"type": "STAND"})
    else:
        game.stand()


def network_or_local_double():
    if is_online_ws_client():
        online_send({"type": "DOUBLE"})
        return

    if server_running:
        if my_seat == game.current_player:
            game.double_down()
        else:
            game.message = "It is not your turn."
        return

    if online_mode and network is not None and network.connected and my_seat is not None:
        network.send({"type": "DOUBLE"})
    else:
        game.double_down()


def network_or_local_split():
    if is_online_ws_client():
        online_send({"type": "SPLIT"})
        return

    if server_running:
        if my_seat == game.current_player:
            game.split()
        else:
            game.message = "It is not your turn."
        return

    if online_mode and network is not None and network.connected and my_seat is not None:
        network.send({"type": "SPLIT"})
    else:
        game.split()


connect_overlay_active = False
connect_host_text = ""  # Server URL for online WebSocket mode.
connect_room_text = ""
connect_input_field = 0
connect_status_message = ""
connect_cursor_visible = True
connect_cursor_last_blink = time.time()
connect_port = 5050  # legacy local port, unused by online WebSocket mode
connect_overlay_mode = "join"  # "create" or "join"

server_running = False
server_room_code = ""
server_port = 5050
server_clients = []
server_clients_lock = threading.Lock()
server_stop_event = threading.Event()
server_socket = None
server_game_state = None
server_pending_commands = []  # Queue of commands from clients for host to execute
server_pending_commands_lock = threading.Lock()

# Seat ownership used by the old embedded local server path.
# Owners are either "HOST" for the hosting machine, or an integer client id for a remote laptop.
server_client_ids = {}
server_client_seats = {}
server_seat_owners = [None for _ in range(5)]
server_next_client_id = 1


def is_disabled_legacy_remote_client():
    return ((online_mode and network is not None and network.connected and not server_running) or is_online_ws_client())


def is_disabled_legacy_host():
    return server_running


def is_multiplayer_active():
    return server_running or (online_mode and network is not None and network.connected) or is_online_ws_client()


def local_controls_seat(seat):
    if seat is None:
        return False
    if is_multiplayer_active():
        return my_seat == seat
    return True


def reconcile_my_seat_from_owner_list(state):
    """Remote client safety net.

    The public table state may say which seats are taken, but this laptop's
    controlled seat is private. Only a seat owned by this client's id counts
    as my_seat. HOST-owned seats are never controlled by guests.
    """
    global my_seat

    if not is_disabled_legacy_remote_client() or my_client_id is None:
        return

    owners = state.get("seat_owners")
    if not isinstance(owners, list):
        return

    mine = None
    my_id_text = str(my_client_id)

    for i, owner in enumerate(owners):
        if str(owner) == my_id_text:
            mine = i
            break

    if mine is not None:
        my_seat = mine
    elif my_seat is not None:
        # If the server says our old seat is now empty/HOST/someone else, release it locally.
        if my_seat >= len(owners) or str(owners[my_seat]) != my_id_text:
            my_seat = None


def get_local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def make_server_number(ip_address, room_code):
    """Legacy helper kept unused in online mode.

    Format: 12 digits for IPv4 octets padded to 3 digits + 4 digit room code.
    Example: 192.168.1.42 + 7392 -> 1921680010427392

    Legacy helper kept unused in online mode.
    """
    try:
        parts = [int(x) for x in str(ip_address).split(".")]
        if len(parts) != 4 or any(x < 0 or x > 255 for x in parts):
            return ""
        room = "".join(ch for ch in str(room_code) if ch.isdigit())
        if len(room) != 4:
            return ""
        return "".join(f"{x:03d}" for x in parts) + room
    except Exception:
        return ""


def format_server_number(server_number):
    digits = "".join(ch for ch in str(server_number) if ch.isdigit())
    if len(digits) != 16:
        return str(server_number)
    return f"{digits[0:3]} {digits[3:6]} {digits[6:9]} {digits[9:12]} {digits[12:16]}"


def parse_server_number(server_number):
    """Legacy helper kept unused in online mode."""
    digits = "".join(ch for ch in str(server_number) if ch.isdigit())
    if len(digits) != 16:
        raise ValueError("Room code must contain 16 digits.")
    octets = [int(digits[i:i + 3]) for i in range(0, 12, 3)]
    if any(x < 0 or x > 255 for x in octets):
        raise ValueError("Room code contains an invalid IP section.")
    host_ip = ".".join(str(x) for x in octets)
    room_code = digits[12:16]
    return host_ip, room_code


def server_send_json(conn, data):
    message = json.dumps(data) + "\n"
    conn.sendall(message.encode("utf-8"))


def server_broadcast(data):
    global server_clients
    dead = []
    with server_clients_lock:
        for conn in list(server_clients):
            try:
                server_send_json(conn, data)
            except Exception:
                dead.append(conn)

        for conn in dead:
            try:
                server_clients.remove(conn)
            except ValueError:
                pass


def server_broadcast_state():
    if server_game_state is None:
        return
    server_broadcast({
        "type": "STATE",
        "state": server_game_state,
    })


def queue_server_command(command):
    # Network threads call this; the pygame/main thread drains it.
    # This keeps all real BlackjackGame changes on the main thread.
    with server_pending_commands_lock:
        server_pending_commands.append(command)


def drain_server_commands():
    with server_pending_commands_lock:
        commands = list(server_pending_commands)
        server_pending_commands.clear()
    return commands


def sync_host_game_to_server():
    """Host pushes the complete authoritative game state to every connected client."""
    global server_game_state
    if not server_running or server_game_state is None:
        return

    server_game_state.clear()
    server_game_state.update(serialize_game_state_for_network(game))
    server_game_state["room_code"] = server_room_code
    server_game_state["host_ip"] = get_local_ip()
    server_game_state["port"] = server_port
    server_game_state["seat_owners"] = [str(owner) if owner is not None else None for owner in server_seat_owners]
    server_game_state["voice_events"] = list(server_voice_events[-40:])
    server_broadcast_state()


def process_host_commands():
    """Host executes commands sent by remote clients. The host game is the authority."""
    global server_pending_commands
    if not server_running:
        return

    for cmd in drain_server_commands():
        cmd_type = cmd.get("type")
        seat = cmd.get("seat")

        if cmd_type == "JOIN_SEAT":
            if isinstance(seat, int) and 0 <= seat < MAX_PLAYERS:
                if not game.round_active and not game.animations and not game.deal_queue and not game.processing_deal_queue:
                    # Apply the remote client's seat claim to the real host game.
                    # Do not require game.seat_is_empty here, because the server may have
                    # already reserved this seat in its network mirror before this frame.
                    game.seat_types[seat] = "human"
                    game.money[seat] = STARTING_MONEY
                    game.round_bets[seat] = 0
                    game.player_hands[seat] = []
                    game.message = f"Player {seat + 1} joined the table."
                    if game.mode == "intro":
                        game.mode = "game"
                    if game.mode == "betting":
                        game.begin_betting()
                    else:
                        game.schedule_auto_betting(0.6)
            continue

        if cmd_type in ["LEAVE_SEAT", "DISCONNECT_SEAT"]:
            if isinstance(seat, int) and 0 <= seat < MAX_PLAYERS:
                if not game.round_active and not game.animations and not game.deal_queue:
                    amount = game.money[seat]
                    game.seat_types[seat] = "empty"
                    game.money[seat] = 0
                    game.round_bets[seat] = 0
                    game.player_hands[seat] = []
                    if cmd_type == "DISCONNECT_SEAT":
                        game.message = f"Player {seat + 1} disconnected."
                    else:
                        game.message = f"Player {seat + 1} left with ${amount}."
                    if not game.any_active_players():
                        game.mode = "game"
                        game.round_active = False
                        game.round_over = True
                    else:
                        game.schedule_auto_betting(0.6)
            continue

        if cmd_type == "BET":
            amount = cmd.get("amount")
            if game.mode == "betting" and seat == game.betting_player and isinstance(amount, int):
                if 0 < amount <= game.money[seat]:
                    game.round_bets[seat] = amount
                    game.advance_betting_player()
            continue

        if cmd_type == "SKIP_BET":
            if game.mode == "betting" and seat == game.betting_player:
                game.round_bets[seat] = 0
                game.advance_betting_player()
            continue

        if cmd_type in ["HIT", "STAND", "DOUBLE", "SPLIT"]:
            if seat != game.current_player:
                continue
            if not game.can_act():
                continue

            if cmd_type == "HIT":
                game.perform_hit()
            elif cmd_type == "STAND":
                game.perform_stand()
            elif cmd_type == "DOUBLE":
                game.perform_double_down()
            elif cmd_type == "SPLIT":
                game.perform_split()


def get_server_client_id(conn):
    return server_client_ids.get(conn)


def get_server_owned_seat(conn):
    client_id = get_server_client_id(conn)
    if client_id is None:
        return None
    return server_client_seats.get(client_id)


def server_handle_command(conn, command):
    command_type = command.get("type")
    client_id = get_server_client_id(conn)

    if command_type == "JOIN":
        code = command.get("room_code")
        if code != server_room_code:
            server_send_json(conn, {"type": "ERROR", "message": "Wrong room code."})
            return

        # A fresh TCP connection starts without a controlled seat. The host's
        # own seat is represented by server_seat_owners == "HOST" and must not
        # be copied into this remote client.
        if client_id is not None:
            server_client_seats[client_id] = None

        server_send_json(conn, {
            "type": "JOIN_OK",
            "client_id": client_id,
            "message": "Connected to JASLI's Casino. Press 1-5 to claim a seat.",
        })
        if server_game_state is not None:
            server_send_json(conn, {"type": "FULL_STATE", "state": server_game_state})
        return

    if command_type == "JOIN_SEAT":
        seat = command.get("seat")
        if not isinstance(seat, int) or seat < 0 or seat >= MAX_PLAYERS:
            server_send_json(conn, {"type": "ERROR", "message": "Invalid seat."})
            return

        current_owned = server_client_seats.get(client_id)
        if current_owned is not None:
            # Only reject if this exact remote client really owns that seat.
            # If the seat is HOST-owned or owned by somebody else, clear the stale mapping.
            if (0 <= current_owned < MAX_PLAYERS) and server_seat_owners[current_owned] == client_id:
                server_send_json(conn, {"type": "ERROR", "message": f"You already control Seat {current_owned + 1}. Press that number to leave first."})
                return
            server_client_seats[client_id] = None

        if server_game_state is not None:
            if server_game_state.get("round_active"):
                server_send_json(conn, {"type": "ERROR", "message": "You can only join between rounds or during betting."})
                return
            seats = server_game_state.get("seat_types", server_game_state.get("seats", []))
            if seat < len(seats) and seats[seat] != "empty":
                server_send_json(conn, {"type": "ERROR", "message": "That seat is already taken."})
                return

        if server_seat_owners[seat] is not None:
            server_send_json(conn, {"type": "ERROR", "message": "That seat is already taken."})
            return

        server_seat_owners[seat] = client_id
        server_client_seats[client_id] = seat

        # Reserve the seat immediately in the network state so the joining laptop
        # gets visible feedback right away. The host main thread will apply the
        # same change to the real BlackjackGame on the next frame.
        if server_game_state is not None:
            seats = server_game_state.setdefault("seat_types", ["empty" for _ in range(MAX_PLAYERS)])
            seats_alias = server_game_state.setdefault("seats", seats.copy())
            money = server_game_state.setdefault("money", [0 for _ in range(MAX_PLAYERS)])
            bets = server_game_state.setdefault("round_bets", [0 for _ in range(MAX_PLAYERS)])
            bets_alias = server_game_state.setdefault("bets", bets.copy())
            if 0 <= seat < MAX_PLAYERS:
                seats[seat] = "human"
                seats_alias[seat] = "human"
                money[seat] = STARTING_MONEY
                bets[seat] = 0
                bets_alias[seat] = 0
                server_game_state["message"] = f"Player {seat + 1} joined the table."
                server_game_state["seat_owners"] = [str(owner) if owner is not None else None for owner in server_seat_owners]

        queue_server_command({"type": "JOIN_SEAT", "seat": seat})
        server_send_json(conn, {"type": "SEAT_ASSIGNED", "seat": seat, "message": f"You control Seat {seat + 1}."})
        server_broadcast_state()
        return

    if command_type == "LEAVE_SEAT":
        seat = get_server_owned_seat(conn)
        if seat is None:
            server_send_json(conn, {"type": "ERROR", "message": "You do not own a seat."})
            return
        if server_game_state is not None and server_game_state.get("round_active"):
            server_send_json(conn, {"type": "ERROR", "message": "You cannot leave during an active hand."})
            return

        server_seat_owners[seat] = None
        server_client_seats[client_id] = None

        if server_game_state is not None:
            seats = server_game_state.setdefault("seat_types", ["empty" for _ in range(MAX_PLAYERS)])
            seats_alias = server_game_state.setdefault("seats", seats.copy())
            money = server_game_state.setdefault("money", [0 for _ in range(MAX_PLAYERS)])
            bets = server_game_state.setdefault("round_bets", [0 for _ in range(MAX_PLAYERS)])
            bets_alias = server_game_state.setdefault("bets", bets.copy())
            amount = money[seat] if 0 <= seat < len(money) else 0
            if 0 <= seat < MAX_PLAYERS:
                seats[seat] = "empty"
                seats_alias[seat] = "empty"
                money[seat] = 0
                bets[seat] = 0
                bets_alias[seat] = 0
                server_game_state["message"] = f"Player {seat + 1} left with ${amount}."
                server_game_state["seat_owners"] = [str(owner) if owner is not None else None for owner in server_seat_owners]

        queue_server_command({"type": "LEAVE_SEAT", "seat": seat})
        server_send_json(conn, {"type": "SEAT_RELEASED", "seat": seat, "message": f"You left Seat {seat + 1}."})
        server_broadcast_state()
        return

    if command_type == "BET":
        seat = get_server_owned_seat(conn)
        amount = command.get("amount")
        if seat is None:
            server_send_json(conn, {"type": "ERROR", "message": "You do not own a seat."})
            return
        if server_game_state is not None:
            if server_game_state.get("mode") != "betting" or server_game_state.get("betting_player") != seat:
                server_send_json(conn, {"type": "ERROR", "message": "It is not your betting turn."})
                return
            money = server_game_state.get("money", [0] * MAX_PLAYERS)
            if not isinstance(amount, int) or amount <= 0 or amount > money[seat]:
                server_send_json(conn, {"type": "ERROR", "message": "Invalid bet."})
                return
        queue_server_command({"type": "BET", "seat": seat, "amount": amount})
        return

    if command_type == "SKIP_BET":
        seat = get_server_owned_seat(conn)
        if seat is None:
            server_send_json(conn, {"type": "ERROR", "message": "You do not own a seat."})
            return
        if server_game_state is not None and (server_game_state.get("mode") != "betting" or server_game_state.get("betting_player") != seat):
            server_send_json(conn, {"type": "ERROR", "message": "It is not your betting turn."})
            return
        queue_server_command({"type": "SKIP_BET", "seat": seat})
        return

    if command_type in ["HIT", "STAND", "DOUBLE", "SPLIT"]:
        seat = get_server_owned_seat(conn)
        if seat is None:
            server_send_json(conn, {"type": "ERROR", "message": "You do not own a seat."})
            return
        if server_game_state is not None:
            if server_game_state.get("current_player") != seat or not server_game_state.get("round_active"):
                server_send_json(conn, {"type": "ERROR", "message": "It is not your turn."})
                return
        queue_server_command({"type": command_type, "seat": seat})
        return

    server_send_json(conn, {"type": "ERROR", "message": f"Unknown command: {command_type}"})


def server_client_thread(conn, addr):
    global server_clients, server_next_client_id
    with server_clients_lock:
        server_clients.append(conn)
        client_id = server_next_client_id
        server_next_client_id += 1
        server_client_ids[conn] = client_id
        server_client_seats[client_id] = None

    buffer = ""
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break

            buffer += data.decode("utf-8")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    command = json.loads(line)
                    server_handle_command(conn, command)
                except Exception as exc:
                    server_send_json(conn, {"type": "ERROR", "message": str(exc)})
    finally:
        client_id = server_client_ids.get(conn)
        if client_id is not None:
            seat = server_client_seats.get(client_id)
            if seat is not None and 0 <= seat < MAX_PLAYERS:
                server_seat_owners[seat] = None
                queue_server_command({"type": "DISCONNECT_SEAT", "seat": seat})
            server_client_seats.pop(client_id, None)
            server_client_ids.pop(conn, None)

        with server_clients_lock:
            try:
                server_clients.remove(conn)
            except ValueError:
                pass
        try:
            conn.close()
        except Exception:
            pass


def server_accept_loop():
    global server_running, server_socket
    try:
        while not server_stop_event.is_set():
            try:
                conn, addr = server_socket.accept()
            except socket.timeout:
                continue
            threading.Thread(target=server_client_thread, args=(conn, addr), daemon=True).start()
    finally:
        server_running = False
        try:
            if server_socket:
                server_socket.close()
        except Exception:
            pass


def start_local_server(room_code=None, port=5050):
    global server_running, server_room_code, server_port, server_clients, server_game_state, server_socket
    global server_client_ids, server_client_seats, server_seat_owners, server_next_client_id, my_seat
    global voice_event_counter, server_voice_events, seen_voice_event_ids

    if server_running:
        return False

    # Legacy local hosting path kept unused in online mode.
    # Otherwise clients mirror mode="intro" and their seat-number keys never reach JOIN_SEAT.
    if game.mode == "intro":
        game.mode = "game"
        game.message = "Online room starting. Press 1-5 to claim a seat."
        try:
            music_player.stop()
        except Exception:
            pass

    if room_code is None or not room_code.strip():
        room_code = str(random.randint(1000, 9999))

    server_room_code = room_code.strip()
    server_port = port
    server_clients = []
    server_client_ids = {}
    server_client_seats = {}
    server_seat_owners = [None for _ in range(MAX_PLAYERS)]
    server_next_client_id = 1
    my_seat = None
    server_game_state = serialize_game_state_for_network(game)
    server_game_state["room_code"] = server_room_code
    server_game_state["host_ip"] = get_local_ip()
    server_game_state["port"] = server_port
    server_game_state["seat_owners"] = [None for _ in range(MAX_PLAYERS)]
    server_pending_commands.clear()

    try:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(("0.0.0.0", server_port))
        server_socket.listen()
        server_socket.settimeout(1.0)
        server_stop_event.clear()
        threading.Thread(target=server_accept_loop, daemon=True).start()
    except Exception:
        try:
            if server_socket:
                server_socket.close()
        except Exception:
            pass
        server_socket = None
        return False

    server_running = True
    return True


def stop_local_server():
    global server_running
    try:
        server_broadcast({"type": "SERVER_CLOSED"})
    except Exception:
        pass
    server_stop_event.set()
    server_running = False
    with server_clients_lock:
        for conn in list(server_clients):
            try:
                conn.close()
            except Exception:
                pass
        server_clients.clear()


def draw_connect_overlay():
    if not connect_overlay_active:
        return

    overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 170))
    SCREEN.blit(overlay, (0, 0))

    box = pygame.Rect(180, 185, 840, 390)
    pygame.draw.rect(SCREEN, (245, 245, 248), box, border_radius=18)
    pygame.draw.rect(SCREEN, BLACK, box, 3, border_radius=18)

    creating = connect_overlay_mode == "create"
    title_text = "Create Online Room" if creating else "Join Online Room"
    hint_text = "ENTER creates a room on the built-in server. ESC cancels." if creating else "Type room code. ENTER joins. ESC cancels."

    title = BIG_FONT.render(title_text, True, BLACK)
    SCREEN.blit(title, (box.centerx - title.get_width() // 2, box.y + 22))

    hint = SMALL_FONT.render(hint_text, True, DARK_GRAY)
    SCREEN.blit(hint, (box.centerx - hint.get_width() // 2, box.y + 78))

    configured = DEFAULT_ONLINE_SERVER_URL.strip() and "YOUR-DEPLOYED-SERVER-HERE" not in DEFAULT_ONLINE_SERVER_URL
    server_status = "Online server: configured inside the app" if configured else "Online server is NOT configured yet. Set DEFAULT_ONLINE_SERVER_URL in the code."
    server_color = DARK_GREEN if configured else RED
    server_surface = SMALL_FONT.render(server_status, True, server_color)
    SCREEN.blit(server_surface, (box.x + 40, box.y + 125))

    if creating:
        info_lines = [
            "Press ENTER to connect to the hard-coded online server and create a room.",
            "The app will show a room code like JASLI-4821.",
            "Send that room code to the other player.",
        ]
        y = box.y + 180
        for line in info_lines:
            surf = SMALL_FONT.render(line, True, BLACK)
            SCREEN.blit(surf, (box.x + 45, y))
            y += 34
    else:
        room_label = SMALL_FONT.render("Room code:", True, BLACK)
        room_rect = pygame.Rect(box.x + 40, box.y + 180, 760, 48)
        pygame.draw.rect(SCREEN, WHITE, room_rect, border_radius=10)
        pygame.draw.rect(SCREEN, BLUE, room_rect, 2, border_radius=10)
        SCREEN.blit(room_label, (room_rect.x + 8, room_rect.y - 24))

        room_text = connect_room_text.upper()
        if connect_cursor_visible:
            room_text += "|"
        room_surface = SMALL_FONT.render(room_text, True, BLACK)
        SCREEN.blit(room_surface, (room_rect.x + 12, room_rect.y + 12))

    privacy_lines = [
        "Players do not type the server URL. It is built into the executable.",
        "Both players connect to the same online WebSocket server. One creates a room; the other joins the room code.",
        "Do not use this for real-money gambling.",
    ]
    y = box.y + 285
    for line in privacy_lines:
        surf = TINY_FONT.render(line, True, DARK_GRAY)
        SCREEN.blit(surf, (box.x + 45, y))
        y += 20

    status_color = RED if "failed" in connect_status_message.lower() or "error" in connect_status_message.lower() or "not configured" in connect_status_message.lower() else BLUE
    status_surface = SMALL_FONT.render(connect_status_message, True, status_color)
    SCREEN.blit(status_surface, (box.x + 40, box.y + 352))

def handle_connect_overlay_event(event):
    global connect_room_text, connect_input_field, connect_status_message
    global connect_overlay_active, app_screen, play_mode

    creating = connect_overlay_mode == "create"

    if event.key == pygame.K_ESCAPE:
        connect_overlay_active = False
        if app_screen != "playing":
            app_screen = "multiplayer_menu"
        return

    # URL is hard-coded, so TAB does nothing now.
    if event.key == pygame.K_TAB:
        return

    if event.key == pygame.K_RETURN:
        server_url = DEFAULT_ONLINE_SERVER_URL.strip()

        if not server_url or "YOUR-DEPLOYED-SERVER-HERE" in server_url:
            connect_status_message = "Online server not configured. Set DEFAULT_ONLINE_SERVER_URL in the code first."
            return

        if creating:
            start_online_host_mode(server_url)
            if is_online_ws_client():
                connect_status_message = "Creating online room..."
                connect_overlay_active = False
                app_screen = "playing"
                play_mode = "online_host"
                try:
                    music_player.stop()
                except Exception:
                    pass
            else:
                connect_status_message = f"Connection failed: {online_last_error}"
            return

        room_code = connect_room_text.strip().upper()
        if not room_code:
            connect_status_message = "Enter the room code."
            return

        if start_online_guest_mode(server_url, room_code):
            connect_status_message = "Connected. Press 1-5 to claim a seat."
            game.mode = "game"
            game.message = connect_status_message
            connect_overlay_active = False
            app_screen = "playing"
            play_mode = "online_guest"
            try:
                music_player.stop()
            except Exception:
                pass
        else:
            connect_status_message = f"Connection failed: {online_last_error}"
        return

    if event.key == pygame.K_BACKSPACE:
        if not creating:
            connect_room_text = connect_room_text[:-1]
        return

    # Allow Ctrl+V paste into the room-code box.
    if event.key == pygame.K_v and (pygame.key.get_mods() & pygame.KMOD_CTRL):
        if not creating:
            try:
                pasted = pygame.scrap.get(pygame.SCRAP_TEXT)
                if pasted:
                    connect_room_text += pasted.decode("utf-8", errors="ignore").strip().upper()
                    connect_room_text = connect_room_text[:16]
            except Exception:
                pass
        return

    if event.unicode and event.unicode.isprintable():
        ch = event.unicode
        if not creating and len(connect_room_text) < 16:
            connect_room_text += ch.upper()
        return



# =========================
# DISABLE OLD DIRECT-LAPTOP HOSTING PATH
# =========================
# This executable build uses the WebSocket server path for multiplayer.
# The old direct laptop-to-laptop socket path is forcibly disabled so the UI
# cannot fall back to same-network hosting.
def is_disabled_legacy_remote_client():
    return False

def is_disabled_legacy_host():
    return False

def is_any_network_mode():
    return is_online_ws_client()

def is_multiplayer_active():
    return is_online_ws_client()

def update_disabled_legacy_socket_messages():
    return

# =========================
# WINDOWS VOICE SUPPORT
# =========================

VOICE_ENABLED = True
voice_queue = queue.Queue()


def clean_voice_text(text):
    text = str(text)
    text = text.replace("$", " dollars ")
    text = text.replace("£", " pounds ")
    text = text.replace("Blackjack", "black jack")
    text = re.sub(r"[^a-zA-Z0-9 .,!?'-]", "", text)
    text = text.replace("'", "''")
    return text


def clear_voice_queue():
    while not voice_queue.empty():
        try:
            voice_queue.get_nowait()
            voice_queue.task_done()
        except queue.Empty:
            break


def voice_worker():
    # Try to use COM SAPI for speech (faster, no external process/window). Fall back to PowerShell.
    speaker = None
    use_com = False
    try:
        import win32com.client
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        use_com = True
    except Exception:
        use_com = False

    while True:
        text = voice_queue.get()

        if text is None:
            break

        text = clean_voice_text(text)

        try:
            if use_com and speaker is not None:
                try:
                    speaker.Speak(text)
                except Exception:
                    use_com = False

            if not use_com:
                command = (
                    "Add-Type -AssemblyName System.Speech; "
                    "$speak = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                    "$speak.Rate = 1; "
                    f"$speak.Speak('{text}');"
                )

                try:
                    si = subprocess.STARTUPINFO()
                    if hasattr(subprocess, 'STARTF_USESHOWWINDOW'):
                        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                        si.wShowWindow = 0
                except Exception:
                    si = None

                creationflags = 0
                if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
                    creationflags = subprocess.CREATE_NO_WINDOW

                try:
                    subprocess.run(
                        ["powershell", "-NoProfile", "-Command", command],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        startupinfo=si,
                        creationflags=creationflags,
                    )
                except Exception:
                    pass
        except Exception:
            pass

        voice_queue.task_done()


voice_thread = None
try:
    voice_thread = threading.Thread(target=voice_worker, daemon=True)
    voice_thread.start()
except Exception:
    # If thread creation fails, disable voice to avoid crashing the game
    VOICE_ENABLED = False
    voice_thread = None


def speak(text):
    # Speak locally first.
    if VOICE_ENABLED:
        voice_queue.put(text)

    # Legacy local hosting voice replication path.
    # v7 used only a one-off VOICE packet; v8 also stores voice_events in the
    # normal STATE stream, which we already know reaches guest laptops.
    try:
        if server_running:
            event = remember_host_voice_event(text)
            server_broadcast({"type": "VOICE", "id": event["id"], "text": event["text"]})
    except Exception:
        pass


def quit_game():
    # attempt to shut down any running legacy local server or network client
    try:
        if server_running:
            stop_local_server()
    except Exception:
        pass

    try:
        if network is not None:
            network.close()
    except Exception:
        pass

    try:
        if online_ws_client is not None:
            online_ws_client.close()
    except Exception:
        pass

    # attempt a graceful shutdown of the voice worker
    try:
        clear_voice_queue()
        if VOICE_ENABLED and voice_queue is not None:
            # send sentinel
            try:
                voice_queue.put_nowait(None)
            except Exception:
                try:
                    voice_queue.put(None)
                except Exception:
                    pass

        # give the worker a short time to finish
        if voice_thread is not None and voice_thread.is_alive():
            voice_thread.join(timeout=0.5)
    except Exception:
        pass

    try:
        pygame.quit()
    except Exception:
        pass

    try:
        sys.exit()
    except SystemExit:
        raise
    except Exception:
        os._exit(0)


# =========================
# PYGAME SETUP
# =========================

pygame.mixer.pre_init(44100, -16, 1, 512)
pygame.init()
try:
    pygame.scrap.init()
except Exception:
    pass

WIDTH, HEIGHT = 1200, 760
FPS = 60

DISPLAY_SURFACE = pygame.display.set_mode((WIDTH, HEIGHT))
SCREEN = DISPLAY_SURFACE
pygame.display.set_caption("JASLI's Casino - Blackjack")
CLOCK = pygame.time.Clock()

# Fullscreen support: toggle with F11. Set to True to start fullscreen.
FULLSCREEN_ON_START = True
is_fullscreen = False
fullscreen_display_rect = pygame.Rect(0, 0, WIDTH, HEIGHT)


def get_logical_mouse_pos(actual_pos):
    if is_fullscreen and SCREEN is not DISPLAY_SURFACE:
        if not fullscreen_display_rect.collidepoint(actual_pos):
            return (-1, -1)

        x = (actual_pos[0] - fullscreen_display_rect.x) * WIDTH / fullscreen_display_rect.width
        y = (actual_pos[1] - fullscreen_display_rect.y) * HEIGHT / fullscreen_display_rect.height
        return (
            int(max(0, min(WIDTH, x))),
            int(max(0, min(HEIGHT, y)))
        )
    return actual_pos


def get_mouse_pos():
    return get_logical_mouse_pos(pygame.mouse.get_pos())


def scale_fullscreen_surface():
    if not is_fullscreen or SCREEN is DISPLAY_SURFACE:
        return

    scaled = pygame.transform.smoothscale(
        SCREEN,
        (fullscreen_display_rect.width, fullscreen_display_rect.height)
    )
    DISPLAY_SURFACE.fill((0, 0, 0))
    DISPLAY_SURFACE.blit(scaled, fullscreen_display_rect)


def set_screen_mode(fullscreen: bool):
    global SCREEN, DISPLAY_SURFACE, is_fullscreen, fullscreen_display_rect
    is_fullscreen = fullscreen

    if fullscreen:
        desktop_sizes = []
        if hasattr(pygame.display, "get_desktop_sizes"):
            desktop_sizes = pygame.display.get_desktop_sizes()

        if desktop_sizes:
            desktop_width, desktop_height = desktop_sizes[0]
        else:
            info = pygame.display.Info()
            desktop_width, desktop_height = info.current_w, info.current_h

        try:
            DISPLAY_SURFACE = pygame.display.set_mode((desktop_width, desktop_height), pygame.FULLSCREEN)
            SCREEN = pygame.Surface((WIDTH, HEIGHT))

            scale = min(desktop_width / WIDTH, desktop_height / HEIGHT)
            scaled_w = max(1, int(WIDTH * scale))
            scaled_h = max(1, int(HEIGHT * scale))
            fullscreen_display_rect = pygame.Rect(
                (desktop_width - scaled_w) // 2,
                (desktop_height - scaled_h) // 2,
                scaled_w,
                scaled_h,
            )
        except pygame.error:
            try:
                DISPLAY_SURFACE = pygame.display.set_mode((WIDTH, HEIGHT), pygame.FULLSCREEN | pygame.SCALED)
                SCREEN = DISPLAY_SURFACE
                fullscreen_display_rect = pygame.Rect(0, 0, WIDTH, HEIGHT)
            except pygame.error:
                DISPLAY_SURFACE = pygame.display.set_mode((WIDTH, HEIGHT))
                SCREEN = DISPLAY_SURFACE
                is_fullscreen = False
                fullscreen_display_rect = pygame.Rect(0, 0, WIDTH, HEIGHT)
    else:
        DISPLAY_SURFACE = pygame.display.set_mode((WIDTH, HEIGHT))
        SCREEN = DISPLAY_SURFACE
        fullscreen_display_rect = pygame.Rect(0, 0, WIDTH, HEIGHT)

    pygame.display.set_caption("JASLI's Casino - Blackjack")

# Apply initial mode
set_screen_mode(FULLSCREEN_ON_START)

# =========================
# MUSIC SYSTEM - INTRO ONLY
# =========================

MUSIC_ENABLED = True
SAMPLE_RATE = 44100

NOTE_FREQ = {
    "C4": 261.63,
    "D4": 293.66,
    "E4": 329.63,
    "G4": 392.00,
    "C5": 523.25,
    "D5": 587.33,
    "E5": 659.25,
    "G5": 783.99,
    "REST": 0.0,
}


class MelodyPlayer:
    def __init__(self):
        self.enabled = MUSIC_ENABLED
        self.cache = {}
        self.melody = []
        self.index = 0
        self.next_note_time = 0
        self.loop = False
        self.volume = 0.25
        self.started = False

        try:
            pygame.mixer.set_num_channels(16)
            self.channel = pygame.mixer.Channel(7)
        except Exception:
            self.enabled = False
            self.channel = None

    def make_tone(self, frequency, duration, volume):
        key = (frequency, duration, volume)

        if key in self.cache:
            return self.cache[key]

        sample_count = int(SAMPLE_RATE * duration)
        samples = array.array("h")

        if frequency <= 0:
            for _ in range(sample_count):
                samples.append(0)
        else:
            for i in range(sample_count):
                t = i / SAMPLE_RATE

                wave = (
                    math.sin(2 * math.pi * frequency * t)
                    + 0.35 * math.sin(2 * math.pi * frequency * 2 * t)
                    + 0.12 * math.sin(2 * math.pi * frequency * 3 * t)
                )

                attack = min(1.0, i / max(1, int(0.025 * SAMPLE_RATE)))
                release = min(1.0, (sample_count - i) / max(1, int(0.06 * SAMPLE_RATE)))
                envelope = min(attack, release)

                value = int(32767 * volume * envelope * wave * 0.6)
                samples.append(value)

        sound = pygame.mixer.Sound(buffer=samples.tobytes())
        self.cache[key] = sound
        return sound

    def play_melody(self, melody, loop=False, volume=0.25):
        if not self.enabled:
            return

        self.melody = melody
        self.loop = loop
        self.volume = volume
        self.index = 0
        self.next_note_time = 0
        self.started = True

    def update(self):
        if not self.enabled or not self.started or not self.melody:
            return

        now = time.time()

        if now < self.next_note_time:
            return

        note_name, duration = self.melody[self.index]
        frequency = NOTE_FREQ.get(note_name, 0.0)

        if frequency > 0:
            sound = self.make_tone(frequency, duration, self.volume)
            self.channel.play(sound)

        self.next_note_time = now + duration * 0.92
        self.index += 1

        if self.index >= len(self.melody):
            if self.loop:
                self.index = 0
            else:
                self.started = False

    def stop(self):
        if self.channel:
            self.channel.stop()
        self.started = False


INTRO_MELODY = [
    ("C4", 0.18), ("E4", 0.18), ("G4", 0.18), ("C5", 0.32),
    ("REST", 0.05),
    ("G4", 0.15), ("C5", 0.15), ("E5", 0.18), ("G5", 0.45),
    ("REST", 0.08),
    ("E5", 0.16), ("D5", 0.16), ("C5", 0.35),
    ("G4", 0.18), ("C5", 0.55),
]

music_player = MelodyPlayer()

# =========================
# ANNOUNCER QUOTES
# =========================

BET_CONFIRMATION_QUOTES = [
    "Bets confirmed. Good luck.",
    "Bets are in. Let's begin.",
    "All bets are down. Let's play.",
    "Cards are ready. Good luck.",
    "Bets locked in. Here we go.",
    "The table is set. Good luck.",
    "All wagers are in. Let's begin.",
    "Bets accepted. May the cards be kind.",
    "Bets are down. Time to play.",
    "Everyone is in. Let's deal.",
]

BOT_NAMES = [
    "Bot Bruno",
    "Bot Clara",
    "Bot Max",
    "Bot Ruby",
    "Bot Victor",
]

# =========================
# COLOURS
# =========================

BACKGROUND = (18, 70, 45)
DARK_GREEN = (8, 60, 34)
LIGHT_GREEN = (35, 145, 80)
WOOD = (120, 72, 32)
WHITE = (245, 245, 245)
BLACK = (20, 20, 20)
RED = (210, 50, 50)
DARK_RED = (120, 20, 20)
GOLD = (235, 190, 70)
GRAY = (180, 180, 180)
DARK_GRAY = (80, 80, 80)
BLUE = (70, 130, 220)
PURPLE = (130, 80, 190)
ORANGE = (220, 130, 50)
CREAM = (245, 238, 210)

SKIN = (221, 172, 126)
SKIN_DARK = (185, 132, 92)
HAIR = (45, 30, 22)
SUIT_BLACK = (18, 18, 25)
SHIRT_WHITE = (238, 238, 235)
BOWTIE = (120, 15, 25)

# =========================
# FONTS
# =========================

FONT = pygame.font.SysFont("arial", 23)
SMALL_FONT = pygame.font.SysFont("arial", 17)
TINY_FONT = pygame.font.SysFont("arial", 14)
BIG_FONT = pygame.font.SysFont("arial", 34, bold=True)
TITLE_FONT = pygame.font.SysFont("arial", 50, bold=True)
WELCOME_FONT = pygame.font.SysFont("arial", 64, bold=True)
CARD_FONT = pygame.font.SysFont("arial", 30, bold=True)
SUIT_FONT = pygame.font.SysFont("arial", 26)

# =========================
# CARD / GAME CONFIG
# =========================

CARD_WIDTH = 72
CARD_HEIGHT = 104

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

DEAL_ANIMATION_SPEED = 0.035

STARTING_MONEY = 50000
MIN_BET = 100
MAX_PLAYERS = 5

BOT_BET_DELAY = 0.8
BOT_ACTION_DELAY = 0.9

INTRO_DURATION = 3.0

# =========================
# TABLE CONFIG
# =========================

TABLE_CENTER_X = 465
TABLE_TOP_Y = 155
TABLE_RADIUS_X = 385
TABLE_RADIUS_Y = 455

DECK_POS = (795, 105)

DEALER_X = TABLE_CENTER_X
DEALER_Y = 74


# =========================
# HELPERS
# =========================

def generate_bet_options(max_money):
    options = []

    for value in range(100, 1001, 100):
        options.append(value)

    for value in range(1000, 10001, 1000):
        options.append(value)

    value = 15000
    while value <= max_money:
        options.append(value)
        value += 5000

    options = sorted(set(options))
    options = [x for x in options if x <= max_money]

    if not options and max_money >= 100:
        options = [100]

    return options


def wrap_text(text, font, max_width):
    words = text.split()
    lines = []
    current = ""

    for word in words:
        test = word if current == "" else current + " " + word

        if font.size(test)[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def build_bottom_semicircle_points(cx, top_y, rx, ry, steps=80):
    points = []

    for i in range(steps + 1):
        theta = math.pi - (math.pi * i / steps)
        x = cx + rx * math.cos(theta)
        y = top_y + ry * math.sin(theta)
        points.append((int(x), int(y)))

    return points


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def lerp(a, b, t):
    return a + (b - a) * t


# =========================
# BUTTON CLASS
# =========================

class Button:
    def __init__(self, x, y, w, h, text, colour, text_colour=WHITE):
        self.rect = pygame.Rect(x, y, w, h)
        self.text = text
        self.colour = colour
        self.text_colour = text_colour

    def draw(self, enabled=True):
        mouse_pos = get_mouse_pos()
        hover = self.rect.collidepoint(mouse_pos)

        if not enabled:
            colour = (140, 140, 140)
        elif hover:
            colour = tuple(min(255, c + 25) for c in self.colour)
        else:
            colour = self.colour

        pygame.draw.rect(SCREEN, colour, self.rect, border_radius=10)
        pygame.draw.rect(SCREEN, BLACK, self.rect, 2, border_radius=10)

        text_surface = SMALL_FONT.render(self.text, True, self.text_colour)
        SCREEN.blit(
            text_surface,
            (
                self.rect.centerx - text_surface.get_width() // 2,
                self.rect.centery - text_surface.get_height() // 2,
            )
        )

    def clicked(self, event):
        return (
            event.type == pygame.MOUSEBUTTONDOWN
            and event.button == 1
            and self.rect.collidepoint(get_logical_mouse_pos(event.pos))
        )


# =========================
# HAND CLASS
# =========================

class BlackjackHand:
    def __init__(self, bet):
        self.cards = []
        self.bet = bet
        self.finished = False
        self.busted = False
        self.doubled = False
        self.from_split = False
        self.result = ""
        self.settled = False


def serialize_hand_for_network(hand):
    return {
        "cards": [list(card) for card in hand.cards],
        "bet": hand.bet,
        "finished": hand.finished,
        "busted": hand.busted,
        "doubled": hand.doubled,
        "from_split": hand.from_split,
        "result": hand.result,
        "settled": hand.settled,
        "pending_action": getattr(hand, "pending_action", None),
    }


def deserialize_hand_from_network(data):
    hand = BlackjackHand(data.get("bet", 0))
    hand.cards = [tuple(card) for card in data.get("cards", [])]
    hand.finished = data.get("finished", False)
    hand.busted = data.get("busted", False)
    hand.doubled = data.get("doubled", False)
    hand.from_split = data.get("from_split", False)
    hand.result = data.get("result", "")
    hand.settled = data.get("settled", False)
    pending = data.get("pending_action")
    if pending:
        hand.pending_action = pending
    return hand


def serialize_game_state_for_network(source_game):
    return {
        "mode": source_game.mode,
        "seat_types": source_game.seat_types.copy(),
        "seats": source_game.seat_types.copy(),
        "money": source_game.money.copy(),
        "round_bets": source_game.round_bets.copy(),
        "bets": source_game.round_bets.copy(),
        "round_start_money": source_game.round_start_money.copy(),
        "betting_player": source_game.betting_player,
        "current_bet_options": source_game.current_bet_options.copy(),
        "current_bet_index": source_game.current_bet_index,
        "dealer_hand": [
            {"card": list(card_data["card"]), "hidden": card_data["hidden"]}
            for card_data in source_game.dealer_hand
        ],
        "player_hands": [
            [serialize_hand_for_network(hand) for hand in hands]
            for hands in source_game.player_hands
        ],
        "current_player": source_game.current_player,
        "current_hand": source_game.current_hand,
        "message": source_game.message,
        "dealer_revealed": source_game.dealer_revealed,
        "round_active": source_game.round_active,
        "round_over": source_game.round_over,
        "auto_betting_time": None,
    }


def apply_network_state_to_game(state):
    """Remote clients mirror the host game state for drawing and interaction."""
    old_mode = game.mode
    old_betting_player = game.betting_player

    incoming_mode = state.get("mode", game.mode)
    # A connected client should always remain interactive. If the host accidentally
    # broadcasts intro mode, keep the client on the table so 1-5 can claim seats.
    if is_disabled_legacy_remote_client() and incoming_mode == "intro":
        incoming_mode = "game"

    game.mode = incoming_mode
    game.seat_types = state.get("seat_types", state.get("seats", game.seat_types)).copy()
    game.money = state.get("money", game.money).copy()
    game.round_bets = state.get("round_bets", state.get("bets", game.round_bets)).copy()
    game.round_start_money = state.get("round_start_money", game.round_start_money).copy()
    game.betting_player = state.get("betting_player", game.betting_player)
    game.current_player = state.get("current_player", game.current_player)
    game.current_hand = state.get("current_hand", game.current_hand)
    game.message = state.get("message", game.message)
    game.dealer_revealed = state.get("dealer_revealed", game.dealer_revealed)
    game.round_active = state.get("round_active", game.round_active)
    game.round_over = state.get("round_over", game.round_over)

    reconcile_my_seat_from_owner_list(state)

    game.dealer_hand = [
        {"card": tuple(card_data.get("card", ("A", "♠"))), "hidden": card_data.get("hidden", False)}
        for card_data in state.get("dealer_hand", [])
    ]

    raw_player_hands = state.get("player_hands")
    if raw_player_hands is not None:
        game.player_hands = [
            [deserialize_hand_from_network(hand_data) for hand_data in hands]
            for hands in raw_player_hands
        ]
        while len(game.player_hands) < MAX_PLAYERS:
            game.player_hands.append([])

    # Clients should not locally run deal animations from the host. They draw the latest authoritative state.
    if is_disabled_legacy_remote_client():
        game.animations = []
        game.deal_queue = []
        game.processing_deal_queue = False
        game.deal_phase = None
        game.auto_betting_time = None

    # Build betting options locally only when betting player/mode changes, so the user's selected bet is not reset every frame.
    if game.mode == "betting" and game.betting_player is not None:
        if old_mode != "betting" or old_betting_player != game.betting_player or not game.current_bet_options:
            if 0 <= game.betting_player < MAX_PLAYERS:
                game.current_bet_options = generate_bet_options(game.money[game.betting_player])
                game.current_bet_index = 0
    else:
        game.current_bet_options = []
        game.current_bet_index = 0


# =========================
# GAME CLASS
# =========================

class BlackjackGame:
    def __init__(self):
        self.mode = "intro"
        self.intro_start_time = time.time()
        self.intro_music_started = False
        self.intro_melody_end_time = None

        self.seat_types = ["empty" for _ in range(MAX_PLAYERS)]
        self.money = [0 for _ in range(MAX_PLAYERS)]

        self.round_bets = [0 for _ in range(MAX_PLAYERS)]
        self.round_start_money = [0 for _ in range(MAX_PLAYERS)]

        self.betting_player = 0
        self.current_bet_options = []
        self.current_bet_index = 0

        self.bet_hold_direction = 0
        self.bet_hold_started_at = 0
        self.bet_hold_last_tick = 0

        self.bot_bet_start_time = None
        self.bot_action_start_time = None

        self.deck = []
        self.player_hands = [[] for _ in range(MAX_PLAYERS)]
        self.dealer_hand = []

        self.current_player = 0
        self.current_hand = 0

        self.message = "Press 1-5 to buy into a seat. Press B to add a bot."
        self.dealer_revealed = False
        self.round_active = False
        self.round_over = True

        self.animations = []
        self.deal_queue = []
        self.processing_deal_queue = False
        self.deal_phase = None

        self.auto_betting_time = None

        self.dealer_hand_target = (DEALER_X, DEALER_Y + 88)

        self.buttons = {
            # Main action buttons are kept close to the ACTIONS title.
            "hit": Button(955, 125, 180, 42, "HIT (H)", BLUE),
            "stand": Button(955, 175, 180, 42, "STAND (S)", DARK_RED),
            "double": Button(955, 225, 180, 42, "DOUBLE (D)", ORANGE),
            "split": Button(955, 275, 180, 42, "SPLIT (P)", PURPLE),

            # Utility/network buttons stay lower so the play actions feel grouped.
            "reset": Button(955, 390, 180, 42, "RESET $ (R)", DARK_GRAY),
            "quit": Button(955, 445, 180, 42, "QUIT GAME (ESC)", RED),
            "host": Button(955, 500, 180, 42, "ONLINE ROOM", GOLD),
        }

    # =========================
    # INTRO
    # =========================

    def update_intro(self):
        if self.mode != "intro":
            return

        # Skip intro entirely in online mode
        if online_mode:
            self.mode = "game"
            self.message = "Connected to server. Waiting for your turn..."
            music_player.stop()
            return

        if not self.intro_music_started:
            self.intro_music_started = True
            music_player.play_melody(INTRO_MELODY, loop=False, volume=0.33)
            self.intro_melody_end_time = None

        # If music is enabled, hide the intro one second after the melody finishes.
        if MUSIC_ENABLED:
            if not music_player.started and self.intro_melody_end_time is None:
                # schedule disappearance one second after music stops
                self.intro_melody_end_time = time.time() + 1.0

            if self.intro_melody_end_time is not None and time.time() >= self.intro_melody_end_time:
                self.mode = "game"
                self.message = "Welcome. Press 1-5 to buy into a seat. Press B to add a bot."
                music_player.stop()
        else:
            # fallback to fixed intro duration when music is disabled
            if time.time() - self.intro_start_time >= INTRO_DURATION:
                self.mode = "game"
                self.message = "Welcome. Press 1-5 to buy into a seat. Press B to add a bot."
                music_player.stop()

    # =========================
    # SEAT / PLAYER HELPERS
    # =========================

    def seat_is_empty(self, index):
        return self.seat_types[index] == "empty"

    def seat_is_human(self, index):
        return self.seat_types[index] == "human"

    def seat_is_bot(self, index):
        return self.seat_types[index] == "bot"

    def active_players(self):
        return [i for i in range(MAX_PLAYERS) if self.seat_types[i] != "empty"]

    def round_participants(self):
        return [
            i for i in range(MAX_PLAYERS)
            if self.seat_types[i] != "empty" and self.round_bets[i] > 0
        ]

    def any_active_players(self):
        return len(self.active_players()) > 0

    def player_display_name(self, index):
        if self.seat_types[index] == "bot":
            return BOT_NAMES[index]
        if self.seat_types[index] == "human":
            return f"Player {index + 1}"
        return f"Seat {index + 1}"

    # =========================
    # DECK / SCORING
    # =========================

    def create_deck(self):
        deck = []

        for suit in SUITS:
            for rank in RANKS:
                deck.append((rank, suit))

        random.shuffle(deck)
        return deck

    def draw_card_from_deck(self):
        if len(self.deck) == 0:
            self.deck = self.create_deck()

        return self.deck.pop()

    def hand_value(self, cards):
        total = 0
        aces = 0

        for rank, suit in cards:
            if rank in ["J", "Q", "K"]:
                total += 10
            elif rank == "A":
                total += 11
                aces += 1
            else:
                total += int(rank)

        while total > 21 and aces > 0:
            total -= 10
            aces -= 1

        return total

    def is_blackjack(self, cards):
        return len(cards) == 2 and self.hand_value(cards) == 21

    def card_split_value(self, card):
        rank, suit = card

        if rank in ["J", "Q", "K"]:
            return 10

        if rank == "A":
            return 11

        return int(rank)

    def dealer_visible_value(self):
        if not self.dealer_hand:
            return 10

        visible_cards = [c["card"] for c in self.dealer_hand if not c["hidden"]]

        if not visible_cards:
            return 10

        rank, suit = visible_cards[0]

        if rank in ["J", "Q", "K"]:
            return 10
        if rank == "A":
            return 11

        return int(rank)

    # =========================
    # ADD / REMOVE HUMANS / BOTS
    # =========================

    def can_add_or_remove_player_now(self):
        if self.mode == "intro":
            return False

        if self.round_active:
            return False

        if self.animations or self.deal_queue or self.processing_deal_queue:
            return False

        if self.mode not in ["game", "betting"]:
            return False

        return True

    def add_human_by_number(self, number):
        index = number - 1

        if index < 0 or index >= MAX_PLAYERS:
            return

        if not self.can_add_or_remove_player_now():
            self.message = "You can only buy in/remove bots between rounds or during betting."
            return

        if self.seat_is_bot(index):
            name = self.player_display_name(index)

            self.seat_types[index] = "empty"
            self.money[index] = 0
            self.round_bets[index] = 0
            self.player_hands[index] = []

            self.message = f"{name} left Seat {number}."

            if not self.any_active_players():
                self.message = "Everyone left. Press 1-5 to buy in or B to add a bot."
                self.mode = "game"
                return

            if self.mode == "betting":
                self.begin_betting()
            else:
                self.schedule_auto_betting(0.6)

            return

        if self.seat_is_human(index):
            # Cash out the human player when their seat number is pressed
            name = self.player_display_name(index)
            amount = self.money[index]

            self.seat_types[index] = "empty"
            self.money[index] = 0
            self.round_bets[index] = 0
            self.player_hands[index] = []

            self.message = f"{name} cashed out with ${amount}."
            try:
                speak(f"{name} has left with ${amount}")
            except Exception:
                pass

            if not self.any_active_players():
                self.message = "Everyone left. Press 1-5 to buy in or B to add a bot."
                self.mode = "game"
                return

            if self.mode == "betting":
                self.begin_betting()
            else:
                self.schedule_auto_betting(0.6)

            return

        self.seat_types[index] = "human"
        self.money[index] = STARTING_MONEY
        self.round_bets[index] = 0
        self.player_hands[index] = []

        self.message = f"Player {number} bought into Seat {number} with ${STARTING_MONEY}."

        if self.mode == "betting":
            self.begin_betting()
        else:
            self.schedule_auto_betting(0.6)

    def add_bot_to_free_seat(self):
        if not self.can_add_or_remove_player_now():
            self.message = "You can only add bots between rounds or during betting."
            return

        free_seats = [i for i in range(MAX_PLAYERS) if self.seat_is_empty(i)]

        if not free_seats:
            self.message = "No free seats available for a bot."
            return

        index = free_seats[0]

        self.seat_types[index] = "bot"
        self.money[index] = STARTING_MONEY
        self.round_bets[index] = 0
        self.player_hands[index] = []

        self.message = f"{BOT_NAMES[index]} joined Seat {index + 1} with ${STARTING_MONEY}."

        if self.mode == "betting":
            self.begin_betting()
        else:
            self.schedule_auto_betting(0.6)

    def remove_bankrupt_players(self):
        removed_names = []

        for i in range(MAX_PLAYERS):
            if self.seat_types[i] != "empty" and self.money[i] <= 0:
                removed_names.append(self.player_display_name(i))
                self.seat_types[i] = "empty"
                self.money[i] = 0
                self.round_bets[i] = 0
                self.player_hands[i] = []

        if removed_names:
            if len(removed_names) == 1:
                self.message = f"{removed_names[0]} is out of money and left the table."
            else:
                self.message = f"{', '.join(removed_names)} are out of money and left the table."

            if not self.any_active_players():
                self.message += " Press 1-5 to buy in or B to add a bot."
                self.mode = "game"
                self.round_active = False
                self.round_over = True
                self.auto_betting_time = None
                return True

        return False

    # =========================
    # AUTO ROUND FLOW
    # =========================

    def schedule_auto_betting(self, delay_seconds):
        self.auto_betting_time = time.time() + delay_seconds

    def update_auto_flow(self):
        if self.mode != "game":
            return

        if self.round_active:
            return

        if self.animations or self.deal_queue or self.processing_deal_queue:
            return

        if not self.any_active_players():
            return

        if self.auto_betting_time is not None and time.time() >= self.auto_betting_time:
            self.auto_betting_time = None
            self.begin_betting()

    # =========================
    # BETTING
    # =========================

    def begin_betting(self):
        if self.mode not in ["game", "betting"]:
            return

        if self.animations or self.deal_queue:
            return

        if not self.any_active_players():
            self.message = "No active seats. Press 1-5 to buy in or B to add a bot."
            return

        clear_voice_queue()

        self.mode = "betting"
        self.round_active = False
        self.round_over = True
        self.dealer_revealed = False
        self.dealer_hand = []
        self.player_hands = [[] for _ in range(MAX_PLAYERS)]
        self.round_bets = [0 for _ in range(MAX_PLAYERS)]

        active = self.active_players()
        self.betting_player = active[0]
        self.prepare_bet_options_for_current_player()

    def next_betting_player_after(self, current):
        for i in range(current + 1, MAX_PLAYERS):
            if self.seat_types[i] != "empty":
                return i

        return None

    def prepare_bet_options_for_current_player(self):
        player_money = self.money[self.betting_player]
        self.current_bet_options = generate_bet_options(player_money)

        if not self.current_bet_options:
            self.remove_bankrupt_players()
            self.mode = "game"
            self.schedule_auto_betting(0.8)
            return

        self.current_bet_index = 0
        self.reset_bet_hold()
        self.bot_bet_start_time = None

        if self.seat_is_bot(self.betting_player):
            self.message = f"{self.player_display_name(self.betting_player)} is choosing a bet..."
        else:
            self.message = f"{self.player_display_name(self.betting_player)}: choose your bet or skip."

    def current_selected_bet(self):
        if not self.current_bet_options:
            return 0

        return self.current_bet_options[self.current_bet_index]

    def increase_bet(self):
        if self.mode != "betting":
            return

        if not self.current_bet_options:
            return

        self.current_bet_index = (self.current_bet_index + 1) % len(self.current_bet_options)

    def decrease_bet(self):
        if self.mode != "betting":
            return

        if not self.current_bet_options:
            return

        self.current_bet_index = (self.current_bet_index - 1) % len(self.current_bet_options)

    def reset_bet_hold(self):
        self.bet_hold_direction = 0
        self.bet_hold_started_at = 0
        self.bet_hold_last_tick = 0

    def change_bet_by_direction(self, direction):
        if direction > 0:
            self.increase_bet()
        elif direction < 0:
            self.decrease_bet()

    def update_bet_hold(self):
        if self.mode != "betting":
            self.reset_bet_hold()
            return

        if self.seat_is_bot(self.betting_player):
            self.reset_bet_hold()
            return

        keys = pygame.key.get_pressed()

        positive = keys[pygame.K_UP] or keys[pygame.K_RIGHT]
        negative = keys[pygame.K_DOWN] or keys[pygame.K_LEFT]

        if positive and not negative:
            direction = 1
        elif negative and not positive:
            direction = -1
        else:
            direction = 0

        now = time.time()

        if direction == 0:
            self.reset_bet_hold()
            return

        if direction != self.bet_hold_direction:
            self.bet_hold_direction = direction
            self.bet_hold_started_at = now
            self.bet_hold_last_tick = now
            self.change_bet_by_direction(direction)
            return

        hold_time = now - self.bet_hold_started_at

        interval = 0.35 - hold_time * 0.07
        interval = max(0.045, interval)

        if now - self.bet_hold_last_tick >= interval:
            self.bet_hold_last_tick = now
            self.change_bet_by_direction(direction)

    def bot_choose_bet(self, player_index):
        money = self.money[player_index]

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

        target = min(target, money)

        options = generate_bet_options(money)
        if not options:
            return 0

        valid_options = [x for x in options if x <= target]

        if not valid_options:
            return options[0]

        return max(valid_options)

    def update_bot_betting(self):
        if self.mode != "betting":
            return

        if not self.seat_is_bot(self.betting_player):
            return

        if self.bot_bet_start_time is None:
            self.bot_bet_start_time = time.time()
            return

        if time.time() - self.bot_bet_start_time < BOT_BET_DELAY:
            return

        bet = self.bot_choose_bet(self.betting_player)

        if bet <= 0:
            self.round_bets[self.betting_player] = 0
            self.advance_betting_player()
            return

        self.round_bets[self.betting_player] = bet
        self.message = f"{self.player_display_name(self.betting_player)} bets ${bet}."
        self.advance_betting_player()

    def confirm_bet(self):
        if self.mode != "betting":
            return

        if self.seat_is_bot(self.betting_player):
            return

        bet = self.current_selected_bet()

        if bet <= 0:
            return

        self.round_bets[self.betting_player] = bet
        self.advance_betting_player()

    def skip_bet(self):
        if self.mode != "betting":
            return

        if self.seat_is_bot(self.betting_player):
            return

        self.round_bets[self.betting_player] = 0
        self.advance_betting_player()

    def advance_betting_player(self):
        next_player = self.next_betting_player_after(self.betting_player)

        if next_player is None:
            if len(self.round_participants()) == 0:
                self.mode = "game"
                self.message = "Everyone skipped. Betting will restart automatically."
                self.schedule_auto_betting(1.0)
                return

            speak(random.choice(BET_CONFIRMATION_QUOTES))
            self.start_deal_round()

        else:
            self.betting_player = next_player
            self.prepare_bet_options_for_current_player()

    # =========================
    # POSITIONS
    # =========================

    def dealer_card_position(self, index):
        start_x = TABLE_CENTER_X - 85
        return start_x + index * (CARD_WIDTH + 10), 212

    def get_player_seat(self, player_index):
        angles = [160, 125, 90, 55, 20]
        angle = math.radians(angles[player_index])

        x = TABLE_CENTER_X + TABLE_RADIUS_X * math.cos(angle)
        y = TABLE_TOP_Y + TABLE_RADIUS_Y * math.sin(angle)

        return int(x), int(y)

    def player_card_position(self, player_index, hand_index, card_index):
        seat_x, seat_y = self.get_player_seat(player_index)

        start_x = seat_x - 105 + hand_index * 145
        start_y = seat_y - 55

        x = start_x + card_index * 30
        y = start_y

        return x, y

    def current_hand_obj(self):
        if self.current_player < len(self.player_hands) and self.current_hand < len(self.player_hands[self.current_player]):
            return self.player_hands[self.current_player][self.current_hand]
        return None

    # =========================
    # DEALER HAND TARGET
    # =========================

    def update_dealer_hand_target(self):
        target = (DEALER_X, DEALER_Y + 88)

        if self.animations:
            anim = self.animations[0]
            tx = anim["x"] + CARD_WIDTH // 2
            ty = anim["y"] + CARD_HEIGHT // 2

            target = (
                lerp(DEALER_X, tx, 0.18),
                lerp(DEALER_Y + 88, ty, 0.18)
            )

        elif self.mode == "betting" and self.seat_types[self.betting_player] != "empty":
            seat_x, seat_y = self.get_player_seat(self.betting_player)
            target = (
                lerp(DEALER_X, seat_x, 0.23),
                lerp(DEALER_Y + 88, seat_y - 95, 0.23)
            )

        elif self.round_active and not self.dealer_revealed and self.seat_types[self.current_player] != "empty":
            seat_x, seat_y = self.get_player_seat(self.current_player)
            target = (
                lerp(DEALER_X, seat_x, 0.26),
                lerp(DEALER_Y + 88, seat_y - 90, 0.26)
            )

        elif self.dealer_revealed:
            target = (TABLE_CENTER_X + 40, 200)

        max_reach_x = 105
        max_reach_y = 72

        target_x = clamp(target[0], DEALER_X - max_reach_x, DEALER_X + max_reach_x)
        target_y = clamp(target[1], DEALER_Y + 52, DEALER_Y + 52 + max_reach_y)

        self.dealer_hand_target = (target_x, target_y)

    # =========================
    # BETTING OVERLAY BUTTONS
    # =========================

    def get_betting_overlay_buttons(self):
        seat_x, seat_y = self.get_player_seat(self.betting_player)

        confirm = Button(seat_x - 130, seat_y - 138, 120, 34, "CONFIRM", DARK_GREEN)
        skip = Button(seat_x + 10, seat_y - 138, 120, 34, "SKIP (K)", DARK_RED)

        return confirm, skip

    # =========================
    # CARD ANIMATION
    # =========================

    def queue_card(self, target, player_index=None, hand_index=None, hidden=False):
        card = self.draw_card_from_deck()

        if target == "dealer":
            target_pos = self.dealer_card_position(len(self.dealer_hand))
        else:
            hand = self.player_hands[player_index][hand_index]
            target_pos = self.player_card_position(player_index, hand_index, len(hand.cards))

        self.deal_queue.append({
            "card": card,
            "target": target,
            "player_index": player_index,
            "hand_index": hand_index,
            "hidden": hidden,
            "target_pos": target_pos,
        })

    def start_next_deal_animation(self):
        if self.animations or not self.deal_queue:
            return

        item = self.deal_queue.pop(0)

        self.animations.append({
            "card": item["card"],
            "target": item["target"],
            "player_index": item["player_index"],
            "hand_index": item["hand_index"],
            "hidden": item["hidden"],
            "x": DECK_POS[0],
            "y": DECK_POS[1],
            "target_x": item["target_pos"][0],
            "target_y": item["target_pos"][1],
            "progress": 0.0,
        })

    def update_animations(self):
        if not self.animations:
            if self.deal_queue:
                self.start_next_deal_animation()

            elif self.processing_deal_queue:
                phase = self.deal_phase
                self.processing_deal_queue = False
                self.deal_phase = None

                if phase == "initial":
                    self.after_initial_deal_finished()
                elif phase == "player_action":
                    self.after_player_action_deal_finished()
                elif phase == "dealer":
                    self.after_dealer_card_dealt()

            return

        anim = self.animations[0]
        anim["progress"] += DEAL_ANIMATION_SPEED

        t = min(1.0, anim["progress"])
        smooth_t = t * t * (3 - 2 * t)

        anim["x"] = DECK_POS[0] + (anim["target_x"] - DECK_POS[0]) * smooth_t
        anim["y"] = DECK_POS[1] + (anim["target_y"] - DECK_POS[1]) * smooth_t

        if t >= 1.0:
            if anim["target"] == "dealer":
                self.dealer_hand.append({
                    "card": anim["card"],
                    "hidden": anim["hidden"],
                })
            else:
                hand = self.player_hands[anim["player_index"]][anim["hand_index"]]
                hand.cards.append(anim["card"])

            self.animations.pop(0)

    # =========================
    # ROUND FLOW
    # =========================

    def start_deal_round(self):
        self.mode = "game"

        self.deck = self.create_deck()
        self.dealer_hand = []
        self.player_hands = [[] for _ in range(MAX_PLAYERS)]
        self.round_start_money = self.money.copy()
        self.bot_action_start_time = None

        participants = self.round_participants()

        if not participants:
            # nothing to deal; return to game mode and schedule auto betting
            self.mode = "game"
            self.message = "No participants to deal. Betting will restart automatically."
            self.schedule_auto_betting(1.0)
            return

        for i in participants:
            bet = self.round_bets[i]

            if self.money[i] < bet:
                self.message = f"{self.player_display_name(i)} does not have enough money."
                return

            self.money[i] -= bet
            self.player_hands[i] = [BlackjackHand(bet)]

        self.current_player = participants[0]
        self.current_hand = 0
        self.dealer_revealed = False
        self.round_active = False
        self.round_over = False

        self.message = "Dealing cards..."

        self.deal_queue = []
        self.animations = []
        self.processing_deal_queue = True
        self.deal_phase = "initial"

        for i in participants:
            self.queue_card("player", i, 0, hidden=False)

        self.queue_card("dealer", hidden=False)

        for i in participants:
            self.queue_card("player", i, 0, hidden=False)

        self.queue_card("dealer", hidden=True)

        self.start_next_deal_animation()

    def after_initial_deal_finished(self):
        self.round_active = True

        dealer_cards = [c["card"] for c in self.dealer_hand]
        dealer_blackjack = self.is_blackjack(dealer_cards)

        if dealer_blackjack:
            self.dealer_revealed = True
            self.finish_all_hands_due_to_dealer_blackjack()
            return

        for p in self.round_participants():
            hand = self.player_hands[p][0]

            if self.is_blackjack(hand.cards):
                payout = int(hand.bet * 2.5)
                self.money[p] += payout
                hand.finished = True
                hand.settled = True
                hand.result = f"Blackjack! Won ${payout - hand.bet}"
                speak(f"{self.player_display_name(p)} has blackjack.")

        self.find_next_active_hand()

    def finish_all_hands_due_to_dealer_blackjack(self):
        for p in self.round_participants():
            for hand in self.player_hands[p]:
                if self.is_blackjack(hand.cards):
                    self.money[p] += hand.bet
                    hand.result = "Push: both Blackjack"
                    speak(f"{self.player_display_name(p)} pushes.")
                else:
                    hand.result = f"Dealer Blackjack. Lost ${hand.bet}"
                    speak(f"{self.player_display_name(p)} loses.")

                hand.finished = True
                hand.settled = True

        self.round_active = False
        self.round_over = True

        bankrupt_removed = self.remove_bankrupt_players()

        if not bankrupt_removed:
            self.message = "Dealer has Blackjack. Next betting starts automatically."
            self.schedule_auto_betting(2.0)

    def find_next_active_hand(self):
        self.bot_action_start_time = None

        for p in range(MAX_PLAYERS):
            if self.seat_types[p] == "empty":
                continue

            if p >= len(self.player_hands):
                continue

            for h in range(len(self.player_hands[p])):
                hand = self.player_hands[p][h]

                if not hand.finished and not hand.settled:
                    self.current_player = p
                    self.current_hand = h
                    value = self.hand_value(hand.cards)
                    self.message = f"{self.player_display_name(p)}, Hand {h + 1}: {value}. Hit, Stand, Double, or Split."
                    return

        if self.all_hands_settled():
            self.round_active = False
            self.round_over = True

            bankrupt_removed = self.remove_bankrupt_players()

            if not bankrupt_removed:
                self.message = "Round over. Next betting starts automatically."
                self.schedule_auto_betting(2.0)
        else:
            self.start_dealer_turn()

    def all_hands_settled(self):
        for p in self.round_participants():
            for hand in self.player_hands[p]:
                if not hand.settled:
                    return False

        return True

    # =========================
    # HUMAN ACTIONS
    # =========================

    def hit(self):
        if not self.can_act():
            return

        if self.seat_is_bot(self.current_player):
            return

        self.perform_hit()

    def stand(self):
        if not self.can_act():
            return

        if self.seat_is_bot(self.current_player):
            return

        self.perform_stand()

    def double_down(self):
        if not self.can_act():
            return

        if self.seat_is_bot(self.current_player):
            return

        self.perform_double_down()

    def split(self):
        if not self.can_act():
            return

        if self.seat_is_bot(self.current_player):
            return

        self.perform_split()

    # =========================
    # SHARED ACTIONS
    # =========================

    def perform_hit(self):
        self.queue_card("player", self.current_player, self.current_hand, hidden=False)
        self.processing_deal_queue = True
        self.deal_phase = "player_action"

        self.message = "Dealing hit card..."
        self.start_next_deal_animation()

        hand = self.current_hand_obj()
        hand.pending_action = "hit"

    def perform_stand(self):
        hand = self.current_hand_obj()
        hand.finished = True
        hand.result = "Stood"
        self.find_next_active_hand()

    def perform_double_down(self):
        if not self.can_double():
            self.message = "Cannot double down now."
            return

        hand = self.current_hand_obj()

        self.money[self.current_player] -= hand.bet
        hand.bet *= 2
        hand.doubled = True

        self.queue_card("player", self.current_player, self.current_hand, hidden=False)
        self.processing_deal_queue = True
        self.deal_phase = "player_action"

        self.message = "Double down: one final card..."
        self.start_next_deal_animation()

        hand.pending_action = "double"

    def perform_split(self):
        if not self.can_split():
            self.message = "Cannot split now. Cards must be exactly the same rank."
            return

        old_hand = self.current_hand_obj()

        card1 = old_hand.cards[0]
        card2 = old_hand.cards[1]

        self.money[self.current_player] -= old_hand.bet

        hand1 = BlackjackHand(old_hand.bet)
        hand2 = BlackjackHand(old_hand.bet)

        hand1.cards = [card1]
        hand2.cards = [card2]

        hand1.from_split = True
        hand2.from_split = True

        self.player_hands[self.current_player][self.current_hand] = hand1
        self.player_hands[self.current_player].insert(self.current_hand + 1, hand2)

        self.message = "Split! Dealing one card to each hand..."

        self.queue_card("player", self.current_player, self.current_hand, hidden=False)
        self.queue_card("player", self.current_player, self.current_hand + 1, hidden=False)

        self.processing_deal_queue = True
        self.deal_phase = "player_action"
        self.start_next_deal_animation()

    def after_player_action_deal_finished(self):
        if not self.round_active:
            return

        hand = self.current_hand_obj()

        if hasattr(hand, "pending_action"):
            action = hand.pending_action
            delattr(hand, "pending_action")

            value = self.hand_value(hand.cards)

            if value > 21:
                hand.busted = True
                hand.finished = True
                hand.result = f"Busted: {value}"
                speak(f"{self.player_display_name(self.current_player)} busted with {value}.")
                self.message = f"{self.player_display_name(self.current_player)} busted with {value}."
                self.find_next_active_hand()
                return

            if action == "double":
                hand.finished = True
                hand.result = f"Doubled: {value}"
                speak(f"{self.player_display_name(self.current_player)} gets {value}.")
                self.message = f"{self.player_display_name(self.current_player)} doubled and got {value}."
                self.find_next_active_hand()
                return

            if action == "hit":
                if value == 21:
                    hand.finished = True
                    hand.result = "21"
                    speak(f"{self.player_display_name(self.current_player)} gets 21.")
                    self.find_next_active_hand()
                else:
                    speak(f"{self.player_display_name(self.current_player)} gets {value}.")
                    self.message = f"{self.player_display_name(self.current_player)}, Hand {self.current_hand + 1}: {value}. Hit or Stand."

    # =========================
    # BOT DECISION MAKING
    # =========================

    def bot_should_split(self, hand):
        if not self.can_split():
            return False

        if len(hand.cards) != 2:
            return False

        rank1 = hand.cards[0][0]
        rank2 = hand.cards[1][0]

        if rank1 != rank2:
            return False

        # Bot only splits exact A+A or 8+8.
        return rank1 in ["A", "8"]

    def bot_should_double(self, hand):
        if not self.can_double():
            return False

        value = self.hand_value(hand.cards)
        dealer = self.dealer_visible_value()

        if len(hand.cards) != 2:
            return False

        if value == 11:
            return True

        if value == 10 and dealer <= 9:
            return True

        if value == 9 and 3 <= dealer <= 6:
            return True

        return False

    def bot_decide_action(self):
        hand = self.current_hand_obj()
        cards = hand.cards
        value = self.hand_value(cards)
        dealer = self.dealer_visible_value()

        if value >= 21:
            return "stand"

        if self.bot_should_split(hand):
            return "split"

        if self.bot_should_double(hand):
            return "double"

        if len(cards) == 2 and value >= 15:
            if value >= 16:
                return "stand"

            if dealer <= 6:
                return "stand"

            return "hit"

        if value <= 11:
            return "hit"

        if value >= 17:
            return "stand"

        if value == 12:
            if 4 <= dealer <= 6:
                return "stand"
            return "hit"

        if 13 <= value <= 16:
            if dealer <= 6:
                return "stand"
            return "hit"

        return "stand"

    def update_bot_action(self):
        if self.mode != "game":
            return

        if not self.round_active or self.round_over:
            return

        if self.animations or self.deal_queue or self.processing_deal_queue:
            return

        if self.dealer_revealed:
            return

        if not self.seat_is_bot(self.current_player):
            return

        if not self.can_act():
            return

        if self.bot_action_start_time is None:
            self.bot_action_start_time = time.time()
            return

        if time.time() - self.bot_action_start_time < BOT_ACTION_DELAY:
            return

        self.bot_action_start_time = None

        action = self.bot_decide_action()
        name = self.player_display_name(self.current_player)

        if action == "split":
            self.message = f"{name} chooses to split."
            self.perform_split()
        elif action == "double":
            self.message = f"{name} doubles down."
            self.perform_double_down()
        elif action == "hit":
            self.message = f"{name} hits."
            self.perform_hit()
        else:
            self.message = f"{name} stands."
            self.perform_stand()

    # =========================
    # DEALER
    # =========================

    def start_dealer_turn(self):
        self.dealer_revealed = True
        self.message = "Dealer's turn..."
        self.dealer_draw_if_needed()

    def dealer_draw_if_needed(self):
        dealer_cards = [c["card"] for c in self.dealer_hand]
        dealer_value = self.hand_value(dealer_cards)

        if dealer_value < 17:
            self.queue_card("dealer", hidden=False)
            self.processing_deal_queue = True
            self.deal_phase = "dealer"
            self.message = "Dealer draws..."
            self.start_next_deal_animation()
        else:
            self.finish_round()

    def after_dealer_card_dealt(self):
        self.dealer_draw_if_needed()

    def finish_round(self):
        dealer_cards = [c["card"] for c in self.dealer_hand]
        dealer_value = self.hand_value(dealer_cards)

        for p in self.round_participants():
            for hand in self.player_hands[p]:
                if hand.settled:
                    continue

                player_value = self.hand_value(hand.cards)

                if hand.busted:
                    hand.result = f"Busted: {player_value}. Lost ${hand.bet}"

                elif dealer_value > 21:
                    self.money[p] += hand.bet * 2
                    hand.result = f"Dealer busts. Won ${hand.bet}"
                    speak(f"{self.player_display_name(p)} wins.")

                elif player_value > dealer_value:
                    self.money[p] += hand.bet * 2
                    hand.result = f"Won: {player_value} vs {dealer_value}"
                    speak(f"{self.player_display_name(p)} wins.")

                elif player_value < dealer_value:
                    hand.result = f"Lost: {player_value} vs {dealer_value}"
                    speak(f"{self.player_display_name(p)} loses.")

                else:
                    self.money[p] += hand.bet
                    hand.result = f"Push: {player_value}"
                    speak(f"{self.player_display_name(p)} pushes.")

                hand.finished = True
                hand.settled = True

        self.round_active = False
        self.round_over = True

        bankrupt_removed = self.remove_bankrupt_players()

        if not bankrupt_removed:
            self.message = "Round over. Next betting starts automatically."
            self.schedule_auto_betting(2.0)

    # =========================
    # ACTION AVAILABILITY
    # =========================

    def can_act(self):
        if self.mode != "game":
            return False

        if not self.round_active:
            return False

        if self.round_over:
            return False

        if self.animations or self.deal_queue:
            return False

        if self.dealer_revealed:
            return False

        if self.seat_types[self.current_player] == "empty":
            return False

        if self.round_bets[self.current_player] <= 0:
            return False

        return True

    def bet_more_than_half_start_money(self):
        hand = self.current_hand_obj()
        if hand is None:
            return False
        start_money = self.round_start_money[self.current_player]
        return hand.bet > start_money / 2

    def can_double(self):
        if not self.can_act():
            return False

        hand = self.current_hand_obj()
        if hand is None:
            return False

        if len(hand.cards) != 2:
            return False

        if self.money[self.current_player] < hand.bet:
            return False

        if self.bet_more_than_half_start_money():
            return False

        return True

    def can_split(self):
        if not self.can_act():
            return False

        hand = self.current_hand_obj()
        if hand is None:
            return False

        if len(hand.cards) != 2:
            return False

        if self.money[self.current_player] < hand.bet:
            return False

        if self.bet_more_than_half_start_money():
            return False

        card1 = hand.cards[0]
        card2 = hand.cards[1]

        # Correct split rule:
        # Cards must be the exact same rank.
        # Q + K is NOT allowed.
        # 10 + K is NOT allowed.
        # Q + Q, K + K, J + J, 10 + 10 are allowed.
        return card1[0] == card2[0]

    # =========================
    # RESET
    # =========================

    def reset_money(self):
        clear_voice_queue()

        for i in range(MAX_PLAYERS):
            if self.seat_types[i] != "empty":
                self.money[i] = STARTING_MONEY

        self.message = f"Active seats reset to ${STARTING_MONEY}. Betting starts automatically."

        self.round_active = False
        self.round_over = True
        self.dealer_revealed = False
        self.player_hands = [[] for _ in range(MAX_PLAYERS)]
        self.dealer_hand = []
        self.round_bets = [0 for _ in range(MAX_PLAYERS)]
        self.animations = []
        self.deal_queue = []
        self.processing_deal_queue = False
        self.mode = "game"
        self.schedule_auto_betting(0.8)

    # =========================
    # DRAW CARD
    # =========================

    def draw_card(self, card, x, y, hidden=False):
        rect = pygame.Rect(x, y, CARD_WIDTH, CARD_HEIGHT)

        if hidden:
            pygame.draw.rect(SCREEN, DARK_RED, rect, border_radius=10)
            pygame.draw.rect(SCREEN, BLACK, rect, 3, border_radius=10)

            inner_rect = pygame.Rect(x + 8, y + 8, CARD_WIDTH - 16, CARD_HEIGHT - 16)
            pygame.draw.rect(SCREEN, (155, 25, 25), inner_rect, border_radius=8)
            pygame.draw.rect(SCREEN, GOLD, inner_rect, 2, border_radius=8)

            centre_circle = (rect.centerx, rect.centery)
            pygame.draw.circle(SCREEN, GOLD, centre_circle, 20, 3)
            pygame.draw.circle(SCREEN, (110, 20, 20), centre_circle, 12)

            back_text = SMALL_FONT.render("BJ", True, GOLD)
            SCREEN.blit(
                back_text,
                (
                    rect.centerx - back_text.get_width() // 2,
                    rect.centery - back_text.get_height() // 2,
                )
            )
            return

        rank, suit = card
        suit_colour = RED if suit in ["♥", "♦"] else BLACK

        pygame.draw.rect(SCREEN, WHITE, rect, border_radius=10)
        pygame.draw.rect(SCREEN, BLACK, rect, 3, border_radius=10)

        rank_text = CARD_FONT.render(rank, True, suit_colour)
        suit_text = SUIT_FONT.render(suit, True, suit_colour)

        SCREEN.blit(rank_text, (x + 8, y + 6))
        SCREEN.blit(suit_text, (x + 10, y + 40))

        centre_suit = TITLE_FONT.render(suit, True, suit_colour)
        SCREEN.blit(
            centre_suit,
            (
                rect.centerx - centre_suit.get_width() // 2,
                rect.centery - centre_suit.get_height() // 2 + 12,
            )
        )

    # =========================
    # DRAW DEALER CHARACTER
    # =========================

    def draw_professional_dealer(self):
        self.update_dealer_hand_target()

        base_x = DEALER_X
        base_y = DEALER_Y

        torso_rect = pygame.Rect(base_x - 38, base_y + 46, 76, 74)

        left_shoulder = (base_x - 34, base_y + 63)
        right_shoulder = (base_x + 34, base_y + 63)

        target_x, target_y = self.dealer_hand_target

        left_hand = (int(target_x - 22), int(target_y))
        right_hand = (int(target_x + 22), int(target_y))

        left_elbow = (
            int((left_shoulder[0] + left_hand[0]) / 2 - 10),
            int((left_shoulder[1] + left_hand[1]) / 2 + 8)
        )
        right_elbow = (
            int((right_shoulder[0] + right_hand[0]) / 2 + 10),
            int((right_shoulder[1] + right_hand[1]) / 2 + 8)
        )

        pygame.draw.line(SCREEN, SUIT_BLACK, left_shoulder, left_elbow, 12)
        pygame.draw.line(SCREEN, SUIT_BLACK, left_elbow, left_hand, 10)
        pygame.draw.circle(SCREEN, SKIN, left_hand, 9)
        pygame.draw.circle(SCREEN, SKIN_DARK, left_hand, 9, 1)

        pygame.draw.line(SCREEN, SUIT_BLACK, right_shoulder, right_elbow, 12)
        pygame.draw.line(SCREEN, SUIT_BLACK, right_elbow, right_hand, 10)
        pygame.draw.circle(SCREEN, SKIN, right_hand, 9)
        pygame.draw.circle(SCREEN, SKIN_DARK, right_hand, 9, 1)

        pygame.draw.rect(SCREEN, SUIT_BLACK, torso_rect, border_radius=14)
        pygame.draw.rect(SCREEN, BLACK, torso_rect, 2, border_radius=14)

        pygame.draw.polygon(
            SCREEN,
            SHIRT_WHITE,
            [
                (base_x - 20, base_y + 50),
                (base_x + 20, base_y + 50),
                (base_x + 13, base_y + 118),
                (base_x - 13, base_y + 118),
            ]
        )

        pygame.draw.polygon(
            SCREEN,
            (30, 30, 38),
            [
                (base_x - 38, base_y + 48),
                (base_x - 9, base_y + 72),
                (base_x - 24, base_y + 118),
                (base_x - 38, base_y + 118),
            ]
        )
        pygame.draw.polygon(
            SCREEN,
            (30, 30, 38),
            [
                (base_x + 38, base_y + 48),
                (base_x + 9, base_y + 72),
                (base_x + 24, base_y + 118),
                (base_x + 38, base_y + 118),
            ]
        )

        pygame.draw.polygon(SCREEN, BOWTIE, [(base_x, base_y + 63), (base_x - 13, base_y + 56), (base_x - 13, base_y + 70)])
        pygame.draw.polygon(SCREEN, BOWTIE, [(base_x, base_y + 63), (base_x + 13, base_y + 56), (base_x + 13, base_y + 70)])
        pygame.draw.circle(SCREEN, DARK_RED, (base_x, base_y + 63), 4)

        pygame.draw.rect(SCREEN, SKIN, (base_x - 10, base_y + 38, 20, 18), border_radius=6)

        pygame.draw.ellipse(SCREEN, SKIN, (base_x - 24, base_y + 2, 48, 48))
        pygame.draw.ellipse(SCREEN, SKIN_DARK, (base_x - 24, base_y + 2, 48, 48), 2)

        pygame.draw.arc(SCREEN, HAIR, (base_x - 25, base_y - 2, 50, 36), math.pi, 2 * math.pi, 10)
        pygame.draw.rect(SCREEN, HAIR, (base_x - 22, base_y + 7, 44, 8), border_radius=8)

        pygame.draw.circle(SCREEN, SKIN, (base_x - 25, base_y + 26), 6)
        pygame.draw.circle(SCREEN, SKIN, (base_x + 25, base_y + 26), 6)

        pygame.draw.circle(SCREEN, BLACK, (base_x - 8, base_y + 24), 2)
        pygame.draw.circle(SCREEN, BLACK, (base_x + 8, base_y + 24), 2)
        pygame.draw.line(SCREEN, SKIN_DARK, (base_x, base_y + 25), (base_x - 2, base_y + 33), 2)
        pygame.draw.arc(SCREEN, (100, 40, 35), (base_x - 9, base_y + 31, 18, 10), 0, math.pi, 2)

        badge = pygame.Rect(base_x + 18, base_y + 82, 20, 10)
        pygame.draw.rect(SCREEN, GOLD, badge, border_radius=3)

    # =========================
    # DRAW SCREENS
    # =========================

    def draw_intro_screen(self):
        SCREEN.fill((12, 45, 32))

        elapsed = time.time() - self.intro_start_time
        alpha_factor = min(1.0, elapsed / 0.8)

        pygame.draw.circle(SCREEN, (30, 95, 58), (WIDTH // 2, HEIGHT // 2), 260)
        pygame.draw.circle(SCREEN, GOLD, (WIDTH // 2, HEIGHT // 2), 265, 4)
        pygame.draw.circle(SCREEN, (120, 72, 32), (WIDTH // 2, HEIGHT // 2), 300, 8)

        welcome = WELCOME_FONT.render("Welcome to JASLI's casino!", True, GOLD)
        blackjack = TITLE_FONT.render("BLACKJACK", True, WHITE)

        welcome.set_alpha(int(255 * alpha_factor))
        blackjack.set_alpha(int(255 * alpha_factor))

        SCREEN.blit(welcome, (WIDTH // 2 - welcome.get_width() // 2, HEIGHT // 2 - 90))
        SCREEN.blit(blackjack, (WIDTH // 2 - blackjack.get_width() // 2, HEIGHT // 2 + 5))

        hint = SMALL_FONT.render("Taking your seat...", True, CREAM)
        hint.set_alpha(int(220 * alpha_factor))
        SCREEN.blit(hint, (WIDTH // 2 - hint.get_width() // 2, HEIGHT // 2 + 80))

    # =========================
    # DRAW TABLE
    # =========================

    def draw_casino_table_shape(self):
        SCREEN.fill(BACKGROUND)

        outer_arc = build_bottom_semicircle_points(
            TABLE_CENTER_X,
            TABLE_TOP_Y - 18,
            TABLE_RADIUS_X + 34,
            TABLE_RADIUS_Y + 34,
            100
        )

        outer_poly = outer_arc + [
            (TABLE_CENTER_X + TABLE_RADIUS_X + 34, TABLE_TOP_Y - 18),
            (TABLE_CENTER_X - TABLE_RADIUS_X - 34, TABLE_TOP_Y - 18),
        ]

        pygame.draw.polygon(SCREEN, WOOD, outer_poly)

        inner_arc = build_bottom_semicircle_points(
            TABLE_CENTER_X,
            TABLE_TOP_Y,
            TABLE_RADIUS_X,
            TABLE_RADIUS_Y,
            100
        )

        inner_poly = inner_arc + [
            (TABLE_CENTER_X + TABLE_RADIUS_X, TABLE_TOP_Y),
            (TABLE_CENTER_X - TABLE_RADIUS_X, TABLE_TOP_Y),
        ]

        pygame.draw.polygon(SCREEN, DARK_GREEN, inner_poly)

        pygame.draw.line(
            SCREEN,
            GOLD,
            (TABLE_CENTER_X - TABLE_RADIUS_X, TABLE_TOP_Y),
            (TABLE_CENTER_X + TABLE_RADIUS_X, TABLE_TOP_Y),
            6
        )

        pygame.draw.lines(SCREEN, GOLD, False, inner_arc, 6)

        inner_decor_arc = build_bottom_semicircle_points(
            TABLE_CENTER_X,
            TABLE_TOP_Y + 70,
            TABLE_RADIUS_X - 70,
            TABLE_RADIUS_Y - 90,
            100
        )

        pygame.draw.lines(SCREEN, LIGHT_GREEN, False, inner_decor_arc, 2)

        title = TITLE_FONT.render("BLACKJACK", True, GOLD)
        SCREEN.blit(title, (TABLE_CENTER_X - title.get_width() // 2, 2))

        rule_text = SMALL_FONT.render("Dealer stands on 17  |  Blackjack pays 3:2", True, CREAM)
        SCREEN.blit(rule_text, (TABLE_CENTER_X - rule_text.get_width() // 2, 58))

    def draw_dealer_area(self):
        self.draw_professional_dealer()

        dealer_label = BIG_FONT.render("DEALER", True, WHITE)
        SCREEN.blit(dealer_label, (TABLE_CENTER_X - dealer_label.get_width() // 2, 122))

        if self.dealer_hand:
            if self.dealer_revealed:
                dealer_value = self.hand_value([c["card"] for c in self.dealer_hand])
                dealer_text = FONT.render(f"Value: {dealer_value}", True, WHITE)
            else:
                visible_cards = [c["card"] for c in self.dealer_hand if not c["hidden"]]
                visible_value = self.hand_value(visible_cards)
                dealer_text = FONT.render(f"Value: {visible_value} + hidden", True, WHITE)

            SCREEN.blit(dealer_text, (TABLE_CENTER_X - dealer_text.get_width() // 2, 322))

        for i, card_data in enumerate(self.dealer_hand):
            x, y = self.dealer_card_position(i)
            hidden = card_data["hidden"] and not self.dealer_revealed
            self.draw_card(card_data["card"], x, y, hidden)

        deck_rect = pygame.Rect(DECK_POS[0], DECK_POS[1], CARD_WIDTH, CARD_HEIGHT)
        pygame.draw.rect(SCREEN, DARK_RED, deck_rect, border_radius=10)
        pygame.draw.rect(SCREEN, BLACK, deck_rect, 3, border_radius=10)

        deck_text = SMALL_FONT.render("DECK", True, WHITE)
        SCREEN.blit(deck_text, (deck_rect.centerx - deck_text.get_width() // 2, deck_rect.centery - 10))

    def draw_player_seats(self):
        for p in range(MAX_PLAYERS):
            seat_x, seat_y = self.get_player_seat(p)

            seat_rect = pygame.Rect(seat_x - 78, seat_y + 42, 156, 56)

            if self.seat_types[p] == "empty":
                seat_colour = (55, 55, 55)
                label_colour = GRAY
            elif self.seat_types[p] == "bot":
                seat_colour = (70, 80, 120)
                label_colour = WHITE
            else:
                seat_colour = (32, 100, 60)
                label_colour = WHITE

            active = (
                self.round_active
                and not self.round_over
                and p == self.current_player
                and not self.dealer_revealed
            )

            betting_active = self.mode == "betting" and p == self.betting_player

            if active or betting_active:
                pygame.draw.rect(SCREEN, GOLD, seat_rect.inflate(10, 10), border_radius=20)

            pygame.draw.rect(SCREEN, seat_colour, seat_rect, border_radius=18)
            pygame.draw.rect(SCREEN, GOLD, seat_rect, 2, border_radius=18)

            if self.seat_types[p] == "empty":
                label = SMALL_FONT.render(f"Seat {p + 1} | Press {p + 1}", True, label_colour)
            elif self.seat_types[p] == "bot":
                label = SMALL_FONT.render(f"{BOT_NAMES[p]} | ${self.money[p]}", True, label_colour)
            else:
                label = SMALL_FONT.render(f"Player {p + 1} | ${self.money[p]}", True, label_colour)

            SCREEN.blit(label, (seat_rect.centerx - label.get_width() // 2, seat_rect.y + 6))

            if self.seat_types[p] == "empty":
                sub = TINY_FONT.render("Buy in here", True, GRAY)
                SCREEN.blit(sub, (seat_rect.centerx - sub.get_width() // 2, seat_rect.y + 32))
            elif self.round_bets and p < len(self.round_bets) and self.round_bets[p] > 0:
                bet_label = TINY_FONT.render(f"Bet ${self.round_bets[p]}", True, GOLD)
                SCREEN.blit(bet_label, (seat_rect.centerx - bet_label.get_width() // 2, seat_rect.y + 32))
            elif self.round_bets and p < len(self.round_bets) and self.round_bets[p] == 0 and self.mode == "game" and not self.round_over:
                skip_label = TINY_FONT.render("Skipped", True, GRAY)
                SCREEN.blit(skip_label, (seat_rect.centerx - skip_label.get_width() // 2, seat_rect.y + 32))

            if not self.player_hands or p >= len(self.player_hands):
                continue

            for h, hand in enumerate(self.player_hands[p]):
                hand_value = self.hand_value(hand.cards)

                hand_active = (
                    self.round_active
                    and not self.round_over
                    and p == self.current_player
                    and h == self.current_hand
                    and not self.dealer_revealed
                )

                label_x, label_y = self.player_card_position(p, h, 0)
                hand_label = TINY_FONT.render(
                    f"H{h + 1} ${hand.bet} | {hand_value}",
                    True,
                    GOLD if hand_active else WHITE
                )
                SCREEN.blit(hand_label, (label_x, label_y - 18))

                if hand.result:
                    result_text = TINY_FONT.render(hand.result, True, GOLD)
                    SCREEN.blit(result_text, (label_x, label_y + CARD_HEIGHT + 4))

                for c_idx, card in enumerate(hand.cards):
                    x, y = self.player_card_position(p, h, c_idx)
                    self.draw_card(card, x, y, hidden=False)

    def draw_betting_overlay(self):
        if self.mode != "betting":
            return

        seat_x, seat_y = self.get_player_seat(self.betting_player)

        panel_rect = pygame.Rect(seat_x - 155, seat_y - 250, 310, 110)
        pygame.draw.rect(SCREEN, (245, 248, 255), panel_rect, border_radius=16)
        pygame.draw.rect(SCREEN, GOLD, panel_rect, 4, border_radius=16)

        title = SMALL_FONT.render(f"{self.player_display_name(self.betting_player)} betting", True, BLACK)
        SCREEN.blit(title, (panel_rect.centerx - title.get_width() // 2, panel_rect.y + 10))

        if self.seat_is_bot(self.betting_player):
            bot_text = BIG_FONT.render("Thinking...", True, BLUE)
            SCREEN.blit(bot_text, (panel_rect.centerx - bot_text.get_width() // 2, panel_rect.y + 40))
            hint = TINY_FONT.render("Bot will choose a cautious bet.", True, DARK_GRAY)
        else:
            bet_text = BIG_FONT.render(f"${self.current_selected_bet()}", True, DARK_GREEN)
            SCREEN.blit(bet_text, (panel_rect.centerx - bet_text.get_width() // 2, panel_rect.y + 38))
            hint = TINY_FONT.render("Hold UP/DOWN to change fast | ENTER confirm | K skip", True, DARK_GRAY)

        SCREEN.blit(hint, (panel_rect.centerx - hint.get_width() // 2, panel_rect.y + 82))

        if not self.seat_is_bot(self.betting_player) and local_controls_seat(self.betting_player):
            confirm_button, skip_button = self.get_betting_overlay_buttons()
            confirm_button.draw(True)
            skip_button.draw(True)

    def draw_table(self):
        self.draw_casino_table_shape()
        self.draw_dealer_area()
        self.draw_player_seats()
        self.draw_betting_overlay()

        for anim in self.animations:
            self.draw_card(
                anim["card"],
                int(anim["x"]),
                int(anim["y"]),
                hidden=anim["hidden"]
            )

    def draw_side_panel(self):
        pygame.draw.rect(SCREEN, (245, 245, 248), (940, 30, 230, 690), border_radius=22)
        pygame.draw.rect(SCREEN, BLACK, (940, 30, 230, 690), 3, border_radius=22)

        panel_title = BIG_FONT.render("ACTIONS", True, BLACK)
        SCREEN.blit(panel_title, (1055 - panel_title.get_width() // 2, 65))

        if is_online_ws_client():
            online_label = TINY_FONT.render(f"ONLINE {online_room_code}", True, DARK_GRAY)
            SCREEN.blit(online_label, (1055 - online_label.get_width() // 2, 103))

        for key, button in self.buttons.items():
            if key == "host":
                continue
            enabled = True

            if key == "hit":
                enabled = self.can_act() and self.seat_is_human(self.current_player) and local_controls_seat(self.current_player)
            elif key == "stand":
                enabled = self.can_act() and self.seat_is_human(self.current_player) and local_controls_seat(self.current_player)
            elif key == "double":
                enabled = self.can_double() and self.seat_is_human(self.current_player) and local_controls_seat(self.current_player)
            elif key == "split":
                enabled = self.can_split() and self.seat_is_human(self.current_player) and local_controls_seat(self.current_player)
            elif key == "reset":
                enabled = self.mode in ["game", "betting"] and not is_disabled_legacy_remote_client()
            elif key == "quit":
                enabled = True
            elif key == "host":
                enabled = False

            button.draw(enabled)

        msg_lines = wrap_text(self.message, SMALL_FONT, 190)

        y = 600
        for line in msg_lines[:4]:
            text_surface = SMALL_FONT.render(line, True, BLACK)
            SCREEN.blit(text_surface, (960, y))
            y += 22

    def draw_guide(self):
        guide_lines = [
            "1-5 Buy In / Leave Seat",
            "B Add Bot" if play_mode in [None, "single"] and not is_disabled_legacy_remote_client() and not is_online_ws_client() else "Online Table",
            "Hold ↑/↓ Bet",
            "Enter Confirm",
            "K Skip",
            "H Hit",
            "S Stand",
            "D Double",
            "P Split",
            "ESC Quit",
        ]
        if play_mode == "online_host":
            guide_lines.insert(-1, f"Room: {online_room_code}")
        elif play_mode == "online_guest":
            guide_lines.insert(-1, "Online guest: control your claimed seat only")

        y = 705
        x = 25

        for line in guide_lines:
            surf = TINY_FONT.render(line, True, WHITE)
            SCREEN.blit(surf, (x, y))
            x += surf.get_width() + 10

    def draw(self):
        if app_screen == "intro" or self.mode == "intro":
            self.draw_intro_screen()
        elif app_screen == "main_menu":
            draw_main_menu()
        elif app_screen == "multiplayer_menu":
            draw_multiplayer_menu()
        elif app_screen == "disabled_host_lobby":
            draw_disabled_host_lobby()
        else:
            self.draw_table()
            self.draw_side_panel()
            self.draw_guide()


# =========================
# START MENU / MODE SELECTION
# =========================

def draw_centered_text(text, font, color, y):
    surf = font.render(text, True, color)
    SCREEN.blit(surf, (WIDTH // 2 - surf.get_width() // 2, y))
    return surf


def draw_menu_button(rect, text, color, enabled=True):
    mouse_pos = get_mouse_pos()
    hover = rect.collidepoint(mouse_pos)
    if not enabled:
        draw_color = (120, 120, 120)
    elif hover:
        draw_color = tuple(min(255, c + 28) for c in color)
    else:
        draw_color = color

    pygame.draw.rect(SCREEN, draw_color, rect, border_radius=18)
    pygame.draw.rect(SCREEN, GOLD, rect, 3, border_radius=18)
    surf = BIG_FONT.render(text, True, WHITE if color != GOLD else BLACK)
    SCREEN.blit(surf, (rect.centerx - surf.get_width() // 2, rect.centery - surf.get_height() // 2))


def draw_main_menu():
    SCREEN.fill((12, 45, 32))
    pygame.draw.circle(SCREEN, (30, 95, 58), (WIDTH // 2, HEIGHT // 2), 285)
    pygame.draw.circle(SCREEN, GOLD, (WIDTH // 2, HEIGHT // 2), 290, 4)
    pygame.draw.circle(SCREEN, WOOD, (WIDTH // 2, HEIGHT // 2), 330, 8)

    draw_centered_text("JASLI's Casino", WELCOME_FONT, GOLD, 105)
    draw_centered_text("Choose your table", TITLE_FONT, WHITE, 185)

    single_rect = pygame.Rect(WIDTH // 2 - 260, 300, 520, 70)
    multi_rect = pygame.Rect(WIDTH // 2 - 260, 395, 520, 70)
    quit_rect = pygame.Rect(WIDTH // 2 - 260, 515, 520, 55)

    draw_menu_button(single_rect, "Single Player", DARK_GREEN)
    draw_menu_button(multi_rect, "Online Multiplayer", BLUE)
    draw_menu_button(quit_rect, "Quit", DARK_RED)

    draw_centered_text("Keyboard: 1 = Single Player, 2 = Online Multiplayer, ESC = Quit", SMALL_FONT, CREAM, 620)

    if menu_status_message:
        draw_centered_text(menu_status_message, SMALL_FONT, GOLD, 655)


def draw_multiplayer_menu():
    SCREEN.fill((12, 45, 32))
    pygame.draw.circle(SCREEN, (30, 95, 58), (WIDTH // 2, HEIGHT // 2), 285)
    pygame.draw.circle(SCREEN, GOLD, (WIDTH // 2, HEIGHT // 2), 290, 4)

    draw_centered_text("Online Server Multiplayer", WELCOME_FONT, GOLD, 105)
    draw_centered_text("Create or join a room on the built-in online server.", SMALL_FONT, CREAM, 190)

    host_rect = pygame.Rect(WIDTH // 2 - 260, 285, 520, 70)
    guest_rect = pygame.Rect(WIDTH // 2 - 260, 380, 520, 70)
    back_rect = pygame.Rect(WIDTH // 2 - 260, 500, 520, 55)

    draw_menu_button(host_rect, "Create Online Room", GOLD)
    draw_menu_button(guest_rect, "Join Online Room", BLUE)
    draw_menu_button(back_rect, "Back", DARK_GRAY)

    safety = [
        "This is online mode: both players use the server built into this app version.",
        "One player creates a room code; the other joins that room code.",
        "Do not use this for real-money gambling.",
    ]
    y = 585
    for line in safety:
        draw_centered_text(line, TINY_FONT, CREAM, y)
        y += 24

    if menu_status_message:
        draw_centered_text(menu_status_message, SMALL_FONT, GOLD, 675)


def draw_disabled_host_lobby():
    SCREEN.fill((12, 45, 32))
    pygame.draw.circle(SCREEN, (30, 95, 58), (WIDTH // 2, HEIGHT // 2), 300)
    pygame.draw.circle(SCREEN, GOLD, (WIDTH // 2, HEIGHT // 2), 305, 4)

    draw_centered_text("Host Table", WELCOME_FONT, GOLD, 70)
    draw_centered_text("Online room created on your WebSocket server:", SMALL_FONT, CREAM, 155)

    code_rect = pygame.Rect(WIDTH // 2 - 320, 205, 640, 72)
    pygame.draw.rect(SCREEN, WHITE, code_rect, border_radius=18)
    pygame.draw.rect(SCREEN, GOLD, code_rect, 4, border_radius=18)
    code_text = BIG_FONT.render(format_server_number(current_server_number), True, BLACK)
    SCREEN.blit(code_text, (code_rect.centerx - code_text.get_width() // 2, code_rect.centery - code_text.get_height() // 2))

    local_ip = get_local_ip()
    draw_centered_text(f"Host IP: {local_ip}   Room: {server_room_code}   Port: {server_port}", SMALL_FONT, CREAM, 300)
    draw_centered_text("Share only the room code with your intended players.", TINY_FONT, CREAM, 330)
    draw_centered_text("Guests choose Online Server Multiplayer → Join Room on Server, then enter server URL and room code.", TINY_FONT, CREAM, 355)

    start_rect = pygame.Rect(WIDTH // 2 - 260, 430, 520, 70)
    stop_rect = pygame.Rect(WIDTH // 2 - 260, 535, 520, 55)
    draw_menu_button(start_rect, "Enter Table", DARK_GREEN)
    draw_menu_button(stop_rect, "Back", DARK_RED)

    draw_centered_text("Keyboard: ENTER = Enter Table, ESC = Stop Hosting", SMALL_FONT, CREAM, 630)


def start_single_player_mode():
    global app_screen, play_mode, online_mode, my_seat, my_client_id, server_state, menu_status_message
    if server_running:
        stop_local_server()
    if network is not None:
        try:
            network.close()
        except Exception:
            pass
    if online_ws_client is not None:
        try:
            online_ws_client.close()
        except Exception:
            pass
    online_mode = False
    my_seat = None
    my_client_id = None
    server_state = None
    play_mode = "single"
    app_screen = "playing"
    menu_status_message = ""
    if game.mode == "intro":
        game.mode = "game"
    game.message = "Single player mode. Press 1-5 to buy into a seat. Press B to add a bot."
    try:
        music_player.stop()
    except Exception:
        pass


def start_disabled_legacy_host_mode():
    global app_screen, play_mode, current_server_number, menu_status_message, my_seat
    if network is not None:
        try:
            network.close()
        except Exception:
            pass
    if not server_running:
        ok = start_local_server()
        if not ok:
            menu_status_message = "Unable to start host. Port may be in use or blocked."
            return
    play_mode = "host"
    my_seat = None
    current_server_number = make_server_number(get_local_ip(), server_room_code)
    app_screen = "disabled_host_lobby"
    menu_status_message = ""
    game.message = "Online host mode. Create a room, then press 1-5 to claim your seat."


def open_online_create_screen():
    global app_screen, play_mode, connect_overlay_active, connect_host_text, connect_room_text
    global connect_status_message, menu_status_message, connect_input_field, connect_overlay_mode
    leave_host_mode_before_joining_as_guest()
    play_mode = "online_host"
    app_screen = "multiplayer_menu"
    connect_overlay_active = True
    connect_overlay_mode = "create"
    connect_input_field = 0
    connect_host_text = DEFAULT_ONLINE_SERVER_URL
    connect_room_text = ""
    connect_status_message = "Press ENTER to create a room on the built-in online server."
    menu_status_message = ""


def open_guest_join_screen():
    global app_screen, play_mode, connect_overlay_active, connect_host_text, connect_room_text
    global connect_status_message, menu_status_message, connect_input_field, connect_overlay_mode
    leave_host_mode_before_joining_as_guest()
    play_mode = "online_guest"
    app_screen = "multiplayer_menu"
    connect_overlay_active = True
    connect_overlay_mode = "join"
    connect_input_field = 0
    connect_host_text = DEFAULT_ONLINE_SERVER_URL
    connect_room_text = ""
    connect_status_message = "Enter the room code, then press ENTER."
    menu_status_message = ""


def handle_menu_event(event):
    global app_screen, menu_status_message

    if event.type == pygame.KEYDOWN:
        if app_screen == "main_menu":
            if event.key in [pygame.K_1, pygame.K_s]:
                start_single_player_mode()
                return True
            if event.key in [pygame.K_2, pygame.K_m]:
                app_screen = "multiplayer_menu"
                menu_status_message = ""
                return True
            if event.key == pygame.K_ESCAPE:
                quit_game()
                return True

        elif app_screen == "multiplayer_menu":
            if event.key in [pygame.K_h, pygame.K_1]:
                open_online_create_screen()
                return True
            if event.key in [pygame.K_g, pygame.K_j, pygame.K_c, pygame.K_2]:
                open_guest_join_screen()
                return True
            if event.key in [pygame.K_ESCAPE, pygame.K_BACKSPACE]:
                app_screen = "main_menu"
                menu_status_message = ""
                return True

        elif app_screen == "disabled_host_lobby":
            if event.key == pygame.K_RETURN:
                app_screen = "playing"
                if game.mode == "intro":
                    game.mode = "game"
                game.message = "Host table is live. Press 1-5 to claim your seat."
                return True
            if event.key in [pygame.K_ESCAPE, pygame.K_BACKSPACE]:
                stop_local_server()
                app_screen = "multiplayer_menu"
                menu_status_message = "Hosting stopped."
                return True

    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        mx, my = get_logical_mouse_pos(event.pos)

        if app_screen == "main_menu":
            single_rect = pygame.Rect(WIDTH // 2 - 260, 300, 520, 70)
            multi_rect = pygame.Rect(WIDTH // 2 - 260, 395, 520, 70)
            quit_rect = pygame.Rect(WIDTH // 2 - 260, 515, 520, 55)
            if single_rect.collidepoint(mx, my):
                start_single_player_mode()
                return True
            if multi_rect.collidepoint(mx, my):
                app_screen = "multiplayer_menu"
                menu_status_message = ""
                return True
            if quit_rect.collidepoint(mx, my):
                quit_game()
                return True

        elif app_screen == "multiplayer_menu":
            host_rect = pygame.Rect(WIDTH // 2 - 260, 285, 520, 70)
            guest_rect = pygame.Rect(WIDTH // 2 - 260, 380, 520, 70)
            back_rect = pygame.Rect(WIDTH // 2 - 260, 500, 520, 55)
            if host_rect.collidepoint(mx, my):
                open_online_create_screen()
                return True
            if guest_rect.collidepoint(mx, my):
                open_guest_join_screen()
                return True
            if back_rect.collidepoint(mx, my):
                app_screen = "main_menu"
                menu_status_message = ""
                return True

        elif app_screen == "disabled_host_lobby":
            start_rect = pygame.Rect(WIDTH // 2 - 260, 430, 520, 70)
            stop_rect = pygame.Rect(WIDTH // 2 - 260, 535, 520, 55)
            if start_rect.collidepoint(mx, my):
                app_screen = "playing"
                if game.mode == "intro":
                    game.mode = "game"
                game.message = "Host table is live. Press 1-5 to claim your seat."
                return True
            if stop_rect.collidepoint(mx, my):
                stop_local_server()
                app_screen = "multiplayer_menu"
                menu_status_message = "Hosting stopped."
                return True

    return False


# =========================
# MAIN LOOP
# =========================

game = BlackjackGame()

while True:
    CLOCK.tick(FPS)

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            quit_game()

        # High-level menus handle their own input before the table controls exist.
        if connect_overlay_active:
            if event.type == pygame.KEYDOWN:
                handle_connect_overlay_event(event)
            continue

        if app_screen != "playing" and app_screen != "intro":
            handle_menu_event(event)
            continue

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                if connect_overlay_active:
                    connect_overlay_active = False
                    continue
                quit_game()
            elif event.key == pygame.K_F11:
                # Toggle fullscreen/windowed
                try:
                    set_screen_mode(not is_fullscreen)
                except Exception:
                    pass
            elif event.key == pygame.K_c:
                game.message = "Use the start menu to connect to the online server before the table starts."
            elif event.key == pygame.K_g:
                game.message = "Use the start menu to connect to the online server before the table starts."

            if connect_overlay_active:
                handle_connect_overlay_event(event)
                continue

        if game.mode == "intro":
            # In online mode, still allow seat claiming even if state says intro.
            if event.type == pygame.KEYDOWN and is_multiplayer_active():
                if event.key in [pygame.K_1, pygame.K_KP1]:
                    network_or_local_join_seat(1)
                elif event.key in [pygame.K_2, pygame.K_KP2]:
                    network_or_local_join_seat(2)
                elif event.key in [pygame.K_3, pygame.K_KP3]:
                    network_or_local_join_seat(3)
                elif event.key in [pygame.K_4, pygame.K_KP4]:
                    network_or_local_join_seat(4)
                elif event.key in [pygame.K_5, pygame.K_KP5]:
                    network_or_local_join_seat(5)

        elif game.mode == "betting":
            if not game.seat_is_bot(game.betting_player):
                confirm_button, skip_button = game.get_betting_overlay_buttons()

                if confirm_button.clicked(event):
                    network_or_local_confirm_bet()

                if skip_button.clicked(event):
                    network_or_local_skip_bet()

            if game.buttons["reset"].clicked(event) and not is_disabled_legacy_remote_client():
                game.reset_money()

            if game.buttons["host"].clicked(event):
                game.message = "Online rooms are created/joined from the start menu."

            if game.buttons["quit"].clicked(event):
                quit_game()

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    network_or_local_confirm_bet()
                elif event.key == pygame.K_k:
                    network_or_local_skip_bet()
                elif event.key == pygame.K_r and not is_disabled_legacy_remote_client():
                    game.reset_money()
                elif event.key == pygame.K_b and is_online_ws_client():
                    online_send({"type": "ADD_BOT"})

                elif event.key == pygame.K_b and is_online_ws_client():
                    online_send({"type": "ADD_BOT"})

                elif event.key == pygame.K_b and not is_disabled_legacy_remote_client():
                    game.add_bot_to_free_seat()
                elif event.key in [pygame.K_1, pygame.K_KP1]:
                    network_or_local_join_seat(1)
                elif event.key in [pygame.K_2, pygame.K_KP2]:
                    network_or_local_join_seat(2)
                elif event.key in [pygame.K_3, pygame.K_KP3]:
                    network_or_local_join_seat(3)
                elif event.key in [pygame.K_4, pygame.K_KP4]:
                    network_or_local_join_seat(4)
                elif event.key in [pygame.K_5, pygame.K_KP5]:
                    network_or_local_join_seat(5)

        else:
            if game.buttons["hit"].clicked(event) and game.can_act() and local_controls_seat(game.current_player):
                network_or_local_hit()

            if game.buttons["stand"].clicked(event) and game.can_act() and local_controls_seat(game.current_player):
                network_or_local_stand()

            if game.buttons["double"].clicked(event) and game.can_double() and local_controls_seat(game.current_player):
                network_or_local_double()

            if game.buttons["split"].clicked(event) and game.can_split() and local_controls_seat(game.current_player):
                network_or_local_split()

            if game.buttons["host"].clicked(event):
                game.message = "Online rooms are created/joined from the start menu."

            if game.buttons["reset"].clicked(event) and not is_disabled_legacy_remote_client():
                game.reset_money()

            if game.buttons["quit"].clicked(event):
                quit_game()

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN and is_online_ws_client():
                    network_or_local_confirm_bet()

                elif event.key == pygame.K_h:
                    network_or_local_hit()

                elif event.key == pygame.K_s:
                    network_or_local_stand()

                elif event.key == pygame.K_d:
                    network_or_local_double()

                elif event.key == pygame.K_p:
                    network_or_local_split()

                elif event.key == pygame.K_r and not is_disabled_legacy_remote_client():
                    game.reset_money()

                elif event.key == pygame.K_b and is_online_ws_client():
                    online_send({"type": "ADD_BOT"})

                elif event.key == pygame.K_b and is_online_ws_client():
                    online_send({"type": "ADD_BOT"})

                elif event.key == pygame.K_b and not is_disabled_legacy_remote_client():
                    game.add_bot_to_free_seat()

                elif event.key in [pygame.K_1, pygame.K_KP1]:
                    network_or_local_join_seat(1)
                elif event.key in [pygame.K_2, pygame.K_KP2]:
                    network_or_local_join_seat(2)
                elif event.key in [pygame.K_3, pygame.K_KP3]:
                    network_or_local_join_seat(3)
                elif event.key in [pygame.K_4, pygame.K_KP4]:
                    network_or_local_join_seat(4)
                elif event.key in [pygame.K_5, pygame.K_KP5]:
                    network_or_local_join_seat(5)

    update_disabled_legacy_socket_messages()
    update_online_messages()

    if not connect_overlay_active:
        if app_screen == "intro":
            game.update_intro()
            music_player.update()
            # The intro method flips game.mode to game when the melody is finished.
            # At that moment we show the clean mode-selection screen instead of the table.
            if game.mode != "intro":
                app_screen = "main_menu"
                game.message = "Choose Single Player or Multiplayer."

        elif app_screen == "playing":
            if is_online_ws_client():
                # Online clients are displays/controllers. The public WebSocket server owns the true deck, turns, money, and results.
                if game.mode == "betting" and my_seat == game.betting_player:
                    game.update_bet_hold()
            elif is_disabled_legacy_remote_client():
                # Legacy local guest path kept only for compatibility with older builds.
                if game.mode == "betting" and my_seat == game.betting_player:
                    game.update_bet_hold()
            else:
                game.update_bet_hold()
                game.update_bot_betting()
                game.update_animations()
                game.update_bot_action()
                game.update_auto_flow()

    # If this app is hosting, execute remote commands then broadcast the authoritative table state.
    if server_running:
        process_host_commands()
        sync_host_game_to_server()

    game.draw()
    draw_connect_overlay()

    if connect_overlay_active:
        if time.time() - connect_cursor_last_blink >= 0.5:
            connect_cursor_visible = not connect_cursor_visible
            connect_cursor_last_blink = time.time()

    scale_fullscreen_surface()
    pygame.display.flip()
    