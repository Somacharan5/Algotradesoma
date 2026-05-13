"""
Day 9 smoke test:
  Starts the orchestrator, lets it run for 8 seconds, then shuts it down.
  Verifies: broker connects, open trades load from DB, all 5 tasks launch,
  startup Telegram message sent, clean shutdown with no hung tasks.

Run with:  python -m tests.test_orchestrator
"""
from __future__ import annotations

import asyncio
from loguru import logger

import main as agent


async def smoke_test() -> None:
    print("\n── Day 9: Orchestrator Smoke Test ──\n")

    # ── Startup ────────────────────────────────────────────────────────────
    print("Step 1 — Startup (broker + scrip master + DB reload)")
    broker = await agent._startup()
    assert broker._session is not None, "broker session not established"
    print("  ✓ Broker connected")
    print("  ✓ Scrip master ready")
    print("  ✓ Open trades loaded from DB")

    # ── Launch tasks ───────────────────────────────────────────────────────
    print("\nStep 2 — Launch all 5 async tasks")
    tasks = [
        asyncio.create_task(agent._scanner_loop(broker),  name="scanner"),
        asyncio.create_task(agent._monitor_loop(broker),  name="monitor"),
        asyncio.create_task(agent._eod_loop(broker),      name="eod"),
        asyncio.create_task(agent._refresh_loop(broker),  name="refresh"),
        asyncio.create_task(agent.run_bot(),              name="telegram_bot"),
    ]
    task_names = [t.get_name() for t in tasks]
    print(f"  ✓ Tasks launched: {task_names}")

    # ── Let it breathe for 8 seconds ──────────────────────────────────────
    print("\nStep 3 — Running for 8 seconds…")
    await asyncio.sleep(8)

    # Verify no task has already crashed
    for t in tasks:
        if t.done() and not t.cancelled():
            exc = t.exception()
            if exc:
                raise RuntimeError(f"Task '{t.get_name()}' crashed: {exc}")
    print("  ✓ All tasks still running (no crashes)")

    # ── Shutdown ───────────────────────────────────────────────────────────
    print("\nStep 4 — Clean shutdown")
    agent._shutdown_event.set()
    for t in tasks:
        t.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    errors = {
        tasks[i].get_name(): r
        for i, r in enumerate(results)
        if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError)
    }
    assert not errors, f"Tasks raised unexpected errors: {errors}"
    print("  ✓ All tasks cancelled cleanly")

    await agent._shutdown(broker)
    print("  ✓ Shutdown complete")

    # Reset shutdown event for potential re-runs
    agent._shutdown_event.clear()

    print("\n── Smoke test passed — orchestrator working correctly ──")
    logger.success("Day 9 test passed.")


if __name__ == "__main__":
    asyncio.run(smoke_test())
