"""AI race engineer, powered by Google Gemini.

Two modes:
  race     — short, spoken radio calls during a session. Low latency, terse.
  debrief  — a longer written analysis after the session.

The circuit briefing (real 2023 strategy + pit loss) is stable for a whole race
and is sent as the system instruction; only the volatile car state goes in the
prompt. Recent calls are replayed back so the engineer follows through on a
plan instead of repeating itself every lap.

Requires GEMINI_API_KEY (or GOOGLE_API_KEY) in the environment.
Get one at https://aistudio.google.com/apikey
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

log = logging.getLogger("simvsreality.engineer")

SECRETS_PATH = (__import__("pathlib").Path(__file__).resolve().parent.parent
                / "data" / "secrets.json")


def read_local_key() -> Optional[str]:
    try:
        import json as _json
        data = _json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
        key = (data.get("gemini_api_key") or "").strip()
        return key or None
    except Exception:
        return None


def write_local_key(key: str):
    import json as _json
    SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRETS_PATH.write_text(_json.dumps({"gemini_api_key": key.strip()}, indent=2),
                            encoding="utf-8")


def clear_local_key():
    try:
        SECRETS_PATH.unlink()
    except FileNotFoundError:
        pass


def _thinking(level: str):
    """A ThinkingConfig for LOW / MEDIUM / HIGH that also works on SDK
    versions without ThinkingLevel (they take a token budget instead)."""
    from google.genai import types
    levels = getattr(types, "ThinkingLevel", None)
    if levels is not None and hasattr(levels, level):
        return types.ThinkingConfig(thinking_level=getattr(levels, level))
    budget = {"LOW": 512, "MEDIUM": 2048, "HIGH": 8192}[level]
    return types.ThinkingConfig(thinking_budget=budget)

# Preferred models, best first. Resolved against the models the key can
# actually see, so this keeps working as the model line-up moves on.
# Newest first, with Google's own "-latest" aliases as the safety net. Model
# ids rot fast: gemini-2.5-flash is still *listed* but 404s for new keys, which
# is why resolve_model verifies and advice() re-resolves on a 404.
RACE_MODELS = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
               "gemini-flash-latest", "gemini-3-flash-preview",
               "gemini-flash-lite-latest"]
# Flash first even for the debrief: on a free-tier key the pro models have
# very tight per-minute quotas, and depth comes from HIGH thinking anyway.
DEBRIEF_MODELS = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-flash-latest",
                  "gemini-pro-latest", "gemini-3.5-flash"]

# Model variants that are not plain text-in / text-out chat models.
_NON_TEXT = ("image", "tts", "audio", "embedding", "robotics", "computer-use",
             "live", "translate", "omni")

PIT_LOSS_S = {
    "Bahrain": 22.0, "Jeddah": 20.0, "Melbourne": 20.0, "Baku": 18.0,
    "Miami": 19.0, "Imola": 26.0, "Monaco": 19.0, "Barcelona": 21.0,
    "Montreal": 17.0, "Red Bull Ring": 19.0, "Silverstone": 27.0,
    "Hungaroring": 20.0, "Spa": 19.0, "Zandvoort": 21.0, "Monza": 24.0,
    "Singapore": 27.0, "Suzuka": 22.0, "Losail": 25.0, "Austin": 21.0,
    "Mexico City": 22.0, "Interlagos": 21.0, "Las Vegas": 20.0,
    "Abu Dhabi": 21.0,
}

# OpenAPI-subset schema (what Gemini's structured output accepts).
ADVICE_SCHEMA = {
    "type": "object",
    "properties": {
        "radio": {
            "type": "string",
            "description": "The radio call, read aloud to the driver. One or "
                           "two short spoken sentences, max ~30 words. Plain "
                           "speech only — no markdown, lists, symbols or "
                           "abbreviations that don't read well aloud.",
        },
        "urgency": {"type": "string", "enum": ["info", "note", "urgent"]},
        "speak": {
            "type": "boolean",
            "description": "False if nothing has changed and the driver does "
                           "not need to hear anything right now.",
        },
        "pit": {
            "type": "object",
            "properties": {
                "recommendation": {
                    "type": "string",
                    "enum": ["stay_out", "pit_window_open", "box_within_3_laps",
                             "box_next_lap", "box_this_lap"],
                },
                "target_lap": {"type": "integer", "nullable": True},
                "compound": {
                    "type": "string",
                    "enum": ["Soft", "Medium", "Hard", "Intermediate", "Wet", "none"],
                },
                "reason": {"type": "string"},
            },
            "required": ["recommendation", "target_lap", "compound", "reason"],
        },
        "driving": {"type": "array", "items": {"type": "string"}},
        "tyre_outlook": {"type": "string"},
    },
    "required": ["radio", "urgency", "speak", "pit", "driving", "tyre_outlook"],
}

# A debrief is a different deliverable from a radio call: setup changes and
# ranked driving work, each tied to the evidence that justifies it.
DEBRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {
            "type": "string",
            "description": "One or two sentences: the single most important "
                           "thing to take from this session.",
        },
        "pace": {
            "type": "string",
            "description": "What the lap times and consistency say, quoting "
                           "the actual figures provided.",
        },
        "setup": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "area": {"type": "string"},
                    "change": {"type": "string"},
                    "why": {"type": "string"},
                    "confidence": {"type": "string",
                                   "enum": ["high", "medium", "low"]},
                },
                "required": ["area", "change", "why", "confidence"],
            },
        },
        "driving": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "where": {"type": "string"},
                    "issue": {"type": "string"},
                    "fix": {"type": "string"},
                    "time_available_s": {"type": "number", "nullable": True},
                },
                "required": ["where", "issue", "fix", "time_available_s"],
            },
        },
        "qualifying_plan": {"type": "string"},
    },
    "required": ["headline", "pace", "setup", "driving", "qualifying_plan"],
}

SYSTEM_ROLE = """You are a Formula 1 race engineer on team radio to your driver \
during a race in the F1 23 video game. You are calm, precise and brief, exactly \
like a real race engineer.

How to work:
- Base every call ONLY on the telemetry and reference data you are given. Never \
invent lap times, gaps, positions or tyre life you were not told.
- Do the strategy arithmetic properly: laps remaining, current degradation per \
lap, the pit loss for this circuit, and whether the tyres and fuel reach the \
end. A stop is only worth it if the time gained on fresher tyres beats the pit \
loss.
- Read fuel correctly. "fuel_laps_in_hand" is the car's MFD delta: laps of fuel \
SPARE once the session is finished, not laps until the tank runs dry. A value \
of +1.0 means the driver can finish with a lap's worth left over and is \
perfectly healthy; only a NEGATIVE value means they will run short and need to \
save fuel. Never tell a driver to box because this number is small and positive.
- You will be shown your own recent calls. Follow through on the plan you \
already gave. Do not repeat a message the driver has already heard; if nothing \
meaningful has changed since your last call, set speak to false and keep the \
radio message short.
- Raise urgency to "urgent" only for something that needs action this lap or \
next: box now, damage, a rival committing to an undercut, weather changing.
- The radio field is spoken aloud by a synthetic voice. Write it as natural \
speech. Say "box, box" not "PIT NOW". Say "one minute twenty-one point four" \
rather than "1:21.400". No markdown, no bullet points, no emoji.
- Driving notes must be specific to this moment (tyre temperature, traction, \
braking, ERS deployment, fuel saving). Never generic filler like "drive \
smoothly" or "stay focused".
- The real 2023 data is a guide to what the circuit demands, not a script. The \
game wears tyres faster than reality over a full race distance."""

DEBRIEF_ROLE = """You are a Formula 1 race engineer debriefing your driver after \
a practice session in the F1 23 video game, and preparing the car for \
qualifying. Be direct and specific, the way a real engineer talks to a driver \
they respect.

Ground rules:
- Base everything ONLY on the data provided. Never invent lap times, \
temperatures or corner numbers. If the data does not support a claim, don't \
make it.
- If the data contains no corner-level comparison, say plainly that you are \
judging from pace and consistency alone, and invent no corner detail.
- Separate the CAR from the DRIVER. A corner where the driver already carries \
more speed than the reference but still loses time is a setup problem; one \
where they brake early and pick the throttle up late is a driving problem.

Reading the setup data (tyre values are per wheel, in RL, RR, FL, FR order):
- A positive front/rear surface delta means the fronts run hotter — typically \
front-limited and understeering. Consider less front wing, or softer front \
suspension and anti-roll bar.
- Rears hotter than fronts points to traction limitation and wheelspin: \
consider more on-throttle differential lock, softer rear anti-roll bar, or \
less rear camber.
- A large left/right delta is normal at circuits with one dominant corner \
direction. Mention it only if it is extreme.
- Peak brake temperatures far above about 750C call for more brake cooling or \
lower brake pressure; temperatures that never climb mean the brakes are \
outside their working window.
- Top speed well below the reference while cornering pace is comparable means \
too much wing for this circuit.
- Speak in the game's own setup vocabulary: front and rear wing, differential \
on and off throttle, camber, toe, suspension and anti-roll bars, ride height, \
brake pressure and bias, tyre pressures. Give a direction and rough size — \
"front wing down one or two clicks" — never a precise number you cannot know.

If a "qualifying" block is present, that is a qualifying post-mortem. Open by \
stating plainly whether the driver took pole, and if not, the exact gap and who \
beat them. Then account for that gap: attribute it to specific corners from the \
corner data where possible, and be honest about how much is car and how much is \
driver. Do not soften it, and do not pad it — a driver wants to know where the \
time went.

Priorities:
- At most four setup changes, most valuable first, each with the evidence.
- At most four driving improvements, each tied to the specific corner or \
distance marker where the data shows the loss, with the time available.
- Finish with a short qualifying plan: compound, how many push laps, out-lap \
preparation, and what to watch.
- Plain prose. No markdown, no headers, no bullet characters — the interface \
does the formatting."""


class RaceEngineer:
    def __init__(self):
        self._client = None
        self._client_key: Optional[str] = None
        self._lock = threading.Lock()
        self._brief_cache: dict = {}
        self._model_cache: dict = {}
        self.history: list = []
        self.last_debrief: Optional[dict] = None
        self.debrief_chat: list = []

    # ------------------------------------------------------------------
    @property
    def api_key(self) -> Optional[str]:
        """Environment first, then the local secrets file.

        The file is how a shipped copy stores a per-user key: it lives beside
        the recorded laps, is git-ignored, and never goes near the source.
        """
        env = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if env:
            return env.strip()
        return read_local_key()

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @property
    def key_source(self) -> Optional[str]:
        if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            return "env"
        return "file" if read_local_key() else None

    def forget_client(self):
        """Drop the cached client so the next call picks up a changed key."""
        with self._lock:
            self._client = None
            self._client_key = None
            self._model_cache.clear()

    def _get_client(self):
        key = self.api_key
        if self._client is None or self._client_key != key:
            from google import genai
            self._client = genai.Client(api_key=key)
            self._client_key = key
            self._model_cache.clear()
        return self._client

    def resolve_model(self, mode: str, avoid: Optional[str] = None) -> str:
        """Pick the best model this key can actually use."""
        if mode in self._model_cache:
            return self._model_cache[mode]
        wanted = [m for m in (RACE_MODELS if mode == "race" else DEBRIEF_MODELS)
                  if m != avoid]
        try:
            client = self._get_client()
            names = set()
            for m in client.models.list():
                name = (m.name or "").split("/")[-1]
                actions = getattr(m, "supported_actions", None) or []
                if not actions or "generateContent" in actions:
                    names.add(name)
            chosen = next((m for m in wanted if m in names), None)
            if chosen is None:  # nothing preferred — newest plain chat model
                pref = sorted(n for n in names
                              if n.startswith("gemini")
                              and ("flash" in n or "pro" in n)
                              and not any(k in n for k in _NON_TEXT))
                chosen = pref[-1] if pref else wanted[0]
        except Exception as exc:
            log.warning("model listing failed (%s); using %s", exc, wanted[0])
            chosen = wanted[0]
        self._model_cache[mode] = chosen
        log.info("race engineer %s mode -> %s", mode, chosen)
        return chosen

    # ------------------------------------------------------------------
    def circuit_briefing(self, track_name: str, event: Optional[str]) -> str:
        if track_name in self._brief_cache:
            return self._brief_cache[track_name]

        pit_loss = PIT_LOSS_S.get(track_name)
        lines = [f"CIRCUIT: {track_name}."]
        if pit_loss:
            lines.append(f"Typical total time lost for a pit stop here: about "
                         f"{pit_loss:.0f} seconds.")
        if event:
            try:
                from . import real_data
                top = real_data.race_strategy_summary(event, top_n=5)
                if top:
                    lines.append(f"\nHow the real 2023 {event} was run by the "
                                 f"top finishers:")
                    for entry in top:
                        stints = entry.get("stints") or []
                        if not stints:
                            continue
                        seq = " -> ".join(
                            f"{s['compound']} L{s['from_lap']}-{s['to_lap']}"
                            + (f" (deg {s['deg_ms_per_lap']/1000:+.2f}s/lap)"
                               if s.get("deg_ms_per_lap") is not None else "")
                            for s in stints)
                        lines.append(f"  P{entry.get('position')} "
                                     f"{entry['driver']}: {len(stints)-1} stop(s) — {seq}")
            except Exception as exc:
                log.warning("no real strategy for %s: %s", event, exc)
                lines.append("\n(No real 2023 strategy available for this "
                             "circuit — rely on the live telemetry.)")
        brief = "\n".join(lines)
        self._brief_cache[track_name] = brief
        return brief

    # ------------------------------------------------------------------
    def advice(self, state: dict, event: Optional[str] = None,
               question: Optional[str] = None, mode: str = "race") -> dict:
        if not self.available:
            raise RuntimeError(
                "No Gemini API key. Paste one into the Race engineer panel, or "
                "set GEMINI_API_KEY and restart the app. Get a key at "
                "https://aistudio.google.com/apikey")

        from google.genai import types
        track = state.get("track") or "Unknown circuit"
        role = SYSTEM_ROLE if mode == "race" else DEBRIEF_ROLE
        system = role + "\n\n" + self.circuit_briefing(track, event)

        parts = ["Current car and race state:", json.dumps(state, indent=2)]
        with self._lock:
            recent = self.history[-4:]
        if recent:
            parts.append("\nYour recent calls this session (do not repeat these):")
            for h in recent:
                parts.append(f"  lap {h.get('lap')}: \"{h.get('radio')}\"")
        if question:
            parts.append(f"\nThe driver just asked you: \"{question}\"\n"
                         f"Answer that directly in the radio message.")
        else:
            parts.append("\nGive your call for right now.")

        client = self._get_client()
        prompt = "\n".join(parts)
        # Race calls must land while the information is still useful, so keep
        # thinking shallow; a debrief can afford to reason properly.
        thinking = _thinking("LOW" if mode == "race" else "HIGH")
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=ADVICE_SCHEMA,
            temperature=0.4,
            max_output_tokens=2048,
            thinking_config=thinking,
        )
        # Plain-JSON config for models that reject a response schema.
        loose = types.GenerateContentConfig(
            system_instruction=system + "\n\nReply with ONLY a JSON object "
            "matching this schema:\n" + json.dumps(ADVICE_SCHEMA),
            response_mime_type="application/json",
            temperature=0.4, max_output_tokens=2048,
            thinking_config=thinking)

        # Walk the candidate models: a retired one 404s and a busy one 503s,
        # and going quiet mid-race is worse than using the next model down.
        first = self.resolve_model(mode)
        candidates = [first] + [m for m in (RACE_MODELS if mode == "race"
                                            else DEBRIEF_MODELS) if m != first]
        last_exc: Optional[Exception] = None
        for candidate in candidates[:4]:
            for cfg in (config, loose):
                try:
                    resp = client.models.generate_content(
                        model=candidate, contents=prompt, config=cfg)
                    self._model_cache[mode] = candidate   # remember what worked
                    return self._finish(resp, state, candidate)
                except Exception as exc:
                    last_exc = exc
                    msg = str(exc)
                    # Only the schema case is worth retrying on this model.
                    if not ("schema" in msg.lower() or "json" in msg.lower()):
                        break
            msg = str(last_exc)
            if any(k in msg for k in ("NOT_FOUND", "404", "UNAVAILABLE", "503",
                                      "overloaded", "INTERNAL", "500")):
                log.warning("model %s unusable (%s); trying next",
                            candidate, msg[:80])
                continue
            break

        _raise_friendly(last_exc)
        raise last_exc

    def _finish(self, resp, state: dict, model: str) -> dict:
        """Parse the model's JSON reply and record it in the session history."""
        text = (resp.text or "").strip()
        if not text:
            raise RuntimeError("Empty response from Gemini.")
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise RuntimeError(f"Gemini did not return JSON: {text[:200]}")
            result = json.loads(text[start:end + 1])

        result.setdefault("speak", True)
        result.setdefault("urgency", "info")
        result.setdefault("driving", [])
        result["at"] = time.time()
        result["lap"] = state.get("lap")
        result["model"] = model
        with self._lock:
            self.history.append(result)
            del self.history[:-30]
        return result

    def reset_history(self):
        with self._lock:
            self.history = []

    # ------------------------------------------------------------------
    def debrief(self, briefing: dict, event: Optional[str] = None,
                question: Optional[str] = None) -> dict:
        """Setup and improvement advice from your recorded lap history."""
        if not self.available:
            raise RuntimeError(
                "No Gemini API key. Add one in the app, or set GEMINI_API_KEY. "
                "Get a key at https://aistudio.google.com/apikey")

        from google.genai import types
        track = briefing.get("track") or "Unknown circuit"
        system = DEBRIEF_ROLE + "\n\n" + self.circuit_briefing(track, event)

        parts = ["Everything recorded from this driver's session:",
                 json.dumps(briefing, indent=2, default=str)]
        if question:
            parts.append(f"\nThe driver specifically asked: \"{question}\"\n"
                         f"Make sure your answer addresses that.")
        else:
            parts.append("\nDebrief the session and prepare the car for qualifying.")

        client = self._get_client()
        prompt = "\n".join(parts)
        thinking = _thinking("HIGH")
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=DEBRIEF_SCHEMA,
            temperature=0.3,
            max_output_tokens=8192,
            thinking_config=thinking,
        )

        first = self.resolve_model("debrief")
        candidates = [first] + [m for m in DEBRIEF_MODELS if m != first]
        last_exc: Optional[Exception] = None
        for candidate in candidates[:4]:
            try:
                resp = client.models.generate_content(
                    model=candidate, contents=prompt, config=config)
                self._model_cache["debrief"] = candidate
                text = (resp.text or "").strip()
                if not text:
                    raise RuntimeError("Empty response from Gemini.")
                try:
                    result = json.loads(text)
                except json.JSONDecodeError:
                    a, b = text.find("{"), text.rfind("}")
                    if a < 0 or b <= a:
                        raise RuntimeError(f"Gemini did not return JSON: {text[:200]}")
                    result = json.loads(text[a:b + 1])
                result["model"] = candidate
                result["at"] = time.time()
                # keep it so follow-up questions have the full context
                with self._lock:
                    self.last_debrief = {"briefing": briefing, "result": result,
                                         "event": event}
                    self.debrief_chat = []
                return result
            except Exception as exc:
                last_exc = exc
                if any(k in str(exc) for k in ("NOT_FOUND", "404", "UNAVAILABLE",
                                               "503", "overloaded", "INTERNAL", "500")):
                    log.warning("debrief model %s unusable; trying next", candidate)
                    continue
                break
        _raise_friendly(last_exc)
        raise last_exc

    # ------------------------------------------------------------------
    def ask_debrief(self, question: str) -> dict:
        """Free-text follow-up about the most recent debrief. Answers from the
        same underlying data the debrief was built on, so it can't drift into
        making numbers up, and keeps a short running conversation."""
        if not self.available:
            raise RuntimeError(
                "No Gemini API key. Add one in the app to use the engineer.")
        with self._lock:
            ctx = self.last_debrief
            chat = list(self.debrief_chat)
        if not ctx:
            raise RuntimeError(
                "Run a debrief first, then you can ask about it.")

        from google.genai import types
        track = ctx["briefing"].get("track") or "Unknown circuit"
        system = (DEBRIEF_ROLE + "\n\n" + self.circuit_briefing(track, ctx["event"])
                  + "\n\nThe driver is now asking follow-up questions about the "
                  "debrief you gave. Answer conversationally in plain prose, "
                  "grounded ONLY in the session data and the debrief below. If "
                  "they ask about something the data cannot answer, say so "
                  "plainly rather than guessing. Keep answers focused — a few "
                  "sentences unless they ask for detail.")

        parts = [
            "The session data this debrief was built from:",
            json.dumps(ctx["briefing"], indent=2, default=str),
            "\nThe debrief you already gave:",
            json.dumps(ctx["result"], indent=2, default=str),
        ]
        for turn in chat[-6:]:
            parts.append(f"\nDriver: {turn['q']}\nYou: {turn['a']}")
        parts.append(f"\nDriver: {question}\nYou:")

        client = self._get_client()
        prompt = "\n".join(parts)
        cfg = types.GenerateContentConfig(
            system_instruction=system, temperature=0.4, max_output_tokens=2048,
            thinking_config=_thinking("MEDIUM"))

        first = self.resolve_model("debrief")
        candidates = [first] + [m for m in DEBRIEF_MODELS if m != first]
        last_exc = None
        for candidate in candidates[:4]:
            try:
                resp = client.models.generate_content(
                    model=candidate, contents=prompt, config=cfg)
                answer = (resp.text or "").strip()
                if not answer:
                    raise RuntimeError("Empty response from Gemini.")
                with self._lock:
                    self.debrief_chat.append({"q": question, "a": answer})
                    del self.debrief_chat[:-20]
                return {"answer": answer, "model": candidate, "at": time.time()}
            except Exception as exc:
                last_exc = exc
                if any(k in str(exc) for k in ("NOT_FOUND", "404", "UNAVAILABLE",
                                               "503", "overloaded", "INTERNAL", "500")):
                    continue
                break
        _raise_friendly(last_exc)
        raise last_exc


def _raise_friendly(exc: Exception):
    """Turn the common Gemini failures into something actionable."""
    msg = str(exc)
    if "API_KEY_INVALID" in msg or "API key not valid" in msg:
        raise RuntimeError(
            "That Gemini API key was rejected. Check GEMINI_API_KEY is the key "
            "itself (it starts with AIza), not a project id, and that the "
            "Generative Language API is enabled for it. "
            "Get a key at https://aistudio.google.com/apikey")
    if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
        raise RuntimeError(
            "Gemini quota reached — free-tier keys allow only a few requests "
            "per minute. Wait about a minute and try again, or turn Auto off "
            "in race mode so the engineer is called less often.")
    if "PERMISSION_DENIED" in msg or "403" in msg:
        raise RuntimeError(
            "Gemini refused this key (permission denied). It may be restricted "
            "to other APIs or to particular referrers.")


engineer = RaceEngineer()


# ---------------------------------------------------------------- state ----
def build_state(live: dict, laps: list) -> dict:
    """Condense the recorder's live status into the state the engineer reasons
    over. Keep it small — it is sent on every call."""
    session = live.get("session") or {}
    lap = live.get("lap") or {}
    car = live.get("car") or {}
    race = live.get("race") or {}

    recent = [
        {"lap": l.get("lap_number"), "time_ms": l.get("lap_time_ms"),
         "tyre": l.get("tyre"), "tyre_age": l.get("tyre_age")}
        for l in laps[:6]
    ][::-1]

    n = lap.get("number")
    # Practice and qualifying are timed, not lap-limited: the game's
    # total_laps there is a rolling estimate, and treating it as a target
    # makes the engineer announce the chequered flag mid-session.
    stype = (session.get("type") or "").lower()
    is_race = stype.startswith("race") or stype.startswith("sprint")
    total = session.get("total_laps") if is_race else None
    time_left = session.get("time_left_s")
    return {
        "session_is_timed": not is_race,
        "session_time_left_s": None if is_race else time_left,
        "track": session.get("track"),
        "session_type": session.get("type"),
        "weather": session.get("weather"),
        "air_temp": session.get("air_temp"),
        "track_temp": session.get("track_temp"),
        "lap": n,
        "total_laps": total,
        "laps_remaining": (total - n) if (total and n and total > n) else None,
        "position": lap.get("position"),
        "last_lap_time_ms": lap.get("last_time_ms"),
        "current_lap_invalid": lap.get("invalid"),
        "gap_to_car_ahead_ms": race.get("delta_ahead_ms"),
        "gap_to_leader_ms": race.get("delta_leader_ms"),
        "pit_stops_made": race.get("pit_stops"),
        "in_pit_lane": bool(race.get("pit_status")),
        "penalties_s": race.get("penalties"),
        "tyre_compound": car.get("tyre"),
        "tyre_age_laps": car.get("tyre_age"),
        "tyre_wear_pct": car.get("tyre_wear"),
        "tyre_wear_trend": race.get("wear_trend"),
        "fuel_kg": car.get("fuel_kg"),
        # The game's MFD delta: laps of fuel IN HAND relative to finishing,
        # NOT laps until the tank is dry. Naming it clearly matters — read as
        # "laps remaining" it makes a healthy car look like it is about to run
        # out, and the engineer calls a pit stop that is not needed.
        "fuel_laps_in_hand": car.get("fuel_laps"),
        "ers_pct": car.get("ers_pct"),
        "recent_laps": recent,
    }
