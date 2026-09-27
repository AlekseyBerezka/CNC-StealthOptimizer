import difflib

import pandas as pd
import streamlit as st

from core.pipeline import AuditPipeline


st.set_page_config(
    page_title="CNC-StealthOptimizer",
    page_icon="🛠",
    layout="wide",
)

SAMPLE_GCODE = """O1000
G50 S2500
G97 S1500 M03 T0101
G00 X60.0 Z2.0
G01 Z-30.0 F0.25
G00 X80.0 Z50.0
G97 S2000 M03 T0202
G00 X60.0 Z2.0
G01 X40.0 F0.1
G00 X80.0 Z50.0
M30"""


with st.sidebar:
    st.header("⚙️ Параметры станка")

    workpiece_diameter = st.number_input("Диаметр заготовки, мм",
                                         min_value=1.0, value=60.0, step=5.0)
    workpiece_length = st.number_input("Длина заготовки, мм",
                                       min_value=1.0, value=150.0, step=10.0)

    st.subheader("Безопасная позиция")
    auto_safe = st.checkbox(
        "Автоопределение по программе", value=True,
        help="Ищет самую частую точку отхода в УП (напр. X100 Z150).",
    )
    if auto_safe:
        safe_x, safe_z = None, None
    else:
        safe_x = st.number_input("Safe X, мм", min_value=1.0, value=150.0, step=10.0)
        safe_z = st.number_input("Safe Z, мм", min_value=1.0, value=200.0, step=10.0)

    st.subheader("Временные параметры")
    tool_change_time = st.number_input("Смена инструмента, сек.",
                                       min_value=0.1, value=3.0, step=0.5)
    rapid_speed_mpm = st.number_input("Скорость G00, м/мин",
                                      min_value=1.0, value=15.0, step=1.0)

    st.subheader("ИИ-аудит")
    model_name = st.text_input("Модель Ollama", value="qwen2.5-coder:7b")
    ai_timeout = st.number_input("Таймаут ИИ, сек.", min_value=30, max_value=1200,
                                 value=300, step=30,
                                 help="7B на CPU обрабатывает 200-строчные УП 2–5 минут.")
    ai_max_lines = st.number_input("Строк УП для ИИ", min_value=40, max_value=400,
                                   value=150, step=10,
                                   help="Если УП длиннее — берём голову+хвост.")
    skip_ai = st.checkbox("Пропустить ИИ-аудит", value=False)


st.title("🛠 CNC-StealthOptimizer")
st.caption("Аудитор УП Fanuc. Структурная безопасность + рекомендации ИИ.")


st.subheader("📥 Исходная УП")
uploaded = st.file_uploader("Загрузить .nc / .txt / .tap",
                            type=["nc", "txt", "tap", "mpf"])
if uploaded is not None:
    gcode_text = uploaded.read().decode("utf-8", errors="replace")
else:
    gcode_text = st.text_area("Или вставьте код вручную:",
                              value=SAMPLE_GCODE, height=300)

run_button = st.button("🚀 Запустить аудит", type="primary",
                       use_container_width=True)


if run_button and gcode_text.strip():
    source_gcode = [ln for ln in gcode_text.splitlines() if ln.strip()]
    with st.spinner("Анализируем УП..."):
        pipeline = AuditPipeline(
            model_name=model_name,
            workpiece_diameter=workpiece_diameter,
            workpiece_length=workpiece_length,
            safe_x=safe_x,
            safe_z=safe_z,
            tool_change_time=tool_change_time,
            rapid_speed_mpm=rapid_speed_mpm,
            ai_timeout=int(ai_timeout),
            ai_max_lines=int(ai_max_lines),
        )
        result = pipeline.run(source_gcode, skip_ai=skip_ai)
    st.session_state["result"] = result


if "result" not in st.session_state:
    st.info("👈 Загрузите УП и нажмите «Запустить аудит».")
    st.stop()

r = st.session_state["result"]
b, f, d = r["baseline"], r["final"], r["delta"]


st.markdown("---")
st.header("📊 Результаты аудита")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Базовое время", f"{b['total_time_seconds']} сек.")
c2.metric("После аудита", f"{f['total_time_seconds']} сек.",
          delta=f"{d['total_seconds']:+.2f} сек.")
c3.metric("Смены инструмента", len(b["tool_sequence"]))
c4.metric("Безопасность",
          "✅ OK" if r["is_safe"] else "❌ БЛОК",
          delta_color="normal" if r["is_safe"] else "inverse")


st.subheader("⏱ Разложение времени по фазам")
chart_df = pd.DataFrame({
    "Фаза": ["Резание", "Быстрые ходы", "Сверление",
             "Смены инструмента"] * 2,
    "Секунды": [
        b["cutting_time_seconds"], b["rapid_time_seconds"],
        b["drilling_time_seconds"], b["tool_change_time_seconds"],
        f["cutting_time_seconds"], f["rapid_time_seconds"],
        f["drilling_time_seconds"], f["tool_change_time_seconds"],
    ],
    "Версия": ["До аудита"] * 4 + ["После аудита"] * 4,
})
st.bar_chart(chart_df, x="Фаза", y="Секунды", color="Версия",
             use_container_width=True)


all_warnings = (b.get("warnings", []) + r.get("integrity_warnings", []))
if all_warnings:
    st.subheader("⚠️ Предупреждения")
    for w in all_warnings:
        st.warning(w)


st.subheader("🤖 Отчёт ИИ-аудитора")
ai = r["ai"]
if ai.get("error"):
    st.error(f"Ошибка связи с Ollama: {ai['error']}")
    if ai.get("hint"):
        st.info(ai["hint"])
elif ai.get("skipped"):
    st.warning("ИИ-аудит был пропущен.")
else:
    st.markdown(f"**Анализ:** {ai.get('analysis') or '—'}")
    recs = ai.get("recommendations", [])
    if recs:
        st.markdown("**Рекомендации технологу:**")
        for rec in recs:
            st.markdown(f"- {rec}")
    else:
        st.caption("Автоматических рекомендаций нет.")
    if ai.get("estimated_savings_seconds"):
        pot = ai["estimated_savings_seconds"]
        hours = (pot * 1_000_000) / 3600.0
        st.warning(
            f"💰 Потенциал экономии: **~{pot} сек./дет.** "
            f"({hours:.0f} маш/часов на 1 млн). "
            f"Требует подтверждения технолога."
        )


st.subheader("🔧 Структурные изменения")
for m in r["compile_logs"]:
    st.markdown(f"- {m}")


st.subheader("🛡 Валидация безопасности")
for log in r["safety_logs"]:
    if "[УСПЕХ" in log:
        st.success(log)
    elif "КРИТИЧЕСК" in log:
        st.error(log)
    elif "ПРЕДУПРЕЖ" in log or "ПЕРЕХВАТ" in log:
        st.warning(log)
    else:
        st.info(log)


with st.expander("🔍 Диагностика T-кодов"):
    for idx, tool, raw, clean in r["tools_debug"]:
        st.text(f"строка {idx:>3}: T{tool}   ←  {raw}")


st.subheader("📝 Diff: до / после")
diff = list(difflib.unified_diff(
    r["source_gcode"], r["final_gcode"],
    fromfile="source.nc", tofile="optimized.nc", lineterm="",
))
if diff:
    st.code("\n".join(diff), language="diff")
else:
    st.caption("Изменений нет.")


st.subheader("📄 Безопасная УП")
final_text = "\n".join(r["final_gcode"])
st.code(final_text, language="gcode")
st.download_button("💾 Скачать optimized_fanuc.nc",
                   data=final_text,
                   file_name="optimized_fanuc.nc",
                   mime="text/plain",
                   use_container_width=True)
