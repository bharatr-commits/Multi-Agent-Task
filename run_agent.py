import asyncio
import sys

from agent_b.task_interpreter import TaskInterpreter
from agent_b.navigator import Navigator


BASE_URLS = {
    "shortcut": "https://app.shortcut.com/",
    "linear": "https://linear.app/",
}


async def main():


    task_text = " ".join(sys.argv[1:])

    interpreter = TaskInterpreter()
    plan = interpreter.plan(task_text)

    app_name = (plan.app or "").strip().lower()
    print("App:", app_name)
    slug = plan.task_slug


    run_id = f"{app_name}_{slug}"

    navigator = Navigator(BASE_URLS)
    await navigator.run_plan(plan, run_id=run_id, app_name=app_name)


if __name__ == "__main__":
    asyncio.run(main())
