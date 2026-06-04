import pygame
import random
import sys
import time
import math

pygame.init()

# -------------------------
# Window setup
# -------------------------

WIDTH, HEIGHT = 700, 850
BOARD_SIZE = 600
CELL_SIZE = BOARD_SIZE // 10

SCREEN = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Snake and Ladder")

FONT = pygame.font.SysFont("arial", 22)
BIG_FONT = pygame.font.SysFont("arial", 34)
TITLE_FONT = pygame.font.SysFont("arial", 46)

BOARD_X = 50
BOARD_Y = 50

clock = pygame.time.Clock()


# -------------------------
# Colours
# -------------------------

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
LIGHT_BLUE = (180, 220, 255)
LIGHT_GREY = (230, 230, 230)
DARK_GREEN = (0, 120, 0)
GREEN = (50, 180, 70)
RED = (200, 40, 40)
BROWN = (150, 90, 40)

PLAYER_COLOURS = [
    (255, 200, 0),
    (255, 80, 80),
    (80, 180, 255),
    (120, 255, 120),
    (200, 120, 255),
    (255, 150, 60),
]


# -------------------------
# Snakes and ladders
# key = start square, value = destination square
# -------------------------

jumps = {
    # Ladders
    3: 22,
    8: 30,
    15: 44,
    28: 55,
    36: 57,
    51: 72,
    66: 87,
    78: 94,

    # Snakes
    24: 6,
    39: 18,
    49: 31,
    64: 42,
    73: 52,
    84: 60,
    92: 70,
    99: 2,
}


# -------------------------
# Game state
# -------------------------

screen_mode = "setup"

setup_stage = "humans"
setup_text = ""
setup_humans = 1

players = []

current_turn = 0
dice_value = 1
displayed_dice_value = 1
message = ""

game_over = False

bot_move_timer = 0
BOT_MOVE_DELAY = 0.35

dice_rolling = False
dice_roll_owner = None
bot_roll_start = 0
BOT_ROLL_TIME = 1.0

# Smooth movement animation
move_animating = False
move_player_index = None
move_path = []
move_path_index = 0
move_progress = 0.0
MOVE_SPEED = 0.08

# Smooth snake/ladder animation
jump_animating = False
jump_player_index = None
jump_start_pos = None
jump_end_pos = None
jump_progress = 0.0
JUMP_SPEED = 0.045


# -------------------------
# Setup helpers
# -------------------------

def create_players(num_humans, num_bots):
    new_players = []

    for i in range(num_humans):
        new_players.append({
            "name": f"Player {i + 1}",
            "type": "human",
            "pos": 1,
            "started": False,
            "colour": PLAYER_COLOURS[len(new_players) % len(PLAYER_COLOURS)],
            "draw_pos": None,
        })

    for i in range(num_bots):
        new_players.append({
            "name": f"Bot {i + 1}",
            "type": "bot",
            "pos": 1,
            "started": False,
            "colour": PLAYER_COLOURS[len(new_players) % len(PLAYER_COLOURS)],
            "draw_pos": None,
        })

    return new_players


def start_game(num_humans, num_bots):
    global players, screen_mode, current_turn
    global dice_value, displayed_dice_value, message
    global game_over, dice_rolling, dice_roll_owner
    global move_animating, jump_animating

    players = create_players(num_humans, num_bots)

    # Random starting player for fairness.
    current_turn = random.randint(0, len(players) - 1)

    dice_value = 1
    displayed_dice_value = 1
    game_over = False
    dice_rolling = False
    dice_roll_owner = None
    move_animating = False
    jump_animating = False

    screen_mode = "game"
    message = f"{players[current_turn]['name']}: roll 6 to start"


# -------------------------
# Board maths
# -------------------------

def get_square_position(square):
    square -= 1

    row = square // 10
    col = square % 10

    if row % 2 == 1:
        col = 9 - col

    x = BOARD_X + col * CELL_SIZE + CELL_SIZE // 2
    y = BOARD_Y + (9 - row) * CELL_SIZE + CELL_SIZE // 2

    return x, y


def lerp(a, b, t):
    return a + (b - a) * t


def smoothstep(t):
    return t * t * (3 - 2 * t)


# -------------------------
# Setup screen
# -------------------------

def draw_setup_screen():
    SCREEN.fill((245, 248, 255))

    title = TITLE_FONT.render("Snake and Ladder", True, BLACK)
    SCREEN.blit(title, (WIDTH // 2 - title.get_width() // 2, 120))

    if setup_stage == "humans":
        prompt = BIG_FONT.render("How many human players?", True, BLACK)
        hint = FONT.render("Type a number, then press ENTER", True, BLACK)
    else:
        prompt = BIG_FONT.render("How many bots?", True, BLACK)
        hint = FONT.render("Type a number, then press ENTER", True, BLACK)

    SCREEN.blit(prompt, (WIDTH // 2 - prompt.get_width() // 2, 260))
    SCREEN.blit(hint, (WIDTH // 2 - hint.get_width() // 2, 315))

    box = pygame.Rect(250, 380, 200, 70)
    pygame.draw.rect(SCREEN, WHITE, box, border_radius=12)
    pygame.draw.rect(SCREEN, BLACK, box, 3, border_radius=12)

    typed = BIG_FONT.render(setup_text, True, BLACK)
    SCREEN.blit(
        typed,
        (
            box.centerx - typed.get_width() // 2,
            box.centery - typed.get_height() // 2,
        )
    )

    note1 = FONT.render("Example: 1 human + 2 bots", True, (80, 80, 80))
    note2 = FONT.render("If humans > 1, bots will automatically be 0.", True, (80, 80, 80))

    SCREEN.blit(note1, (WIDTH // 2 - note1.get_width() // 2, 500))
    SCREEN.blit(note2, (WIDTH // 2 - note2.get_width() // 2, 535))


def handle_setup_key(event):
    global setup_text, setup_stage, setup_humans

    if event.key == pygame.K_BACKSPACE:
        setup_text = setup_text[:-1]

    elif event.key == pygame.K_RETURN:
        if setup_text == "":
            return

        value = int(setup_text)

        if setup_stage == "humans":
            if value < 1:
                value = 1

            setup_humans = value

            if value > 1:
                start_game(value, 0)
            else:
                setup_stage = "bots"
                setup_text = ""

        elif setup_stage == "bots":
            if value < 0:
                value = 0

            start_game(setup_humans, value)

    else:
        if event.unicode.isdigit():
            setup_text += event.unicode


# -------------------------
# Drawing board
# -------------------------

def draw_board():
    for row in range(10):
        for col in range(10):
            x = BOARD_X + col * CELL_SIZE
            y = BOARD_Y + row * CELL_SIZE

            if (row + col) % 2 == 0:
                colour = LIGHT_GREY
            else:
                colour = LIGHT_BLUE

            pygame.draw.rect(SCREEN, colour, (x, y, CELL_SIZE, CELL_SIZE))
            pygame.draw.rect(SCREEN, BLACK, (x, y, CELL_SIZE, CELL_SIZE), 1)

    for square in range(1, 101):
        x, y = get_square_position(square)
        text = FONT.render(str(square), True, BLACK)
        SCREEN.blit(text, (x - 16, y - 16))


def draw_ladder(start, end):
    start_x, start_y = get_square_position(start)
    end_x, end_y = get_square_position(end)

    dx = end_x - start_x
    dy = end_y - start_y
    length = math.hypot(dx, dy)

    if length == 0:
        return

    ux = dx / length
    uy = dy / length

    px = -uy
    py = ux

    width = 16

    left_start = (start_x + px * width, start_y + py * width)
    left_end = (end_x + px * width, end_y + py * width)

    right_start = (start_x - px * width, start_y - py * width)
    right_end = (end_x - px * width, end_y - py * width)

    pygame.draw.line(SCREEN, BROWN, left_start, left_end, 5)
    pygame.draw.line(SCREEN, BROWN, right_start, right_end, 5)

    rung_count = 7

    for i in range(1, rung_count):
        t = i / rung_count

        cx = start_x + dx * t
        cy = start_y + dy * t

        p1 = (cx + px * width, cy + py * width)
        p2 = (cx - px * width, cy - py * width)

        pygame.draw.line(SCREEN, BROWN, p1, p2, 4)


def draw_snake(start, end):
    start_x, start_y = get_square_position(start)
    end_x, end_y = get_square_position(end)

    dx = end_x - start_x
    dy = end_y - start_y

    points = []
    segments = 24
    length = math.hypot(dx, dy)

    for i in range(segments + 1):
        t = i / segments

        x = start_x + dx * t
        y = start_y + dy * t

        wave = math.sin(t * math.pi * 5) * 18

        if length != 0:
            px = -dy / length
            py = dx / length
            x += px * wave
            y += py * wave

        points.append((x, y))

    pygame.draw.lines(SCREEN, GREEN, False, points, 14)
    pygame.draw.lines(SCREEN, DARK_GREEN, False, points, 4)

    pygame.draw.circle(SCREEN, GREEN, (start_x, start_y), 17)
    pygame.draw.circle(SCREEN, DARK_GREEN, (start_x, start_y), 17, 3)

    pygame.draw.circle(SCREEN, BLACK, (start_x - 6, start_y - 4), 3)
    pygame.draw.circle(SCREEN, BLACK, (start_x + 6, start_y - 4), 3)

    pygame.draw.line(SCREEN, RED, (start_x, start_y + 12), (start_x, start_y + 25), 3)
    pygame.draw.line(SCREEN, RED, (start_x, start_y + 25), (start_x - 6, start_y + 31), 2)
    pygame.draw.line(SCREEN, RED, (start_x, start_y + 25), (start_x + 6, start_y + 31), 2)


def draw_snakes_and_ladders():
    for start, end in jumps.items():
        if end > start:
            draw_ladder(start, end)
        else:
            draw_snake(start, end)


def draw_players():
    square_index = {}

    for player in players:
        pos = player["pos"]

        if pos not in square_index:
            square_index[pos] = 0

        index = square_index[pos]
        square_index[pos] += 1

        if player["draw_pos"] is not None:
            x, y = player["draw_pos"]
        else:
            x, y = get_square_position(pos)

        offsets = [
            (-14, -14),
            (14, -14),
            (-14, 14),
            (14, 14),
            (0, 0),
            (0, -22),
        ]

        offset_x, offset_y = offsets[index % len(offsets)]

        # During animation, don't offset too much because it looks weird.
        if player["draw_pos"] is not None:
            offset_x, offset_y = 0, 0

        centre = (int(x + offset_x), int(y + offset_y))

        pygame.draw.circle(SCREEN, player["colour"], centre, 16)
        pygame.draw.circle(SCREEN, BLACK, centre, 16, 2)

        label = FONT.render(str(players.index(player) + 1), True, BLACK)
        SCREEN.blit(
            label,
            (
                centre[0] - label.get_width() // 2,
                centre[1] - label.get_height() // 2,
            )
        )


# -------------------------
# Dice graphics
# -------------------------

def draw_dice_face(x, y, size, value):
    rect = pygame.Rect(x, y, size, size)

    pygame.draw.rect(SCREEN, WHITE, rect, border_radius=14)
    pygame.draw.rect(SCREEN, BLACK, rect, 4, border_radius=14)

    pip_radius = size // 12

    left = x + size * 0.25
    middle = x + size * 0.5
    right = x + size * 0.75

    top = y + size * 0.25
    centre = y + size * 0.5
    bottom = y + size * 0.75

    positions = {
        1: [(middle, centre)],
        2: [(left, top), (right, bottom)],
        3: [(left, top), (middle, centre), (right, bottom)],
        4: [(left, top), (right, top), (left, bottom), (right, bottom)],
        5: [(left, top), (right, top), (middle, centre), (left, bottom), (right, bottom)],
        6: [(left, top), (right, top), (left, centre), (right, centre), (left, bottom), (right, bottom)],
    }

    for px, py in positions[value]:
        pygame.draw.circle(SCREEN, BLACK, (int(px), int(py)), pip_radius)


def draw_ui():
    current_player = players[current_turn]

    pygame.draw.rect(SCREEN, (245, 245, 245), (0, 650, WIDTH, 200))

    turn_text = BIG_FONT.render(
        f"Turn: {current_player['name']}",
        True,
        BLACK
    )
    SCREEN.blit(turn_text, (50, 665))

    status = "Started" if current_player["started"] else "Need 6 to start"
    status_text = FONT.render(status, True, (80, 80, 80))
    SCREEN.blit(status_text, (50, 710))

    msg_text = FONT.render(message, True, BLACK)
    SCREEN.blit(msg_text, (50, 760))

    controls = FONT.render(
        "SPACE = start dice (auto stop 1s) | movement is automatic | R = restart",
        True,
        (80, 80, 80)
    )
    SCREEN.blit(controls, (50, 805))

    draw_dice_face(520, 675, 90, displayed_dice_value)

    dice_label = FONT.render("Dice", True, BLACK)
    SCREEN.blit(dice_label, (545, 770))


# -------------------------
# Game logic
# -------------------------

def next_turn():
    global current_turn, message

    current_turn = (current_turn + 1) % len(players)
    current_player = players[current_turn]

    if current_player["type"] == "human":
        if current_player["started"]:
            message = f"{current_player['name']}: press SPACE to roll"
        else:
            message = f"{current_player['name']}: roll 6 to start"
    else:
        message = f"{current_player['name']} is thinking..."


def check_win(player):
    global game_over, message

    if player["pos"] == 100:
        game_over = True
        message = f"{player['name']} wins! Press R to restart."
        return True

    return False


def start_dice_roll():
    global dice_rolling, dice_roll_owner, message, bot_roll_start

    if game_over or dice_rolling or move_animating or jump_animating:
        return

    player = players[current_turn]

    dice_rolling = True
    dice_roll_owner = current_turn
    bot_roll_start = time.time()

    if player["type"] == "human":
        message = f"{player['name']} is rolling..."
    else:
        message = f"{player['name']} is rolling..."


def stop_dice_roll():
    global dice_rolling, dice_roll_owner
    global dice_value, displayed_dice_value
    global message

    if not dice_rolling:
        return

    if dice_roll_owner != current_turn:
        return

    player = players[current_turn]

    dice_value = random.randint(1, 6)
    displayed_dice_value = dice_value

    dice_rolling = False
    dice_roll_owner = None

    # Must roll 6 to start.
    if not player["started"]:
        if dice_value == 6:
            player["started"] = True
            message = f"{player['name']} rolled 6 and started!"
            start_smooth_move(current_turn, 6)
        else:
            message = f"{player['name']} rolled {dice_value}. Need 6 to start."
            next_turn()

        return

    new_pos = player["pos"] + dice_value

    if new_pos > 100:
        distance = 100 - player["pos"]
        message = f"{player['name']} rolled {dice_value}. Need {distance}. No move!"

        if dice_value == 6:
            message += " Bonus roll!"
        else:
            next_turn()

        return

    message = f"{player['name']} rolled {dice_value}."
    start_smooth_move(current_turn, dice_value)


def start_smooth_move(player_index, steps):
    global move_animating, move_player_index, move_path
    global move_path_index, move_progress

    player = players[player_index]

    move_path = []

    start_square = player["pos"]

    for i in range(1, steps + 1):
        move_path.append(start_square + i)

    if not move_path:
        return

    move_animating = True
    move_player_index = player_index
    move_path_index = 0
    move_progress = 0.0

    player["draw_pos"] = get_square_position(player["pos"])


def update_smooth_move():
    global move_animating, move_player_index, move_path
    global move_path_index, move_progress, message

    if not move_animating:
        return

    player = players[move_player_index]

    if move_path_index >= len(move_path):
        finish_smooth_move()
        return

    current_square = player["pos"]
    target_square = move_path[move_path_index]

    start_x, start_y = get_square_position(current_square)
    end_x, end_y = get_square_position(target_square)

    move_progress += MOVE_SPEED
    t = min(1.0, move_progress)
    t = smoothstep(t)

    x = lerp(start_x, end_x, t)
    y = lerp(start_y, end_y, t)

    player["draw_pos"] = (x, y)

    if move_progress >= 1.0:
        player["pos"] = target_square
        player["draw_pos"] = get_square_position(player["pos"])

        move_path_index += 1
        move_progress = 0.0

        if move_path_index >= len(move_path):
            finish_smooth_move()


def finish_smooth_move():
    global move_animating, move_player_index, move_path
    global move_path_index, move_progress, message

    if move_player_index is None:
        return

    player = players[move_player_index]
    player["draw_pos"] = None

    move_animating = False
    move_player_index = None
    move_path = []
    move_path_index = 0
    move_progress = 0.0

    # Snakes and ladders only trigger after final dice move.
    if player["pos"] in jumps:
        old_pos = player["pos"]
        new_pos = jumps[player["pos"]]

        if new_pos > old_pos:
            message = f"{player['name']} landed on a ladder: {old_pos} -> {new_pos}"
        else:
            message = f"{player['name']} landed on a snake: {old_pos} -> {new_pos}"

        start_jump_animation(players.index(player), old_pos, new_pos)
        return

    finish_turn_after_all_movement(player)


def start_jump_animation(player_index, old_square, new_square):
    global jump_animating, jump_player_index
    global jump_start_pos, jump_end_pos, jump_progress

    player = players[player_index]

    jump_animating = True
    jump_player_index = player_index
    jump_start_pos = get_square_position(old_square)
    jump_end_pos = get_square_position(new_square)
    jump_progress = 0.0

    player["draw_pos"] = jump_start_pos
    player["pos"] = new_square


def update_jump_animation():
    global jump_animating, jump_player_index
    global jump_start_pos, jump_end_pos, jump_progress

    if not jump_animating:
        return

    player = players[jump_player_index]

    jump_progress += JUMP_SPEED
    t = min(1.0, jump_progress)
    t = smoothstep(t)

    x = lerp(jump_start_pos[0], jump_end_pos[0], t)
    y = lerp(jump_start_pos[1], jump_end_pos[1], t)

    player["draw_pos"] = (x, y)

    if jump_progress >= 1.0:
        player["draw_pos"] = None

        finished_player = player

        jump_animating = False
        jump_player_index = None
        jump_start_pos = None
        jump_end_pos = None
        jump_progress = 0.0

        finish_turn_after_all_movement(finished_player)


def finish_turn_after_all_movement(player):
    global message

    if check_win(player):
        return

    # Bonus roll rule.
    if dice_value == 6:
        if player["type"] == "human":
            message += f" {player['name']} rolled 6! Roll again."
        else:
            message += f" {player['name']} rolled 6 and gets another turn."
    else:
        next_turn()


def restart_game():
    global current_turn, dice_value, displayed_dice_value, message
    global game_over, dice_rolling, dice_roll_owner
    global move_animating, move_player_index, move_path, move_path_index, move_progress
    global jump_animating, jump_player_index, jump_start_pos, jump_end_pos, jump_progress

    for player in players:
        player["pos"] = 1
        player["started"] = False
        player["draw_pos"] = None

    current_turn = random.randint(0, len(players) - 1)
    dice_value = 1
    displayed_dice_value = 1

    game_over = False
    dice_rolling = False
    dice_roll_owner = None

    move_animating = False
    move_player_index = None
    move_path = []
    move_path_index = 0
    move_progress = 0.0

    jump_animating = False
    jump_player_index = None
    jump_start_pos = None
    jump_end_pos = None
    jump_progress = 0.0

    message = f"{players[current_turn]['name']}: roll 6 to start"


# -------------------------
# Main loop
# -------------------------

while True:
    SCREEN.fill(WHITE)

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            pygame.quit()
            sys.exit()

        if event.type == pygame.KEYDOWN:
            if screen_mode == "setup":
                handle_setup_key(event)

            elif screen_mode == "game":
                current_player = players[current_turn]

                if event.key == pygame.K_r:
                    restart_game()

                if not game_over:
                    if current_player["type"] == "human":
                            if event.key == pygame.K_SPACE:
                                # Single press to start dice rolling; auto-stops after BOT_ROLL_TIME
                                if not dice_rolling and not move_animating and not jump_animating:
                                    start_dice_roll()

    if screen_mode == "setup":
        draw_setup_screen()

    elif screen_mode == "game":
        current_player = players[current_turn]

        if dice_rolling:
            displayed_dice_value = random.randint(1, 6)
            # Auto-stop rolling after BOT_ROLL_TIME seconds
            if time.time() - bot_roll_start >= BOT_ROLL_TIME:
                stop_dice_roll()

        update_smooth_move()
        update_jump_animation()

        if not game_over and current_player["type"] == "bot":
            now = time.time()

            if not dice_rolling and not move_animating and not jump_animating:
                if now - bot_move_timer > BOT_MOVE_DELAY:
                    start_dice_roll()
                    bot_move_timer = now

            elif dice_rolling:
                if now - bot_roll_start > BOT_ROLL_TIME:
                    stop_dice_roll()

        draw_board()
        draw_snakes_and_ladders()
        draw_players()
        draw_ui()

    pygame.display.update()
    clock.tick(60)