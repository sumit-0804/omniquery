import os

from dotenv import load_dotenv
from langchain_typesafe import TypeSafeClassifier

load_dotenv()

# Jev is served through OpenRouter's System One API, so it reuses the OpenRouter key.
OPENROUTER_BASE_URL = "https://openrouter.ai/api"
JEV_MODEL = "typesafe/jev-1.13"


def pick_classifier(model: str = JEV_MODEL, timeout: float = 15.0) -> TypeSafeClassifier:
    return TypeSafeClassifier(
        model=model,
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=OPENROUTER_BASE_URL,
        timeout=timeout,
    )
