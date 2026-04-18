# SolSpecs — Full System Architecture & Build Context

## What This Project Is

SolSpecs is a wearable climate safety system for outdoor workers (construction, agriculture, delivery). It monitors environmental heat conditions and physiological heat stress response simultaneously, alerting workers through voice before symptoms appear. It also provides AI-powered visual environmental assessment through camera + Gemini Vision.

The system runs across TWO compute platforms that must communicate as one unified system.

---

## Hardware Architecture

### Device 1: The Glasses (Raspberry Pi Zero 2W)

**Compute:** Raspberry Pi Zero 2W running Raspbian Lite
**Power:** 1200mAh PiSugar battery pack
**Network:** WiFi (connects to phone hotspot)

**Sensors on the glasses frame:**
- Pi Camera (CSI ribbon cable) — captures what the worker is looking at
- Transparent OLED screen (SPI, SSD1306 or SH1106 driver) — shows heat tier color dot (green/yellow/orange/red)
- DHT11 temperature + humidity sensor (GPIO digital pin) — ambient environmental conditions
- Photoresistor (needs MCP3008 ADC since Pi has no analog pins, OR use a digital light sensor like BH1750 via I2C if available, OR use a simple RC timing circuit on a GPIO pin) — tracks sun vs shade exposure
- Sound sensor module (same ADC issue — use the digital output pin of the sound sensor which goes HIGH when threshold exceeded, read as GPIO digital) — noise exposure detection

**Role:** Sensor node + camera + display. Does NOT run AI. Sends data to UNO Q, receives display commands back.

### Device 2: The Armband (Arduino UNO Q)

**Compute:** Qualcomm Dragonwing QRB2210 (Linux Debian) + STM32U585 (Zephyr/Arduino)
**Power:** USB-C PD power bank (20000mAh)
**Network:** WiFi 5 dual-band (connects to phone hotspot)

**Sensors on/in the armband:**
- EMG electrodes → signal conditioning → STM32 analog pin A0
- MAX30102 heart rate + SpO2 → STM32 I2C (address 0x57)
- Thermistor (skin temperature) → STM32 analog pin A1
- Water level sensor (sweat detection) → STM32 analog pin A2
- MPU9250 IMU (fall detection + exertion) → STM32 I2C (address 0x68)

**Audio output:** Bluetooth earbuds paired to the Qualcomm Linux side

**Role:** Central brain. Runs state machine, sensor fusion, AI API calls, voice output. Receives data from Pi Zero. Sends display commands to Pi Zero.

### Device 3: Phone (in pocket)

- Provides WiFi hotspot for both Pi Zero and UNO Q
- Runs a browser page that serves GPS coordinates via local HTTP
- No app needed — just a web page with Geolocation API

---

## Network Topology

```
Phone (WiFi Hotspot + GPS)
├── 192.168.x.x — provides internet for API calls
│
├── Pi Zero 2W (Glasses)
│   IP: assigned by hotspot DHCP
│   Runs: Flask server on port 5001
│   Endpoints:
│     GET /sensors     → returns JSON of glasses sensor readings
│     GET /capture     → captures a frame, returns JPEG bytes
│     POST /display    → receives {color: "green|yellow|orange|red"} and updates OLED
│     GET /health      → returns {"status": "ok"} for heartbeat
│
├── Arduino UNO Q (Armband)
│   IP: assigned by hotspot DHCP
│   Runs: Python main process on Qualcomm Linux side
│   Communicates with:
│     - STM32 MCU via internal serial bridge (Arduino RPC)
│     - Pi Zero via HTTP (glasses_ip:5001)
│     - Phone GPS via HTTP (phone_ip:5000)
│     - Gemini API via internet
│     - ElevenLabs API via internet
│     - Bluetooth earbuds via Linux bluetooth stack
│
└── Phone GPS Server
    Runs: browser page with Geolocation API
    Or: Termux Flask server
    Endpoint: GET /location → {latitude, longitude, accuracy}
```

---

## Communication Protocol

### Pi Zero → UNO Q (sensor data, every 2 seconds)

The UNO Q polls `GET http://<pi_ip>:5001/sensors` and receives:

```json
{
  "ambient_temp_c": 34.2,
  "ambient_humidity_pct": 65.0,
  "light_level": 850,
  "is_direct_sun": true,
  "noise_level_db": 78,
  "noise_above_threshold": false,
  "timestamp": 1713456789.123
}
```

### UNO Q → Pi Zero (display commands, as needed)

The UNO Q sends `POST http://<pi_ip>:5001/display` with:

```json
{
  "heat_tier": "orange",
  "message": "HR HIGH"
}
```

The Pi Zero updates the OLED to show the corresponding color and optional short text.

### UNO Q → Pi Zero (camera capture, on demand)

When EMG gesture or button triggers "scan environment":

1. UNO Q sends `GET http://<pi_ip>:5001/capture`
2. Pi Zero captures a frame from Pi Camera
3. Returns JPEG bytes with Content-Type: image/jpeg
4. UNO Q sends the image bytes to Gemini Vision API
5. Gemini returns description text
6. UNO Q sends text to ElevenLabs TTS
7. Audio plays through Bluetooth earbuds

### STM32 → Qualcomm Linux (internal, via serial bridge)

The STM32 MCU on the UNO Q reads all armband sensors at 20Hz and sends JSON lines over the internal serial connection:

```json
{
  "emg_raw": 512,
  "heart_rate": 82,
  "spo2": 97,
  "skin_temp_raw": 620,
  "sweat_raw": 340,
  "accel_x": 0.02,
  "accel_y": -0.05,
  "accel_z": 1.01,
  "gyro_x": 0.5,
  "gyro_y": -0.3,
  "gyro_z": 0.1
}
```

The Qualcomm Linux side reads this via `/dev/ttyACM0` or the Arduino RPC bridge.

### Phone → UNO Q (GPS, polled every 5 seconds)

UNO Q polls `GET http://<phone_ip>:5000/location` and receives:

```json
{
  "latitude": 32.8801,
  "longitude": -117.234,
  "accuracy": 5.2
}
```

---

## Software Components to Build

### On the Pi Zero 2W (Python)

**File: `glasses_server.py`**

A Flask server that:
1. Reads DHT11 via `adafruit_dht` library on a GPIO pin (e.g., GPIO4)
2. Reads photoresistor — either via MCP3008 SPI ADC, or via RC timing on a GPIO pin, or via the sound sensor's digital threshold pin approach. If no ADC available, use a simple HIGH/LOW digital read through a voltage divider tuned so that direct sunlight = HIGH, shade = LOW.
3. Reads sound sensor — use the digital output (DO) pin which goes HIGH when sound exceeds the onboard potentiometer threshold. Set the pot to ~85dB equivalent during calibration.
4. Serves `GET /sensors` returning the combined JSON
5. Serves `GET /capture` — uses `picamera2` library to capture a JPEG frame and return it as bytes
6. Serves `POST /display` — receives heat tier color and updates the OLED via SPI/I2C using `luma.oled` library
7. Serves `GET /health` — simple heartbeat

**Dependencies:** flask, adafruit-circuitpython-dht, picamera2, luma.oled, Pillow

**Startup:** Runs on boot via systemd service so it's ready when the UNO Q comes online.

**OLED Display Logic:**
- Green tier: solid green circle
- Yellow tier: solid yellow circle
- Orange tier: solid orange circle, blinking slowly
- Red tier: solid red circle, blinking fast
- Optional: 2-line text for short messages like "HR: 112" or "SHADE NOW"

### On the Arduino UNO Q — STM32 Side (Arduino C++)

**File: `solspecs_mcu.ino`**

An Arduino sketch that:
1. Reads EMG on analog pin A0 at ~500Hz (or as fast as practical)
2. Reads MAX30102 via I2C (use SparkFun_MAX3010x library)
3. Reads thermistor on analog pin A1
4. Reads water level sensor on analog pin A2
5. Reads MPU9250 via I2C at address 0x68 (raw register reads, same as MPU6050)
6. Packs all readings into a JSON line
7. Sends over Serial at 20Hz to the Qualcomm Linux side
8. Receives commands from Linux side (currently unused but reserved for future vibration motor control if added)

**Libraries needed:** ArduinoJson, SparkFun_MAX3010x (or MAX30105), Wire

### On the Arduino UNO Q — Qualcomm Linux Side (Python)

This is the brain. Multiple modules:

**File: `main.py`** — Entry point. Wires everything together. Starts all subsystems.

**File: `core/state_machine.py`** — Central state machine (already written for BlindGuide, needs adaptation):
- Receives fused sensor data from both Pi Zero and STM32
- Runs heat stress scoring algorithm (WBGT + physiological fusion)
- Runs fall detection logic
- Runs fatigue tracking
- Runs noise exposure tracking
- Manages heat tier (green/yellow/orange/red)
- Triggers voice alerts via callbacks
- Triggers OLED updates via callbacks
- Processes EMG gestures

Key changes from BlindGuide version:
- Remove obstacle/ultrasonic processing (unless we add those later)
- Remove navigation processing
- Add WBGT calculation from DHT11 data
- Add cumulative sun exposure timer from photoresistor data
- Add heat tier escalation logic (green→yellow→orange→red)
- Add periodic status check (every 30 min in green, every 10 min in yellow, every 5 min in orange)
- Add noise exposure cumulative tracking

**File: `core/ai_pipeline.py`** — Gemini Vision + ElevenLabs TTS (already written, needs prompt changes):
- Scene description prompt changed to outdoor worker safety focus
- Emphasis on: sun vs shade, nearest shade/shelter, safety hazards, water/hydration stations

New Gemini prompt:
```
You are an AI safety system for an outdoor worker. Analyze this image of their work environment.
Report concisely (under 4 sentences):
1. Sun exposure: Is the worker in direct sunlight or shade?
2. Nearest shade: Where is the closest shaded or covered area, and roughly how far?
3. Hazards: Any visible safety risks — unguarded edges, moving equipment, trip hazards, unstable ground?
4. Hydration: Is water or a cooling station visible?
Speak directly to the worker. Use simple directions like "to your left" or "behind you."
```

**File: `core/emg_classifier.py`** — EMG gesture detection (already written, reuse as-is):
- Quick flex → "read me my status"
- Sustained flex → "scan my environment"

**File: `core/glasses_client.py`** — NEW. HTTP client that talks to the Pi Zero:
- Polls `/sensors` every 2 seconds
- Requests `/capture` when AI scan is triggered
- Sends `/display` when heat tier changes
- Handles connection loss gracefully (Pi Zero might be out of range briefly)

**File: `core/phone_gps_client.py`** — NEW. HTTP client that talks to the phone GPS:
- Polls `/location` every 5 seconds
- Stores current lat/lng for emergency GPS readout

**File: `core/mcu_bridge.py`** — Serial bridge to STM32 (already written, reuse with minor changes):
- Reads JSON lines from internal serial
- Parses sensor data
- Feeds into state machine

**File: `core/heat_stress.py`** — NEW. The core climate algorithm:

```python
def compute_wbgt_estimate(temp_c: float, humidity_pct: float, in_direct_sun: bool) -> float:
    """
    Estimate Wet Bulb Globe Temperature from temperature and humidity.
    Full WBGT requires a wet bulb thermometer and a black globe thermometer,
    but a reasonable estimate can be computed from temp + humidity.
    
    Simplified Liljegren approximation:
    WBGT ≈ 0.7 * Tw + 0.2 * Tg + 0.1 * Ta
    
    Where:
    - Tw (wet bulb) ≈ estimated from temp + humidity using Stull (2011) formula
    - Tg (globe) ≈ Ta + solar_offset (add ~5-7°C in direct sun)
    - Ta = ambient air temperature
    """
    # Stull (2011) wet bulb temperature estimation
    tw = temp_c * math.atan(0.151977 * (humidity_pct + 8.313659) ** 0.5) + \
         math.atan(temp_c + humidity_pct) - \
         math.atan(humidity_pct - 1.676331) + \
         0.00391838 * humidity_pct ** 1.5 * math.atan(0.023101 * humidity_pct) - \
         4.686035
    
    # Globe temperature estimate
    solar_offset = 7.0 if in_direct_sun else 1.0
    tg = temp_c + solar_offset
    
    wbgt = 0.7 * tw + 0.2 * tg + 0.1 * temp_c
    return wbgt


def compute_heat_stress_tier(wbgt, heart_rate, spo2, skin_temp, 
                              breathing_rate, sweat_level, 
                              sun_exposure_minutes, exertion_level):
    """
    Fuse environmental and physiological signals into a heat stress tier.
    
    Returns: "green", "yellow", "orange", or "red"
    
    OSHA WBGT thresholds for moderate work:
    - < 26°C: low risk
    - 26-28°C: moderate risk  
    - 28-30°C: high risk
    - > 30°C: very high risk
    
    Physiological thresholds:
    - HR > 100: mild concern
    - HR > 120: significant concern
    - HR > 140: critical
    - SpO2 < 95: concern
    - SpO2 < 90: critical
    - Skin temp > 37.5°C: mild concern
    - Skin temp > 38.5°C: critical
    """
    score = 0  # accumulate risk points
    
    # Environmental scoring
    if wbgt > 30: score += 4
    elif wbgt > 28: score += 3
    elif wbgt > 26: score += 2
    elif wbgt > 24: score += 1
    
    # Sun exposure scoring
    if sun_exposure_minutes > 45: score += 3
    elif sun_exposure_minutes > 30: score += 2
    elif sun_exposure_minutes > 15: score += 1
    
    # Heart rate scoring
    if heart_rate > 140: score += 4
    elif heart_rate > 120: score += 3
    elif heart_rate > 100: score += 2
    elif heart_rate > 90: score += 1
    
    # SpO2 scoring (inverted — lower is worse)
    if spo2 < 90: score += 4
    elif spo2 < 93: score += 2
    elif spo2 < 95: score += 1
    
    # Skin temperature scoring
    if skin_temp > 38.5: score += 4
    elif skin_temp > 37.5: score += 2
    elif skin_temp > 37.0: score += 1
    
    # Map score to tier
    if score >= 10: return "red"
    elif score >= 6: return "orange"
    elif score >= 3: return "yellow"
    else: return "green"
```

**File: `core/audio.py`** — NEW. Audio output manager:
- ElevenLabs TTS for spoken alerts
- Plays audio through Bluetooth earbuds via Linux `aplay` or `mpv`
- Queue system so alerts don't overlap
- Priority system: red alerts interrupt everything, yellow waits for current audio to finish

---

## Data Flow Summary

```
GLASSES (Pi Zero)                    ARMBAND (UNO Q)
┌──────────────────┐                ┌──────────────────────────────────┐
│ DHT11 ──────────►│                │          Qualcomm Linux          │
│ Photoresistor ──►│──GET /sensors──►│  ┌──────────────────────────┐   │
│ Sound sensor ───►│   (every 2s)   │  │     State Machine        │   │
│                  │                │  │  - Heat stress scoring    │   │
│ Pi Camera ──────►│──GET /capture──►│  │  - Tier management       │   │
│   (on demand)    │   (on EMG)     │  │  - Alert generation      │   │
│                  │                │  │  - Fall detection         │   │
│ OLED Display ◄──│◄─POST /display─│  │  - Noise tracking        │   │
│                  │   (on change)  │  │  - Fatigue tracking      │   │
└──────────────────┘                │  └──────────┬───────────────┘   │
                                    │             │                    │
                                    │  ┌──────────▼───────────────┐   │
                                    │  │     AI Pipeline          │   │
PHONE                               │  │  - Gemini Vision         │   │
┌──────────────────┐                │  │  - ElevenLabs TTS        │   │
│ GPS ────────────►│──GET /location─►│  │  - Voice conversation    │   │
│                  │   (every 5s)   │  └──────────────────────────┘   │
│ WiFi hotspot ───►│                │                                  │
└──────────────────┘                │  ┌──────────────────────────┐   │
                                    │  │     STM32 MCU            │   │
                                    │  │  - EMG → A0              │   │
                                    │  │  - MAX30102 → I2C        │   │
                                    │  │  - Thermistor → A1       │   │
                                    │  │  - Water level → A2      │   │
                                    │  │  - MPU9250 → I2C         │   │
                                    │  │  Sends JSON at 20Hz      │   │
                                    │  └──────────────────────────┘   │
                                    │                                  │
                                    │  Audio out → BT earbuds         │
                                    └──────────────────────────────────┘
```

---

## Existing Code That Can Be Reused

The project was previously called "BlindGuide" and has working, tested code for:

1. **`core/state_machine.py`** — Full state machine with health monitoring, gesture processing, and alert callbacks. Needs: remove obstacle/ultrasonic code, add WBGT calculation, add heat tier logic, add cumulative sun exposure timer, add noise cumulative tracking. Keep: HR alerting, SpO2 alerting, fall detection, skin temp alerting, fatigue/HRV tracking.

2. **`core/ai_pipeline.py`** — Gemini Vision scene description + ElevenLabs TTS + conversation mode with context memory. Needs: change the scene description prompt to focus on outdoor worker safety (sun exposure, shade, hazards, hydration). Keep: everything else, especially the conversation history and TTS pipeline.

3. **`core/emg_classifier.py`** — Threshold-based and ML-based EMG gesture classification. Two gestures: quick flex = "status", sustained flex = "scan". Needs: no changes. Keep: as-is.

4. **`core/mcu_bridge.py`** — Serial bridge with simulator mode for laptop testing. Needs: update the SimulatorBridge to generate heat-relevant fake data (high ambient temps, rising HR, etc). Keep: SerialBridge class, simulator infrastructure.

5. **`main.py`** — Entry point that wires everything. Needs: significant rewrite to add Pi Zero client, remove navigation, add heat tier management, restructure callbacks.

---

## What Needs to Be Built New

1. **`glasses/glasses_server.py`** — Flask server running on Pi Zero. Reads DHT11, photoresistor, sound sensor. Serves sensor data, camera frames, and OLED display updates.

2. **`core/glasses_client.py`** — HTTP client on UNO Q that polls the Pi Zero server. Handles connection drops gracefully.

3. **`core/heat_stress.py`** — WBGT estimation algorithm + heat tier scoring with multi-signal fusion.

4. **`core/phone_gps_client.py`** — Simple HTTP client polling phone for GPS coordinates.

5. **`core/audio.py`** — Audio output manager with queue and priority system for voice alerts.

6. **`firmware/solspecs_mcu.ino`** — Arduino sketch for STM32 reading all armband sensors and sending JSON over serial.

7. **`phone/gps_server.py`** or **`phone/gps.html`** — Tiny server or web page running on the phone serving GPS coordinates.

---

## Alert Messages (exact text for ElevenLabs TTS)

### Heat Stress Alerts
- **Green periodic (every 30 min):** "Status check. All vitals normal. Heat stress level green. You've had {X} minutes of sun exposure this hour."
- **Yellow (single signal):** "Your heart rate is climbing. Drink water and slow your pace."
- **Yellow (sun):** "You've been in direct sun for {X} minutes. Consider moving to shade if possible."
- **Orange (multi-signal):** "Heat stress warning. Heart rate elevated at {HR} beats per minute. Skin temperature rising. You've been in direct sun for {X} minutes. Take a shade break now."
- **Red (critical):** "Danger. Heat stress critical. Heart rate {HR}. Stop work immediately. Sit down in shade. If you feel dizzy or nauseous, alert your supervisor or say help."

### Fall Detection
- "It seems like you may have fallen. Are you okay? Say I'm fine, or I'll alert your supervisor in 15 seconds. Your GPS location is {lat}, {lng}."

### Fatigue
- "You've been working for {X} hours and your fatigue indicators are elevated. OSHA recommends a rest break in shade with water."

### Noise
- "You've been exposed to hazardous noise levels for over {X} hours today. Hearing protection is recommended."

### Status Readout (on EMG gesture)
- "Current status. Heart rate {HR}. Blood oxygen {SpO2} percent. Skin temperature {temp} degrees. Ambient temperature {ambient} degrees, humidity {humidity} percent. Wet bulb globe temperature estimate {wbgt} degrees. Heat stress level {tier}. Sun exposure {X} minutes this hour. Noise exposure {level}."

### AI Scan
- "Capturing environment." → [Gemini response] → spoken through earbuds

---

## Testing Strategy (without hardware)

All modules should support a `--simulate` flag:
- `glasses_server.py --simulate` → serves fake sensor data, returns a test image for /capture, logs OLED commands to console
- `main.py --simulate` → uses SimulatorBridge for MCU data, uses a mock glasses client, runs full state machine
- `main.py --interactive` → keyboard commands to trigger scenarios (heat spike, fall, gesture, etc.)

This allows full end-to-end testing on a laptop before any hardware is connected.

---

## Discovery / Configuration

On startup, the UNO Q needs to find the Pi Zero on the network. Options:
1. **mDNS:** Pi Zero advertises as `solspecs-glasses.local`. UNO Q connects to `http://solspecs-glasses.local:5001`. Requires avahi-daemon on both. This is the cleanest approach.
2. **Hardcoded IP:** Configure the Pi Zero with a static IP on the hotspot. Simple but fragile.
3. **Broadcast discovery:** UNO Q sends a UDP broadcast, Pi Zero responds with its IP. More robust than hardcoded.

Recommendation: Use mDNS with hardcoded IP as fallback. Set Pi Zero hostname to `solspecs-glasses` and UNO Q will find it at `solspecs-glasses.local`.

---

## File Structure

```
solspecs/
├── README.md
├── requirements.txt                 # Python deps for UNO Q Linux side
│
├── main.py                          # Entry point — runs on UNO Q Linux
├── config.py                        # IP addresses, thresholds, API keys
│
├── core/
│   ├── __init__.py
│   ├── state_machine.py             # Central brain — sensor fusion + alerts
│   ├── heat_stress.py               # WBGT calculation + tier scoring
│   ├── ai_pipeline.py               # Gemini Vision + ElevenLabs TTS
│   ├── emg_classifier.py            # EMG gesture detection
│   ├── mcu_bridge.py                # STM32 serial communication
│   ├── glasses_client.py            # HTTP client → Pi Zero
│   ├── phone_gps_client.py          # HTTP client → Phone GPS
│   └── audio.py                     # Audio output queue + playback
│
├── glasses/
│   ├── glasses_server.py            # Flask server — runs on Pi Zero
│   ├── requirements_glasses.txt     # Python deps for Pi Zero
│   └── oled_display.py              # OLED rendering (heat tier colors)
│
├── firmware/
│   └── solspecs_mcu/
│       └── solspecs_mcu.ino         # Arduino sketch for STM32
│
├── phone/
│   ├── gps_server.py                # Termux Flask GPS server (Android)
│   └── gps.html                     # Browser-based GPS page (any phone)
│
└── tests/
    ├── test_heat_stress.py           # Unit tests for WBGT + tier scoring
    ├── test_state_machine.py         # Integration tests for state machine
    └── simulate_scenarios.py         # Interactive scenario simulator
```

---

## Environment Variables

```bash
# On UNO Q
export GEMINI_API_KEY="your-key"
export ELEVENLABS_API_KEY="your-key"
export GLASSES_URL="http://solspecs-glasses.local:5001"  # or IP
export PHONE_GPS_URL="http://192.168.x.x:5000"
export SOLSPECS_MODE="simulate"  # or "live"

# On Pi Zero
export FLASK_PORT=5001
```

---

## Priority Build Order

1. **`core/heat_stress.py`** — the core algorithm, testable immediately with no hardware
2. **`core/glasses_client.py`** — HTTP client for Pi Zero communication
3. **`glasses/glasses_server.py`** — Flask server for Pi Zero
4. **`core/state_machine.py`** — adapt from BlindGuide, add heat tier logic
5. **`core/audio.py`** — audio output queue
6. **`main.py`** — wire everything together
7. **`firmware/solspecs_mcu.ino`** — Arduino sketch (need hardware for this)
8. **`core/ai_pipeline.py`** — adapt prompts from BlindGuide
9. **`phone/gps.html`** — simple GPS web page
10. **`tests/simulate_scenarios.py`** — interactive testing

Items 1-6 can be built and tested on a laptop RIGHT NOW with no hardware.
