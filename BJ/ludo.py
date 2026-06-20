import pygame
import sys
import random
import math
import time

pygame.init()

# =========================
# CONFIG
# =========================

WIDTH, HEIGHT = 1100, 900
FPS = 60

SCREEN = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Ludo in Pygame")
CLOCK = pygame.time.Clock()

WHITE = (245, 245, 245)
BLACK = (20, 20, 20)
GRAY = (180, 180, 180)
DARK_GRAY = (90, 90, 90)

RED = (220, 60, 60)
GREEN = (60, 170, 90)
YELLOW = (235, 200, 60)
BLUE = (70, 110, 220)

BOARD_BG = (250, 250, 250)
SAFE_COLOR = (210, 210, 255)

FONT = pygame.font.SysFont("arial", 24)
SMALL_FONT = pygame.font.SysFont("arial", 18)
BIG_FONT = pygame.font.SysFont("arial", 34, bold=True)
TITLE_FONT = pygame.font.SysFont("arial", 48, bold=True)

CELL = 50
BOARD_ORIGIN_X = 50
BOARD_ORIGIN_Y = 75

PANEL_X = 840
PANEL_Y = 50
PANEL_W = 230
PANEL_H = 760

ALL_PLAYERS = ["red", "green", "yellow", "blue"]
PLAYERS = []
PLAYER_TYPES = {}

PLAYER_COLORS = {
    "red": RED,
    "green": GREEN,
    "yellow": YELLOW,
    "blue": BLUE,
}

# =========================
# BOARD PATH SETUP
# =========================

MAIN_PATH = [
    (6, 13), (6, 12), (6, 11), (6, 10), (6, 9), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8),
    (0, 7), (0, 6),
    (1, 6), (2, 6), (3, 6), (4, 6), (5, 6), (6, 5), (6, 4), (6, 3), (6, 2), (6, 1), (6, 0),
    (7, 0), (8, 0),
    (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (9, 6), (10, 6), (11, 6), (12, 6), (13, 6), (14, 6),
    (14, 7), (14, 8),
    (13, 8), (12, 8), (11, 8), (10, 8), (9, 8), (8, 9), (8, 10), (8, 11), (8, 12), (8, 13), (8, 14),
    (7, 14), (6, 14)
]

START_INDEX = {
    "red": 0,
    "green": 13,
    "yellow": 26,
    "blue": 39,
}

HOME_LANES = {
    "red":    [(7, 13), (7, 12), (7, 11), (7, 10), (7, 9), (7, 8)],
    "green":  [(1, 7), (2, 7), (3, 7), (4, 7), (5, 7), (6, 7)],
    "yellow": [(7, 1), (7, 2), (7, 3), (7, 4), (7, 5), (7, 6)],
    "blue":   [(13, 7), (12, 7), (11, 7), (10, 7), (9, 7), (8, 7)],
}

SAFE_INDICES = {0, 8, 13, 21, 26, 34, 39, 47}

HOME_POSITIONS = {
    "red":    [(1.5, 10.5), (3.5, 10.5), (1.5, 12.5), (3.5, 12.5)],
    "green":  [(1.5, 1.5), (3.5, 1.5), (1.5, 3.5), (3.5, 3.5)],
    "yellow": [(10.5, 1.5), (12.5, 1.5), (10.5, 3.5), (12.5, 3.5)],
    "blue":   [(10.5, 10.5), (12.5, 10.5), (10.5, 12.5), (12.5, 12.5)],
}

# =========================
# SCREEN STATE
# =========================

screen_mode = "setup"
setup_stage = "humans"
setup_text = ""
setup_humans = 1

game = None


# =========================
# CLASSES
# =========================

class Token:
    def __init__(self, color, token_id):
        self.color = color
        self.token_id = token_id
        self.steps = -1
        # -1 = home
        # 0..51 = main path
        # 52..57 = home lane
        # 57 = finished

    def is_home(self):
        return self.steps == -1

    def is_finished(self):
        return self.steps == 57


class Game:
    def __init__(self):
        self.tokens = {
            color: [Token(color, i) for i in range(4)]
            for color in PLAYERS
        }

        self.current_player_idx = 0

        self.dice_value = 1
        self.displayed_dice_value = 1

        self.dice_rolling = False
        self.roll_owner = None
        self.bot_roll_start = 0
        self.bot_action_timer = 0

        self.rolled = False
        self.selected_token = None
        self.pending_steps = 0
        self.moving_token = False

        self.delay_next_turn = False
        self.delay_start_time = 0
        self.delay_duration = 1.0

        self.message = f"{self.current_player.upper()}'s turn - Press SPACE to start rolling"
        self.winner = None

    @property
    def current_player(self):
        return PLAYERS[self.current_player_idx]

    @property
    def current_player_type(self):
        return PLAYER_TYPES[self.current_player]

    def next_turn(self):
        self.current_player_idx = (self.current_player_idx + 1) % len(PLAYERS)

        self.rolled = False
        self.dice_rolling = False
        self.roll_owner = None
        self.selected_token = None
        self.pending_steps = 0
        self.moving_token = False
        self.delay_next_turn = False

        if self.current_player_type == "human":
            self.message = f"{self.current_player.upper()}'s turn - Press SPACE to start rolling"
        else:
            self.message = f"{self.current_player.upper()} is thinking..."

    def schedule_next_turn(self, delay=1.0):
        self.delay_next_turn = True
        self.delay_start_time = time.time()
        self.delay_duration = delay

    def update_delayed_turn(self):
        if self.delay_next_turn:
            if time.time() - self.delay_start_time >= self.delay_duration:
                self.delay_next_turn = False
                self.next_turn()

    def start_dice_roll(self):
        if self.winner is not None:
            return

        if self.dice_rolling or self.rolled or self.moving_token or self.delay_next_turn:
            return

        self.dice_rolling = True
        self.roll_owner = self.current_player
        self.bot_roll_start = time.time()

        if self.current_player_type == "human":
            self.message = f"{self.current_player.upper()} is rolling..."
        else:
            self.message = f"{self.current_player.upper()} is rolling..."

    def stop_dice_roll(self):
        if not self.dice_rolling:
            return

        if self.roll_owner != self.current_player:
            return

        # Uniform dice roll: each value 1-6 has equal probability.
        self.dice_value = random.randint(1, 6)
        self.displayed_dice_value = self.dice_value

        self.dice_rolling = False
        self.roll_owner = None
        self.rolled = True

        movable = self.get_movable_tokens(self.current_player, self.dice_value)

        if not movable:
            if self.dice_value == 6:
                self.message = f"{self.current_player.upper()} rolled 6 but has no move. Roll again."
                self.rolled = False
            else:
                self.message = f"{self.current_player.upper()} rolled {self.dice_value}. No valid move."
                self.schedule_next_turn(1.0)
            return

        # If only one token can move, auto-select it.
        if len(movable) == 1:
            self.message = f"{self.current_player.upper()} rolled {self.dice_value}. Auto-selected token."
            self.select_token_for_movement(movable[0])
            return

        if self.current_player_type == "human":
            self.message = f"{self.current_player.upper()} rolled {self.dice_value}. Click a token."
        else:
            self.message = f"{self.current_player.upper()} rolled {self.dice_value}."

    def get_movable_tokens(self, color, roll):
        movable = []

        for token in self.tokens[color]:
            if self.is_move_valid(token, roll):
                movable.append(token)

        return movable

    def is_move_valid(self, token, roll):
        if token.is_finished():
            return False

        if token.is_home():
            return roll == 6

        new_steps = token.steps + roll
        return new_steps <= 57

    def select_token_for_movement(self, token):
        if not self.rolled:
            return

        if token.color != self.current_player:
            return

        if not self.is_move_valid(token, self.dice_value):
            return

        self.selected_token = token
        self.moving_token = True

        if token.is_home():
            # Rolling 6 brings a token out to the start square.
            self.pending_steps = 1
        else:
            self.pending_steps = self.dice_value

        required = get_required_arrow_for_token(token)
        self.message = f"{token.color.upper()} token selected. Moving automatically..."
        # start the automatic movement timer (used for both bots and humans)
        self.bot_action_timer = time.time()

    def move_selected_token_one_step(self):
        if self.selected_token is None:
            return

        token = self.selected_token

        if self.pending_steps <= 0:
            return

        if token.is_home():
            token.steps = 0
        else:
            token.steps += 1

        self.pending_steps -= 1

        if self.pending_steps > 0:
            required = get_required_arrow_for_token(token)
            self.message = f"Keep moving. Press {arrow_name(required)}. Steps left: {self.pending_steps}"
        else:
            self.finish_selected_token_move()

    def finish_selected_token_move(self):
        token = self.selected_token

        self.selected_token = None
        self.pending_steps = 0
        self.moving_token = False

        if not token.is_finished():
            self.handle_captures(token)

        if self.check_win(token.color):
            self.winner = token.color
            self.message = f"{token.color.upper()} wins! Press R to restart."
            return

        if self.dice_value == 6:
            self.rolled = False
            self.message = f"{self.current_player.upper()} rolled 6 - press SPACE to roll again"
        else:
            self.next_turn()

    def handle_captures(self, moved_token):
        if moved_token.steps > 51:
            return

        moved_main_index = (START_INDEX[moved_token.color] + moved_token.steps) % 52

        if moved_main_index in SAFE_INDICES:
            return

        moved_pos = MAIN_PATH[moved_main_index]

        captured_anyone = False

        for color in PLAYERS:
            if color == moved_token.color:
                continue

            for token in self.tokens[color]:
                if token.steps < 0 or token.steps > 51:
                    continue

                other_main_index = (START_INDEX[color] + token.steps) % 52
                other_pos = MAIN_PATH[other_main_index]

                if other_pos == moved_pos:
                    token.steps = -1
                    captured_anyone = True
                    self.message = f"{moved_token.color.upper()} captured {color.upper()}!"

        return captured_anyone

    def check_win(self, color):
        return all(token.is_finished() for token in self.tokens[color])

    # =========================
    # AGGRESSIVE BOT LOGIC
    # =========================

    def get_token_final_steps_after_roll(self, token, roll):
        if token.is_home():
            if roll == 6:
                return 0
            return token.steps

        return token.steps + roll

    def would_capture_with_move(self, token, roll):
        final_steps = self.get_token_final_steps_after_roll(token, roll)

        # Cannot capture from home lane or finished area.
        if final_steps < 0 or final_steps > 51:
            return False

        final_main_index = (START_INDEX[token.color] + final_steps) % 52

        # Safe cells cannot be captured on.
        if final_main_index in SAFE_INDICES:
            return False

        final_pos = MAIN_PATH[final_main_index]

        for color in PLAYERS:
            if color == token.color:
                continue

            for enemy in self.tokens[color]:
                if enemy.steps < 0 or enemy.steps > 51:
                    continue

                enemy_main_index = (START_INDEX[color] + enemy.steps) % 52
                enemy_pos = MAIN_PATH[enemy_main_index]

                if enemy_pos == final_pos:
                    return True

        return False

    def choose_best_bot_token(self):
        movable = self.get_movable_tokens(self.current_player, self.dice_value)

        if not movable:
            return None

        # 1. First priority: capture enemy token.
        capture_moves = [
            token for token in movable
            if self.would_capture_with_move(token, self.dice_value)
        ]

        if capture_moves:
            # If multiple captures exist, pick the most advanced token.
            return max(capture_moves, key=lambda t: t.steps)

        # 2. Second priority: move the token furthest along.
        active_tokens = [token for token in movable if not token.is_home()]

        if active_tokens:
            return max(active_tokens, key=lambda t: t.steps)

        # 3. Otherwise, bring a token out from home.
        return movable[0]


# =========================
# SETUP LOGIC
# =========================

def start_game(num_humans, num_bots):
    global PLAYERS, PLAYER_TYPES, game, screen_mode

    total = num_humans + num_bots
    total = max(1, min(4, total))

    PLAYERS = ALL_PLAYERS[:total]
    PLAYER_TYPES = {}

    for i, color in enumerate(PLAYERS):
        if i < num_humans:
            PLAYER_TYPES[color] = "human"
        else:
            PLAYER_TYPES[color] = "bot"

    game = Game()
    screen_mode = "game"


def handle_setup_key(event):
    global setup_text, setup_stage, setup_humans

    if event.key == pygame.K_BACKSPACE:
        setup_text = setup_text[:-1]

    elif event.key == pygame.K_RETURN:
        if setup_text == "":
            return

        value = int(setup_text)

        if setup_stage == "humans":
            value = max(1, min(4, value))
            setup_humans = value

            if setup_humans == 4:
                start_game(4, 0)
            else:
                setup_stage = "bots"
                setup_text = ""

        elif setup_stage == "bots":
            max_bots = 4 - setup_humans
            value = max(0, min(max_bots, value))
            start_game(setup_humans, value)

    else:
        if event.unicode.isdigit():
            setup_text += event.unicode


def draw_setup_screen():
    SCREEN.fill((245, 248, 255))

    title = TITLE_FONT.render("Ludo", True, BLACK)
    SCREEN.blit(title, (WIDTH // 2 - title.get_width() // 2, 120))

    subtitle = FONT.render("Pygame Edition", True, DARK_GRAY)
    SCREEN.blit(subtitle, (WIDTH // 2 - subtitle.get_width() // 2, 175))

    if setup_stage == "humans":
        prompt = BIG_FONT.render("How many human players?", True, BLACK)
        hint = FONT.render("Enter 1 to 4, then press ENTER", True, DARK_GRAY)
    else:
        max_bots = 4 - setup_humans
        prompt = BIG_FONT.render("How many bots?", True, BLACK)
        hint = FONT.render(f"Enter 0 to {max_bots}, then press ENTER", True, DARK_GRAY)

    SCREEN.blit(prompt, (WIDTH // 2 - prompt.get_width() // 2, 280))
    SCREEN.blit(hint, (WIDTH // 2 - hint.get_width() // 2, 330))

    box = pygame.Rect(WIDTH // 2 - 100, 390, 200, 80)
    pygame.draw.rect(SCREEN, WHITE, box, border_radius=14)
    pygame.draw.rect(SCREEN, BLACK, box, 3, border_radius=14)

    typed = BIG_FONT.render(setup_text, True, BLACK)
    SCREEN.blit(
        typed,
        (
            box.centerx - typed.get_width() // 2,
            box.centery - typed.get_height() // 2
        )
    )

    notes = [
        "Example: 1 human + 3 bots",
        "Colours are assigned in this order:",
        "RED, GREEN, YELLOW, BLUE"
    ]

    y = 540
    for line in notes:
        surf = FONT.render(line, True, DARK_GRAY)
        SCREEN.blit(surf, (WIDTH // 2 - surf.get_width() // 2, y))
        y += 35


# =========================
# BOARD HELPERS
# =========================

def board_to_pixel(col, row):
    x = BOARD_ORIGIN_X + col * CELL
    y = BOARD_ORIGIN_Y + row * CELL
    return x, y


def board_center(col, row):
    x, y = board_to_pixel(col, row)
    return x + CELL // 2, y + CELL // 2


def home_position_center(color, token_id):
    colf, rowf = HOME_POSITIONS[color][token_id]
    x = BOARD_ORIGIN_X + round(colf * CELL)
    y = BOARD_ORIGIN_Y + round(rowf * CELL)
    return x, y


def fill_cell(col, row, color):
    x, y = board_to_pixel(col, row)
    pygame.draw.rect(SCREEN, color, (x, y, CELL, CELL))
    pygame.draw.rect(SCREEN, BLACK, (x, y, CELL, CELL), 1)


def get_token_grid_position(token):
    if token.is_home():
        return None

    if token.is_finished():
        return HOME_LANES[token.color][-1]

    if token.steps <= 51:
        idx = (START_INDEX[token.color] + token.steps) % 52
        return MAIN_PATH[idx]

    lane_index = token.steps - 52
    return HOME_LANES[token.color][lane_index]


def get_token_draw_position(token):
    if token.is_home():
        return home_position_center(token.color, token.token_id)

    col, row = get_token_grid_position(token)
    return board_center(col, row)


def get_next_token_draw_position(token):
    if token.is_home():
        col, row = MAIN_PATH[START_INDEX[token.color]]
        return board_center(col, row)

    next_steps = token.steps + 1

    if next_steps <= 51:
        idx = (START_INDEX[token.color] + next_steps) % 52
        col, row = MAIN_PATH[idx]
        return board_center(col, row)

    if next_steps <= 57:
        lane_index = next_steps - 52
        col, row = HOME_LANES[token.color][lane_index]
        return board_center(col, row)

    return get_token_draw_position(token)


def get_required_arrow_for_token(token):
    x1, y1 = get_token_draw_position(token)
    x2, y2 = get_next_token_draw_position(token)

    dx = x2 - x1
    dy = y2 - y1

    if abs(dx) >= abs(dy):
        if dx > 0:
            return pygame.K_RIGHT
        else:
            return pygame.K_LEFT
    else:
        if dy > 0:
            return pygame.K_DOWN
        else:
            return pygame.K_UP


def arrow_name(key):
    if key == pygame.K_RIGHT:
        return "RIGHT"
    if key == pygame.K_LEFT:
        return "LEFT"
    if key == pygame.K_UP:
        return "UP"
    if key == pygame.K_DOWN:
        return "DOWN"
    return "UNKNOWN"


# =========================
# DRAW BOARD
# =========================

def draw_grid():
    for r in range(15):
        for c in range(15):
            x, y = board_to_pixel(c, r)
            pygame.draw.rect(SCREEN, WHITE, (x, y, CELL, CELL))
            pygame.draw.rect(SCREEN, GRAY, (x, y, CELL, CELL), 1)


def draw_home_areas():
    areas = {
        "green":  (0, 0, (210, 245, 220)),
        "yellow": (9, 0, (255, 245, 200)),
        "red":    (0, 9, (255, 220, 220)),
        "blue":   (9, 9, (220, 230, 255)),
    }

    for color, (col, row, area_color) in areas.items():
        x, y = board_to_pixel(col, row)

        pygame.draw.rect(SCREEN, area_color, (x, y, CELL * 6, CELL * 6))
        pygame.draw.rect(SCREEN, BLACK, (x, y, CELL * 6, CELL * 6), 3)

        inner = pygame.Rect(x + CELL, y + CELL, CELL * 4, CELL * 4)
        pygame.draw.rect(SCREEN, WHITE, inner, border_radius=18)
        pygame.draw.rect(SCREEN, BLACK, inner, 2, border_radius=18)

        for i in range(4):
            px, py = home_position_center(color, i)
            pygame.draw.circle(SCREEN, area_color, (px, py), 26)
            pygame.draw.circle(SCREEN, BLACK, (px, py), 26, 2)


def draw_cross_paths():
    for r in range(15):
        if r < 6 or r > 8:
            fill_cell(6, r, WHITE)
            fill_cell(8, r, WHITE)
        fill_cell(7, r, WHITE)

    for c in range(15):
        if c < 6 or c > 8:
            fill_cell(c, 6, WHITE)
            fill_cell(c, 8, WHITE)
        fill_cell(c, 7, WHITE)


def draw_home_lanes():
    for pos in HOME_LANES["red"]:
        fill_cell(pos[0], pos[1], (255, 180, 180))

    for pos in HOME_LANES["green"]:
        fill_cell(pos[0], pos[1], (180, 240, 190))

    for pos in HOME_LANES["yellow"]:
        fill_cell(pos[0], pos[1], (255, 235, 150))

    for pos in HOME_LANES["blue"]:
        fill_cell(pos[0], pos[1], (180, 205, 255))


def draw_center_triangle():
    cx, cy = board_to_pixel(6, 6)

    points_red = [
        (cx, cy + CELL * 3),
        (cx + CELL * 3, cy + CELL * 3),
        (cx + CELL * 1.5, cy + CELL * 1.5)
    ]

    points_green = [
        (cx, cy),
        (cx + CELL * 3, cy),
        (cx + CELL * 1.5, cy + CELL * 1.5)
    ]

    points_yellow = [
        (cx + CELL * 3, cy),
        (cx + CELL * 3, cy + CELL * 3),
        (cx + CELL * 1.5, cy + CELL * 1.5)
    ]

    points_blue = [
        (cx, cy),
        (cx, cy + CELL * 3),
        (cx + CELL * 1.5, cy + CELL * 1.5)
    ]

    pygame.draw.polygon(SCREEN, GREEN, points_green)
    pygame.draw.polygon(SCREEN, YELLOW, points_yellow)
    pygame.draw.polygon(SCREEN, RED, points_red)
    pygame.draw.polygon(SCREEN, BLUE, points_blue)

    pygame.draw.rect(SCREEN, BLACK, (cx, cy, CELL * 3, CELL * 3), 2)


def draw_safe_cells():
    for idx in SAFE_INDICES:
        col, row = MAIN_PATH[idx]
        x, y = board_to_pixel(col, row)

        pygame.draw.circle(SCREEN, SAFE_COLOR, (x + CELL // 2, y + CELL // 2), 13)
        pygame.draw.circle(SCREEN, BLACK, (x + CELL // 2, y + CELL // 2), 13, 1)


def get_token_stack_key(token):
    if token.is_home():
        return ("home", token.color, token.token_id)

    col, row = get_token_grid_position(token)
    return ("board", col, row)


def draw_tokens():
    token_positions = {}

    for color in PLAYERS:
        for token in game.tokens[color]:
            key = get_token_stack_key(token)
            token_positions.setdefault(key, []).append(token)

    movable = []
    if game.rolled and game.dice_value is not None and not game.moving_token:
        movable = game.get_movable_tokens(game.current_player, game.dice_value)

    for key, tokens_here in token_positions.items():
        if key[0] == "home":
            _, color, token_id = key
            base_x, base_y = home_position_center(color, token_id)
        else:
            _, col, row = key
            base_x, base_y = board_center(col, row)

        n = len(tokens_here)

        # Very tight offsets so units remain visually centered.
        if n == 1:
            offsets = [(0, 0)]
        elif n == 2:
            offsets = [(-4, 0), (4, 0)]
        elif n == 3:
            offsets = [(-4, -3), (4, -3), (0, 4)]
        else:
            offsets = [(-4, -4), (4, -4), (-4, 4), (4, 4)]

        for token, (ox, oy) in zip(tokens_here, offsets):
            color = PLAYER_COLORS[token.color]
            centre = (base_x + ox, base_y + oy)

            if game.selected_token is token:
                pygame.draw.circle(SCREEN, WHITE, centre, 23)
                pygame.draw.circle(SCREEN, BLACK, centre, 23, 2)

            pygame.draw.circle(SCREEN, BLACK, centre, 16)
            pygame.draw.circle(SCREEN, color, centre, 13)

            # Draw token number
            try:
                num_surf = SMALL_FONT.render(str(token.token_id + 1), True, BLACK)
                SCREEN.blit(num_surf, (centre[0] - num_surf.get_width() // 2, centre[1] - num_surf.get_height() // 2))
            except Exception:
                pass


# =========================
# DICE + UI
# =========================

def draw_dice_face(rect, value):
    pygame.draw.rect(SCREEN, WHITE, rect, border_radius=14)
    pygame.draw.rect(SCREEN, BLACK, rect, 4, border_radius=14)

    cx, cy = rect.center

    left = rect.left + 30
    right = rect.right - 30
    top = rect.top + 30
    bottom = rect.bottom - 30

    positions = {
        "tl": (left, top),
        "tr": (right, top),
        "ml": (left, cy),
        "mr": (right, cy),
        "bl": (left, bottom),
        "br": (right, bottom),
        "c": (cx, cy)
    }

    pip_map = {
        1: ["c"],
        2: ["tl", "br"],
        3: ["tl", "c", "br"],
        4: ["tl", "tr", "bl", "br"],
        5: ["tl", "tr", "c", "bl", "br"],
        6: ["tl", "tr", "ml", "mr", "bl", "br"]
    }

    for key in pip_map[value]:
        pygame.draw.circle(SCREEN, BLACK, positions[key], 8)


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


def draw_ui():
    pygame.draw.rect(SCREEN, (245, 245, 248), (PANEL_X, PANEL_Y, PANEL_W, PANEL_H), border_radius=18)
    pygame.draw.rect(SCREEN, BLACK, (PANEL_X, PANEL_Y, PANEL_W, PANEL_H), 2, border_radius=18)

    title = BIG_FONT.render("LUDO", True, BLACK)
    SCREEN.blit(title, (PANEL_X + PANEL_W // 2 - title.get_width() // 2, PANEL_Y + 25))

    dice_rect = pygame.Rect(PANEL_X + 55, PANEL_Y + 95, 120, 120)
    draw_dice_face(dice_rect, game.displayed_dice_value)

    if game.dice_rolling:
        roll_text = SMALL_FONT.render("Rolling...", True, DARK_GRAY)
    else:
        roll_text = SMALL_FONT.render("Dice", True, DARK_GRAY)

    SCREEN.blit(roll_text, (dice_rect.centerx - roll_text.get_width() // 2, dice_rect.bottom + 10))

    info = FONT.render("Current Turn", True, BLACK)
    SCREEN.blit(info, (PANEL_X + PANEL_W // 2 - info.get_width() // 2, 320))

    turn_text = BIG_FONT.render(
        game.current_player.upper(),
        True,
        PLAYER_COLORS[game.current_player]
    )
    SCREEN.blit(turn_text, (PANEL_X + PANEL_W // 2 - turn_text.get_width() // 2, 355))

    type_text = SMALL_FONT.render(
        PLAYER_TYPES[game.current_player].upper(),
        True,
        DARK_GRAY
    )
    SCREEN.blit(type_text, (PANEL_X + PANEL_W // 2 - type_text.get_width() // 2, 405))

    msg_lines = wrap_text(game.message, SMALL_FONT, 195)
    y = 455

    for line in msg_lines:
        surf = SMALL_FONT.render(line, True, BLACK)
        SCREEN.blit(surf, (PANEL_X + 18, y))
        y += 24

    help_lines = [
        "SPACE: Start dice (auto stop 1s)",
        "Click token or press 1-4",
        "Movement is automatic",
        "Bots target captures",
        "Roll 6 to leave home",
        "Safe cells protect tokens",
        "R: Restart"
    ]

    y = 610
    for line in help_lines:
        surf = SMALL_FONT.render(line, True, DARK_GRAY)
        SCREEN.blit(surf, (PANEL_X + 18, y))
        y += 26


def draw_board():
    SCREEN.fill(BOARD_BG)

    draw_grid()
    draw_home_areas()
    draw_cross_paths()
    draw_home_lanes()
    draw_center_triangle()
    # Safe-cell markers hidden: safe-cell protection still works, but the unwanted blue circles are no longer drawn.
    # draw_safe_cells()
    draw_tokens()
    draw_ui()


# =========================
# INPUT HELPERS
# =========================

def get_clicked_token(mouse_pos):
    mx, my = mouse_pos
    current = game.current_player

    if not game.rolled or game.dice_value is None or game.moving_token:
        return None

    movable = game.get_movable_tokens(current, game.dice_value)

    for token in movable:
        x, y = get_token_draw_position(token)

        if math.hypot(mx - x, my - y) <= 30:
            return token

    return None


def handle_human_arrow_press(key):
    if game.winner is not None:
        return

    if game.current_player_type != "human":
        return

    if game.delay_next_turn:
        return

    if not game.moving_token or game.selected_token is None:
        return

    required = get_required_arrow_for_token(game.selected_token)

    if key == required:
        game.move_selected_token_one_step()
    else:
        game.message = f"Wrong arrow. Press {arrow_name(required)}."


def restart_to_setup():
    global screen_mode, setup_stage, setup_text, setup_humans, game
    global PLAYERS, PLAYER_TYPES

    screen_mode = "setup"
    setup_stage = "humans"
    setup_text = ""
    setup_humans = 1
    game = None
    PLAYERS = []
    PLAYER_TYPES = {}


# =========================
# MAIN LOOP
# =========================

while True:
    CLOCK.tick(FPS)

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            pygame.quit()
            sys.exit()

        if event.type == pygame.KEYDOWN:
            if screen_mode == "setup":
                handle_setup_key(event)

            elif screen_mode == "game":
                if event.key == pygame.K_r:
                    restart_to_setup()

                elif game.winner is None and not game.delay_next_turn:
                    if game.current_player_type == "human":
                        if event.key == pygame.K_SPACE:
                            # Single press to start dice rolling; auto-stops after 2 seconds
                            if not game.rolled and not game.moving_token and not game.dice_rolling:
                                game.start_dice_roll()

                        if event.key in [
                            pygame.K_UP,
                            pygame.K_DOWN,
                            pygame.K_LEFT,
                            pygame.K_RIGHT,
                        ]:
                            handle_human_arrow_press(event.key)
                        # Allow selecting tokens by pressing 1-4
                        if event.key in [pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4]:
                            idx = event.key - pygame.K_1
                            current = game.current_player
                            if 0 <= idx < len(game.tokens[current]):
                                token = game.tokens[current][idx]
                                movable = game.get_movable_tokens(current, game.dice_value)
                                if token in movable:
                                    game.select_token_for_movement(token)

        if screen_mode == "game" and game is not None:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if game.winner is None and not game.delay_next_turn and game.current_player_type == "human":
                    token = get_clicked_token(event.pos)

                    if token:
                        game.select_token_for_movement(token)

    if screen_mode == "setup":
        draw_setup_screen()

    elif screen_mode == "game":
        game.update_delayed_turn()

        if game.dice_rolling:
            game.displayed_dice_value = random.randint(1, 6)

            # Auto-stop rolling after 1 second (human or bot)
            if time.time() - game.bot_roll_start >= 1.0:
                game.stop_dice_roll()

        if game.winner is None and not game.delay_next_turn and game.current_player_type == "bot":
            now = time.time()

            if not game.rolled and not game.dice_rolling and not game.moving_token:
                if now - game.bot_action_timer > 0.7:
                    game.start_dice_roll()
                    game.bot_action_timer = now

            elif game.dice_rolling:
                if now - game.bot_roll_start > 1.0:
                    game.stop_dice_roll()
                    game.bot_action_timer = now

            elif game.rolled and not game.moving_token:
                if now - game.bot_action_timer > 0.7:
                    chosen_token = game.choose_best_bot_token()

                    if chosen_token:
                        game.select_token_for_movement(chosen_token)

                    game.bot_action_timer = now

            elif game.moving_token:
                if now - game.bot_action_timer > 0.25:
                    game.move_selected_token_one_step()
                    game.bot_action_timer = now

        # Automatic movement handler for humans (and any moving token)
        if game.moving_token and not game.delay_next_turn:
            now = time.time()
            if game.bot_action_timer is None:
                game.bot_action_timer = now
            if now - game.bot_action_timer > 0.25:
                game.move_selected_token_one_step()
                game.bot_action_timer = now

        draw_board()

    pygame.display.flip()
    