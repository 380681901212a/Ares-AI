import threading

from langchain_ollama import ChatOllama

from tools.ollama_runtime import get_coder_model_name, get_text_model_name

_llm_instance: ChatOllama | None = None
_llm_lock = threading.Lock()

_text_llm_instance: ChatOllama | None = None
_text_llm_lock = threading.Lock()

def get_text_llm() -> ChatOllama:
    """General-purpose LLM for text analysis and language-sensitive tasks."""
    global _text_llm_instance
    with _text_llm_lock:
        if _text_llm_instance is None:
            _text_llm_instance = ChatOllama(
                model=get_text_model_name(),
                temperature=0.3,
                num_ctx=8192,
                keep_alive=0,
                request_timeout=180,
            )
    return _text_llm_instance

def reset_text_llm() -> None:
    global _text_llm_instance
    with _text_llm_lock:
        _text_llm_instance = None


def get_llm() -> ChatOllama:
    """Returns a shared ChatOllama instance. Safe to call from any thread."""
    global _llm_instance
    with _llm_lock:
        if _llm_instance is None:
            _llm_instance = ChatOllama(
                model=get_coder_model_name(),
                temperature=0.1,
                num_ctx=8192,
                keep_alive=0,          # Release model from VRAM after each response → prevents OOM
                request_timeout=180,
            )
    return _llm_instance


def reset_llm() -> None:
    """Clears the cached LLM instance so the next get_llm() call creates a fresh one.

    Call this in exception handlers after an Ollama crash / connection reset,
    to prevent subsequent agents from reusing a stale/broken connection object.
    """
    global _llm_instance
    with _llm_lock:
        _llm_instance = None
