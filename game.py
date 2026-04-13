"""
Ultrasonic Catch Game  ─  2-player  (스태미나 생존 모드)
──────────────────────────────────────────────────────
시간이 지남에 따라 스태미나가 감소한다.
하늘에서 떨어지는 아이템을 잡아 스태미나를 회복하거나,
나쁜 아이템을 피해야 한다.
먼저 스태미나가 0이 되는 플레이어가 진다.

    0 ~ 10 cm  → 왼쪽 레인
   10 ~ 20 cm  → 가운데 레인
   20 ~ 30 cm  → 오른쪽 레인

아두이노가 없으면 키보드로 테스트 가능:
    P1  ─  A(왼쪽) / S(가운데) / D(오른쪽)
    P2  ─  ← / ↓ / →
"""

import sys
import os
import threading
import random
import time
import math
import pygame
import serial
import serial.tools.list_ports

# ═══════════════════════════════════════════════════════════════
#  경로 설정 (exe 배포 시에도 assets 폴더 인식)
# ═══════════════════════════════════════════════════════════════
BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

def load_image(filename: str, size: tuple) -> pygame.Surface | None:
    """assets/{filename} 로드 후 size로 스케일. 없으면 None 반환."""
    path = os.path.join(ASSETS_DIR, filename)
    if os.path.exists(path):
        try:
            img = pygame.image.load(path).convert_alpha()
            return pygame.transform.smoothscale(img, size)
        except Exception:
            pass
    return None

# ═══════════════════════════════════════════════════════════════
#  설정
# ═══════════════════════════════════════════════════════════════
SCREEN_W, SCREEN_H = 1920, 1080
FPS = 60
SERIAL_BAUD = 9600
SERIAL_TIMEOUT = 0.05

MAX_STAMINA    = 100.0   # 최대 스태미나
STAMINA_DRAIN  = 5.0     # 초당 자연 감소량

# 아이템 종류 (name, color, stamina_change, spawn_weight)
# 크게 회복 / 조금 회복 / 조금 깎기 / 크게 깎기
ITEM_DEFS = [
    ("heal_big",   ( 80, 230, 100), +30, 20),
    ("heal_small", (160, 255, 160), +15, 30),
    ("hurt_small", (255, 160,  60), -15, 30),
    ("hurt_big",   (255,  60,  60), -30, 20),
]
ITEM_WEIGHTS = [d[3] for d in ITEM_DEFS]

ITEM_SPAWN_INTERVAL_MS = 800
ITEM_FALL_SPEED_MIN    = 220   # px/s
ITEM_FALL_SPEED_MAX    = 370

# 색상 팔레트
BG         = (14,  14,  28)
DIVIDER    = (60,  60, 100)
P1_COLOR   = (80, 160, 255)
P2_COLOR   = (255, 140,  60)
TEXT_COLOR = (220, 220, 255)

# ═══════════════════════════════════════════════════════════════
#  Serial 스레드 (논블로킹)
# ═══════════════════════════════════════════════════════════════
class SerialReader:
    """백그라운드 스레드에서 Serial을 읽어 최신 거리값을 저장한다."""
    def __init__(self, port: str):
        self.distance: float = 15.0
        self._lock = threading.Lock()
        self._running = True
        try:
            self._ser = serial.Serial(port, SERIAL_BAUD, timeout=SERIAL_TIMEOUT)
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            self.connected = True
            print(f"[Serial] {port} 연결됨")
        except serial.SerialException as e:
            self._ser = None
            self.connected = False
            print(f"[Serial] {port} 연결 실패: {e}")

    def _run(self):
        while self._running and self._ser:
            try:
                if not self._ser.is_open:
                    break
                line = self._ser.readline().decode("utf-8", errors="ignore").strip()
                if line:
                    val = float(line)
                    val = max(0.0, min(30.0, val))
                    with self._lock:
                        self.distance = val
            except (ValueError, serial.SerialException, AttributeError, OSError):
                time.sleep(0.05)

    def get(self) -> float:
        with self._lock:
            return self.distance

    def close(self):
        self._running = False
        if self._ser and self._ser.is_open:
            self._ser.close()


def detect_serial_ports():
    ports = [p.device for p in serial.tools.list_ports.comports()]
    print(f"[Serial] 감지된 포트: {ports if ports else '없음 (키보드 모드)'}")
    return ports


# ═══════════════════════════════════════════════════════════════
#  게임 오브젝트
# ═══════════════════════════════════════════════════════════════
HALF_W     = SCREEN_W // 2          # 960 — 각 플레이어 영역 너비
LANE_COUNT = 3
LANE_W     = HALF_W // LANE_COUNT   # 320

def lane_center_x(offset_x: int, lane: int) -> int:
    """플레이어 영역 x 오프셋과 레인(0~2)으로 화면 x 반환"""
    return offset_x + LANE_W * lane + LANE_W // 2


class Player:
    W, H = 80, 80

    def __init__(self, name: str, color: tuple, area_x: int, img_file: str):
        self.name      = name
        self.color     = color
        self.area_x    = area_x
        self.stamina   = MAX_STAMINA
        self.lane      = 1
        self.x         = float(lane_center_x(area_x, 1))
        self.y         = float(SCREEN_H - 140)
        self.target_x  = self.x
        self.move_speed = 900          # px/s
        self.image     = load_image(img_file, (self.W, self.H))
        self.rect      = pygame.Rect(0, 0, self.W, self.H)
        self._sync_rect()

    def set_distance(self, dist: float):
        if   dist < 10: new_lane = 0
        elif dist < 20: new_lane = 1
        else:           new_lane = 2
        if new_lane != self.lane:
            self.lane     = new_lane
            self.target_x = float(lane_center_x(self.area_x, self.lane))

    def update(self, dt: float):
        dx   = self.target_x - self.x
        move = self.move_speed * dt
        if abs(dx) <= move:
            self.x = self.target_x
        else:
            self.x += move * (1 if dx > 0 else -1)
        self._sync_rect()

    def _sync_rect(self):
        self.rect.centerx = int(self.x)
        self.rect.centery = int(self.y)

    def draw(self, surf: pygame.Surface):
        if self.image:
            surf.blit(self.image, self.rect.topleft)
        else:
            shadow_surf = pygame.Surface(self.rect.inflate(6, 6).size, pygame.SRCALPHA)
            pygame.draw.ellipse(shadow_surf, (0, 0, 0, 80), shadow_surf.get_rect())
            surf.blit(shadow_surf, self.rect.inflate(6, 6).topleft)
            pygame.draw.rect(surf, self.color, self.rect, border_radius=16)
            hl = pygame.Rect(self.rect.x + 8, self.rect.y + 8, 20, 8)
            pygame.draw.ellipse(surf, tuple(min(c + 80, 255) for c in self.color), hl)


class Item:
    W, H = 56, 56

    _img_cache: dict = {}

    def __init__(self, kind_idx: int, lane: int, area_x: int):
        d = ITEM_DEFS[kind_idx]
        self.name   = d[0]
        self.color  = d[1]
        self.points = d[2]   # 스태미나 변화량
        self.x      = lane_center_x(area_x, lane)
        self.y      = float(-self.H)
        self.speed  = random.uniform(ITEM_FALL_SPEED_MIN, ITEM_FALL_SPEED_MAX)
        self.rect   = pygame.Rect(0, 0, self.W, self.H)
        self._sync_rect()
        self.alive  = True
        if self.name not in Item._img_cache:
            Item._img_cache[self.name] = load_image(f"{self.name}.png", (self.W, self.H))
        self.image = Item._img_cache[self.name]

    def _sync_rect(self):
        self.rect.centerx = self.x
        self.rect.centery = int(self.y)

    def update(self, dt: float):
        self.y += self.speed * dt
        self._sync_rect()
        if self.y > SCREEN_H + self.H:
            self.alive = False

    def draw(self, surf: pygame.Surface):
        if self.image:
            surf.blit(self.image, self.rect.topleft)
        elif self.points > 0:
            pygame.draw.ellipse(surf, self.color, self.rect)
            hl = pygame.Rect(self.rect.x + 8, self.rect.y + 8, 14, 7)
            pygame.draw.ellipse(surf, tuple(min(c + 80, 255) for c in self.color), hl)
        else:
            cx, cy = self.rect.centerx, self.rect.centery
            r = self.W // 2
            pts = [(cx, cy - r), (cx - r, cy + r), (cx + r, cy + r)]
            pygame.draw.polygon(surf, self.color, pts)
            pygame.draw.polygon(surf, (255, 255, 255), pts, 2)


# ═══════════════════════════════════════════════════════════════
#  스태미나 변화 팝업
# ═══════════════════════════════════════════════════════════════
class ScorePopup:
    def __init__(self, x: int, y: int, points: int, font: pygame.font.Font):
        self.x, self.y = float(x), float(y)
        self.points = points
        self.font   = font
        self.alpha  = 255
        self.vy     = -90
        self.color  = (80, 255, 120) if points > 0 else (255, 80, 80)
        self.alive  = True

    def update(self, dt: float):
        self.y     += self.vy * dt
        self.alpha -= 400 * dt
        if self.alpha <= 0:
            self.alive = False

    def draw(self, surf: pygame.Surface):
        sign = "+" if self.points > 0 else ""
        s = self.font.render(f"{sign}{self.points}", True, self.color)
        s.set_alpha(max(0, int(self.alpha)))
        surf.blit(s, (int(self.x) - s.get_width() // 2, int(self.y)))


# ═══════════════════════════════════════════════════════════════
#  레인 가이드라인
# ═══════════════════════════════════════════════════════════════
def draw_lanes(surf: pygame.Surface):
    for side in (0, HALF_W):
        for i in range(1, LANE_COUNT):
            x = side + LANE_W * i
            pygame.draw.line(surf, (35, 35, 60), (x, 0), (x, SCREEN_H))


# ═══════════════════════════════════════════════════════════════
#  메인 게임 클래스
# ═══════════════════════════════════════════════════════════════
class Game:
    CHAR_SIZE = 180   # 게임오버 화면 캐릭터 크기

    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
        pygame.display.set_caption("Ultrasonic Catch Game")
        self.clock = pygame.time.Clock()

        self.font_sm  = pygame.font.SysFont("consolas", 28, bold=True)
        self.font_md  = pygame.font.SysFont("consolas", 44, bold=True)
        self.font_lg  = pygame.font.SysFont("consolas", 80, bold=True)
        self.font_pop = pygame.font.SysFont("consolas", 36, bold=True)

        # Serial 연결
        ports = detect_serial_ports()
        self.serial1 = SerialReader(ports[0]) if len(ports) >= 1 else None
        self.serial2 = SerialReader(ports[1]) if len(ports) >= 2 else None
        self.kb_mode = (self.serial1 is None or not self.serial1.connected)

        # 플레이어 (P1=왼쪽/개, P2=오른쪽/고양이)
        self.p1 = Player("Player 1", P1_COLOR, area_x=0,      img_file="p1.png")
        self.p2 = Player("Player 2", P2_COLOR, area_x=HALF_W, img_file="p2.png")
        self.bg_image = load_image("bg.png", (SCREEN_W, SCREEN_H))

        # 게임오버 화면에 띄울 캐릭터 이미지 (개/고양이)
        self.dog_img = load_image("dog.png", (self.CHAR_SIZE, self.CHAR_SIZE))
        self.cat_img = load_image("cat.png", (self.CHAR_SIZE, self.CHAR_SIZE))

        # 아이템 / 팝업
        self.items1: list[Item] = []
        self.items2: list[Item] = []
        self.popups: list[ScorePopup] = []

        # 타이머
        self.start_ticks = pygame.time.get_ticks()
        self.spawn_ticks  = pygame.time.get_ticks()

        # 게임 상태
        self.state         = "playing"   # "playing" | "gameover"
        self.winner        = ""          # 게임오버 시 승자 표시 문자열
        self.winner_color  = TEXT_COLOR
        self.winner_img    = None        # 게임오버 화면에 띄울 이미지
        self.winner_label  = ""          # 이미지 없을 때 플레이스홀더 텍스트

        # 방방 모션용 시간 누적
        self.bounce_time = 0.0

        # 재시작 버튼
        btn_w, btn_h = 340, 90
        self.restart_btn = pygame.Rect(
            SCREEN_W // 2 - btn_w // 2,
            SCREEN_H // 2 + 230,
            btn_w, btn_h
        )

        # 키보드 테스트용
        self.kb_lane1 = 1
        self.kb_lane2 = 1

    # ── 입력 처리 ─────────────────────────────────────────────
    def handle_events(self) -> bool:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return False
                # 게임오버 화면: R 키 재시작
                if self.state == "gameover" and event.key == pygame.K_r:
                    self.__init__()
                    return True
                # 키보드 레인 조작
                if self.kb_mode:
                    if event.key == pygame.K_a: self.kb_lane1 = 0
                    if event.key == pygame.K_s: self.kb_lane1 = 1
                    if event.key == pygame.K_d: self.kb_lane1 = 2
                if not self.serial2 or not self.serial2.connected:
                    if event.key == pygame.K_LEFT:  self.kb_lane2 = 0
                    if event.key == pygame.K_DOWN:  self.kb_lane2 = 1
                    if event.key == pygame.K_RIGHT: self.kb_lane2 = 2

            # 게임오버 화면: 재시작 버튼 클릭
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self.state == "gameover" and self.restart_btn.collidepoint(event.pos):
                    self.__init__()
                    return True

        return True

    # ── 업데이트 ──────────────────────────────────────────────
    def update(self, dt: float):
        # 게임오버 상태: 방방 모션 시간만 누적
        if self.state == "gameover":
            self.bounce_time += dt
            return

        if self.state != "playing":
            return

        # 거리 읽기 → 레인 설정
        if self.serial1 and self.serial1.connected:
            self.p1.set_distance(self.serial1.get())
        else:
            self.p1.set_distance([5.0, 15.0, 25.0][self.kb_lane1])

        if self.serial2 and self.serial2.connected:
            self.p2.set_distance(self.serial2.get())
        else:
            self.p2.set_distance([5.0, 15.0, 25.0][self.kb_lane2])

        self.p1.update(dt)
        self.p2.update(dt)

        # ── 스태미나 자연 감소 ──────────────────────────────
        self.p1.stamina = max(0.0, self.p1.stamina - STAMINA_DRAIN * dt)
        self.p2.stamina = max(0.0, self.p2.stamina - STAMINA_DRAIN * dt)

        # ── 스태미나 0 → 게임오버 판정 ──────────────────────
        p1_dead = self.p1.stamina <= 0
        p2_dead = self.p2.stamina <= 0

        if p1_dead or p2_dead:
            if p1_dead and p2_dead:
                self.winner       = "DRAW"
                self.winner_color = TEXT_COLOR
                self.winner_img   = None
                self.winner_label = ""
            elif p2_dead:
                # P2 먼저 0 → P1 승
                self.winner       = "Player 1 Win!"
                self.winner_color = P1_COLOR
                self.winner_img   = self.dog_img
                self.winner_label = "DOG"
            else:
                # P1 먼저 0 → P2 승
                self.winner       = "Player 2 Win!"
                self.winner_color = P2_COLOR
                self.winner_img   = self.cat_img
                self.winner_label = "CAT"
            self.state = "gameover"
            return

        # ── 아이템 생성 ──────────────────────────────────────
        now = pygame.time.get_ticks()
        if now - self.spawn_ticks >= ITEM_SPAWN_INTERVAL_MS:
            self.spawn_ticks = now
            for area_x, item_list in ((0, self.items1), (HALF_W, self.items2)):
                lane = random.randint(0, LANE_COUNT - 1)
                idx  = random.choices(range(len(ITEM_DEFS)), weights=ITEM_WEIGHTS)[0]
                item_list.append(Item(idx, lane, area_x))

        # ── 아이템 이동 & 충돌 ───────────────────────────────
        for item_list, player in ((self.items1, self.p1), (self.items2, self.p2)):
            for item in item_list:
                item.update(dt)
                if item.alive and item.rect.colliderect(player.rect):
                    player.stamina = max(0.0, min(MAX_STAMINA, player.stamina + item.points))
                    self.popups.append(ScorePopup(item.x, int(item.y), item.points, self.font_pop))
                    item.alive = False

        self.items1 = [i for i in self.items1 if i.alive]
        self.items2 = [i for i in self.items2 if i.alive]

        for p in self.popups:
            p.update(dt)
        self.popups = [p for p in self.popups if p.alive]

    # ── 렌더링 ────────────────────────────────────────────────
    def draw(self):
        if self.bg_image:
            self.screen.blit(self.bg_image, (0, 0))
        else:
            self.screen.fill(BG)

        draw_lanes(self.screen)
        pygame.draw.line(self.screen, DIVIDER, (HALF_W, 0), (HALF_W, SCREEN_H), 4)

        for item in self.items1 + self.items2:
            item.draw(self.screen)

        self.p1.draw(self.screen)
        self.p2.draw(self.screen)

        for pop in self.popups:
            pop.draw(self.screen)

        self._draw_hud()

        if self.state == "gameover":
            self._draw_gameover()

        pygame.display.flip()

    # ── HUD (스태미나 바, 디버그 등) ──────────────────────────
    def _draw_stamina_bar(self, center_x: int, y: int, stamina: float, player_color: tuple):
        """스태미나 바: 높으면 player_color, 낮으면 빨간색으로 변화"""
        bar_w, bar_h = 500, 36
        x = center_x - bar_w // 2

        # 배경
        pygame.draw.rect(self.screen, (40, 40, 60), (x, y, bar_w, bar_h), border_radius=10)

        # 채워진 부분 (스태미나 비율에 따라 색상 보간)
        fill_w = int(bar_w * stamina / MAX_STAMINA)
        if fill_w > 0:
            ratio = stamina / MAX_STAMINA
            r = int(player_color[0] * ratio + 255 * (1 - ratio))
            g = int(player_color[1] * ratio + 60  * (1 - ratio))
            b = int(player_color[2] * ratio + 60  * (1 - ratio))
            pygame.draw.rect(self.screen, (min(255, r), min(255, g), min(255, b)),
                             (x, y, fill_w, bar_h), border_radius=10)

        # 테두리
        pygame.draw.rect(self.screen, player_color, (x, y, bar_w, bar_h), 3, border_radius=10)

        # 수치 텍스트 (바 위에 오버레이)
        label = self.font_sm.render(f"{int(stamina)}", True, (255, 255, 255))
        self.screen.blit(label, (x + bar_w // 2 - label.get_width() // 2,
                                  y + bar_h // 2 - label.get_height() // 2))

    def _draw_hud(self):
        # ── 스태미나 바 (각 플레이어 영역 상단 중앙) ──
        self._draw_stamina_bar(HALF_W // 2,            16, self.p1.stamina, P1_COLOR)
        self._draw_stamina_bar(HALF_W + HALF_W // 2,   16, self.p2.stamina, P2_COLOR)

        # ── 플레이어 이름 ──
        s1 = self.font_md.render("Player 1", True, P1_COLOR)
        self.screen.blit(s1, (16, 62))
        s2 = self.font_md.render("Player 2", True, P2_COLOR)
        self.screen.blit(s2, (SCREEN_W - s2.get_width() - 16, 62))

        # ── 경과 시간 (화면 중앙 상단) ──
        elapsed_s = (pygame.time.get_ticks() - self.start_ticks) / 1000
        timer_surf = self.font_md.render(f"{elapsed_s:.0f}s", True, TEXT_COLOR)
        self.screen.blit(timer_surf, (SCREEN_W // 2 - timer_surf.get_width() // 2, 16))

        # ── Serial 디버그 ──
        d1    = self.serial1.get() if (self.serial1 and self.serial1.connected) else None
        d2    = self.serial2.get() if (self.serial2 and self.serial2.connected) else None
        conn1 = "OK" if (self.serial1 and self.serial1.connected) else "KB"
        conn2 = "OK" if (self.serial2 and self.serial2.connected) else "KB"
        dbg1  = self.font_sm.render(f"[{conn1}] {d1:.1f}cm" if d1 is not None else f"[{conn1}]", True, P1_COLOR)
        dbg2  = self.font_sm.render(f"[{conn2}] {d2:.1f}cm" if d2 is not None else f"[{conn2}]", True, P2_COLOR)
        self.screen.blit(dbg1, (16, 115))
        self.screen.blit(dbg2, (SCREEN_W - dbg2.get_width() - 16, 115))

        # ── 레인 인디케이터 (하단) ──
        for lane in range(LANE_COUNT):
            for player, area_x, p_color in (
                (self.p1, 0,      P1_COLOR),
                (self.p2, HALF_W, P2_COLOR),
            ):
                cx    = lane_center_x(area_x, lane)
                color = p_color if lane == player.lane else (50, 50, 70)
                pygame.draw.circle(self.screen, color, (cx, SCREEN_H - 35), 14)

        # ── 아이템 범례 (처음 10초만 표시) ──
        elapsed_ms = pygame.time.get_ticks() - self.start_ticks
        if elapsed_ms < 10000:
            alpha = max(0, 255 - int((elapsed_ms - 7000) / 3000 * 255)) if elapsed_ms > 7000 else 255
            legend = [
                ("● 크게 회복 +30", ( 80, 230, 100)),
                ("● 조금 회복 +15", (160, 255, 160)),
                ("▲ 조금 깎기 -15", (255, 160,  60)),
                ("▲ 크게 깎기 -30", (255,  60,  60)),
            ]
            for i, (text, color) in enumerate(legend):
                s = self.font_sm.render(text, True, color)
                s.set_alpha(alpha)
                self.screen.blit(s, (SCREEN_W // 2 - s.get_width() // 2, 95 + i * 36))

        # ── 키보드 모드 안내 ──
        if self.kb_mode:
            hint = self.font_sm.render("Keyboard: P1=A/S/D  P2=←/↓/→", True, (100, 100, 140))
            self.screen.blit(hint, (SCREEN_W // 2 - hint.get_width() // 2, SCREEN_H - 55))

    # ── 게임오버 화면 ─────────────────────────────────────────
    def _draw_gameover(self):
        # 반투명 오버레이
        overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 185))
        self.screen.blit(overlay, (0, 0))

        # 방방 뛰는 캐릭터 (화면 중앙)
        bounce_y = int(math.sin(self.bounce_time * 5) * 38)   # ±38px, 5rad/s
        char_cx  = SCREEN_W // 2
        char_cy  = SCREEN_H // 2 - 120 + bounce_y

        if self.winner_img:
            # 이미지가 있으면 그대로 표시
            self.screen.blit(self.winner_img, (
                char_cx - self.CHAR_SIZE // 2,
                char_cy - self.CHAR_SIZE // 2
            ))
        elif self.winner != "DRAW":
            # 이미지 없을 때 플레이스홀더 (나중에 dog.png / cat.png 넣으면 자동 대체됨)
            ph_rect = pygame.Rect(
                char_cx - self.CHAR_SIZE // 2,
                char_cy - self.CHAR_SIZE // 2,
                self.CHAR_SIZE, self.CHAR_SIZE
            )
            pygame.draw.rect(self.screen, self.winner_color, ph_rect, border_radius=24)
            lbl = self.font_md.render(self.winner_label, True, (255, 255, 255))
            self.screen.blit(lbl, (
                char_cx - lbl.get_width() // 2,
                char_cy - lbl.get_height() // 2
            ))

        # "Player X Win!" 텍스트
        w_surf = self.font_lg.render(self.winner, True, self.winner_color)
        self.screen.blit(w_surf, (SCREEN_W // 2 - w_surf.get_width() // 2, SCREEN_H // 2 + 80))

        # 재시작 버튼 (마우스 호버 시 밝아짐)
        hover      = self.restart_btn.collidepoint(pygame.mouse.get_pos())
        btn_color  = (90, 190, 90) if hover else (55, 140, 55)
        btn_border = (140, 230, 140)
        pygame.draw.rect(self.screen, btn_color,  self.restart_btn, border_radius=18)
        pygame.draw.rect(self.screen, btn_border, self.restart_btn, 3, border_radius=18)
        btn_text = self.font_md.render("RESTART", True, (255, 255, 255))
        self.screen.blit(btn_text, (
            self.restart_btn.centerx - btn_text.get_width()  // 2,
            self.restart_btn.centery - btn_text.get_height() // 2
        ))

        # ESC 안내
        esc = self.font_sm.render("ESC  종료", True, (160, 160, 200))
        self.screen.blit(esc, (SCREEN_W // 2 - esc.get_width() // 2, self.restart_btn.bottom + 22))

    # ── 메인 루프 ─────────────────────────────────────────────
    def run(self):
        running = True
        while running:
            dt      = self.clock.tick(FPS) / 1000.0
            running = self.handle_events()
            self.update(dt)
            self.draw()

        if self.serial1: self.serial1.close()
        if self.serial2: self.serial2.close()
        pygame.quit()
        sys.exit()


# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    Game().run()
