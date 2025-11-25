import os
import json
from datetime import datetime
from playwright.async_api import Page


class UIStateCapture:
    def __init__(self, page: Page, run_dir: str, task_text: str, app: str):
        self.page = page
        self.run_dir = run_dir
        self.task_text = task_text
        self.app = app
        self.states = []

        os.makedirs(run_dir, exist_ok=True)

    async def capture(self, step_index, description, tag=""):
        filename = f"{step_index:02d}_{tag or 'state'}.png"
        path = os.path.join(self.run_dir, filename)

        try:
            await self.page.wait_for_timeout(200)
            await self.page.screenshot(path=path, full_page=True)
        except Exception as e:
            print(f"Screenshot failed or page navigated: {e}")
            return

        try:
            modal_present = await self._has_modal()
        except Exception as e:
            print(f"_has_modal failed: {e}")
            modal_present = False

        try:
            z_modal_count = await self._z_index_modal_count()
        except Exception as e:
            print(f"_z_index_modal_count failed: {e}")
            z_modal_count = 0

        try:
            url = self.page.url
        except Exception:
            url = ""

        self.states.append(
            {
                "step_index": step_index,
                "description": description,
                "screenshot": filename,
                "url": url,
                "modal_present": modal_present,
                "high_z_modals": z_modal_count,
                "timestamp": datetime.utcnow().isoformat(),
                "task_text": self.task_text,
                "app": self.app,
                "tag": tag,
            }
        )

    async def _has_modal(self) -> bool:
        selectors = "[role='dialog'], .modal, .ReactModal__Overlay, .notion-overlay, .DialogOverlay, [data-modal], [data-testid*='modal']"
        modals = await self.page.query_selector_all(selectors)
        return len(modals) > 0

    async def _z_index_modal_count(self) -> int:
        try:
            elements = await self.page.query_selector_all("*")
        except Exception:
            return 0

        count = 0
        for el in elements:
            try:
                z = await el.evaluate("el => parseInt(getComputedStyle(el).zIndex) || 0")
                if z >= 999:
                    count += 1
            except Exception:
                continue
        return count

    def save_metadata(self):
        meta_path = os.path.join(self.run_dir, "steps.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(self.states, f, indent=2)
