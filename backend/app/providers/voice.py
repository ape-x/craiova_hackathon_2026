"""GPT-Live WebRTC handshake. Browser delegates to the validated backend."""
from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

from .base import HTTPProvider, ProviderError, secret_value


LIVE_INSTRUCTIONS = (
    "Ești interfața vocală Recovery Live. Vorbește concis și natural, în română. "
    "Transportul, cazarea, prețurile și disponibilitatea provin din surse reale: "
    "furnizori sau evidențe confirmate de operator. Relatează numai datele furnizate de aplicație, "
    "păstrând sursa, momentul verificării și limitele lor. "
    "Problema inițială și datele pasagerilor pot fi ipoteze de planificare furnizate de utilizator; "
    "nu le prezenta drept fapte observate sau identități verificate. "
    "O cotație de preț nu este o rezervare și nu garantează că locurile ori camerele rămân disponibile. "
    "Acceptarea unei propuneri de către pasager înregistrează preferința; rezervările sunt confirmate "
    "numai după confirmări externe explicite înregistrate de backend. "
    "Nu afirma că s-au blocat locuri, făcut plăți sau emis bilete fără acele dovezi.\n"
    "Backchannel policy: Confirmă scurt că asculți, fără a întrerupe descrierea problemei.\n"
    "Interruption policy: Oprește vorbirea când utilizatorul intervine și ascultă corecția completă.\n"
    "Delegation policy:\n"
    "Backend tools: Aplicația verifică cererea în raport cu datele operaționale curente, "
    "actualizează constrângerile explicite ale pasagerilor și prioritățile de recuperare, "
    "calculează și compară propuneri folosind resursele importate și poate focaliza harta. "
    "Poate porni căutări la furnizori pentru un caz existent, dacă cererea curentă numește explicit "
    "cazul sau zborul și grupurile sunt deja legate de el; poate verifica progresul acelui caz. "
    "Un răspuns de înregistrare a jobului confirmă numai cererea: nu anunța itinerarii găsite sau "
    "rezervări înaintea rezultatului verificat. Limita de apeluri spusă de operator include "
    "interpretarea, separat de apelurile ulterioare ale jobului; nu o mări. "
    "Dacă sunt necesare importuri sau căutări încă neexecutate, spune concret ce lipsește. "
    "Nu promite că ai căutat, creat sau rezervat transport ori cazare fără rezultatul backendului.\n"
    "Generate <|SpawnThinking|> when: Utilizatorul descrie problema, modifică o cerință, "
    "corectează datele, răspunde unei clarificări sau cere un calcul, o comparație ori focalizarea hărții. "
    "Ascultă întreaga cerere, inclusiv condițiile și corecțiile, înainte de a cere procesarea.\n"
    "Do not generate <|SpawnThinking|> when: Este doar un salut sau repetarea unui rezultat încă actual; "
    "ori nu se poate înțelege cererea fără o scurtă clarificare.\n"
    "Generate <|SpawnThinking|> before giving an answer that depends on backend work. "
    "Așteaptă rezultatul verificat. Nu inventa progres, acțiuni executate, prețuri, disponibilitate, "
    "rezervări, trafic sau condiții de zbor. Dacă backendul raportează o limită ori o eroare, "
    "explică ce nu este confirmat. Nu repeta o comandă deja executată."
)


class VoiceProvider(HTTPProvider):
    def __init__(self, settings, *, client=None, connect=None):
        super().__init__(settings, client=client)
        self._connect = connect
        self._sockets = {}
        self._watchdogs: dict[str, asyncio.Task] = {}
        self.session_status: dict[str, dict] = {}

    @property
    def configured(self) -> bool:
        return bool(secret_value(getattr(self.settings, "openai_api_key", "")))

    async def create(self, sdp: str) -> dict:
        if not self.configured:
            raise ProviderError("OPENAI_API_KEY is missing; GPT-Live is unavailable.", provider="GPT-Live", code="not_configured")
        if not isinstance(sdp, str) or not sdp.startswith("v=0") or len(sdp) > 65536:
            raise ProviderError("A valid WebRTC SDP offer is required.", provider="GPT-Live", code="invalid_offer")
        result = await self.request("POST", getattr(self.settings, "openai_base_url", "https://api.openai.com/v1").rstrip("/") + "/live/sessions", provider="GPT-Live", headers={"Authorization": f"Bearer {secret_value(self.settings.openai_api_key)}", "Content-Type": "application/json"}, json={
            "session": {
                "model": getattr(self.settings, "openai_voice_model", "gpt-live-1"),
                # Live is the safe default; the simulation policy is available
                # only when the server explicitly selects its internal mode.
                "instructions": LIVE_INSTRUCTIONS if getattr(self.settings, "operation_mode", "live") != "simulation" else (
                    "Ești interfața vocală Airport Recovery Live. Vorbește concis și natural, în română. "
                    "Utilizatorul poate construi scenarii proprii, descrise liber; exemplele din interfață nu sunt o listă limitativă. "
                    "Toate modificările, resursele adăugate, ofertele și transportul sunt simulate.\n"
                    "Backchannel policy: Confirmă scurt că asculți, fără a întrerupe descrierea unui scenariu.\n"
                    "Interruption policy: Oprește vorbirea când utilizatorul intervine și ascultă corecția completă.\n"
                    "Delegation policy:\n"
                    "Backend tools: Aplicația interpretează scenarii libere, inclusiv cereri compuse, "
                    "creează sau modifică entități și condiții simulate în limitele modelului, validează datele, "
                    "calculează planuri, compară alternative și focalizează harta. "
                    "Poate reseta scenariul și poate reprezenta vremea prin condiții descriptive și "
                    "restricții simulate de aterizare/decolare. Pentru un scenariu demonstrativ nou, "
                    "backendul alege și explică valorile omise și compară aeroporturile de deviere. "
                    "Nu cere separat permisiunea pentru valori demonstrative sau pentru resetarea cerută. "
                    "Nu cere utilizatorului să aleagă devierea când a lăsat alegerea agentului. "
                    "Aplicația decide ce poate reprezenta și cere detaliile lipsă; nu promite capabilități nelimitate.\n"
                    "Generate <|SpawnThinking|> when: Utilizatorul descrie un scenariu sau schimbă condițiile, "
                    "adaugă detalii, răspunde unei clarificări a aplicației, corectează o cerere, "
                    "solicită resetarea, un scenariu nou, schimbarea vremii, o comparație, un calcul sau focalizarea hărții. "
                    "Ascultă întreaga cerere, inclusiv condițiile și corecțiile, înainte de a cere executarea.\n"
                    "Do not generate <|SpawnThinking|> when: Este doar un salut sau repetarea unui rezultat încă actual; "
                    "ori nu se poate înțelege ce cere utilizatorul fără o scurtă clarificare.\n"
                    "Generate <|SpawnThinking|> before giving an answer that depends on backend work. "
                    "Așteaptă rezultatul verificat; nu inventa acțiuni executate, disponibilitate reală, rezervări sau trafic. "
                    "Dacă aplicația cere clarificări sau raportează o limită, spune concret ce lipsește ori ce nu poate simula. "
                    "Nu înlocui cererea cu cel mai apropiat exemplu prestabilit. "
                    "Nu anunța succesul unei cereri neexecutate și nu repeta o comandă deja executată."
                ),
                "audio": {"output": {"voice": "marin"}},
                "delegation": {"type": "client", "acknowledgement_delay_ms": 600},
                "client": {"data_channel": {
                    "allowed_client_events": ["session.close", "session.thinking.append", "session.commentary.append", "session.input_audio.mute", "session.input_audio.unmute"],
                    "allowed_server_events": "all",
                }},
            },
            "transport": {"type": "webrtc", "sdp": sdp},
        })
        try:
            session_id, answer = result["session"]["id"], result["transport"]["sdp"]
            if not isinstance(session_id, str) or not session_id or not isinstance(answer, str) or not answer.startswith("v=0"):
                raise ValueError("invalid session")
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("GPT-Live returned an invalid WebRTC session.", provider="GPT-Live", code="invalid_response") from exc
        max_seconds = min(600, max(30, int(getattr(self.settings, "voice_max_seconds", 180))))
        await self._attach_watchdog(session_id, max_seconds)
        return {"session_id": session_id, "sdp": answer, "max_seconds": max_seconds}

    async def _attach_watchdog(self, session_id: str, max_seconds: int) -> None:
        """Server owns a second connection so a vanished tab cannot disable TTL."""
        connector = self._connect
        if connector is None:
            from websockets.asyncio.client import connect
            connector = connect
        base = getattr(self.settings, "openai_base_url", "https://api.openai.com/v1").rstrip("/")
        websocket_base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        try:
            socket = await asyncio.wait_for(connector(
                websocket_base + "/live/sessions/" + quote(session_id, safe="") + "/attach",
                additional_headers={"Authorization": f"Bearer {secret_value(self.settings.openai_api_key)}"},
                open_timeout=min(10, self.timeout), max_size=2**20,
            ), timeout=min(10, self.timeout))
        except Exception as exc:
            self.session_status[session_id] = {"state": "unconfirmed", "message": "Watchdog connection failed after session initialization; final provider usage is unconfirmed."}
            raise ProviderError("GPT-Live initialized but its server duration watchdog could not connect; final usage is unconfirmed. Do not retry automatically.", provider="GPT-Live", code="watchdog_failed") from exc
        self._sockets[session_id] = socket
        self.session_status[session_id] = {"state": "active", "max_seconds": max_seconds}
        self._watchdogs[session_id] = asyncio.create_task(self._watch(session_id, socket, max_seconds))

    async def _watch(self, session_id: str, socket, max_seconds: float) -> None:
        async def receive_final():
            while True:
                event = json.loads(await socket.recv())
                if event.get("type") == "session.closed":
                    self.session_status[session_id] = {"state": "closed", "usage": event.get("usage", {})}
                    return
        try:
            try:
                await asyncio.wait_for(receive_final(), timeout=max_seconds)
            except asyncio.TimeoutError:
                self.session_status[session_id] = {"state": "closing", "reason": "server_duration_limit"}
                await socket.send(json.dumps({"type": "session.close"}))
                await asyncio.wait_for(receive_final(), timeout=15)
        except asyncio.CancelledError:
            self.session_status[session_id] = {"state": "unconfirmed", "message": "Server stopped before final usage."}
            raise
        except Exception:
            self.session_status[session_id] = {"state": "unconfirmed", "message": "Voice control connection ended before final usage."}
        finally:
            self._sockets.pop(session_id, None)
            self._watchdogs.pop(session_id, None)
            await socket.close()
            while len(self.session_status) > 100:
                finished = next((key for key, value in self.session_status.items() if value.get("state") not in ("active", "closing")), None)
                if finished is None:
                    break
                self.session_status.pop(finished)

    async def close(self, session_id: str) -> None:
        """Only sessions initialized by this provider instance may be closed."""
        socket, task = self._sockets.get(session_id), self._watchdogs.get(session_id)
        if socket is None or task is None:
            return
        try:
            await socket.send(json.dumps({"type": "session.close"}))
            await asyncio.wait_for(asyncio.shield(task), timeout=16)
        except Exception:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def aclose(self) -> None:
        await asyncio.gather(*(self.close(session_id) for session_id in list(self._sockets)), return_exceptions=True)
        await super().aclose()

    create_session = create
