"""
modules/ai_agent.py
Multi-tool AI agent powered by OpenRouter (OpenAI-compatible).
Falls back to a rule-based responder when no API key is configured.

Agent capabilities:
  • analyze_flood_risk(region)  – live GEE flood analysis
  • list_active_alerts()        – pull current alert table
  • explain_risk_level(level)   – educational context
  • recommend_action(risk)      – response recommendation
  • analyze_climate_layer(...)  – any of the 15 environmental layers
  • list_lgas(state)            – LGAs of a Nigerian state

Regions are Nigeria, its 37 states (incl. FCT) and 774 LGAs; free-text
names ("Surulere, Lagos", "Kogi") are resolved via modules/nigeria_admin.py.

Monitor: re-runs flood checks for monitored regions and auto-creates alerts.
On PythonAnywhere (no threads in web apps) run_monitor.py calls
run_monitor_cycle() as a scheduled task; locally a background thread can
be started instead.
"""

import json
import logging
import re
import threading
import time
from datetime import datetime
from typing import Dict, Any, Optional, List

import requests as _requests

from modules.nigeria_admin import ADMIN

logger = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 30   # per chat session, to bound memory and token use
MAX_SESSIONS = 200

SYSTEM_PROMPT = (
    "You are ANSA, the AI assistant for ANSASphere — a real-time environmental "
    "intelligence platform for Nigeria powered by Google Earth Engine satellite data. "
    "You can analyse the whole country, any of its 36 states plus the FCT, or any of "
    "its 774 Local Government Areas (LGAs). "
    "You have tools to run live analyses for 15 environmental layers: "
    "flood risk (Sentinel-1 SAR), vegetation (NDVI), land cover, heatwaves, "
    "active fires, land surface temperature, soil moisture, ground deformation, "
    "forest structure, elevation/terrain, rainfall (CHIRPS), snow cover, "
    "crop stress, air pollution (NO₂), sea level/water storage, and glaciers. "
    "You can also check active alerts, list a state's LGAs and recommend emergency actions. "
    "Be concise, precise, and action-oriented. Always cite the satellite dataset and date. "
    "When risk is elevated, prioritise life-safety information."
)

_REGION_PARAM = {
    "type": "string",
    "description": (
        "'nigeria', a state id (e.g. 'lagos', 'kogi', 'akwa-ibom', 'fct') or an LGA "
        "as 'state/lga' (e.g. 'lagos/surulere', 'rivers/port-harcourt'). Plain names "
        "such as 'Surulere, Lagos' are also accepted."
    ),
}

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------
TOOL_DECLARATIONS = [
    {
        "name": "analyze_flood_risk",
        "description": (
            "Run a real-time Sentinel-1 SAR flood analysis for Nigeria, a state or an LGA "
            "and return flood extent, area statistics, and risk level."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "region_id": _REGION_PARAM,
                "event_date": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD) for the analysis window. Defaults to today.",
                },
            },
            "required": ["region_id"],
        },
    },
    {
        "name": "list_active_alerts",
        "description": "Return the current list of active flood alerts in the system.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "explain_risk_level",
        "description": "Provide an educational explanation for a flood risk level (0–4).",
        "parameters": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Risk level 0–4"}
            },
            "required": ["level"],
        },
    },
    {
        "name": "recommend_action",
        "description": "Return recommended response actions for a given flood risk level.",
        "parameters": {
            "type": "object",
            "properties": {
                "risk_level": {"type": "integer", "description": "Risk level 0–4"},
                "region": {"type": "string", "description": "Region name"},
            },
            "required": ["risk_level", "region"],
        },
    },
    {
        "name": "analyze_climate_layer",
        "description": (
            "Run a real-time environmental analysis for one of 15 satellite-derived layers: "
            "vegetation (NDVI), land_cover, heatwaves, fires, temperature (LST), "
            "soil_moisture, deformation (InSAR proxy), forest_structure, elevation, "
            "rainfall (CHIRPS), snow, crop_stress, pollution (NO2), sea_level (GRACE), "
            "glacier. Returns statistics and a map tile URL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layer_id": {
                    "type": "string",
                    "description": (
                        "One of: vegetation, land_cover, heatwaves, fires, temperature, "
                        "soil_moisture, deformation, forest_structure, elevation, rainfall, "
                        "snow, crop_stress, pollution, sea_level, glacier"
                    ),
                },
                "region_id": _REGION_PARAM,
                "date": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD). Defaults to today.",
                },
            },
            "required": ["layer_id", "region_id"],
        },
    },
    {
        "name": "list_climate_layers",
        "description": "Return the catalogue of all 15 available environmental monitoring layers.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_lgas",
        "description": "List the Local Government Areas (id and name) of a Nigerian state.",
        "parameters": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "description": "State id or name, e.g. 'lagos', 'Akwa Ibom'"},
            },
            "required": ["state"],
        },
    },
]

# Build OPENROUTER_TOOLS from TOOL_DECLARATIONS (done after the list is fully defined)
OPENROUTER_TOOLS = [
    {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
    for t in TOOL_DECLARATIONS
]

# Risk explanations (fallback).  {pct} is filled with the configured minimum
# flooded-area fraction for that level (config.FLOOD_*_THRESHOLD).
RISK_EXPLANATIONS = {
    0: "No significant flooding detected. Normal conditions prevail.",
    1: (
        "WATCH: Minor flooding possible in low-lying areas. "
        "At least {pct} of the area shows new surface water compared with the same "
        "season last year. Monitor conditions and stay informed."
    ),
    2: (
        "ADVISORY: Moderate flooding likely. At least {pct} of the area shows new "
        "surface water. Prepare emergency kits; avoid flood-prone routes."
    ),
    3: (
        "WARNING: Significant flooding underway. At least {pct} of the area is newly "
        "inundated. Evacuate low-lying areas. Activate local emergency response protocols."
    ),
    4: (
        "EMERGENCY: Severe flooding. At least {pct} of the area is newly inundated. "
        "Immediate evacuation required. Coordinate with NEMA and the State Emergency "
        "Management Agency (SEMA)."
    ),
}

RECOMMENDED_ACTIONS = {
    0: ["Continue routine monitoring", "Maintain flood preparedness plans"],
    1: [
        "Increase monitoring frequency",
        "Alert local emergency coordinators",
        "Check drainage infrastructure",
        "Advise communities in flood-prone areas to stay vigilant",
    ],
    2: [
        "Activate early-warning systems",
        "Pre-position relief supplies",
        "Open evacuation shelters",
        "Issue public advisory via SMS and radio",
        "Inspect and reinforce river embankments",
    ],
    3: [
        "Issue mandatory evacuation orders for high-risk zones",
        "Deploy search-and-rescue teams",
        "Activate the State Emergency Operations Centre",
        "Request military/NGO support",
        "Establish temporary medical posts",
    ],
    4: [
        "Declare a state of emergency",
        "Request federal (NEMA) and international humanitarian assistance",
        "Aerial rescue operations for stranded communities",
        "Mass casualty management protocols",
        "Full mobilisation of all emergency services",
    ],
}


def _has_word(text: str, words) -> bool:
    """Whole-word keyword test ('do' must not match 'Ondo' or 'today')."""
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


class AIAgent:
    """
    Conversational AI agent with tool-calling capability.
    Detects OpenRouter availability at init time; degrades gracefully.
    """

    def __init__(self, flood_detector, alert_system,
                 openrouter_api_key: str = "",
                 openrouter_model: str = "openai/gpt-4o-mini",
                 climate_layers=None,
                 monitored_regions: Optional[List[str]] = None,
                 threads_allowed: bool = True):
        self.detector = flood_detector
        self.alerts = alert_system
        self.climate = climate_layers   # ClimateLayerAnalyzer (may be None)
        self.api_key = openrouter_api_key
        self._model = openrouter_model
        self._ai_available = False
        self._chat_histories: Dict[str, List[Dict]] = {}   # session_id → message list
        self._lock = threading.Lock()

        # Monitor state
        self.threads_allowed = threads_allowed
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_running = False
        self._monitored_regions: List[str] = [
            r for r in (monitored_regions or ["nigeria"]) if ADMIN.get(r)
        ] or ["nigeria"]

        self._init_openrouter()

    # ------------------------------------------------------------------
    # OpenRouter initialisation
    # ------------------------------------------------------------------

    def _init_openrouter(self):
        if not self.api_key:
            logger.info("No OpenRouter API key – using rule-based agent fallback.")
            return
        try:
            # Verify connectivity with a lightweight models check
            resp = _requests.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10,
            )
            resp.raise_for_status()
            self._ai_available = True
            logger.info("OpenRouter agent initialised (model=%s).", self._model)
        except Exception as exc:
            logger.warning("OpenRouter init failed: %s — using rule-based fallback.", exc)
            self._ai_available = False

    @property
    def has_ai(self) -> bool:
        return self._ai_available

    # ------------------------------------------------------------------
    # Chat API
    # ------------------------------------------------------------------

    def chat(self, message: str, session_id: str = "default") -> Dict[str, Any]:
        """Process a user message and return agent response + any tool results."""
        if self.has_ai:
            return self._openrouter_chat(message, session_id)
        return self._rule_based_chat(message)

    # ------------------------------------------------------------------
    # OpenRouter multi-turn chat with tool calling
    # ------------------------------------------------------------------

    def _session_history(self, session_id: str) -> List[Dict]:
        with self._lock:
            if session_id not in self._chat_histories:
                if len(self._chat_histories) >= MAX_SESSIONS:
                    # Drop the oldest session (dicts keep insertion order)
                    self._chat_histories.pop(next(iter(self._chat_histories)))
                self._chat_histories[session_id] = []
            return self._chat_histories[session_id]

    @staticmethod
    def _trim_history(history: List[Dict]):
        """Keep the last MAX_HISTORY_MESSAGES, starting at a user turn so no
        tool result is left without the assistant message that requested it."""
        if len(history) <= MAX_HISTORY_MESSAGES:
            return
        del history[:len(history) - MAX_HISTORY_MESSAGES]
        while history and history[0].get("role") != "user":
            history.pop(0)

    def _openrouter_chat(self, message: str, session_id: str) -> Dict[str, Any]:
        history = self._session_history(session_id)
        self._trim_history(history)
        history.append({"role": "user", "content": message})
        tool_results = []

        # Agentic loop – max 5 tool rounds
        for _ in range(5):
            try:
                resp = _requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "HTTP-Referer": "https://ansasphere.app",
                        "X-Title": "ANSASphere",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
                        "tools": OPENROUTER_TOOLS,
                        "tool_choice": "auto",
                    },
                    timeout=45,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.exception("OpenRouter API error: %s", exc)
                return self._rule_based_chat(message)

            choice = data["choices"][0]["message"]

            # No tool calls → final response
            if not choice.get("tool_calls"):
                content = choice.get("content") or ""
                history.append({"role": "assistant", "content": content})
                return {
                    "response": content,
                    "tool_calls": tool_results,
                    "engine": f"openrouter/{self._model}",
                }

            # Process tool calls
            history.append({"role": "assistant", "content": choice.get("content"),
                            "tool_calls": choice["tool_calls"]})
            for tc in choice["tool_calls"]:
                fn_name = tc["function"]["name"]
                try:
                    fn_args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    fn_args = {}

                result = self._dispatch_tool(fn_name, fn_args)
                tool_results.append({"tool": fn_name, "result": result})
                history.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result),
                })

        # Fallback if loop exhausted without a text response
        return self._rule_based_chat(message)

    # ------------------------------------------------------------------
    # Rule-based fallback
    # ------------------------------------------------------------------

    def _risk_explanation(self, level: int) -> str:
        pct = self.detector.thresholds.get(level)
        text = RISK_EXPLANATIONS.get(level, "Unknown level.")
        return text.format(pct=f"{pct * 100:g} %") if pct is not None else text

    def _rule_based_chat(self, message: str) -> Dict[str, Any]:
        msg_lower = message.lower()
        tool_calls = []

        # --- Route to tool based on keywords ---
        if _has_word(msg_lower, ["flood", "floods", "flooding", "water", "inundation",
                                 "risk", "analyze", "analyse", "check"]):
            region_id = self._extract_region(message)
            result = self._dispatch_tool("analyze_flood_risk", {"region_id": region_id})
            tool_calls.append({"tool": "analyze_flood_risk", "result": result})

            risk_lvl = result.get("risk", {}).get("level", 0)
            area = result.get("stats", {}).get("flooded_area_km2", 0) or 0
            label = result.get("risk", {}).get("label", "Unknown")
            region_label = result.get("region_label", region_id)
            if result.get("success"):
                text = (
                    f"**Flood Status — {region_label}**\n\n"
                    f"Risk Level: **{label}** (Level {risk_lvl}/4)\n"
                    f"Flooded Area: **{area:,.1f} km²**\n\n"
                    f"{self._risk_explanation(risk_lvl)}"
                )
            else:
                text = (f"**Flood Status — {region_label}**\n\n"
                        f"Analysis unavailable: {result.get('message', 'unknown error')}")

        elif _has_word(msg_lower, ["alert", "alerts", "warning", "warnings", "active"]):
            result = self._dispatch_tool("list_active_alerts", {})
            tool_calls.append({"tool": "list_active_alerts", "result": result})
            alerts = result.get("alerts", [])
            if alerts:
                lines = [f"**{a['region']}** — Level {a['level']}: {a['message']}"
                         for a in alerts[:5]]
                text = "**Active Flood Alerts**\n\n" + "\n".join(lines)
            else:
                text = "No active flood alerts at this time."

        elif _has_word(msg_lower, ["lga", "lgas"]) and ADMIN.resolve(message):
            region = ADMIN.resolve(message)
            state_id = region.state_id or region.id
            result = self._dispatch_tool("list_lgas", {"state": state_id})
            tool_calls.append({"tool": "list_lgas", "result": result})
            names = [l["name"] for l in result.get("lgas", [])]
            text = (f"**{result.get('state', state_id)}** has {len(names)} LGAs:\n\n"
                    + ", ".join(names)) if names else "I couldn't find that state."

        elif _has_word(msg_lower, ["recommend", "action", "actions", "do", "response"]):
            region_id = self._extract_region(message)
            res = self._dispatch_tool("analyze_flood_risk", {"region_id": region_id})
            tool_calls.append({"tool": "analyze_flood_risk", "result": res})
            lvl = res.get("risk", {}).get("level", 0)
            actions = RECOMMENDED_ACTIONS.get(lvl, [])
            text = (
                f"**Recommended Actions for {res.get('region_label', region_id)} (Level {lvl})**\n\n"
                + "\n".join(f"• {a}" for a in actions)
            )

        elif _has_word(msg_lower, ["explain", "what", "mean", "means", "level"]):
            # Extract risk level number from text
            lvl = 1
            for word in msg_lower.split():
                if word.isdigit():
                    lvl = min(int(word), 4)
                    break
            result = self._dispatch_tool("explain_risk_level", {"level": lvl})
            tool_calls.append({"tool": "explain_risk_level", "result": result})
            text = result.get("explanation", "")

        elif _has_word(msg_lower, ["hello", "hi", "hey", "help"]):
            text = (
                "Hello! I'm **ANSA**, your AI assistant for ANSASphere.\n\n"
                "I can help you:\n"
                "• **Analyze flood risk** for Nigeria, a state or an LGA — *'Check flood risk in Kogi'*, *'Flood risk in Surulere, Lagos'*\n"
                "• **List active alerts** — *'Show active alerts'*\n"
                "• **List a state's LGAs** — *'LGAs in Bayelsa'*\n"
                "• **Recommend actions** — *'What should we do in Benue?'*\n"
                "• **Explain risk levels** — *'Explain level 3'*\n\n"
                "What would you like to know?"
            )
        else:
            text = (
                "I can analyze flood risk, list alerts, and recommend emergency actions "
                "for Nigeria, any state or any LGA. "
                "Try asking: *'What is the flood status in Bayelsa?'*"
            )

        return {"response": text, "tool_calls": tool_calls, "engine": "rule-based"}

    # ------------------------------------------------------------------
    # Tool dispatcher
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_region_arg(value: Optional[str]) -> Optional[str]:
        region = ADMIN.get(value) or ADMIN.resolve(value)
        return region.id if region else None

    def _dispatch_tool(self, name: str, args: Dict) -> Dict:
        try:
            if name in ("analyze_flood_risk", "analyze_climate_layer"):
                raw = args.get("region_id") or "nigeria"
                region_id = self._resolve_region_arg(raw)
                if region_id is None:
                    return {"error": f"Unknown Nigerian region '{raw}'. Use a state or "
                                     "LGA name, e.g. 'lagos' or 'lagos/surulere'."}
                if name == "analyze_flood_risk":
                    return self.detector.analyze(
                        region_id=region_id,
                        event_date=args.get("event_date"),
                    )
                if self.climate is None:
                    return {"error": "Climate layer analyser not initialised."}
                return self.climate.analyze(
                    layer_id=args.get("layer_id", "vegetation"),
                    region_id=region_id,
                    date=args.get("date"),
                )
            if name == "list_active_alerts":
                return {"alerts": self.alerts.get_active()}
            if name == "explain_risk_level":
                lvl = max(0, min(int(args.get("level", 0)), 4))
                return {
                    "level": lvl,
                    "label": ["None", "Watch", "Advisory", "Warning", "Emergency"][lvl],
                    "explanation": self._risk_explanation(lvl),
                }
            if name == "recommend_action":
                lvl = int(args.get("risk_level", 0))
                region = args.get("region", "the region")
                return {
                    "risk_level": lvl,
                    "region": region,
                    "actions": RECOMMENDED_ACTIONS.get(lvl, []),
                }
            if name == "list_climate_layers":
                if self.climate is None:
                    return {"layers": []}
                return {"layers": self.climate.list_layers()}
            if name == "list_lgas":
                region = ADMIN.get(args.get("state")) or ADMIN.resolve(args.get("state"))
                if region is None or region.level == "country":
                    return {"error": f"Unknown state '{args.get('state')}'."}
                state = ADMIN.get(region.state_id) if region.level == "lga" else region
                return {"state": state.label,
                        "lgas": [{"id": l.id, "name": l.name} for l in ADMIN.lgas(state.id)]}
        except Exception as exc:
            logger.exception("Tool dispatch error (%s): %s", name, exc)
            return {"error": str(exc)}
        return {"error": f"Unknown tool: {name}"}

    def _extract_region(self, text: str) -> str:
        """Most specific Nigerian region named in *text*; defaults to the whole country."""
        region = ADMIN.resolve(text)
        return region.id if region else "nigeria"

    # ------------------------------------------------------------------
    # Monitor
    # ------------------------------------------------------------------

    def start_monitor(self, regions: Optional[List[str]] = None,
                      interval: int = 3600) -> bool:
        """Start the background thread.  Returns False when threads aren't
        allowed (PythonAnywhere) — use run_monitor.py as a scheduled task."""
        if regions:
            valid = [r for r in regions if ADMIN.get(r)]
            if valid:
                self._monitored_regions = valid
        if not self.threads_allowed:
            return False
        self._monitor_running = True
        if self._monitor_thread and self._monitor_thread.is_alive():
            return True   # already running — the loop picks up the new regions
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(max(60, interval),),
            daemon=True,
            name="ansa-monitor",
        )
        self._monitor_thread.start()
        logger.info("Background monitor started (interval=%ds, regions=%s)",
                    interval, self._monitored_regions)
        return True

    def stop_monitor(self):
        self._monitor_running = False

    def _monitor_loop(self, interval: int):
        while self._monitor_running:
            try:
                self.run_monitor_cycle()
            except Exception as exc:
                logger.error("Monitor cycle error: %s", exc)
            # Sleep in short steps so stop_monitor() takes effect promptly
            for _ in range(interval):
                if not self._monitor_running:
                    return
                time.sleep(1)

    def run_monitor_cycle(self) -> List[Dict]:
        """Run one flood check per monitored region; create alerts for risk ≥ 2."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        summary = []
        for region_id in self._monitored_regions:
            try:
                result = self.detector.analyze(region_id=region_id,
                                               event_date=today,
                                               use_cache=False)
                risk = result.get("risk", {}).get("level", 0)
                area = result.get("stats", {}).get("flooded_area_km2", 0) or 0
                summary.append({"region_id": region_id, "success": result.get("success"),
                                "risk": risk, "message": result.get("message")})

                if result.get("success") and risk >= 2:
                    self.alerts.create(
                        region=result.get("region_label", region_id),
                        level=risk,
                        message=(
                            f"Automated detection: {area:,.0f} km² flooded "
                            f"({result['stats'].get('flood_fraction_pct', 0):.1f}% of area). "
                            f"Risk: {result['risk']['label']}."
                        ),
                        source="auto-monitor",
                    )
                    logger.info("Auto-alert created: %s level=%d", region_id, risk)
            except Exception as exc:
                logger.warning("Monitor failed for %s: %s", region_id, exc)
                summary.append({"region_id": region_id, "success": False, "message": str(exc)})
        self.alerts.set_meta("monitor_last_run", datetime.utcnow().isoformat())
        return summary

    @property
    def monitor_status(self) -> Dict:
        return {
            "running": self._monitor_running,
            "mode": "thread" if self.threads_allowed else "scheduled-task",
            "regions": self._monitored_regions,
            "last_run": self.alerts.get_meta("monitor_last_run"),
            "engine": f"openrouter/{self._model}" if self.has_ai else "rule-based",
        }
