import socket
import json
import threading
import queue
import random
import sys

# This file contains LAN patch support helpers.
# Import from your game module or paste the relevant logic into blackjack.py.

MAX_PLAYERS = 5
CLIENT_QUEUE = queue.Queue()

class NetworkClient:
    def __init__(self, room_code, host="127.0.0.1", port=5050):
        self.room_code = room_code
        self.host = host
        self.port = port
        self.socket = None
        self.receive_thread = None
        self.connected = False
        self.receive_queue = queue.Queue()
        self._receive_buffer = ""

    def connect(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(5)

        try:
            self.socket.connect((self.host, self.port))
        except Exception as exc:
            print(f"Failed to connect to {self.host}:{self.port}: {exc}")
            return False

        self.connected = True
        self.receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
        self.receive_thread.start()
        self.send({"type": "JOIN", "room_code": self.room_code})
        return True

    def send(self, data):
        try:
            message = json.dumps(data) + "\n"
            self.socket.sendall(message.encode("utf-8"))
        except Exception as exc:
            print(f"Failed to send data: {exc}")
            self.connected = False

    def _receive_loop(self):
        try:
            while self.connected:
                try:
                    data = self.socket.recv(4096)
                except OSError:
                    break

                if not data:
                    break

                self._receive_buffer += data.decode("utf-8")
                while "\n" in self._receive_buffer:
                    line, self._receive_buffer = self._receive_buffer.split("\n", 1)
                    if not line.strip():
                        continue
                    try:
                        parsed = json.loads(line)
                        self.receive_queue.put(parsed)
                    except json.JSONDecodeError:
                        print("Failed to parse JSON from server:", line)

        finally:
            self.connected = False
            try:
                self.socket.close()
            except Exception:
                pass

    def disconnect(self):
        self.connected = False
        if self.receive_thread:
            try:
                self.receive_thread.join(timeout=1)
            except Exception:
                pass
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass

    def poll(self):
        messages = []
        while not self.receive_queue.empty():
            messages.append(self.receive_queue.get())
        return messages


def make_command_join_seat(seat):
    return {"type": "JOIN_SEAT", "seat": seat}


def make_command_leave_seat(seat):
    return {"type": "LEAVE_SEAT", "seat": seat}


def make_command_bet(seat, amount):
    return {"type": "BET", "seat": seat, "amount": amount}


def make_command_action(seat, action):
    return {"type": action.upper(), "seat": seat}


def parse_server_state(message):
    if not isinstance(message, dict):
        return None
    if message.get("type") != "STATE":
        return None
    return message.get("state")


def patch_game_event_loop(game, network_client):
    """
    Example helper for the game loop.
    - `game` should be your BlackjackGame instance.
    - `network_client` should be a NetworkClient instance.
    """
    while game.running:
        for event in game.pygame.event.get():
            if event.type == game.pygame.QUIT:
                game.running = False
            # Add your own event processing as needed.

        for message in network_client.poll():
            state = parse_server_state(message)
            if state is not None:
                print("Received server state:", state)
                # Update your game state from state data here.

        game.update()
        game.draw()
        game.pygame.display.flip()


def get_local_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        local_ip = sock.getsockname()[0]
        sock.close()
        return local_ip
    except Exception:
        return "127.0.0.1"


def launch_test_client(room_code, host="127.0.0.1", port=5050):
    client = NetworkClient(room_code, host=host, port=port)
    if not client.connect():
        print("Could not connect to the server.")
        return None
    print("Connected to server.")
    return client


def print_patch_instructions():
    print("LAN patch helper module loaded.")
    print("Use NetworkClient, make_command_*, and patch_game_event_loop to wire network behavior into Blackjack.")
