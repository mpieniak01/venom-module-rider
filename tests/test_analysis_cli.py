"""The public CLI talks to the real local API/domain, not a separate runner."""

import asyncio
import socket

import uvicorn
from test_analysis_execution import close, finish, rig
from test_rider import domain, host_app

from venom_module_rider import analysis_cli, analysis_tasks
from venom_module_rider.analysis_contract import AnalysisSpec
from venom_module_rider.analysis_tasks import HostTasks


async def test_cli_end_to_end_http_submit_recover_report_compare_and_errors(
    tmp_path,
    monkeypatch,
    capsys,
):
    manager, session, host = await rig(tmp_path, monkeypatch)
    service, _pi = domain()
    app = host_app(service)
    app.state.rider_analysis = manager
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        bound.listen()
        port = bound.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        serving = asyncio.create_task(server.serve(sockets=[bound]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    await asyncio.sleep(0.01)

            async def command(*args):
                capsys.readouterr()
                result = await asyncio.to_thread(
                    analysis_cli.main, ["--url", f"http://127.0.0.1:{port}", *args]
                )
                return result, capsys.readouterr().out

            assert (await command("profiles"))[0] == 0
            code, text = await command("submit", session, "--request-id", "cli")
            assert code == 0 and '"request_id": "cli"' in text
            row = manager.store.by_request("cli")
            assert (await finish(manager, row["id"]))["state"] == "succeeded"
            for args in [
                ("runs", "--session", session),
                ("show", row["id"]),
                ("recover", "cli"),
                ("report", row["id"]),
                ("compare", row["id"], row["id"]),
                ("cancel", row["id"]),
            ]:
                code, text = await command(*args)
                assert code == 0 and row["id"] in text
            code, text = await command("recover", "unknown")
            assert code == 1 and '"status": 404' in text
            invalid = tmp_path / "invalid.json"
            invalid.write_text("not JSON")
            code, text = await command("submit", session, "--spec", str(invalid))
            assert code == 1 and "analysis_cli_failed" in text
            assert len(manager.store.list()) == 1
        finally:
            server.should_exit = True
            await serving
            await service.close()
            await close(manager, host)


async def test_llm_off_reuses_host_state_and_existing_queue_classes(
    tmp_path, monkeypatch
):
    manager, session, host = await rig(tmp_path, monkeypatch)
    monkeypatch.setattr(analysis_tasks, "get_state_manager", lambda: host.state)

    def absent():
        raise RuntimeError("llm_off")

    monkeypatch.setattr(analysis_tasks, "get_orchestrator", absent)
    try:
        manager.host_tasks = HostTasks()
        row = await manager.submit(
            "no-chat", session, AnalysisSpec().model_dump(mode="json")
        )
        final = await finish(manager, row["id"])
        assert final["state"] == "succeeded"
        assert manager.host_tasks.state is host.state
    finally:
        await close(manager, host)
