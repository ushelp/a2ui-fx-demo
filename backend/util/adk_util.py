import os

import dotenv
from google.adk.models import LiteLlm

dotenv.load_dotenv()

class AdkUtil:

    @staticmethod
    def get_adk_litellm_model() -> LiteLlm:

        # Local Ollama
        local_ollama_base_url = "http://localhost:11434"
        local_ollama_model = "ollama_chat/qwen3.8:27b-mlx"

        llm_model = os.environ.get("LLM_MODEL") or local_ollama_base_url
        llm_api_base = os.environ.get("LLM_API_BASE") or local_ollama_model
        llm_api_key = os.environ.get("LLM_API_KEY")

        """LiteLLM client; defaults to local Ollama, works with any provider."""
        kwargs: dict = {
            "model": llm_model,
            # "max_tokens": 8192
        }
        if llm_model.startswith("ollama"):
            kwargs["api_base"] = llm_api_base
            kwargs["thinking"] = False
        else:
            if llm_api_base:
                kwargs["api_base"] = llm_api_base
            if llm_api_key:
                kwargs["api_key"] = llm_api_key
        return LiteLlm(**kwargs)
