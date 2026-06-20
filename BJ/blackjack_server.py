import socket
import threading
import json
import random

HOST = "0.0.0.0"
PORT = 5050
ROOM_CODE = str(random.randint(1000, 9999))
MAX_PLAYERS = 5
STARTING_MONEY = 50000

clients = []
clients_lock = threading.Lock()

# This is a lobby/table scaffold first.
# Later, the full BlackjackGame logic should be moved into this server.
game_state = {
    "room_code": ROOM_CODE,
    "seats": ["empty" for _ in range(MAX_PLAYERS)],
    "money": [0 for _ in range(MAX_PLAYERS)],
    "bets": [0 for _ in range(MAX_PLAYERS)],
    "message": "Waiting for players...",
}


def send_json(conn, data):
    message = json.dumps(data) + "\n"
    conn.sendall(message.encode("utf-8"))


def broadcast(data):
    with clients_lock:
        dead = []
        for conn in clients:
            try:
                send_json(conn, data)
            except Exception:
                dead.append(conn)

        for conn in dead:
            try:
                clients.remove(conn)
            except ValueError:
                pass


def broadcast_state():
    broadcast({
        "type": "STATE",
        "state": game_state,
    })


def handle_command(conn, command):
    command_type = command.get("type")

    if command_type == "JOIN":
        code = command.get("room_code")

        if code != ROOM_CODE:
            send_json(conn, {
                "type": "ERROR",
                "message": "Wrong room code.",
            })
            return

        send_json(conn, {
            "type": "JOIN_OK",
            "message": "Connected to JASLI's Casino.",
        })
        broadcast_state()
        return

    if command_type == "JOIN_SEAT":
        seat = command.get("seat")

        if not isinstance(seat, int) or seat < 0 or seat >= MAX_PLAYERS:
            send_json(conn, {"type": "ERROR", "message": "Invalid seat."})
            return

        if game_state["seats"][seat] != "empty":
            send_json(conn, {"type": "ERROR", "message": "That seat is already taken."})
            return

        game_state["seats"][seat] = "human"
        game_state["money"][seat] = STARTING_MONEY
        game_state["bets"][seat] = 0
        game_state["message"] = f"Player {seat + 1} joined the table."
        broadcast_state()
        return

    if command_type == "LEAVE_SEAT":
        seat = command.get("seat")

        if not isinstance(seat, int) or seat < 0 or seat >= MAX_PLAYERS:
            send_json(conn, {"type": "ERROR", "message": "Invalid seat."})
            return

        amount = game_state["money"][seat]
        game_state["seats"][seat] = "empty"
        game_state["money"][seat] = 0
        game_state["bets"][seat] = 0
        game_state["message"] = f"Player {seat + 1} left with ${amount}."
        broadcast_state()
        return

    if command_type == "BET":
        seat = command.get("seat")
        amount = command.get("amount")

        if not isinstance(seat, int) or seat < 0 or seat >= MAX_PLAYERS:
            send_json(conn, {"type": "ERROR", "message": "Invalid seat."})
            return

        if game_state["seats"][seat] == "empty":
            send_json(conn, {"type": "ERROR", "message": "Seat is empty."})
            return

        if not isinstance(amount, int) or amount <= 0:
            send_json(conn, {"type": "ERROR", "message": "Invalid bet."})
            return

        if amount > game_state["money"][seat]:
            send_json(conn, {"type": "ERROR", "message": "Not enough money."})
            return

        game_state["bets"][seat] = amount
        game_state["message"] = f"Player {seat + 1} bets ${amount}."
        broadcast_state()
        return

    if command_type in ["HIT", "STAND", "DOUBLE", "SPLIT"]:
        seat = command.get("seat")
        if not isinstance(seat, int) or seat < 0 or seat >= MAX_PLAYERS:
            send_json(conn, {"type": "ERROR", "message": "Invalid seat."})
            return

        game_state["message"] = f"Player {seat + 1}: {command_type}."
        broadcast_state()
        return

    send_json(conn, {
        "type": "ERROR",
        "message": f"Unknown command: {command_type}",
    })


def client_thread(conn, addr):
    print(f"Client connected: {addr}")

    with clients_lock:
        clients.append(conn)

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
                    handle_command(conn, command)
                except Exception as exc:
                    send_json(conn, {
                        "type": "ERROR",
                        "message": str(exc),
                    })

    finally:
        print(f"Client disconnected: {addr}")
        with clients_lock:
            try:
                clients.remove(conn)
            except ValueError:
                pass
        try:
            conn.close()
        except Exception:
            pass


def main():
    print("================================")
    print("JASLI's Casino LAN Server")
    print("================================")
    print(f"Room code: {ROOM_CODE}")
    print(f"Port: {PORT}")
    print("Players on the same Wi-Fi can join using this laptop's local IP.")
    print("Find host IP on Windows with: ipconfig")
    print("Look for IPv4 Address, usually like 192.168.x.x")
    print("================================")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen()

    while True:
        conn, addr = server.accept()
        threading.Thread(target=client_thread, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    main()
