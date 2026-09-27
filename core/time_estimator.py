import re


class FanucTimeEstimator:
    """
    Оценщик машинного времени токарной УП для Fanuc.

    Особенности:
      - работает с «плотным» форматом Fanuc без пробелов: G00X100.Z150.
      - учитывает G96 (постоянная скорость резания) и G97 (обороты).
      - считает G00 (rapid), G01/G02/G03 (резание) и G83 (peck-сверление).
      - предупреждает о G71/G70 — их внутренние проходы пока не симулируются.
      - считает M01 как опциональную паузу (по умолчанию 0 сек.,
        но с предупреждением о возможных задержках).
    """

    DEFAULT_RAPID_SPEED_MPM = 15.0

    # Адресные регексы. (?<![A-Z]) — адрес не должен быть частью слова,
    # но должен ловить X в G00X100 без пробелов.
    X_RE = re.compile(r'(?<![A-Z])X\s*(-?[\d.]+)')
    Z_RE = re.compile(r'(?<![A-Z])Z\s*(-?[\d.]+)')
    F_RE = re.compile(r'(?<![A-Z])F\s*([\d.]+)')
    S_RE = re.compile(r'(?<![A-Z])S\s*(\d+)')
    # T-код: фиксированно 2 цифры номера + 0..2 цифры корректора, без лишних цифр.
    T_RE = re.compile(r'(?<![A-Z])T(\d{1,2})(\d{0,2})(?![0-9])')
    G50_RE = re.compile(r'(?<![A-Z])G50.*?S\s*(\d+)')
    MOTION_RE = re.compile(r'(?<![A-Z])G0?([0-3])(?![0-9])')
    M01_RE = re.compile(r'(?<![A-Z])M01(?![0-9])')
    ADDR_RE = re.compile(r'(?<![A-Z])([XZRFQSP])\s*(-?[\d.]+)')

    def __init__(self,
                 tool_change_time: float = 3.0,
                 rapid_speed_mpm: float = DEFAULT_RAPID_SPEED_MPM):
        self.tool_change_time = float(tool_change_time)
        self.rapid_speed_mpm = float(rapid_speed_mpm)

    # ------------------------------------------------------------------ #

    @staticmethod
    def _strip_line(raw_line: str) -> str:
        """Убирает все виды комментариев Fanuc: (..), <..>, ;, %, \\."""
        line = raw_line.upper()
        line = re.sub(r'\(.*?\)', '', line)
        line = re.sub(r'<.*?>', '', line)
        line = line.split(';')[0]
        line = re.sub(r'[%\\]', '', line)
        return line.strip()

    @staticmethod
    def _parse_addresses(line: str) -> dict:
        """Все адреса в кадре: {'X': 100.0, 'Z': 150.0, ...}."""
        out = {}
        for m in FanucTimeEstimator.ADDR_RE.finditer(line):
            try:
                out[m.group(1)] = float(m.group(2))
            except ValueError:
                continue
        return out

    @staticmethod
    def _rpm_from_g96(surface_speed_mpm: float,
                      diameter_mm: float,
                      max_rpm: int) -> float:
        d = max(abs(diameter_mm), 0.1)
        rpm = (surface_speed_mpm * 1000.0) / (3.141592653589793 * d)
        return min(rpm, max_rpm)

    # ------------------------------------------------------------------ #

    def debug_tool_codes(self, gcode_lines):
        """Диагностика: список (номер_строки, tool, original_line, cleaned)."""
        found = []
        for i, raw in enumerate(gcode_lines, 1):
            clean = self._strip_line(raw)
            if not clean:
                continue
            m = self.T_RE.search(clean)
            if m:
                tool = m.group(1).zfill(2)
                found.append((i, tool, raw.rstrip(), clean))
        return found

    # ------------------------------------------------------------------ #

    def estimate_cycle_time(self, gcode_lines):
        total_cutting = 0.0
        total_rapid = 0.0
        total_tool_change = 0.0
        total_drilling = 0.0

        tool_sequence = []
        current_tool = None

        curr_x, curr_z = 0.0, 0.0
        curr_feed = 0.2
        curr_surface_speed = 120.0
        max_rpm = 4000
        has_g50 = False

        mode = 'G97'
        motion = 'G00'

        saw_g71 = False
        saw_g70 = False
        m01_count = 0

        for raw_line in gcode_lines:
            line = self._strip_line(raw_line)
            if not line:
                continue

            # --- Смена инструмента ---
            m_t = self.T_RE.search(line)
            if m_t:
                tool_num = m_t.group(1).zfill(2)
                if current_tool != tool_num:
                    total_tool_change += self.tool_change_time
                    tool_sequence.append(tool_num)
                    current_tool = tool_num

            # --- Ограничение оборотов ---
            m_g50 = self.G50_RE.search(line)
            if m_g50:
                max_rpm = int(m_g50.group(1))
                has_g50 = True

            # --- Режим шпинделя ---
            if 'G96' in line:
                mode = 'G96'
            elif 'G97' in line:
                mode = 'G97'

            # --- Подача / скорость ---
            m_f = self.F_RE.search(line)
            if m_f:
                curr_feed = float(m_f.group(1))
            m_s = self.S_RE.search(line)
            if m_s:
                curr_surface_speed = float(m_s.group(1))

            # --- Считаем M01 (опциональные стопы) ---
            if self.M01_RE.search(line):
                m01_count += 1

            # --- Отметки про циклы ---
            if 'G71' in line or 'G72' in line:
                saw_g71 = True
            if 'G70' in line:
                saw_g70 = True

            # --- G83 peck-сверление ---
            if 'G83' in line:
                addrs = self._parse_addresses(line)
                z_target = addrs.get('Z', 0.0)
                r_plane = addrs.get('R', 2.0)
                q_val = addrs.get('Q', 3000.0)
                f_drill = addrs.get('F', 0.1)

                # Q обычно в микронах (3000 = 3мм), но иногда в мм.
                q_mm = q_val / 1000.0 if q_val > 50 else q_val
                q_mm = max(q_mm, 0.5)

                depth = abs(z_target - r_plane)

                if mode == 'G96':
                    rpm = self._rpm_from_g96(curr_surface_speed,
                                             abs(addrs.get('X', 10.0)) or 10.0,
                                             max_rpm)
                else:
                    rpm = curr_surface_speed or 1000

                if f_drill > 0 and rpm > 0:
                    # Основное время + ~80% на отводы и повторные врезания
                    total_drilling += (depth / (f_drill * rpm)) * 60.0 * 1.8

                if 'Z' in addrs:
                    curr_z = addrs['Z']
                if 'X' in addrs:
                    curr_x = addrs['X']
                continue

            # --- Модальное движение ---
            m_motion = self.MOTION_RE.search(line)
            if m_motion:
                motion = f"G0{m_motion.group(1)}"

            # --- Координаты ---
            m_x = self.X_RE.search(line)
            m_z = self.Z_RE.search(line)
            if not (m_x or m_z):
                continue

            new_x = float(m_x.group(1)) if m_x else curr_x
            new_z = float(m_z.group(1)) if m_z else curr_z

            dx = (new_x - curr_x) / 2.0
            dz = new_z - curr_z
            distance = (dx ** 2 + dz ** 2) ** 0.5

            if motion in ('G01', 'G02', 'G03'):
                if mode == 'G96':
                    avg_d = (abs(curr_x) + abs(new_x)) / 2.0
                    rpm = self._rpm_from_g96(curr_surface_speed, avg_d, max_rpm)
                else:
                    rpm = curr_surface_speed
                if curr_feed > 0 and rpm > 0:
                    total_cutting += (distance / (curr_feed * rpm)) * 60.0
            elif motion == 'G00':
                rapid_mm_per_min = self.rapid_speed_mpm * 1000.0
                if rapid_mm_per_min > 0:
                    total_rapid += (distance / rapid_mm_per_min) * 60.0

            curr_x, curr_z = new_x, new_z

        # --- Избыточность ---
        redundant_tools = []
        seen = set()
        last = None
        for t in tool_sequence:
            if t != last:
                if t in seen:
                    redundant_tools.append(t)
                seen.add(t)
                last = t

        total_time = (total_cutting + total_rapid +
                      total_tool_change + total_drilling)

        report = {
            "total_time_seconds": round(total_time, 2),
            "cutting_time_seconds": round(total_cutting, 2),
            "rapid_time_seconds": round(total_rapid, 2),
            "drilling_time_seconds": round(total_drilling, 2),
            "tool_change_time_seconds": round(total_tool_change, 2),
            "tool_sequence": tool_sequence,
            "m01_count": m01_count,
            "has_redundancy": len(redundant_tools) > 0,
            "redundant_tools": sorted(set(redundant_tools)),
            "warnings": [],
        }

        if not has_g50:
            report["warnings"].append(
                "G50 не найден в программе — при работе в G96 "
                "ограничение оборотов отсутствует."
            )

        if saw_g71 or saw_g70:
            report["warnings"].append(
                "Обнаружены циклы G71/G70. Их внутренние черновые проходы "
                "симулируются приблизительно — учтены только явные G01-кадры "
                "в блоках N..N. Реальное время цикла может быть больше."
            )

        if m01_count:
            report["warnings"].append(
                f"Обнаружено {m01_count} кадров M01 (опциональный стоп). "
                f"Если оператор нажимает кнопку M01 — каждая пауза может "
                f"добавить 1–3 секунды. В расчёте это время не учтено."
            )

        return report
