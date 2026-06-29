import json
import os
import pathlib

import streamlit as st

from main import app
from tools.global_asset_index import load_global_index
from tools.ollama_runtime import (
    get_coder_model_name,
    get_vision_model_name,
    is_model_installed,
    list_installed_models,
    list_running_models,
    unload_running_models,
)
from tools.runtime_paths import WORKSPACE_ROOT, ensure_run_paths, make_run_id
from tools.vision_analyzer import analyze_image_with_vision

_BASE_DIR = pathlib.Path(__file__).parent.resolve()
_REGISTRY_PATH = str(_BASE_DIR / "registry" / "agents.json")

st.set_page_config(page_title="Ares AI Ecosystem v2.0", layout="wide")

st.markdown("""\n<style>\n@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');\nhtml, body, [class*="css"] {\n    font-family: 'Inter', sans-serif;\n    background-color: #0d1117;\n}\n.stButton>button {\n    color: white;\n    background-color: #BB0000;\n    border: none;\n}\n</style>\n""", unsafe_allow_html=True)


def _list_demo_images(run_workspace_dir: str | None = None) -> list[str]:
    candidates = []
    roots = []
    if run_workspace_dir:
        roots.append(pathlib.Path(run_workspace_dir))
    roots.append(WORKSPACE_ROOT)

    seen = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
                continue
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            candidates.append(resolved)
            if len(candidates) >= 25:
                return candidates
    return candidates


with st.sidebar:
    st.header("Agent Registry")
    registry_path = _REGISTRY_PATH
    if os.path.exists(registry_path):
        try:
            with open(registry_path, "r", encoding="utf-8") as handle:
                agents = json.load(handle)
        except json.JSONDecodeError:
            agents = []

        if not agents:
            st.info("Registry is empty — agents will appear here after first run.")
        else:
            for index, agent in enumerate(agents):
                with st.expander(agent.get("agent_name", "Unknown")):
                    st.caption(agent.get("system_prompt", "")[:100] + "...")
                    if st.button("Delete", key=f"del_{index}"):
                        agents.pop(index)
                        with open(registry_path, "w", encoding="utf-8") as handle:
                            json.dump(agents, handle, indent=4)
                        st.rerun()
    else:
        st.info("Registry file not found.")

    st.divider()
    st.header("Ollama Runtime")
    st.caption(f"Coder: {get_coder_model_name()}")
    st.caption(f"Vision: {get_vision_model_name()}")
    vision_ready = is_model_installed(get_vision_model_name())
    if vision_ready:
        st.success("Vision Ready")
    else:
        st.warning("Vision Missing")

    running_models = list_running_models()
    installed_models = list_installed_models()
    global_index = load_global_index()
    global_assets = global_index.get("assets", [])
    accepted_cached = [
        asset for asset in global_assets
        if asset.get("status") == "accepted" and asset.get("exists", True)
    ]
    st.caption(
        f"Global cache: {len(global_assets)} indexed / {len(accepted_cached)} accepted"
    )
    if st.button("Unload Loaded Models", key="unload_models"):
        unloaded = unload_running_models()
        if unloaded:
            st.success("Unloaded: " + ", ".join(unloaded))
        else:
            st.info("No loaded models were found.")
        running_models = list_running_models()
    if running_models:
        st.caption("Loaded now:")
        st.json(running_models)
    else:
        st.caption("Loaded now: none")
    if installed_models:
        st.caption("Installed:")
        st.json(installed_models)


    st.divider()
    st.sidebar.markdown("### 🖼️ Джерела зображень")
    col1, col2, col3 = st.sidebar.columns(3)
    with col1:
        st.toggle("🌐 Інтернет", value=True, key="web_images")
    with col2:
        st.toggle("🤖 AI", value=True, key="ai_images")
    with col3:
        st.toggle("📁 Локальні", value=False, key="local_images")
    st.divider()
    st.sidebar.slider("Sandbox timeout (сек)", 30, 600, 120, key="sandbox_timeout")
    st.divider()
    st.header("Vision Quick Test")
    current_run_workspace = None
    if "ares_state" in st.session_state and st.session_state.ares_state:
        current_run_workspace = st.session_state.ares_state.get("run_workspace_dir")
    demo_images = _list_demo_images(current_run_workspace)
    selected_demo_image = st.selectbox(
        "Sample image",
        options=[""] + demo_images,
        index=0,
        key="vision_demo_image",
    )
    demo_queries_text = st.text_area(
        "Queries (one per line)",
        value="",
        key="vision_demo_queries",
        height=80,
        placeholder="e.g. 2022 Audi Q7 exterior photo",
    )
    if st.button("Run Vision Test", key="run_vision_test"):
        if not selected_demo_image:
            st.warning("Choose an image first.")
        else:
            queries = [line.strip() for line in demo_queries_text.splitlines() if line.strip()]
            with st.spinner("Analyzing image with the vision model..."):
                result = analyze_image_with_vision(selected_demo_image, queries, unload_after=False)
            st.json(result)

st.title("Ares AI Ecosystem v2.0 — Control Panel")

if "ares_state" not in st.session_state:
    st.session_state.ares_state = None
if "ui_logs" not in st.session_state:
    st.session_state.ui_logs = []


def _safe_state_update(target: dict, updates: dict) -> None:
    """Merges update dict into target, skipping None values to prevent overwriting valid state."""
    for key, value in updates.items():
        if value is not None:
            target[key] = value


def run_graph() -> None:
    print("\n" + "=" * 60)
    print("🚀 ARES AI v2.0 — RUN STARTING")
    print(f"TASK: {st.session_state.ares_state.get('original_task', '')}")
    print("=" * 60)
    with st.spinner("Agents are working..."):
        for output in app.stream(st.session_state.ares_state):
            for node_name, node_data in output.items():
                print(f"✅ [NODE FINISHED]: {node_name.upper()}")
                st.session_state.ui_logs.append(
                    {"role": "agent", "name": node_name, "content": node_data}
                )
                # Safe merge: skip None values to avoid clobbering valid state
                _safe_state_update(st.session_state.ares_state, node_data)
    print("=" * 60)
    print("🏁 EXECUTION COMPLETE OR WAITING FOR HUMAN")
    print("=" * 60 + "\n")


tab_main, tab_history = st.tabs(["🚀 Control Panel", "📋 Run History"])

with tab_history:
    st.header("📋 Run History")
    logs_dir = pathlib.Path("logs")
    if logs_dir.exists():
        log_files = sorted(list(logs_dir.glob("*.jsonl")), reverse=True)
        if log_files:
            selected_log = st.selectbox("Select Run", options=log_files, format_func=lambda x: x.name)
            if selected_log:
                try:
                    with open(selected_log, "r", encoding="utf-8") as f:
                        events = [json.loads(line) for line in f]
                    events.reverse()
                    
                    event_types = list(set(e.get("event") for e in events if "event" in e))
                    selected_filter = st.multiselect("Filter by event", options=event_types, default=event_types)
                    
                    for event in events:
                        if event.get("event") in selected_filter:
                            with st.expander(f"[{event.get('ts')[:19]}] {str(event.get('node')).upper()} - {event.get('event')}"):
                                st.json(event.get("data", {}))
                except Exception as e:
                    st.error(f"Error loading log: {e}")
        else:
            st.info("No run logs found.")
    else:
        st.info("Logs directory does not exist.")

with tab_main:
    
    st.markdown("---")
    uploaded = st.file_uploader("📎 Прикріпити файл (PDF/Excel/CSV)", type=["pdf", "xlsx", "csv"])
    if uploaded:
        save_path = f"workspace/{uploaded.name}"
        os.makedirs("workspace", exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(uploaded.getvalue())
        if "ares_state" not in st.session_state or st.session_state.ares_state is None:
            st.session_state.ares_state = {}
        st.session_state.ares_state["uploaded_file_path"] = save_path
        st.success(f"Файл {uploaded.name} завантажено!")
    task_input = st.text_area(
        "Enter the task for the Ecosystem:",
        placeholder="e.g. Create a Word report for Audi Q7 2022 in top configuration",
    )

    if st.button("Run Ecosystem"):
        if not task_input.strip():
            st.warning("Please enter a task.")
        else:
            run_paths = ensure_run_paths(make_run_id())
            st.session_state.ares_state = {
                "original_task": task_input,
                "image_settings": {
                    "web_images_enabled": st.session_state.get("web_images", True),
                    "ai_images_enabled": st.session_state.get("ai_images", True),
                    "local_images_enabled": st.session_state.get("local_images", False),
                    "images_disabled": not (
                        st.session_state.get("web_images", True) or
                        st.session_state.get("ai_images", True) or
                        st.session_state.get("local_images", False)
                    )
                },
                "qa_attempts": 0,
                "execution_attempts": 0,
                **run_paths,
                "step_count": 0,
                "max_steps": 35,
                "human_response": "",
                "core_memory": [],
                "asset_requirements": [],
                "missing_image_queries": [],
                "accepted_asset_paths": [],
                "qa_passed": False,
                "sub_agent_plan": [],
                "plan_needs_approval": False,
                "plan_approved": False,
                "current_sub_agent_index": 0,
                "sub_agent_results": {},
            }
            st.session_state.ui_logs = []
            st.session_state.ui_logs.append({"role": "user", "content": task_input})
            run_graph()
            st.rerun()

    for log in st.session_state.ui_logs:
        if log["role"] == "user":
            st.chat_message("user").write(log["content"])
        elif log["role"] == "agent":
            with st.expander(f"Agent Log: {log['name'].upper()}"):
                st.json(log["content"])

    if st.session_state.ares_state:
        state = st.session_state.ares_state
        st.caption(f"Run ID: {state.get('run_id', 'unknown')}")

        # ── Multi-Agent Pipeline Progress ────────────────────────────────────────
        sub_plan = state.get("sub_agent_plan") or []
        if sub_plan:
            st.markdown("---")
            st.subheader("📋 Pipeline Progress")
            current_idx = state.get("current_sub_agent_index", 0)
            progress_val = current_idx / len(sub_plan) if len(sub_plan) > 0 else 0
            st.progress(progress_val)
            
            sub_results = state.get("sub_agent_results") or {}
            for i, task in enumerate(sub_plan):
                tid = task.get("task_id", f"task_{i}")
                if tid in sub_results:
                    st.success(f"✅ {tid}")
                elif i == current_idx:
                    st.warning(f"⚡ {tid}")

        if state.get("plan_needs_approval") and not state.get("plan_approved"):
            st.markdown("---")
            st.warning("План створено. Будь ласка, перегляньте та затвердіть його.")
            
            edited_plan = st.data_editor(state.get("sub_agent_plan", []), key="plan_editor")
            
            col1, col2, col3 = st.columns(3)
            if col1.button("✅ Затвердити план"):
                st.session_state.ares_state["sub_agent_plan"] = edited_plan
                st.session_state.ares_state["plan_approved"] = True
                run_graph()
                st.rerun()
            if col3.button("❌ Скасувати"):
                st.session_state.ares_state["step_count"] = state.get("max_steps", 35)
                st.rerun()

        elif state.get("needs_human_help"):
            question = state.get("clarifier_message") or "Additional information is needed."
            st.warning(question)
            user_reply = st.chat_input("Reply to the agent...")
            if user_reply:
                st.session_state.ui_logs.append({"role": "user", "content": user_reply})
                st.session_state.ares_state["human_response"] = user_reply
                run_graph()
                st.rerun()

        elif not state.get("needs_human_help") and state.get("final_result") is not None:
            st.markdown("---")
            st.subheader("Final Result")

            memory = state.get("core_memory") or []
            if memory:
                with st.expander("Core Memory Log", expanded=False):
                    for index, entry in enumerate(memory):
                        st.markdown(f"`{index + 1}.` {entry}")

            steps_used = state.get("step_count", 0)
            max_steps = state.get("max_steps", 35)
            st.caption(f"Steps used: {steps_used} / {max_steps}")

            accepted_assets = state.get("accepted_asset_paths") or []
            if accepted_assets:
                with st.expander("Accepted Assets", expanded=False):
                    st.json(accepted_assets)

            sub_results = state.get("sub_agent_results") or {}
            if sub_results:
                with st.expander("Sub-Agent Results", expanded=True):
                    st.json(sub_results)

            errors = state.get("errors", [])
            if errors:
                st.error("QA or security issues were detected.")
                st.json(errors)
            else:
                st.success("No QA/security issues detected.")

            final_result = state.get("final_result")
            if final_result:
                st.info("Final Execution Result")
                st.markdown(final_result, unsafe_allow_html=True)

        elif state.get("step_count", 0) >= state.get("max_steps", 35):
            st.markdown("---")
            st.warning(f"⚠️ Пайплайн примусово зупинено: досягнуто ліміт кроків ({state.get('max_steps')}).")
            st.info("Ви можете збільшити max_steps або уточнити запит.")
            memory = state.get("core_memory") or []
            if memory:
                with st.expander("Core Memory Log", expanded=False):
                    for index, entry in enumerate(memory):
                        st.markdown(f"`{index + 1}.` {entry}")
