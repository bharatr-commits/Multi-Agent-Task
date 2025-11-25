# Agent B – UI State Capture System for Softlight Take-Home


Agent B takes a **natural language task** like:

> “Create a project in Linear named `AI onboarding` with description `Help new teammates understand our AI tools`.”

and then:

1. **Understands the task** and decides which app it refers to (e.g. Linear).
2. **Plans a sequence of UI actions** using an LLM (navigate, click, fill, wait, explore (I haven't finished explore fully)).
3. **Drives a real browser** with Playwright to perform those actions.
4. **Captures screenshots** of each important UI state (including non-URL states like modals).
5. **Saves a structured dataset** of the UI workflow (images + metadata).

The result is a per-task folder containing:

- Step-by-step screenshots.
- A `steps.json` describing each UI state.

---

## Architecture

The system has three core pieces:

### 1. TaskInterpreter (`task_interpreter.py`)

- Input: raw natural language task string.
- Output: a `TaskPlan` with:
  - `app` – which app to use (e.g. `linear`, `shortcut`, `notion`, or `generic`).
  - `task_text` – original task string.
  - `task_slug` – a short,identifier (e.g. `create_project_ai_onboarding`).

TaskInterpreter uses a lightweight LLM prompt to:

- Infer the target app name from the text.
- Generate a deterministic slug for naming run folders.

> Detailed UI steps are planned later by the Navigator.

---

### 2. Navigator (`navigator.py`)

The **Navigator** is the core agent that plans and executes UI steps.

Responsibilities:

1. **Step Planning** (`_plan_steps`)
   - Given `app_name` and `task_text`, it asks an LLM to output a list of steps, each with:
     - `index` – step order.
     - `description` – human-readable description.
     - `action_type` – one of: `navigate | click | fill | wait | explore`.
     - `selector_hint` – optional hint (e.g. `text~=New project|Create project`).

2. **Browser Control**
   - Uses **Playwright** to launch Chromium (`headless=False` for demo).
   - Maintains per-app **storage state** (`store_states/<app>.json`) so login only happens once.
   - On first run for an app:
     - Opens the base URL (e.g. `https://linear.app/`).
     - Lets the user log in manually.
     - Detects when the UI looks “logged in” and saves the storage state.

3. **Step Execution** (`_execute_step`)
   - For each planned step:
     - `navigate` / `click` → `_click_with_llm_only`
     - `fill` → `_fill_with_llm`
     - `wait` → small delay
   - After each step, calls `UIStateCapture.capture(...)` to record the UI state.

4. **LLM-Driven Clicks** (`_click_with_llm_only`)
   - Identifies the **active scope**:
     - If a modal/dialog is visible, focuses on that.
     - Otherwise, uses the whole page.
   - Collects **clickable candidates**:
     - Buttons, links, elements with `role="button"`, etc.
   - Sends to the LLM:
     - Task text, step description, selector_hint, URL, and candidate metadata.
   - LLM returns `{ "chosen_index": <int> }`.
   - Navigator clicks that element (normal click → forced click → JS fallback).
   - Retries with different candidates if a click fails.

5. **LLM-Driven Fills** (`_fill_with_llm`)
   - Extracts the value to type from quotes in the step description if present.
   - If missing, asks the LLM to generate an appropriate value (name / description / summary).
   - Collects all typeable fields:
     - `<input>`, `<textarea>`, `contenteditable`, `role="textbox"`.
   - LLM chooses which field index to use.
   - Navigator focuses that element and types the value.

---

### 3. UIStateCapture (`ui_state_capture.py`)

Responsible for **recording UI states**.

For each step:

1. Waits a short time for the DOM to settle.
2. Takes a **full-page screenshot**.
3. Detects whether a **modal/overlay** is present:
   - Checks dialog/modal selectors and high `z-index` elements.
4. Records metadata:
   - `step_index`
   - `description`
   - `tag` (action type: click/fill/wait/navigate)
   - `path` (screenshot filename)
   - `url` (current page URL)
   - `has_modal`
   - `z_modal_count`
   - `captured_at` timestamp (UTC)

On completion, it writes all states out to `steps.json` in the run directory.

---

## Entry Point (`run_agent.py`)

`run_agent.py` ties everything together:

1. Reads the **task text** from the command-line arguments.
2. Calls `TaskInterpreter.plan(task_text)` to get:
   - `app`
   - `task_slug`
3. Constructs a `run_id`:

   ```python
   run_id = f"{app_name}_{task_slug}"
   ```

4. Creates a `Navigator` with `BASE_URLS` mapping:

   ```python
   BASE_URLS = {
       "shortcut": "https://app.shortcut.com/",
       "linear": "https://linear.app/",
       # add more apps here if needed
   }
   ```

5. Calls:

   ```python
   await navigator.run_plan(plan, run_id=run_id, app_name=app_name)
   ```

This triggers planning, browser automation, and UI state capture.

---

## Setup

### Requirements

- Python 3.9+
- `openai`
- `playwright`
- Other standard libs (`json`, `dataclasses`, `typing`, etc.)

### Install Dependencies

```bash
pip install -r requirements.txt
python -m playwright install
```

### Configure OpenAI

Set your API key in `llm_client.py` as api_key = "":

---

## Running the Agent

Example:

```bash
python run_agent.py "Create a project in Linear named 'AI onboarding' with description 'Help new teammates understand our AI tools'"
```

What happens:

1. **TaskInterpreter**:
   - Detects `app = "linear"`.
   - Produces `task_slug = "create_project_ai_onboarding"`.

2. **Navigator**:
   - Launches Chromium and reuses the saved Linear session (or prompts for login once).
   - Asks the LLM to plan a sequence of UI steps.
   - For each step:
     - Uses LLM+DOM to choose where to click or type.
     - Executes the action.
     - Calls `UIStateCapture` to screenshot the current UI state.

3. **Output**:
   - A new directory under `runs/`:

     ```text
     runs/
       linear_create_project_ai_onboarding/
         00_navigate.png
         01_click.png
         02_fill.png
         03_fill.png
         04_click.png
         steps.json
         plan.txt
     ```

   - `steps.json` contains the metadata for each step, e.g.:

     ```json
     {
       "step_index": 2,
       "description": "Fill in the project name 'AI onboarding'.",
       "tag": "fill",
       "path": "02_fill.png",
       "url": "https://linear.app/...",
       "has_modal": true,
       "z_modal_count": 1,
       "captured_at": "2025-11-24T22:13:45Z"
     }
     ```

This structure gives you a **mini-dataset** per task: screenshots + detailed step descriptions and context.

---

## Extending to Other Apps & Tasks

To support a new app:

1. Add it to `BASE_URLS` in `run_agent.py`.
2. Log in once so the storage state (i.e. your login credentials) can be saved.
3. Use natural language tasks that mention the app, e.g.:
   - “Create a story in Shortcut called 'Onboarding workflow'.”
4. (Optional) Refine the planning prompt in `Navigator._plan_steps` with app-specific hints if needed.

The system does not rely on hardcoded selectors; instead it uses:

- The **DOM structure** at runtime, and
- The **LLM’s understanding** of the step description,

to choose where to click and type, which allows it to generalize to unseen tasks.

---
