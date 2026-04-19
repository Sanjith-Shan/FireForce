# SolSpecs — Firefighter Wildfire AR/VR HUD

SolSpecs is a real-time biometric and situational-awareness system for wildland firefighters. A wearable sensor array on the Arduino UNO Q streams heart rate, SpO₂, skin temperature, sweat level, and IMU data at 20 Hz to an onboard Python state machine that fuses the signals into an OSHA-compliant heat stress tier (green → yellow → orange → red). The computed state is served over HTTP/HTTPS to a Meta Quest 3 browser, which renders a Three.js VR panorama with a fire-spread simulation, live vitals HUD panels, AI-powered fuel classification overlays, Dijkstra evacuation routing, and Web Speech API voice alerts — all accessible from a single URL at startup.

---

## Hardware

| Component | Role |
|-----------|------|
| Arduino UNO Q (Qualcomm Snapdragon Linux) | Central compute, runs `main.py` |
| STM32 co-processor (Arduino RPC) | Sensor ADC, EMG, IMU at 20 Hz |
| MAX30102 | Heart rate + SpO₂ |
| MPU-9250 | Accelerometer / gyroscope (fall detection, exertion) |
| NTC thermistor (10 kΩ, B=3950) | Skin temperature |
| GSR electrode pair | Galvanic skin response (sweat proxy) |
| Photoresistor | Direct-sun detection for WBGT |
| Sound sensor (ADC) | Noise exposure accumulation |
| Pi Zero 2W + DHT22 (glasses unit) | Ambient temp, humidity, secondary camera |
| Pi Camera Module 3 (on glasses) | Gemini Vision fuel classification |
| Meta Quest 3 | AR/VR HUD display |

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/Sanjith-Shan/SolSpecs.git
cd SolSpecs
pip install -r requirements.txt
```

### 2. Set API keys

```bash
export GEMINI_API_KEY="your-gemini-key"        # fuel classification + scene analysis
export ELEVENLABS_API_KEY="your-el-key"        # voice synthesis (optional)
export QUALCOMM_AI_API_KEY="your-qualcomm-key" # LLM conversation (optional)
```

All keys are optional for simulation mode — the system falls back to mock responses when they are not set.

### 3. Run in simulation mode (laptop, no hardware)

```bash
python main.py --simulate
```

The HUD is served at **http://localhost:8080/hud**.

### 4. Run with HTTPS (required for Quest 3 WebXR)

```bash
python main.py --simulate --https
```

The HUD is served at **https://\<your-machine-ip\>:8443/hud**.  
A temporary self-signed certificate is generated at startup using the `cryptography` package.

### 5. Interactive scenario testing

```bash
python main.py --simulate --interactive
```

Keyboard commands:
- `h` — heat spike (elevated HR, hot environment)
- `c` — critical conditions (low SpO₂)
- `n` — return to normal
- `f` — simulate fall
- `s` — EMG status readout
- `e` — AI environment scan
- `a` — ask the AI a question

---

## Connecting the Quest 3

1. Ensure the Quest 3 and the server machine are on the **same Wi-Fi network**.
2. Start the server: `python main.py --simulate --https`
3. Note the machine's local IP (e.g., `192.168.1.42`).
4. Open the Quest 3 browser and navigate to:  
   `https://192.168.1.42:8443/hud`
5. Accept the self-signed certificate warning (tap **Advanced → Proceed**).
6. Tap **ENTER VR MODE** to enter the Three.js panorama with full HUD, fire simulation, and voice alerts.
7. Tap **ENTER AR MODE** for camera passthrough with DOM overlay (if the headset supports `immersive-ar`).

> **WebXR note:** Quest 3 requires HTTPS for `navigator.xr.requestSession()`. HTTP will load the HUD but the VR/AR buttons will be absent or non-functional.

---

## Project structure

```
SolSpecs/
├── main.py                   # Entry point — wires all subsystems + Flask server
├── config.py                 # All thresholds, ports, API keys
├── requirements.txt
├── core/
│   ├── state_machine.py      # Sensor fusion, heat tier, fall detection
│   ├── sensor_server.py      # Flask API: /sensors /status /fire-config /analyze-fuel /hud
│   ├── heat_stress.py        # WBGT estimation + heat stress tier scoring
│   ├── ai_pipeline.py        # Gemini Vision + Qualcomm LLM + ElevenLabs TTS
│   ├── mcu_bridge.py         # Serial bridge to STM32 (+ SimulatorBridge)
│   ├── glasses_client.py     # HTTP client to Pi Zero glasses unit
│   ├── phone_gps_client.py   # GPS client from paired phone
│   └── audio.py              # Text-to-speech priority queue
├── hud/
│   ├── index.html            # Three.js VR/AR HUD (all phases)
│   └── fire_simulation.js    # Cellular automata fire engine + Dijkstra evacuation
├── phone/
│   └── gps_server.py         # Flask server running on the paired phone
└── tests/
    ├── test_heat_stress.py
    ├── test_state_machine.py
    └── test_sensor_server.py  # Sensor server routes + get_current_state()
```

---

## Running tests

```bash
pytest tests/ -v
```
