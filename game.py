"""
Ultrasonic Catch Game  ─  2-player
──────────────────────────────────
각 플레이어는 USB로 연결된 아두이노(초음파 센서)로 캐릭터를 좌/중/우 3개 레인 중
하나로 이동시켜 떨어지는 아이템을 잡아 점수를 겨룬다.

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
SCREEN_W, SCREEN_H = 960, 680
FPS = 60
GAME_SECONDS = 60          # 제한 시간(초)
SERIAL_BAUD = 9600
SERIAL_TIMEOUT = 0.05      # 논블로킹 읽기용

# 아이템 종류  (name, color, points, spawn_weight)
ITEM_DEFS = [
    ("coin",    (80, 230, 100),  +10,  45),   # 초록 코인
    ("star",    (255, 215,  0),  +25,  15),   # 금별
    ("bomb",    (255,  60,  60), -15,  25),   # 빨간 폭탄
    ("skull",   (180,  60, 220), -30,  15),   # 보라 해골
]
ITEM_WEIGHTS = [d[3] for d in ITEM_DEFS]

ITEM_SPAWN_INTERVAL_MS = 800   # ms마다 각 플레이어 레인에 아이템 생성
ITEM_FALL_SPEED_MIN = 220      # px/s
ITEM_FALL_SPEED_MAX = 370

# 색상 팔레트
BG          = (14, 14, 28)
DIVIDER     = (60, 60, 100)
P1_COLOR    = (80, 160, 255)
P2_COLOR    = (255, 140, 60)
TEXT_COLOR  = (220, 220, 255)
SHADOW      = (0, 0, 0, 140)

# ═══════════════════════════════════════════════════════════════
#  Serial 스레드 (논블로킹)
# ═══════════════════════════════════════════════════════════════
class SerialReader:
    """백그라운드 스레드에서 Serial을 읽어 최신 거리값을 저장한다."""
    def __init__(self, port: str):
        self.distance: float = 15.0   # 기본값 = 가운데
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
    """연결된 COM 포트 목록 반환"""
    ports = [p.device for p in serial.tools.list_ports.comports()]
    print(f"[Serial] 감지된 포트: {ports if ports else '없음 (키보드 모드)'}")
    return ports


# ═══════════════════════════════════════════════════════════════
#  게임 오브젝트
# ═══════════════════════════════════════════════════════════════
HALF_W = SCREEN_W // 2          # 각 플레이어 영역 너비 = 480px
LANE_COUNT = 3
LANE_W = HALF_W // LANE_COUNT   # 160px

def lane_center_x(offset_x: int, lane: int) -> int:
    """플레이어 영역 x 오프셋과 레인(0~2)으로 화면 x 반환"""
    return offset_x + LANE_W * lane + LANE_W // 2


class Player:
    W, H = 52, 52

    def __init__(self, name: str, color: tuple, area_x: int, img_file: str):
        self.name = name
        self.color = color
        self.area_x = area_x       # 플레이어 영역 왼쪽 경계
        self.score = 0
        self.lane = 1              # 0=왼, 1=중, 2=오
        self.x = float(lane_center_x(area_x, 1))
        self.y = float(SCREEN_H - 80)
        self.target_x = self.x
        self.move_speed = 600      # px/s
        self.image = load_image(img_file, (self.W, self.H))  # None이면 도형으로 그림

        # 충돌 박스
        self.rect = pygame.Rect(0, 0, self.W, self.H)
        self._sync_rect()

    # distance → lane
    def set_distance(self, dist: float):
        if dist < 10:
            new_lane = 0
        elif dist < 20:
            new_lane = 1
        else:
            new_lane = 2
        if new_lane != self.lane:
            self.lane = new_lane
            self.target_x = float(lane_center_x(self.area_x, self.lane))

    def update(self, dt: float):
        dx = self.target_x - self.x
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
            # 이미지 없을 때 fallback: 도형
            shadow_rect = self.rect.inflate(6, 6)
            shadow_surf = pygame.Surface(shadow_rect.size, pygame.SRCALPHA)
            pygame.draw.ellipse(shadow_surf, (0, 0, 0, 80), shadow_surf.get_rect())
            surf.blit(shadow_surf, shadow_rect.topleft)
            pygame.draw.rect(surf, self.color, self.rect, border_radius=12)
            hl = pygame.Rect(self.rect.x + 6, self.rect.y + 6, 14, 6)
            pygame.draw.ellipse(surf, tuple(min(c + 80, 255) for c in self.color), hl)


class Item:
    W, H = 38, 38

    # 아이템 이미지 캐시 (같은 종류는 한 번만 로드)
    _img_cache: dict = {}

    def __init__(self, kind_idx: int, lane: int, area_x: int):
        d = ITEM_DEFS[kind_idx]
        self.name    = d[0]
        self.color   = d[1]
        self.points  = d[2]
        self.x = lane_center_x(area_x, lane)
        self.y = float(-self.H)
        self.speed = random.uniform(ITEM_FALL_SPEED_MIN, ITEM_FALL_SPEED_MAX)
        self.rect = pygame.Rect(0, 0, self.W, self.H)
        self._sync_rect()
        self.alive = True
        # 이미지 캐시에서 가져오거나 로드
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
            # fallback: 코인/별
            pygame.draw.ellipse(surf, self.color, self.rect)
            hl = pygame.Rect(self.rect.x + 6, self.rect.y + 6, 10, 5)
            pygame.draw.ellipse(surf, tuple(min(c + 80, 255) for c in self.color), hl)
        else:
            # fallback: 폭탄/해골
            cx, cy = self.rect.centerx, self.rect.centery
            r = self.W // 2
            pts = [(cx, cy - r), (cx - r, cy + r), (cx + r, cy + r)]
            pygame.draw.polygon(surf, self.color, pts)
            pygame.draw.polygon(surf, (255, 255, 255), pts, 2)


# ═══════════════════════════════════════════════════════════════
#  점수 팝업
# ═══════════════════════════════════════════════════════════════
class ScorePopup:
    def __init__(self, x: int, y: int, points: int, font: pygame.font.Font):
        self.x, self.y = float(x), float(y)
        self.points = points
        self.font = font
        self.alpha = 255
        self.vy = -90   # px/s 위로
        self.color = (80, 255, 120) if points > 0 else (255, 80, 80)
        self.alive = True

    def update(self, dt: float):
        self.y += self.vy * dt
        self.alpha -= 400 * dt
        if self.alpha <= 0:
            self.alive = False

    def draw(self, surf: pygame.Surface):
        text = f"+{self.points}" if self.points > 0 else str(self.points)
        s = self.font.render(text, True, self.color)
        s.set_alpha(max(0, int(self.alpha)))
        surf.blit(s, (int(self.x) - s.get_width() // 2, int(self.y)))


# ═══════════════════════════════════════════════════════════════
#  레인 가이드라인 그리기
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
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
        pygame.display.set_caption("Ultrasonic Catch Game")
        self.clock = pygame.time.Clock()

        self.font_sm  = pygame.font.SysFont("consolas", 20, bold=True)
        self.font_md  = pygame.font.SysFont("consolas", 30, bold=True)
        self.font_lg  = pygame.font.SysFont("consolas", 52, bold=True)
        self.font_pop = pygame.font.SysFont("consolas", 24, bold=True)

        # Serial 연결
        ports = detect_serial_ports()
        self.serial1 = SerialReader(ports[0]) if len(ports) >= 1 else None
        self.serial2 = SerialReader(ports[1]) if len(ports) >= 2 else None
        self.kb_mode = (self.serial1 is None or not self.serial1.connected)

        # 플레이어 (P1=왼쪽 영역, P2=오른쪽 영역)
        self.p1 = Player("P1", P1_COLOR, area_x=0,      img_file="p1.png")
        self.p2 = Player("P2", P2_COLOR, area_x=HALF_W, img_file="p2.png")
        self.bg_image = load_image("bg.png", (SCREEN_W, SCREEN_H))

        # 아이템 / 팝업
        self.items1: list[Item] = []
        self.items2: list[Item] = []
        self.popups: list[ScorePopup] = []

        # 타이머
        self.start_ticks = pygame.time.get_ticks()
        self.spawn_ticks  = pygame.time.get_ticks()

        self.state = "playing"   # "playing" | "gameover"
        self.kb_lane1 = 1        # 키보드 테스트용
        self.kb_lane2 = 1

    # ── 거리 → 레인 매핑 ──────────────────────────────────────
    def _dist_to_lane(self, dist: float) -> int:
        if dist < 10:   return 0
        if dist < 20:   return 1
        return 2

    # ── 입력 처리 ─────────────────────────────────────────────
    def handle_events(self) -> bool:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return False
                # 게임오버 화면에서 R = 재시작
                if self.state == "gameover" and event.key == pygame.K_r:
                    self.__init__()
                    return True
                # 키보드 테스트 모드
                if self.kb_mode:
                    if event.key == pygame.K_a: self.kb_lane1 = 0
                    if event.key == pygame.K_s: self.kb_lane1 = 1
                    if event.key == pygame.K_d: self.kb_lane1 = 2
                if not self.serial2 or not self.serial2.connected:
                    if event.key == pygame.K_LEFT:  self.kb_lane2 = 0
                    if event.key == pygame.K_DOWN:  self.kb_lane2 = 1
                    if event.key == pygame.K_RIGHT: self.kb_lane2 = 2
        return True

    # ── 업데이트 ──────────────────────────────────────────────
    def update(self, dt: float):
        if self.state != "playing":
            return

        # 남은 시간 계산
        elapsed_ms = pygame.time.get_ticks() - self.start_ticks
        if elapsed_ms >= GAME_SECONDS * 1000:
            self.state = "gameover"
            return

        # 거리 읽기
        if self.serial1 and self.serial1.connected:
            self.p1.set_distance(self.serial1.get())
        else:
            # 키보드 fallback
            fake_dist = [5.0, 15.0, 25.0][self.kb_lane1]
            self.p1.set_distance(fake_dist)

        if self.serial2 and self.serial2.connected:
            self.p2.set_distance(self.serial2.get())
        else:
            fake_dist = [5.0, 15.0, 25.0][self.kb_lane2]
            self.p2.set_distance(fake_dist)

        self.p1.update(dt)
        self.p2.update(dt)

        # 아이템 생성
        now = pygame.time.get_ticks()
        if now - self.spawn_ticks >= ITEM_SPAWN_INTERVAL_MS:
            self.spawn_ticks = now
            lane = random.randint(0, LANE_COUNT - 1)
            idx  = random.choices(range(len(ITEM_DEFS)), weights=ITEM_WEIGHTS)[0]
            self.items1.append(Item(idx, lane, area_x=0))

            lane = random.randint(0, LANE_COUNT - 1)
            idx  = random.choices(range(len(ITEM_DEFS)), weights=ITEM_WEIGHTS)[0]
            self.items2.append(Item(idx, lane, area_x=HALF_W))

        # 아이템 이동 & 충돌
        for item_list, player in ((self.items1, self.p1), (self.items2, self.p2)):
            for item in item_list:
                item.update(dt)
                if item.alive and item.rect.colliderect(player.rect):
                    player.score += item.points
                    self.popups.append(
                        ScorePopup(item.x, int(item.y), item.points, self.font_pop)
                    )
                    item.alive = False

        self.items1 = [i for i in self.items1 if i.alive]
        self.items2 = [i for i in self.items2 if i.alive]

        # 팝업 갱신
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

        # 중앙 분리선
        pygame.draw.line(self.screen, DIVIDER, (HALF_W, 0), (HALF_W, SCREEN_H), 3)

        # 아이템
        for item in self.items1 + self.items2:
            item.draw(self.screen)

        # 플레이어
        self.p1.draw(self.screen)
        self.p2.draw(self.screen)

        # 팝업
        for pop in self.popups:
            pop.draw(self.screen)

        # HUD
        self._draw_hud()

        if self.state == "gameover":
            self._draw_gameover()

        pygame.display.flip()

    def _draw_hud(self):
        elapsed_ms = pygame.time.get_ticks() - self.start_ticks
        remaining  = max(0, GAME_SECONDS - elapsed_ms / 1000)

        # 타이머 (상단 가운데)
        timer_surf = self.font_md.render(f"{remaining:.1f}s", True, TEXT_COLOR)
        self.screen.blit(timer_surf, (SCREEN_W // 2 - timer_surf.get_width() // 2, 12))

        # P1 점수 (왼쪽)
        s1 = self.font_md.render(f"P1  {self.p1.score:+}", True, P1_COLOR)
        self.screen.blit(s1, (16, 12))

        # P2 점수 (오른쪽)
        s2 = self.font_md.render(f"P2  {self.p2.score:+}", True, P2_COLOR)
        self.screen.blit(s2, (SCREEN_W - s2.get_width() - 16, 12))

        # 거리 디버그 표시 (센서값 실시간 확인)
        d1 = self.serial1.get() if (self.serial1 and self.serial1.connected) else None
        d2 = self.serial2.get() if (self.serial2 and self.serial2.connected) else None
        conn1 = "OK" if (self.serial1 and self.serial1.connected) else "KB"
        conn2 = "OK" if (self.serial2 and self.serial2.connected) else "KB"
        dbg1 = self.font_sm.render(f"[{conn1}] {d1:.1f}cm" if d1 is not None else f"[{conn1}]", True, P1_COLOR)
        dbg2 = self.font_sm.render(f"[{conn2}] {d2:.1f}cm" if d2 is not None else f"[{conn2}]", True, P2_COLOR)
        self.screen.blit(dbg1, (16, 45))
        self.screen.blit(dbg2, (SCREEN_W - dbg2.get_width() - 16, 45))

        # 레인 인디케이터 (현재 레인 표시)
        for lane in range(LANE_COUNT):
            for player, area_x, p_color in (
                (self.p1, 0,      P1_COLOR),
                (self.p2, HALF_W, P2_COLOR),
            ):
                cx = lane_center_x(area_x, lane)
                color = p_color if lane == player.lane else (50, 50, 70)
                pygame.draw.circle(self.screen, color, (cx, SCREEN_H - 20), 8)

        # 아이템 범례 (처음 10초만 표시)
        elapsed_ms = pygame.time.get_ticks() - self.start_ticks
        if elapsed_ms < 10000:
            alpha = max(0, 255 - int((elapsed_ms - 7000) / 3000 * 255)) if elapsed_ms > 7000 else 255
            legend = [
                ("● +10", (80, 230, 100)),
                ("★ +25", (255, 215,  0)),
                ("▲ -15", (255,  60, 60)),
                ("▲ -30", (180,  60, 220)),
            ]
            for i, (text, color) in enumerate(legend):
                s = self.font_sm.render(text, True, color)
                s.set_alpha(alpha)
                self.screen.blit(s, (SCREEN_W // 2 - 50, 55 + i * 22))

        # 키보드 모드 안내
        if self.kb_mode:
            hint = self.font_sm.render("Keyboard: P1=A/S/D  P2=←/↓/→", True, (100, 100, 140))
            self.screen.blit(hint, (SCREEN_W // 2 - hint.get_width() // 2, SCREEN_H - 42))

    def _draw_gameover(self):
        # 반투명 오버레이
        overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 170))
        self.screen.blit(overlay, (0, 0))

        # 승자 결정
        if self.p1.score > self.p2.score:
            winner, w_color = "Player 1 WIN!", P1_COLOR
        elif self.p2.score > self.p1.score:
            winner, w_color = "Player 2 WIN!", P2_COLOR
        else:
            winner, w_color = "DRAW!", TEXT_COLOR

        w_surf = self.font_lg.render(winner, True, w_color)
        self.screen.blit(w_surf, (SCREEN_W // 2 - w_surf.get_width() // 2, SCREEN_H // 2 - 90))

        score_text = f"P1  {self.p1.score:+}   vs   P2  {self.p2.score:+}"
        s_surf = self.font_md.render(score_text, True, TEXT_COLOR)
        self.screen.blit(s_surf, (SCREEN_W // 2 - s_surf.get_width() // 2, SCREEN_H // 2 + 10))

        r_surf = self.font_sm.render("R  재시작     ESC  종료", True, (160, 160, 200))
        self.screen.blit(r_surf, (SCREEN_W // 2 - r_surf.get_width() // 2, SCREEN_H // 2 + 75))

    # ── 메인 루프 ─────────────────────────────────────────────
    def run(self):
        running = True
        while running:
            dt = self.clock.tick(FPS) / 1000.0
            running = self.handle_events()
            self.update(dt)
            self.draw()

        # 정리
        if self.serial1: self.serial1.close()
        if self.serial2: self.serial2.close()
        pygame.quit()
        sys.exit()


# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    Game().run()
