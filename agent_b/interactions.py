import json
import re
from typing import List, Optional

from playwright.async_api import Page

from agent_b.llm_client import get_client


def extract_value_from_description(desc: str) -> Optional[str]:
    
    m = re.search(r"'([^']+)'|\"([^\"]+)\"", desc)
    if not m:
        return None
    return m.group(1) or m.group(2)


async def get_active_scope_root(page: Page):
    
    selectors = (
        "[role='dialog'], .modal, .ReactModal__Content, .DialogOverlay, [data-modal], "
        "[data-testid*='modal'], [class*='panel-container'], [data-model='Panel']"
    )
    try:
        nodes = await page.query_selector_all(selectors)
    except Exception:
        nodes = []

    if not nodes:
        return None

    max_el = None
    max_z = -1
    for el in nodes:
        try:
            z = await el.evaluate("el => parseInt(getComputedStyle(el).zIndex) || 0")
        except Exception:
            z = 0
        if z >= max_z:
            max_z = z
            max_el = el
    return max_el or nodes[-1]


async def get_active_scope_signature(page: Page) -> Optional[str]:
    
    root = await get_active_scope_root(page)
    if not root:
        return None
    try:
        sig = await root.evaluate(
            """(el) => {
                return JSON.stringify({
                    id: el.id || "",
                    dataModel: el.getAttribute("data-model") || "",
                    dataId: el.getAttribute("data-id") || "",
                    className: el.className || ""
                });
            }"""
        )
        return sig
    except Exception:
        return None


def is_finalize_step(desc: str) -> bool:
    
    d = desc.lower()
    finalize_keywords = [
        "save",
        "saved",
        "submit",
        "confirm",
        "finish",
        "done",
        "complete",
        "completed",
        "created",
        "added",
    ]
    return any(kw in d for kw in finalize_keywords)


async def dispatch_js_click(el) -> bool:
    
    try:
        await el.evaluate(
            """
            (node) => {
              const rect = node.getBoundingClientRect();
              const x = rect.left + rect.width / 2;
              const y = rect.top + rect.height / 2;
              const types = [
                "pointerover","mouseover",
                "pointerenter","mouseenter",
                "pointerdown","mousedown",
                "pointerup","mouseup",
                "click"
              ];
              for (const type of types) {
                const evt = new MouseEvent(type, {
                  bubbles: true,
                  cancelable: true,
                  clientX: x,
                  clientY: y,
                  button: 0
                });
                node.dispatchEvent(evt);
              }
            }
            """
        )
        return True
    except Exception as e:
        print(f"Failed to dispatch events: {e}")
        return False


async def robust_click(page: Page, el, label: str) -> bool:
   
    try:
        await el.click()
        print(f"Pointer-clicked: {label}")
        await page.wait_for_timeout(1000)
        return True
    except Exception as e:
        print(f"Pointer click failed, trying force=True: {e}")

    try:
        await el.click(force=True)
        print(f"Forced pointer-clicked: {label}")
        await page.wait_for_timeout(1000)
        return True
    except Exception as e:
        print(f"Force click failed, falling back to JS: {e}")

    if await dispatch_js_click(el):
        print(f"JS-clicked: {label}")
        await page.wait_for_timeout(1500)
        return True

    print(f"JS click failed for: {label}")
    return False


async def click_with_llm_only(
    page: Page,
    step_description: str,
    task_text: str,
    selector_hint: Optional[str] = None,
):
    
    click_selectors = "a, button, [role='link'], [role='button'], [data-testid*='button']"

    try:
        root = await get_active_scope_root(page)
        if root:
            elements = await root.query_selector_all(click_selectors)
            if not elements:
                elements = await page.query_selector_all(click_selectors)
        else:
            elements = await page.query_selector_all(click_selectors)
    except Exception as e:
        print(f"Failed to query clickable candidates: {e}")
        return

    candidates: List[dict] = []
    handles: List = []

    for idx, el in enumerate(elements):
        try:
            text = (await el.inner_text()).strip()
            aria = await el.get_attribute("aria-label") or ""
            title = await el.get_attribute("title") or ""
            data_testid = await el.get_attribute("data-testid") or ""
            cls = await el.get_attribute("class") or ""
            tag = await el.evaluate("el => el.tagName.toLowerCase()")
            role = await el.get_attribute("role") or ""
            btn_type = await el.get_attribute("type") or ""
            in_save_options = await el.evaluate("el => !!el.closest('.save-options')")
        except Exception:
            continue

        combined = " | ".join(t for t in [text, aria, title, data_testid] if t)
        candidates.append(
            {
                "index": idx,
                "text": text,
                "aria": aria,
                "title": title,
                "data_testid": data_testid,
                "class": cls,
                "type": btn_type,
                "combined_text": combined,
                "tag": tag,
                "role": role,
                "in_save_options": bool(in_save_options),
            }
        )
        handles.append(el)

    if not candidates:
        print("No clickable candidates found on page.")
        return

    client = get_client()
    excluded_indices: List[int] = []
    finalize = is_finalize_step(step_description)

    if finalize:
        filtered_candidates: List[dict] = []
        filtered_handles: List = []
        for c, h in zip(candidates, handles):
            if c.get("in_save_options"):
                filtered_candidates.append(c)
                filtered_handles.append(h)
        if filtered_candidates:
            candidates = filtered_candidates
            handles = filtered_handles

    for attempt in range(3):
        payload = {
            "task": task_text,
            "step_description": step_description,
            "selector_hint": selector_hint,
            "url": page.url,
            "excluded_indices": excluded_indices,
            "candidates": candidates[:80],
        }
        system_prompt = """
                    You are a UI agent that must decide which clickable element best completes the described step in a web app.
                    You are given a list of candidates with text and ARIA metadata.
                    If a selector_hint is provided, prefer candidates whose text or attributes match it.
                    You will also be given excluded_indices: these are candidate indices that have already been tried and failed. Do NOT choose them again.
                    Return strictly JSON: { "chosen_index": <int> } where chosen_index is the 'index' of the chosen candidate.
                    """

        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                temperature=0.1,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload, indent=2)},
                ],
            )
            raw = response.choices[0].message.content.strip()
        except Exception as e:
            print(f"LLM call failed: {e}")
            return

        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[len("json"):].strip()

        try:
            data = json.loads(raw)
            chosen = data.get("chosen_index", 0)
        except Exception:
            print(f"Failed to parse click output: {raw}")
            return

        indices = [c["index"] for c in candidates]
        if chosen not in indices:
            print(f"Chosen index {chosen} not in candidates.")
            return
        if chosen in excluded_indices:
            print(f"Chosen index {chosen} is already excluded, stopping.")
            return

        pos = indices.index(chosen)
        el = handles[pos]
        label = candidates[pos]["combined_text"] or candidates[pos]["text"] or f"idx={chosen}"

        success = await robust_click(page, el, label)

        if not success:
            excluded_indices.append(chosen)
            print(f"Click on index {chosen} failed, retrying with a different candidate.")
            continue

        return None


async def type_into_element(page: Page, el, value: str):
    
    try:
        tag = await el.evaluate("el => el.tagName.toLowerCase()")
        contenteditable_attr = await el.get_attribute("contenteditable")
        role = await el.get_attribute("role") or ""
        is_contenteditable = (
            contenteditable_attr is not None and contenteditable_attr.lower() == "true"
        )
    except Exception:
        tag = ""
        role = ""
        is_contenteditable = False

    try:
        await el.click(timeout=2000)
    except Exception as e:
        print(f"Failed to click element before typing: {e}")

    if is_contenteditable or role == "textbox":
        try:
            await el.evaluate(
                "(node, value) => { node.focus(); node.innerText = value; }", value
            )
            return
        except Exception as e:
            print(f"Failed to set innerText on contenteditable/textbox: {e}")

    if tag in ("input", "textarea"):
        try:
            await el.fill("")
            await el.type(value)
            return
        except Exception as e:
            print(f"fill/type failed on input/textarea, falling back to JS: {e}")
            try:
                await el.evaluate(
                    "(node, value) => {"
                    " node.focus();"
                    " node.value = value;"
                    " node.dispatchEvent(new Event('input', { bubbles: true }));"
                    " node.dispatchEvent(new Event('change', { bubbles: true }));"
                    "}",
                    value,
                )
                return
            except Exception as e2:
                print(f"[Type] JS value set failed on input/textarea: {e2}")

    try:
        await page.keyboard.type(value)
    except Exception as e:
        print(f"Keyboard.type failed on generic element: {e}")


async def fill_with_llm(
    page: Page,
    step_description: str,
    task_text: str,
    selector_hint: Optional[str] = None,
):
   

    await page.wait_for_timeout(200)

    desc_lower = step_description.lower()
    explicit_value = extract_value_from_description(step_description)

    if "description" in desc_lower:
        target_role = "description"
    elif "summary" in desc_lower:
        target_role = "summary"
    elif "name" in desc_lower or "title" in desc_lower:
        target_role = "name"
    else:
        target_role = "generic"

    def default_value_for_role(role: str) -> str:
        if role == "description":
            return "This is an auto-generated description for this item."
        if role == "summary":
            return "This is an auto-generated summary for this item."
        if role == "name":
            return "Sample Project"
        return "Sample Project"

    selectors = "input:not([type=hidden]), textarea, [contenteditable='true'], [role='textbox']"
    try:
        root = await get_active_scope_root(page)
        if root:
            raw_elements = await root.query_selector_all(selectors)
            if not raw_elements:
                raw_elements = await page.query_selector_all(selectors)
        else:
            raw_elements = await page.query_selector_all(selectors)
    except Exception as e:
        print(f"Failed to query editable elements: {e}")
        return

    candidates: List[dict] = []
    handles: List = []

    for idx, el in enumerate(raw_elements):
        try:
            tag = await el.evaluate("el => el.tagName.toLowerCase()")
            role = await el.get_attribute("role") or ""
            placeholder = (await el.get_attribute("placeholder")) or ""
            aria = (await el.get_attribute("aria-label")) or ""
            contenteditable_attr = await el.get_attribute("contenteditable")
            is_contenteditable = (
                contenteditable_attr is not None
                and contenteditable_attr.lower() == "true"
            )
            text = (await el.inner_text() or "").strip()
            extra_label = await el.evaluate(
                """(node) => {
                    function getLabel(n) {
                        if (!n) return "";
                        if (n.tagName === "LABEL") return n.innerText || "";
                        const prev = n.previousElementSibling;
                        if (prev && prev.textContent && prev.textContent.trim().length) {
                            return prev.textContent;
                        }
                        const parent = n.parentElement;
                        if (parent && parent !== document.body) {
                            const parentPrev = parent.previousElementSibling;
                            if (parentPrev && parentPrev.textContent && parentPrev.textContent.trim().length) {
                                return parentPrev.textContent;
                            }
                            return getLabel(parent);
                        }
                        return "";
                    }
                    return getLabel(node).trim();
                }"""
            )
            label_text = f"{placeholder} {aria} {extra_label}".lower()
        except Exception:
            continue

        candidates.append(
            {
                "index": idx,
                "tag": tag,
                "role": role,
                "placeholder": placeholder,
                "aria": aria,
                "is_contenteditable": is_contenteditable,
                "label_text": label_text,
                "inner_text": text,
            }
        )
        handles.append(el)


    if not candidates:
        print("No editable candidates found.")
        return

    client = get_client()
    payload = {
        "task": task_text,
        "step_description": step_description,
        "target_role": target_role,
        "selector_hint": selector_hint,
        "explicit_value": explicit_value,  # may be None
        "url": page.url,
        "candidates": candidates[:80],
    }

    system_prompt = """
            You are a UI agent that must decide which editable field best matches the described step
            in a web app AND what text value should be typed into that field.

            You receive:
            - task: overall automation task
            - step_description: human-readable step text
            - target_role: one of "name", "description", "summary", "generic"
            - selector_hint: optional string matching field labels/placeholders/etc
            - explicit_value: a string if the step text already specifies a value in quotes, otherwise null
            - candidates: editable fields with index, tag, role, label_text, placeholder, aria, etc.

            Rules:
            - First, choose the SINGLE best candidate field whose label_text / placeholder / aria / context
            match the intent of step_description and target_role.
            - If explicit_value is a non-empty string, you MUST use it as the value to type.
            - If explicit_value is null or empty, generate a short value appropriate for target_role and the task.
            - "name": short title-like text
            - "description": 1–2 concise sentences
            - "summary": one short summary sentence
            - "generic": a short, reasonable value relevant to the task

            Respond with STRICT JSON ONLY:
            {
            "chosen_index": <int>,   // the candidate.index to use
            "value": "<string to type>"
            }
            """

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, indent=2)},
            ],
        )
        raw = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Call failed (combined chooser+value): {e}")
        return

    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[len("json"):].strip()

    try:
        data = json.loads(raw)
        chosen = data.get("chosen_index", 0)
        value = data.get("value", "") or ""
    except Exception:
        print(f"Failed to parse combined fill output: {raw}")
        return

    if not isinstance(value, str) or not value.strip():
        value = default_value_for_role(target_role)

    indices = [c["index"] for c in candidates]
    if chosen not in indices:
        print(f"Chosen index {chosen} not in candidates.")
        return

    pos = indices.index(chosen)
    el = handles[pos]
    debug_label = (
        f"tag={candidates[pos]['tag']}, role='{candidates[pos]['role']}', "
        f"placeholder='{candidates[pos]['placeholder']}', aria='{candidates[pos]['aria']}', "
        f"label_text='{candidates[pos]['label_text']}'"
    )
    await type_into_element(page, el, value)
    await page.wait_for_timeout(300)
