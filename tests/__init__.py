
import os

# Follow-up suggestions are an extra background LLM call; only their own tests turn them on.
os.environ.setdefault("RAGFLOW_FOLLOWUP_QUESTIONS", "0")
