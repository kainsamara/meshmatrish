// Процессор: ATtiny13A
// Частота: 1.2 MHz (internal 9.6 MHz, CKDIV8 fuse)
// Программатор: USBasp

#include <avr/sleep.h>
#include <avr/interrupt.h>
#include <util/delay.h>

#define TRIGGER_PIN  PB3
#define BTN_PIN      PB2
#define LED1_PIN     PB1
#define LED2_PIN     PB0

#define MAX_BRIGHTNESS  150
#define FADE_MS         800
#define BLINK_COUNT     3

volatile bool triggered = false;
volatile bool buttonPressed = false;

ISR(PCINT0_vect) {
  // Внешний триггер
  if (!(PINB & (1 << TRIGGER_PIN))) {
    triggered = true;
  }

  // Кнопка PB2
  if (!(PINB & (1 << BTN_PIN))) {
    buttonPressed = true;
  }
}

// ATtiny13A:
// OC0A = PB0
// OC0B = PB1
// Поэтому PB0 управляется OCR0A, а PB1 — OCR0B.

static inline void pwmOff() {
  // Сначала отключаем аппаратный PWM от физических выводов.
  TCCR0A &= ~((1 << COM0A1) | (1 << COM0A0) |
              (1 << COM0B1) | (1 << COM0B0));

  OCR0A = 0;
  OCR0B = 0;

  // И только теперь принудительно держим оба вывода LOW.
  PORTB &= ~((1 << LED1_PIN) | (1 << LED2_PIN));
}

static inline void setBothBrightness(uint8_t b) {
  OCR0A = b;  // PB0 / LED2
  OCR0B = b;  // PB1 / LED1

  TCCR0A |= (1 << COM0A1);
  TCCR0A |= (1 << COM0B1);
}

static inline void eye1On() {
  pwmOff();
  OCR0B = MAX_BRIGHTNESS;       // PB1 / LED1
  TCCR0A |= (1 << COM0B1);      // включаем только OC0B
}

static inline void eye2On() {
  pwmOff();
  OCR0A = MAX_BRIGHTNESS;       // PB0 / LED2
  TCCR0A |= (1 << COM0A1);      // включаем только OC0A
}

void ledsOff() {
  pwmOff();
}

void fadeTo(uint8_t target, uint16_t duration_ms) {
  uint8_t cur = OCR0B;
  if (cur == target) return;

  uint8_t steps = (target > cur) ? (target - cur) : (cur - target);
  uint16_t step_ms = duration_ms / steps;
  if (step_ms < 1) step_ms = 1;

  uint8_t b = cur;

  while (steps--) {
    if (target > cur)
      b++;
    else
      b--;

    setBothBrightness(b);

    uint16_t rem = step_ms;
    while (rem--) _delay_ms(1);
  }
}

void blinkFade(byte c) {
  for (uint8_t i = 0; i < c; i++) {
    fadeTo(MAX_BRIGHTNESS, FADE_MS);
    fadeTo(0, FADE_MS);
  }

  // Гарантированно выключаем оба LED после всей анимации.
  ledsOff();
}

// Два переменных мигания "глазами":
// LED1 -> LED2 -> пауза -> LED1 -> LED2.
// Между переключениями 400 мс.
void blinkEyesTwice() {
  for (uint8_t i = 0; i < 2; i++) {

    // LED1: только PB1 / OC0B.
    eye1On();
    _delay_ms(400);

    // Полностью отключаем аппаратный PWM перед переключением.
    ledsOff();
    _delay_ms(2);

    // LED2: только PB0 / OC0A.
    eye2On();
    _delay_ms(400);

    // Пауза между циклами.
    ledsOff();
    _delay_ms(400);
  }

  // Абсолютно гарантированное выключение.
  ledsOff();
}

void setup() {
  DDRB |= (1 << PB0) | (1 << PB1);

  TCCR0A = (1 << COM0A1) | (1 << COM0B1) |
           (1 << WGM01) | (1 << WGM00);
  TCCR0B = (1 << CS00);

  ledsOff();

  // PB3 и PB2 — входы с подтяжкой.
  pinMode(TRIGGER_PIN, INPUT_PULLUP);
  pinMode(BTN_PIN, INPUT_PULLUP);

  ADCSRA = 0;
  ACSR |= (1 << ACD);

  // PB3 = PCINT3, PB2 = PCINT2.
  PCMSK |= (1 << PCINT3) | (1 << PCINT2);

  GIMSK |= (1 << PCIE);
  sei();

  blinkFade(1);
}

void loop() {
  if (triggered) {
    GIMSK &= ~(1 << PCIE);          // запрещаем PCINT на время fade
    triggered = false;

    blinkFade(BLINK_COUNT);

    // Дополнительная страховка от остаточного состояния PWM.
    ledsOff();

    _delay_ms(50);

    triggered = false;
    buttonPressed = false;

    // Очищаем флаг прерывания после анимации.
    GIFR |= (1 << PCIF);
    GIMSK |= (1 << PCIE);
  }

  if (buttonPressed) {
    GIMSK &= ~(1 << PCIE);          // запрещаем PCINT на время анимации
    buttonPressed = false;

    blinkEyesTwice();

    // После анимации светодиоды гарантированно выключены.
    ledsOff();

    _delay_ms(50);

    buttonPressed = false;

    // Убираем возможный PCINT, возникший из-за дребезга кнопки.
    GIFR |= (1 << PCIF);
    GIMSK |= (1 << PCIE);
  }

  // Финальная страховка перед сном.
  ledsOff();

  goToSleep();
}

void goToSleep() {
  // Перед Power Down ещё раз гарантируем LOW на LED.
  ledsOff();

  set_sleep_mode(SLEEP_MODE_PWR_DOWN);
  sleep_enable();
  sleep_cpu();
  sleep_disable();
}
