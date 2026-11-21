"""Individual bot lifecycle with at most three managed bots per destination."""

from threading import Event, RLock, Thread

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from app.api import Auth
from app.bots import run_bot
from app.core.config import get_settings
from app.runtime import live
from app.security import require_admin

router = APIRouter(tags=["Bots"])


class BotOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval_seconds: float = Field(default=2, ge=0.5, le=30)
    rounds: int | None = Field(default=None, strict=True, ge=1, le=20)


class BotCount(BotOptions):
    count: int = Field(strict=True, ge=0, le=9)


class BotManager:
    def __init__(self):
        self.lock = RLock()
        self.jobs = {}
        self.closing = False

    def _start(self, number, options):
        name = f"bot-{number}"
        if self.closing:
            raise HTTPException(503, "La aplicación se está deteniendo")
        if name not in get_settings().bot_tokens:
            raise HTTPException(409, f"Falta configurar el token de {name}")
        if number in self.jobs and self.jobs[number][0].is_alive():
            raise HTTPException(409, f"{name} ya está activo o terminando una solicitud")
        stop = Event()
        thread = Thread(target=self._run, args=(number, options, stop), name=name, daemon=True)
        self.jobs[number] = (thread, stop)
        with live.lock:
            live.bots[name] = {
                "state": "running",
                "confirmed_requests": 0,
                "completed_rounds": 0,
                "interval_seconds": options.interval_seconds,
                "rounds": options.rounds,
            }
        thread.start()
        live.emit(
            bot_id=name,
            operation="BOT",
            status="success",
            cluster_id=live.destination(name),
            message="Bot iniciado",
        )

    def _run(self, number, options, stop):
        name = f"bot-{number}"
        failed = False

        def report(bot, data):
            # HTTP CRUD events come from the API; do not emit a second success from the client.
            if data.get("phase") == "failed" and not data.get("error_code", "").startswith("HTTP_"):
                live.emit(
                    bot_id=bot,
                    operation=data["operation"],
                    collection=data["resource"],
                    cluster_id=live.destination(bot),
                    status="error",
                    stage="api",
                    message=f"No se recibió respuesta de la API ({data.get('error_code', 'NetworkError')})",
                )
            with live.lock:
                for field in ("completed_rounds", "round", "exception_code"):
                    if field in data:
                        live.bots[bot][field] = data[field]

        try:
            failed = not run_bot(
                number, get_settings().api_base_url, options.rounds, options.interval_seconds, stop, report
            )
        except Exception as exc:
            failed = True
            live.emit(
                bot_id=name,
                operation="BOT",
                status="error",
                cluster_id=live.destination(name),
                message=f"No fue posible ejecutar el bot ({type(exc).__name__})",
            )
        finally:
            with live.lock:
                last_cluster = live.destination(name)
                live.bots[name]["state"] = "error" if failed else "stopped" if stop.is_set() else "completed"
            live.emit(
                bot_id=name,
                operation="BOT",
                status="error" if failed else "success",
                cluster_id=last_cluster,
                message="Bot con error; revisa la última operación"
                if failed
                else "Bot detenido"
                if stop.is_set()
                else "Ciclos completados",
            )

    def stop(self, number):
        with self.lock:
            job = self.jobs.get(number)
            if job and job[0].is_alive():
                job[1].set()
                with live.lock:
                    live.bots[f"bot-{number}"]["state"] = "stopping"
                live.emit(
                    bot_id=f"bot-{number}",
                    operation="BOT",
                    status="sent",
                    cluster_id=live.destination(f"bot-{number}"),
                    message="Deteniendo; la solicitud en curso puede finalizar",
                )
        return live.snapshot()

    def start(self, number, options):
        with self.lock:
            self._start(number, options)
        return live.snapshot()

    def resize(self, body):
        with self.lock:
            active = [i for i, (thread, _) in self.jobs.items() if thread.is_alive()]
            needed = max(0, body.count - len(active))
            candidates = [i for i in range(1, 10) if i not in active][:needed]
            if any(f"bot-{i}" not in get_settings().bot_tokens for i in candidates):
                raise HTTPException(409, "Faltan tokens para la cantidad solicitada")
            for number in sorted(active, reverse=True)[: max(0, len(active) - body.count)]:
                self.stop(number)
            for number in candidates:
                self._start(number, body)
        return live.snapshot()

    def shutdown(self):
        with self.lock:
            self.closing = True
            jobs = list(self.jobs.items())
            for number, _ in jobs:
                self.stop(number)
        for _, (thread, _) in jobs:
            thread.join(timeout=35)


manager = BotManager()


@router.get("/bots")
def state(actor: Auth):
    require_admin(actor)
    return live.snapshot()


@router.put("/bots")
def resize(body: BotCount, actor: Auth):
    require_admin(actor)
    return manager.resize(body)


@router.post("/bots/{number}/start", status_code=202)
def start(body: BotOptions, actor: Auth, number: int = Path(ge=1, le=9)):
    require_admin(actor)
    return manager.start(number, body)


@router.post("/bots/{number}/stop", status_code=202)
def stop(actor: Auth, number: int = Path(ge=1, le=9)):
    require_admin(actor)
    return manager.stop(number)


@router.post("/bots/stop", status_code=202)
def stop_all(actor: Auth):
    require_admin(actor)
    return manager.resize(BotCount(count=0))
