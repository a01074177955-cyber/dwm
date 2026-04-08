// ─── Ultrasonic Sensor Controller ────────────────────────────────────────────
// HC-SR04: TRIG=13, ECHO=12
// Serial 9600baud → 거리(cm) 전송, 범위 0.0~30.0
// ─────────────────────────────────────────────────────────────────────────────

const int TRIG_PIN = 13;
const int ECHO_PIN = 12;

float lastValidDist = 15.0;   // 측정 실패 시 사용할 기본값 (가운데)

void setup() {
  Serial.begin(9600);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  digitalWrite(TRIG_PIN, LOW);
}

void loop() {
  // 트리거 펄스 발사
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  // 에코 수신 (30ms timeout → 약 510cm 이상이면 0 반환)
  long duration = pulseIn(ECHO_PIN, HIGH, 30000);

  float distance;
  if (duration == 0) {
    // 측정 실패 → 마지막 유효 값 유지
    distance = lastValidDist;
  } else {
    distance = duration * 0.034 / 2.0;
    distance = constrain(distance, 0.0, 30.0);
    lastValidDist = distance;
  }

  // 소수점 1자리로 전송 (예: "14.3\n")
  Serial.println(distance, 1);

  delay(50);   // 20Hz 업데이트
}
