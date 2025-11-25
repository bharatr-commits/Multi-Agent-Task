# planner.py
import json
from typing import List

from agent_b.llm_client import get_client
from agent_b.task_interpreter import Step


SYSTEM_PROMPT = """
                You are a planning agent that converts a natural language UI task
                into a sequence of high-level UI interaction steps for a browser automation agent.

                You MUST respond with STRICT JSON ONLY, no prose, matching this schema:

                {
                "steps": [
                    {
                    "index": 0,
                    "description": "<human readable step>",
                    "action_type": "<one of: navigate | click | fill | wait | explore>",
                    "selector_hint": "<string or null>"
                    },
                    ...
                ]
                }

                Action types:
                - "navigate": opening a main view, section, or page (board, projects, backlog, workspace home).
                - "click": clicking a clearly identifiable control (button, link, menu item, pill, field).
                - "fill": typing into or setting one or more visible form fields (title, description, summary, assignee, etc.).
                - "wait": waiting for a success state / confirmation / resulting list or detail view.
                - "explore": when the UI requires user-driven exploration or intermediate inspection (optional).

                selector_hint:
                - Prefer either a CSS selector (e.g. "button[type=submit]") OR
                - A text-based hint of the form "text~=Some text|Other text" to be used with regex,
                e.g. "text~=New project|Create project", "text~=New story|Create Story".
                - Use null if there is no obvious target.

                Indexing:
                - Steps MUST start at index 0 and increase by 1 with no gaps.

                Step guidelines:

                1) Use literal names from the task
                - If the task mentions an entity name in quotes, such as:
                - "a project called 'AI onboarding'"
                - "the story 'Implement AI-powered search'"
                - "a page titled 'AI research notes'"
                then:
                - Include a dedicated step that LOCATES and CLICKS that entity by its visible text.
                - Example step:
                    {
                    "index": 2,
                    "description": "Click the project named 'AI onboarding' to open its details.",
                    "action_type": "click",
                    "selector_hint": "text~=AI onboarding"
                    }

                2) Break editing of a specific field into clear sub-steps
                - When the user wants to update a specific field of an existing entity, e.g.:
                - "update its lead to 'Bharat Reddy'"
                - "change the owner to 'Alice'"
                - "set the status to 'In Progress'"
                you MUST decompose into at least:
                - A step to open the entity's details if needed (using its name in quotes).
                - A step to click the relevant field control.
                - A step to set/fill the new value.

                - Example pattern (Linear / Shortcut / generic apps):
                - Click the project/story "AI onboarding".
                - Click the "Lead" / "Owner" / "Status" field.
                - Set the value to the requested string.

                - The field-click step should use selector_hint with the field label text, for example:
                - "selector_hint": "text~=Lead|Owner"
                - "selector_hint": "text~=Status"
                - "selector_hint": "text~=Assignee|Owner"

                - The fill step description should include the target value in quotes, e.g.:
                - "Fill the Lead field with 'Bharat Reddy'."

                3) Creating entities (projects, stories, pages)
                - For tasks like "create a project/story/page with name X and description Y":
                - Include a step to navigate to the relevant section (Projects, Backlog, Pages, etc.).
                - Include a step to click the "New" / "Create" control.
                - Include one or more "fill" steps for the title/name, summary, and description.
                    - Use the literal values from the task text in quotes.
                - Include a final "click" step to save/create, with selector_hint such as:
                    - "text~=Create|Save|Done"
                    - or a reasonable CSS selector like "button[type=submit]".
                - Include a "wait" step for the created entity to appear.

                4) Use non-URL UI states explicitly
                - For modals, side panels, or property editors that do not change the URL:
                - Include steps that open these UI states (clicking the correct button/field).
                - Then use "fill" or "click" steps to modify fields inside them.

                5) Use concise but specific descriptions
                - Each description should be clear enough that a human or agent could infer what to click or type.
                - Prefer explicit phrasing like:
                - "Click the project named 'AI onboarding' to open its details."
                - "Click the Lead field for this project."
                - "Set the Lead value to 'Bharat Reddy'."

                6) Number of steps
                - Focus on 4–10 meaningful steps.
                - Do not collapse multiple major UI actions into one step; favor clarity over brevity.

                If you are not sure about the exact UI of the app, produce a reasonable generic plan
                for a modern web app of that type (e.g. project/issue trackers like Linear/Shortcut,
                or workspace apps like Notion).
                """


def plan_steps(app_name: str, task_text: str) -> List[Step]:
    
    client = get_client()

    user_prompt = {
        "app": app_name,
        "task": task_text,
        "hint": "Plan concrete UI steps for this app and task following the schema.",
    }

    response = client.chat.completions.create(
        model="gpt-4o",
        temperature=0.1,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_prompt, indent=2)},
        ],
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[len("json") :].strip()

    data = json.loads(raw)
    steps_data = data.get("steps", [])

    steps: List[Step] = []
    for s in steps_data:
        steps.append(
            Step(
                index=int(s["index"]),
                description=s["description"],
                action_type=s["action_type"],
                selector_hint=s.get("selector_hint"),
            )
        )

    steps.sort(key=lambda s: s.index)
    return steps
