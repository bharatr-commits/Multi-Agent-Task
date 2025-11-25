import os
import re
from typing import Dict, List, Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from agent_b.task_interpreter import TaskPlan, Step
from agent_b.ui_state_capture import UIStateCapture

from agent_b.planner import plan_steps
from agent_b.interactions import click_with_llm_only, fill_with_llm


class Navigator:
    def __init__(self, base_urls: Dict[str, str], storage_state_dir: str = "store_states"):
        self.base_urls = base_urls
        self.storage_state_dir = storage_state_dir
        os.makedirs(self.storage_state_dir, exist_ok=True)

    async def run_plan(self, plan: TaskPlan, run_id: str, app_name: str):
        run_dir = os.path.join("runs", run_id)
        os.makedirs(run_dir, exist_ok=True)

        storage_path = os.path.join(self.storage_state_dir, f"{app_name}.json")
        first_run = not os.path.exists(storage_path)

        steps: List[Step] = plan_steps(app_name, plan.task_text)
        # print("Planned steps:")
        # for s in steps:
        #     print(f"{s.index}. [{s.action_type}] {s.description} (hint={s.selector_hint})")
        plan_file = os.path.join(run_dir, "plan.txt")
        with open(plan_file, "w") as f:
            f.write(f"Task: {plan.task_text}\n\n")
            f.write("Planned Steps\n")
            for s in steps:
                line = f"{s.index}. [{s.action_type}] {s.description} (hint={s.selector_hint})\n"
                f.write(line)

        has_initial_nav = any(s.index == 0 and s.action_type == "navigate" for s in steps)

        async with async_playwright() as p:
            browser: Browser = await p.chromium.launch(headless=False)
            context: BrowserContext = await self._get_context(browser, app_name)
            page: Page = await context.new_page()
            capturer = UIStateCapture(page, run_dir, task_text=plan.task_text, app=app_name)

            base_url = self.base_urls.get(app_name)
            if base_url:
                await page.goto(base_url)
                await page.wait_for_timeout(1000)
                initial_url = page.url

                if first_run:
                    print(
                        f"[Navigator] First time for '{app_name}'. "
                        f"Waiting for you to log in or sign up in the browser window."
                    )
                    await self._wait_for_login(page, app_name, initial_url)
                else:
                    if not await self._looks_logged_in(page, None):
                        print(
                            f"[Navigator] Existing state for '{app_name}' "
                            f"does not look logged in. Waiting for login."
                        )
                        await self._wait_for_login(page, app_name, initial_url)

                if not has_initial_nav:
                    await capturer.capture(-1, "Open base app", tag="home")
            else:
                print(f"[Navigator] No base URL configured for app '{app_name}'")

            for step in steps:
                await self._execute_step(page, capturer, step, plan.task_text)

            capturer.save_metadata()
            await context.storage_state(path=storage_path)
            await context.close()
            await browser.close()

    async def _wait_for_login(
        self,
        page: Page,
        app_name: str,
        initial_url: str,
        max_wait_seconds: int = 600,
    ):
        stable_logged_in = 0
        for i in range(max_wait_seconds):
            try:
                if await self._looks_logged_in(page, initial_url):
                    stable_logged_in += 1
                    if stable_logged_in >= 5:
                        print(f"[Navigator] Detected logged-in state for '{app_name}'.")
                        return
                else:
                    stable_logged_in = 0
            except Exception:
                stable_logged_in = 0

            if i % 15 == 0 and i > 0:
                print(f"[Navigator] Still waiting for login to complete for '{app_name}'...")
            await page.wait_for_timeout(1000)
        print(f"[Navigator] Timeout while waiting for login for '{app_name}'. Continuing anyway.")

    async def _looks_logged_in(self, page: Page, initial_url: Optional[str]) -> bool:
        try:
            url = page.url or ""
        except Exception:
            return False

        if initial_url and url == initial_url:
            return False

        if re.search(
            r"(login|signin|sign-in|sign_in|signup|sign-up|sign_up|auth|oauth)",
            url,
            re.IGNORECASE,
        ):
            return False

        password_input = await page.query_selector(
            "input[type='password'], input[name*='password' i]"
        )
        if password_input:
            return False

        auth_cta = await page.query_selector(
            "text=/log in|sign in|sign up|sign up free|continue with|use email/i"
        )
        if auth_cta:
            return False

        return True

    async def _get_context(self, browser: Browser, app: str) -> BrowserContext:
        storage_path = os.path.join(self.storage_state_dir, f"{app}.json")
        if os.path.exists(storage_path):
            return await browser.new_context(storage_state=storage_path)
        return await browser.new_context()

    async def _execute_step(
        self,
        page: Page,
        capturer: UIStateCapture,
        step: Step,
        task_text: str,
    ):
        print(f"[Step {step.index}] {step.description}")

        if step.action_type in ("navigate", "click"):
            await click_with_llm_only(
                page,
                step_description=step.description,
                task_text=task_text,
                selector_hint=step.selector_hint,
            )
        elif step.action_type == "fill":
            await fill_with_llm(
                page,
                step_description=step.description,
                task_text=task_text,
                selector_hint=step.selector_hint,
            )
        elif step.action_type == "wait":
            await page.wait_for_timeout(1500)

        await capturer.capture(step.index, step.description, tag=step.action_type)
