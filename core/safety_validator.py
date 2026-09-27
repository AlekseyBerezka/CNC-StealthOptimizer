import re


class FanucSafetyValidator:
    """
    Валидатор физической безопасности УП Fanuc.

    Проверки:
      1. G50 S0 — автозамена на безопасное значение.
      2. G96 без предшествующего G50 — авто-вставка G50.
    """

    MOTION_RE = re.compile(r'(?<![A-Z])G0?([0-3])(?![0-9])')
    X_RE = re.compile(r'(?<![A-Z])X\s*(-?[\d.]+)')
    Z_RE = re.compile(r'(?<![A-Z])Z\s*(-?[\d.]+)')
    S0_RE = re.compile(r'(?<![A-Z])S0(?!\d)')

    def __init__(self,
                 workpiece_diameter: float = 60.0,
                 workpiece_length: float = 150.0,
                 safe_g50_rpm: int = 2000):
        self.wp_dia = float(workpiece_diameter)
        self.wp_len = float(workpiece_length)
        self.safe_g50_rpm = int(safe_g50_rpm)

    # ------------------------------------------------------------------ #

    @staticmethod
    def _strip(line: str) -> str:
        line = line.upper()
        line = re.sub(r'\(.*?\)', '', line)
        line = re.sub(r'<.*?>', '', line)
        line = re.sub(r'[%\\]', '', line)
        return line.split(';')[0].strip()

    # ------------------------------------------------------------------ #

    def validate_and_fix_program(self, gcode_lines):
        logs = []
        fixed = []
        is_safe = True
        has_g50 = False

        for idx, raw_line in enumerate(gcode_lines, 1):
            line = self._strip(raw_line)
            if not line:
                fixed.append(raw_line)
                continue

            # --- G50 S0 → заменить ---
            if 'G50' in line and self.S0_RE.search(line):
                replaced = re.sub(
                    r'(?<![A-Z])S0(?!\d)',
                    f'S{self.safe_g50_rpm}',
                    raw_line,
                )
                fixed.append(replaced)
                logs.append(
                    f"[АВТО-ИСПРАВЛЕНИЕ][Кадр {idx}]: "
                    f"G50 S0 заменён на G50 S{self.safe_g50_rpm}."
                )
                has_g50 = True
                continue

            # --- Отслеживаем G50 ---
            if 'G50' in line and re.search(r'(?<![A-Z])S\d+', line):
                has_g50 = True

            # --- G96 без G50 → вставить G50 ---
            if 'G96' in line and not has_g50:
                fixed.append(
                    f"G50 S{self.safe_g50_rpm} (AUTO INSERTED BEFORE G96);"
                )
                logs.append(
                    f"[АВТО-ИСПРАВЛЕНИЕ][Кадр {idx}]: "
                    f"Перед G96 добавлено G50 S{self.safe_g50_rpm}."
                )
                has_g50 = True

            fixed.append(raw_line)

        if is_safe:
            logs.append(
                "[УСПЕХ] Валидатор: критических ошибок не обнаружено."
            )

        return is_safe, logs, fixed
