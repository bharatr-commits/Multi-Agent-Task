import os
from openai import OpenAI
api_key = ""
def get_client() -> OpenAI:
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
    return OpenAI(api_key=api_key)
