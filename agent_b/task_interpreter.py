import json
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI
from agent_b.llm_client import get_client


@dataclass
class TaskInterpreterConfig:
    model: str = "gpt-4o"
    temperature: float = 0.1

@dataclass
class Step:
    index: int
    description: str
    action_type: str
    selector_hint: Optional[str] = None


@dataclass
class TaskPlan:
    app: str          
    task_text: str    
    task_slug: str    


class TaskInterpreter:
    def __init__(
        self,
        client: Optional[OpenAI] = None,
        config: Optional[TaskInterpreterConfig] = None,
    ):
        self.client = client or get_client()
        self.config = config or TaskInterpreterConfig()

    def plan(self, task_text: str) -> TaskPlan:
        

        system_prompt = """
                      You are a task interpretation agent for a browser automation system.

                      Your job is to:
                      1) Identify which app the task refers to.
                      2) Generate a short, machine-friendly task slug.

                      You MUST respond with STRICT JSON ONLY, with this schema:

                      {
                        "app": "<string, e.g. 'notion' | 'linear' | 'shortcut' | 'generic'>",
                        "task_slug": "<2-3 word summary in snake_case, e.g. 'create_project_aionboarding'>"
                      }

                      Rules for "app":
                      - If the task clearly targets Shortcut, set "app": "shortcut" (lowercase).
                      - If the task clearly targets Linear, set "app": "linear" (lowercase).
                      - If the task clearly targets Notion, set "app": "notion" (lowercase).
                      - DO NOT output URLs or mixed case (no "Linear.app", "Notion.so", etc.).
                      - If you cannot infer the app, set "app": "generic".

                      Rules for "task_slug":
                      - 2–3 short words describing the task.
                      - Use lowercase snake_case (only letters, digits, and underscores).
                        Examples:
                          "create_project`_aionboarding"
                          "filter_tasks_by_status"
                          "update_story_status"
                      - The slug should be deterministic given the task text (avoid randomness).

                      Respond with JSON ONLY. No extra text, no markdown, no comments.
                      """

        user_prompt = f"""
                      Task: "{task_text}"

                      Decide:
                      - which app this is about
                      - a short snake_case slug for this task

                      Remember: respond with JSON ONLY, no explanation.
                      """

        response = self.client.chat.completions.create(
            model=self.config.model,
            temperature=self.config.temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        raw = response.choices[0].message.content.strip()

        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[len("json"):].strip()

        data = json.loads(raw)

        app = (data.get("app") or "generic").strip().lower()
        slug = (data.get("task_slug") or "task").strip()

        return TaskPlan(app=app, task_text=task_text, task_slug=slug)
