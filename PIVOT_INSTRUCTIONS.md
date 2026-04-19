We are pivoting SolSpecs from an outdoor worker wearable into a firefighter AR HUD built on Meta Quest 3. The theme is Environment/Climate/Energy — wildfires are a direct climate change consequence. The Quest 3 runs a WebXR browser app showing a heads-up display through AR passthrough. The armband with UNO Q still provides biometric data over WiFi. Everything renders in the Quest 3 browser.

## What we keep, what we cut, what we build

KEEP (do not delete or rewrite):
- config.py — update thresholds and prompts but keep structure
- core/heat_stress.py — WBGT + tier scoring still applies to firefighter heat stress
- core/state_machine.py — sensor fusion, tier logic, fall detection, alerts all still apply
- core/emg_classifier.py — gesture control stays
- core/mcu_bridge.py — SimulatorBridge + SerialBridge stays
- core/audio.py — priority queue audio stays (voice alerts through Quest 3 audio)
- core/phone_gps_client.py — GPS stays for position on fire map
- core/ai_pipeline.py — keep Gemini Vision + ElevenLabs, update prompts for firefighter context
- core/qualcomm_llm.py — keep, update system prompt for firefighter context
- tests/ — keep existing tests, add new ones

CUT (delete these):
- glasses/glasses_server.py — Pi Zero is no longer the display, Quest 3 replaces it
- glasses/oled_display.py — no OLED, HUD is on Quest 3
- core/glasses_client.py — replaced by Quest 3 polling the UNO Q directly

BUILD NEW:
1. A Flask HTTP sensor server on the UNO Q that serves biometric data as JSON (replaces the glasses server concept — now the Quest 3 polls the UNO Q directly)
2. A complete WebXR HUD app (single HTML file) that runs in the Quest 3 browser with AR passthrough
3. A Rothermel-based cellular automata fire spread simulation in JavaScript
4. A fire map renderer on a canvas element in the HUD
5. Biometric vitals panel in the HUD
6. Alert system in the HUD
7. Timers (thermal exposure, air supply, time on scene)

## System architecture

```
Quest 3 Browser (WebXR AR Passthrough)
│  Renders full-screen HUD overlay on top of real world
│  Runs fire simulation locally in JavaScript
│  Polls UNO Q for biometric data every 2 seconds
│
│  GET http://<unoq-ip>:8080/sensors → biometric JSON
│  GET http://<unoq-ip>:8080/status → heat tier + alerts
│
└── All on same phone WiFi hotspot

UNO Q (Armband)
│  STM32 reads sensors via Bridge RPC (already working)
│  Python side runs Flask on port 8080
│  Serves sensor data + computed heat stress tier
│  Also runs state machine for alert logic
│
└── Sensors: Modulino Movement, thermistor, water level, GSR, MAX30102 (when it arrives), DHT11
```

The Quest 3 browser fetches JSON from the UNO Q every 2 seconds. All rendering, fire simulation, and HUD logic runs client-side in the Quest 3 browser as JavaScript. The UNO Q only serves data.

## HUD Layout (what the firefighter sees through AR passthrough)

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  ┌─── VITALS ───────┐              ┌──── FIRE MAP ─────┐   │
│  │ HR:  92 bpm  ███ │              │   64x64 grid       │   │
│  │ SpO2: 97%   ████ │              │   green = safe     │   │
│  │ Temp: 37.2  ██░░ │              │   orange = burning │   │
│  │ Hydra: OK   ████ │              │   red = 10min pred │   │
│  │                   │              │   darkred = 30min  │   │
│  │ HEAT: 🟡 YELLOW  │              │   blue dot = you   │   │
│  └───────────────────┘              │   wind arrow       │   │
│                                     │   time slider      │   │
│                                     └────────────────────┘   │
│                                                             │
│  ┌─── TIMERS ───────────────────────────────────────────┐   │
│  │ THERMAL EXPOSURE: 12:45  │  AIR: 18:30  │  ON SCENE: │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                             │
│                    ┌──── ALERT ────┐                        │
│                    │ (fades in/out) │                        │
│                    └────────────────┘                        │
│                                          [MAYDAY]           │
└─────────────────────────────────────────────────────────────┘
```

## Fire Simulation Spec

Cellular automata on a 64x64 grid. Each cell has:
- fuel_type: 0=water, 1=grass, 2=brush, 3=forest, 4=urban, 5=rock/bare
- elevation: 0-100 (affects spread rate — fire goes uphill faster)
- state: 0=unburned, 1=burning, 2=burned

Spread rules per tick (1 tick = 1 simulated minute):
- Burning cell attempts to ignite each of 8 neighbors
- Spread probability = base_rate[fuel_type] * wind_factor * slope_factor
- wind_factor: 2.0 in wind direction, 1.0 perpendicular, 0.3 against wind
- slope_factor: 1.0 + 0.5 * (neighbor_elevation - cell_elevation) / 10 (clamped 0.5 to 3.0)
- base_rate: water=0, grass=0.6, brush=0.4, forest=0.3, urban=0.2, rock=0
- If random() < spread_probability, neighbor ignites
- Burning cells become burned after 3 ticks (grass), 8 ticks (brush), 15 ticks (forest), 10 ticks (urban)

Wind is configurable: direction (0-360 degrees) and speed (affects multiplier).

For the demo: pre-generate a forest map with a clearing, a river, and some brush. Start fire at one edge. Show it spreading over simulated time. The firefighter's position (blue dot) is placed where the fire will reach in ~5-10 simulated minutes, creating dramatic tension.

The simulation runs at ~2 ticks per second for visual effect, but a time slider lets you scrub forward to see +10, +20, +30 minute predictions instantly.

## Sensor Data JSON format (what UNO Q serves)

```json
{
  "heart_rate": 92,
  "spo2": 97,
  "skin_temp_c": 37.2,
  "ambient_temp_c": 34.5,
  "ambient_humidity_pct": 65,
  "wbgt": 29.8,
  "heat_tier": "yellow",
  "hydration": "ok",
  "sweat_level": 340,
  "gsr": 450,
  "accel_x": 0.05,
  "accel_y": -0.05,
  "accel_z": -0.97,
  "fall_detected": false,
  "sun_exposure_min": 18,
  "noise_exposure_min": 45,
  "thermal_exposure_s": 765,
  "timestamp": 1713456789
}
```

## Config updates needed

Update config.py:
- SCENE_PROMPT → firefighter context: "You are an AI safety system for a firefighter in an active wildfire zone. Analyze this image. Report: 1) Visible fire, smoke, or ember activity and direction. 2) Structural integrity of nearby buildings or terrain stability. 3) Egress routes — clear paths away from fire. 4) Hazards — downed power lines, gas lines, unstable structures, flashover risk. Be concise, under 4 sentences."
- CONVERSATION_SYSTEM_PROMPT → firefighter context: "You are an AI assistant for a firefighter during active wildfire operations. You have access to their live biometric data and fire spread predictions. Help them stay safe. Advise on heat stress, hydration, egress routes, and tactical decisions. Keep responses under 3 sentences — they're in an active emergency."
- Add: SENSOR_SERVER_PORT = 8080
- Add: FIRE_GRID_SIZE = 64
- Add: FIRE_TICK_RATE = 2.0

## Detailed build phases

---

PHASE 1: Sensor HTTP Server

Create core/sensor_server.py — a Flask app that runs on the UNO Q Python side:

- Imports the existing state_machine and reads its current state
- GET /sensors → returns the full JSON blob above
- GET /status → returns just {heat_tier, alerts[], thermal_exposure_s, air_remaining_s}
- GET /fire-config → returns wind/terrain config for the fire sim (so we can adjust from the UNO Q side later)
- CORS headers on all responses (Access-Control-Allow-Origin: *) — critical, the Quest 3 browser is a different origin
- Runs on 0.0.0.0:8080

Also create core/sensor_server_mock.py — serves realistic fake data that cycles through scenarios (normal → heating up → critical → recovery) for testing in the Quest 3 browser without the UNO Q. This should be runnable standalone: python core/sensor_server_mock.py and it serves on port 8080.

Test: run the mock server, open http://localhost:8080/sensors in browser, confirm valid JSON.

---

PHASE 2: Fire Simulation Engine

Create hud/fire_simulation.js — pure JavaScript, no dependencies:

- FireGrid class with constructor(size, seed)
- generateTerrain(seed) — procedurally generates a plausible wildfire terrain: mostly forest, a river cutting through, a clearing, some brush areas, random elevation
- setFire(x, y) — ignites a cell
- tick(wind_direction, wind_speed) — advances simulation one step using the Rothermel-inspired spread rules from the spec above
- getState() — returns the full grid state for rendering
- predictFuture(minutes) — runs the simulation forward N ticks from current state WITHOUT modifying current state, returns predicted grid
- reset() — resets to initial terrain

Test: create a minimal test.html that includes fire_simulation.js, creates a 64x64 grid, starts a fire, runs 30 ticks, and renders the result to a canvas. Open in Chrome to verify fire spreads plausibly — respects wind direction, doesn't cross water, goes uphill faster.

---

PHASE 3: HUD Renderer

Create hud/index.html — a single self-contained HTML file with embedded CSS and JavaScript:

Layout the HUD as described in the spec above. Use position:fixed CSS to pin elements to screen regions. Use a transparent background so AR passthrough shows through.

Components:
- Vitals panel (top-left): heart rate with bar, SpO2 with bar, skin temp with bar, hydration status, heat tier badge with color
- Fire map (top-right): canvas element rendering the fire grid, color-coded cells, blue dot for user position, wind direction arrow
- Timers (bottom-left): thermal exposure (counts up), air remaining (counts down from 30:00), time on scene (counts up)
- Alert banner (center): appears with fade-in animation, auto-fades after 5 seconds, shows text like "HEAT STRESS WARNING" or "FIRE APPROACHING"
- MAYDAY button (bottom-right): big red circle, tappable

Styling: dark semi-transparent panels with rounded corners, white/green text, red for critical values. Think sci-fi HUD aesthetic — thin borders, monospace numbers, subtle glow effects. The background must be transparent/see-through for AR passthrough.

Include fire_simulation.js via script tag (or inline it).

On load:
- Initialize fire simulation with a pre-configured demo scenario
- Start polling http://SENSOR_SERVER_IP:8080/sensors every 2 seconds (configurable at top of file)
- Start fire simulation ticking at 2 ticks/second
- Start timers counting
- If sensor data shows fall_detected=true, show MAYDAY alert
- If heat_tier changes, update vitals panel and potentially show alert
- If fire reaches within 3 cells of user position, show "FIRE APPROACHING YOUR POSITION" alert

Add a time scrubber slider under the fire map — dragging it shows the predicted fire state at +5, +10, +15, +20, +25, +30 minutes. Releasing it snaps back to current state.

For testing: at the top of the file, set SENSOR_SERVER_IP to "localhost". Run core/sensor_server_mock.py on port 8080. Open hud/index.html in Chrome. The HUD should render with fake biometric data updating every 2 seconds and fire spreading across the map.

---

PHASE 4: WebXR Passthrough Integration

Modify hud/index.html to support WebXR immersive-ar mode:

Add a "Start AR" button that, when tapped, requests an immersive-ar WebXR session with the 'camera-access' feature. In immersive mode, the HUD elements render as a DOM overlay on top of the camera passthrough.

Key WebXR code structure:
```javascript
const session = await navigator.xr.requestSession('immersive-ar', {
    requiredFeatures: ['camera-access'],
    domOverlay: { root: document.getElementById('hud-container') }
});
```

The domOverlay feature lets your existing HTML/CSS render directly on top of the passthrough feed. No Three.js or WebGL needed for the HUD itself — just DOM overlay. The fire map canvas and vitals panels render as normal HTML on top of the real world.

If WebXR is not available (laptop testing), fall back to the fullscreen browser view with a black background. This way the same file works on both Quest 3 and laptop.

Test: Open the Quest 3 browser, navigate to http://<laptop-ip>:8080/hud (we'll serve it from the mock server), tap "Start AR", confirm HUD appears floating over the real world through passthrough.

To serve the HUD file from the mock server, add a route:
- GET / or GET /hud → serves hud/index.html

---

PHASE 5: Wire main.py to sensor server

Update main.py:
- In addition to running the state machine loop, start the Flask sensor server in a background thread
- The sensor server reads from the state machine's current state (it already computes everything)
- The state machine still processes MCU bridge data and runs all alert/tier logic
- Voice alerts still play through the UNO Q's audio output (Bluetooth earbuds in the helmet)

In --simulate mode:
- SimulatorBridge provides fake MCU data (already works)
- Sensor server serves the fused results to the Quest 3
- Open Quest 3 browser to http://<computer-ip>:8080/hud

In --live mode:
- Real MCU bridge reads from STM32
- Same sensor server serves to Quest 3

Test: run python main.py --simulate, open browser to localhost:8080/sensors, confirm data updates. Open localhost:8080/hud (or the full path to index.html), confirm HUD shows live updating data.

---

PHASE 6: Demo scenario configuration

Create hud/demo_scenario.js — a pre-configured fire scenario for the hackathon demo:

- Terrain: 64x64 grid, mostly forest (type 3), a river running diagonally (type 0), a clearing in the center (type 1 grass), some brush (type 2) around edges, gentle elevation increasing toward top-right
- Fire start position: bottom-left corner, 3 cells ignited
- Wind: blowing from bottom-left to top-right at moderate speed (pushes fire toward the firefighter)
- Firefighter position: center of the clearing — fire will reach them in about 5 minutes of simulation time
- Pre-run the simulation invisibly for ~20 ticks so when the demo starts, fire is already visibly spreading (not starting from a tiny dot)

The demo flow:
1. Firefighter puts on helmet (Quest 3)
2. HUD appears through AR passthrough
3. Fire map shows fire spreading from the south
4. Vitals show normal (green tier)
5. Over 2-3 minutes of real time, simulated biometrics escalate (mock server cycles through scenarios)
6. Heat tier goes yellow → "HEAT STRESS WARNING — HYDRATE NOW"
7. Fire approaches position on map → "FIRE APPROACHING YOUR POSITION — ESTIMATED 3 MINUTES"
8. HR hits critical → tier goes red → "DANGER — HEAT STRESS CRITICAL — PULL BACK"
9. MAYDAY button press → "MAYDAY ACTIVATED — COORDINATES SENT"

This creates a complete dramatic arc in under 4 minutes — perfect for judging.

---

After all phases, confirm the following end-to-end test works:

1. Run: python core/sensor_server_mock.py
2. Open Chrome on laptop: http://localhost:8080/hud
3. See HUD with vitals updating, fire spreading, timers counting
4. Watch scenario cycle through normal → warning → critical
5. See alerts appear and fade
6. Use time scrubber to see fire prediction

Then test on Quest 3:
1. Same mock server running on laptop
2. Quest 3 browser: http://<laptop-ip>:8080/hud
3. Tap "Start AR"
4. See HUD overlaid on real world through passthrough
5. Same data, same fire map, same alerts — but in AR
