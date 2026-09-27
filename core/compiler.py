import re
from collections import Counter


class StructuralCompiler:
    """
    Гарантирует структурную безопасность УП:

      1. Перед каждым T-вызовом — отход за габариты заготовки,
         если его ещё нет.
      2. M30/M02 — в конце основной программы.

    ВАЖНО: файл может содержать несколько вариантов обработки,
    разделённых M30 (например, резьба метчиком ИЛИ резьба резцом).
    Анализируется ТОЛЬКО основная программа — до первого M30.
    Всё, что после — передаётся как есть, без правок.
    """

    T_RE = re.compile(r'(?<![A-Z])T(\d{1,2})(\d{0,2})(?![0-9])')
    FINISH_RE = re.compile(r'(?<![A-Z])M(0?2|0?30)(?![0-9])')
    X_RE = re.compile(r'(?<![A-Z])X\s*(-?[\d.]+)')
    Z_RE = re.compile(r'(?<![A-Z])Z\s*(-?[\d.]+)')

    def __init__(self,
                 safe_x: float = None,
                 safe_z: float = None,
                 workpiece_diameter: float = 60.0,
                 workpiece_length: float = 150.0,
                 safety_margin: float = 5.0,
                 skip_first_retract: bool = False):
        self._safe_x = safe_x
        self._safe_z = safe_z
        self.wp_dia = float(workpiece_diameter)
        self.wp_len = float(workpiece_length)
        self.margin = float(safety_margin)
        self.skip_first_retract = skip_first_retract

    # ------------------------------------------------------------------ #
    #  Утилиты
    # ------------------------------------------------------------------ #

    @staticmethod
    def _strip(line: str) -> str:
        line = line.upper()
        line = re.sub(r'\(.*?\)', '', line)
        line = re.sub(r'<.*?>', '', line)
        line = re.sub(r'[%\\]', '', line)
        return line.split(';')[0].strip()

    @property
    def _min_safe_x(self) -> float:
        return self.wp_dia + self.margin

    @property
    def _min_safe_z(self) -> float:
        return self.margin

    # ------------------------------------------------------------------ #
    #  Разделение на основную программу и варианты
    # ------------------------------------------------------------------ #

    def _split_program(self, gcode_lines):
        """
        Разделяет УП на основную программу и хвост.

        Возвращает (main_including_m30, tail_after).

        В реальной практике технологи пишут несколько вариантов
        обработки одной операции (например, нарезание резьбы метчиком
        ИЛИ резцом), разделяя их M30. Оператор запускает нужный вариант
        с определённой строки. Аудитор анализирует только первый —
        это основная программа.
        """
        for i, line in enumerate(gcode_lines):
            if self.FINISH_RE.search(line.upper()):
                return gcode_lines[:i + 1], gcode_lines[i + 1:]
        return list(gcode_lines), []

    # ------------------------------------------------------------------ #
    #  Определение safe-позиции
    # ------------------------------------------------------------------ #

    def _find_return_point(self, gcode_lines):
        candidates = Counter()
        for raw in gcode_lines:
            clean = self._strip(raw)
            if not clean:
                continue
            m_x = self.X_RE.search(clean)
            m_z = self.Z_RE.search(clean)
            if not (m_x and m_z):
                continue
            try:
                fx = float(m_x.group(1))
                fz = float(m_z.group(1))
            except ValueError:
                continue
            if fx < self._min_safe_x and fz < self._min_safe_z:
                continue
            pair = (round(fx, 1), round(fz, 1))
            candidates[pair] += 1

        if candidates:
            return max(
                candidates.items(),
                key=lambda kv: (kv[1], -kv[0][0] - kv[0][1]),
            )[0]
        return None

    def _fallback_safe_point(self, gcode_lines):
        max_x = 0.0
        for raw in gcode_lines:
            s = self._strip(raw)
            m_x = self.X_RE.search(s)
            if m_x:
                try:
                    max_x = max(max_x, float(m_x.group(1)))
                except ValueError:
                    pass
        return (max(max_x, self.wp_dia) + self.margin,
                self.margin + 100.0)

    def _get_safe_position(self, gcode_lines):
        if self._safe_x is not None and self._safe_z is not None:
            return self._safe_x, self._safe_z, "задана пользователем"
        found = self._find_return_point(gcode_lines)
        if found:
            return (found[0], found[1],
                    f"самая частая точка отхода ({found[0]:.0f}, {found[1]:.0f})")
        fb = self._fallback_safe_point(gcode_lines)
        return (fb[0], fb[1],
                "вычислена как max(X)+margin, margin+100")

    # ------------------------------------------------------------------ #
    #  Детектор «уже отведён»
    # ------------------------------------------------------------------ #

    def _already_retracted(self, result_buffer) -> bool:
        """
        Идём назад от текущей позиции до предыдущего T-вызова.
        Собираем максимальные X и Z, которых станок достиг в этом окне.

        Отход считается существующим, если:
          - max(X) >= wp_dia + margin  (резец вышел за диаметр детали),
          ИЛИ
          - max(Z) >= margin            (резец вышел за торец детали).
        """
        max_x = None
        max_z = None

        for line in reversed(result_buffer):
            s = self._strip(line)
            if not s:
                continue

            if self.T_RE.search(s):
                break

            m_x = self.X_RE.search(s)
            m_z = self.Z_RE.search(s)
            if m_x:
                try:
                    fx = float(m_x.group(1))
                    max_x = fx if max_x is None else max(max_x, fx)
                except ValueError:
                    pass
            if m_z:
                try:
                    fz = float(m_z.group(1))
                    max_z = fz if max_z is None else max(max_z, fz)
                except ValueError:
                    pass

        if max_x is not None and max_x >= self._min_safe_x:
            return True
        if max_z is not None and max_z >= self._min_safe_z:
            return True
        return False

    def _has_any_tool_before(self, result_buffer) -> bool:
        for line in result_buffer:
            if self.T_RE.search(self._strip(line)):
                return True
        return False

    # ------------------------------------------------------------------ #
    #  Формирование кадров безопасности
    # ------------------------------------------------------------------ #

    def _safety_frames(self, sx: float, sz: float):
        return [
            f"G00Z{sz:.1f}(SAFE Z - RETRACT);",
            f"G00X{sx:.1f}(SAFE X - RETRACT);",
        ]

    # ------------------------------------------------------------------ #
    #  Основной проход по программе
    # ------------------------------------------------------------------ #

    def insert_safety_frames(self, gcode_lines):
        logs = []
        result = []
        sx, sz, how = self._get_safe_position(gcode_lines)
        logs.append(f"Safe-позиция: X{sx:.1f} Z{sz:.1f} ({how}).")
        logs.append(
            f"Безопасная зона: X >= {self._min_safe_x:.1f} "
            f"ИЛИ Z >= {self._min_safe_z:.1f}."
        )

        for i, line in enumerate(gcode_lines):
            if self.T_RE.search(line.upper()):
                is_first = not self._has_any_tool_before(result)

                if is_first and self.skip_first_retract:
                    logs.append(
                        f"Строка {i + 1}: первый T-вызов, отход пропущен "
                        f"по настройке."
                    )
                elif self._already_retracted(result):
                    logs.append(
                        f"Строка {i + 1}: отход уже есть — кадры не добавлены."
                    )
                else:
                    result.extend(self._safety_frames(sx, sz))
                    logs.append(
                        f"Строка {i + 1}: добавлены кадры отхода "
                        f"X{sx:.1f} Z{sz:.1f}."
                    )
            result.append(line)

        return result, logs

    def enforce_finish_code(self, gcode_lines):
        """
        Переносит M30/M02 в самый конец переданного фрагмента.
        На входе ожидается main_including_m30 — ровно один M30.
        """
        logs = []
        cleaned = []
        finish_frames = []

        for i, line in enumerate(gcode_lines):
            if self.FINISH_RE.search(line.upper()):
                finish_frames.append(line.strip())
                logs.append(
                    f"M30/M02 найден в строке {i + 1}: перенесён в конец."
                )
                continue
            cleaned.append(line)

        if not finish_frames:
            cleaned.append("M30")
            logs.append("M30/M02 отсутствовал — добавлен M30 в конец.")
        else:
            cleaned.append(finish_frames[-1])

        return cleaned, logs

    # ------------------------------------------------------------------ #
    #  Публичный интерфейс
    # ------------------------------------------------------------------ #

    def compile(self, source_gcode):
        logs = []

        # 1. Разделяем на основную программу и хвост вариантов.
        main, tail = self._split_program(source_gcode)

        if tail:
            logs.append(
                f"Основная программа заканчивается M30/M02; "
                f"далее {len(tail)} строк альтернативных вариантов — "
                f"не анализируются и остаются без изменений."
            )

        # 2. Обрабатываем только основную программу.
        step1, logs_a = self.insert_safety_frames(main)
        step2, logs_b = self.enforce_finish_code(step1)

        # 3. Приклеиваем хвост обратно.
        result = step2 + tail

        return result, logs + logs_a + logs_b
