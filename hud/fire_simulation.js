/**
 * SolSpecs — Fire Simulation Engine
 * Rothermel-inspired cellular automata on a configurable grid.
 * No dependencies. Works in browser and Node.
 */

// Minimal binary min-heap used by calculateEvacRoute
class _MinHeap {
    constructor() { this._h = []; }
    push(item) { this._h.push(item); this._up(this._h.length - 1); }
    pop()  {
        const top = this._h[0], last = this._h.pop();
        if (this._h.length) { this._h[0] = last; this._down(0); }
        return top;
    }
    get size() { return this._h.length; }
    _up(i)   { while (i > 0) { const p = (i-1)>>1; if (this._h[p][0] <= this._h[i][0]) break; [this._h[p],this._h[i]]=[this._h[i],this._h[p]]; i=p; } }
    _down(i) { const n=this._h.length; for(;;) { let m=i,l=2*i+1,r=2*i+2; if(l<n&&this._h[l][0]<this._h[m][0])m=l; if(r<n&&this._h[r][0]<this._h[m][0])m=r; if(m===i)break; [this._h[m],this._h[i]]=[this._h[i],this._h[m]]; i=m; } }
}

// Seeded PRNG (mulberry32) — gives reproducible terrain per seed
function _mulberry32(seed) {
    return function () {
        seed |= 0;
        seed = (seed + 0x6D2B79F5) | 0;
        let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}

class FireGrid {
    // ── Class-level constants ─────────────────────────────────────────

    // fuel_type indices
    static FUEL_WATER  = 0;
    static FUEL_GRASS  = 1;
    static FUEL_BRUSH  = 2;
    static FUEL_FOREST = 3;
    static FUEL_URBAN  = 4;
    static FUEL_ROCK   = 5;

    // Probability per tick that a burning cell ignites a neighboring cell
    // (before wind and slope adjustment)
    static BASE_RATES = [
        0,    // water  — fireproof
        0.6,  // grass  — fast
        0.4,  // brush  — medium
        0.3,  // forest — slow
        0.2,  // urban
        0,    // rock   — fireproof
    ];

    // Ticks a burning cell stays on fire before going to "burned" state
    static BURN_DURATION = [
        Infinity, // water  (never ignites)
        3,        // grass
        8,        // brush
        15,       // forest
        10,       // urban
        Infinity, // rock
    ];

    static FUEL_NAMES = ['water', 'grass', 'brush', 'forest', 'urban', 'rock'];

    // ── Constructor ───────────────────────────────────────────────────

    constructor(size) {
        this.size = size;
        this.cells = null;       // live grid: cells[y][x]
        this._initialCells = null; // snapshot after generateTerrain, for reset()
    }

    // ── Public API ────────────────────────────────────────────────────

    /**
     * Procedurally generate terrain with a river, clearing, and brush patches.
     * Deterministic for a given seed.
     * @param {number} seed  Integer seed for the PRNG
     */
    generateTerrain(seed) {
        const rng = _mulberry32(seed);
        const N = this.size;

        // Step 1 — Initialise all cells as unburned forest
        this.cells = Array.from({ length: N }, (_r, y) =>
            Array.from({ length: N }, (_c, x) => ({
                fuel_type:  FireGrid.FUEL_FOREST,
                elevation:  0,
                state:      0,   // 0=unburned, 1=burning, 2=burned
                burn_ticks: 0,
            }))
        );

        // Step 2 — Elevation: gradient increasing toward top-right + smoothed noise
        for (let y = 0; y < N; y++) {
            for (let x = 0; x < N; x++) {
                const gradient = (x + (N - 1 - y)) / (2 * (N - 1)); // 0→1 SW to NE
                this.cells[y][x].elevation = gradient * 55 + rng() * 30;
            }
        }
        // 3 passes of box-blur smoothing
        for (let pass = 0; pass < 3; pass++) {
            const snap = this.cells.map(row => row.map(c => c.elevation));
            for (let y = 0; y < N; y++) {
                for (let x = 0; x < N; x++) {
                    let sum = 0, count = 0;
                    for (let dy = -1; dy <= 1; dy++) {
                        for (let dx = -1; dx <= 1; dx++) {
                            const ny = y + dy, nx = x + dx;
                            if (ny >= 0 && ny < N && nx >= 0 && nx < N) {
                                sum += snap[ny][nx];
                                count++;
                            }
                        }
                    }
                    this.cells[y][x].elevation = Math.round(sum / count);
                }
            }
        }

        // Step 3 — Clearing: oval of grass in the center-left area
        //   Fire starts bottom-left; clearing is where the firefighter stands.
        //   River will be right of the clearing so it doesn't block the main fire path.
        const CX = Math.floor(N * 0.44);  // 28 for N=64
        const CY = Math.floor(N * 0.50);  // 32 for N=64
        for (let y = 0; y < N; y++) {
            for (let x = 0; x < N; x++) {
                const dx = x - CX, dy = y - CY;
                // Oval: wider east-west than north-south
                if ((dx * dx) / (10 * 10) + (dy * dy) / (8 * 8) < 1) {
                    this.cells[y][x].fuel_type = FireGrid.FUEL_GRASS;
                }
            }
        }

        // Step 4 — River: winding north-to-south, to the right of the clearing
        //   Rivers are in valleys (lower elevation).
        let riverX = Math.floor(N * 0.63); // start x ≈ 40
        for (let y = 0; y < N; y++) {
            riverX += Math.floor(rng() * 3) - 1;           // wander ±1 per row
            riverX = Math.max(Math.floor(N * 0.50),         // keep right of clearing
                     Math.min(Math.floor(N * 0.80), riverX));
            const rx = riverX;
            for (let w = -1; w <= 1; w++) {                 // 3 cells wide
                const nx = rx + w;
                if (nx >= 0 && nx < N) {
                    this.cells[y][nx].fuel_type = FireGrid.FUEL_WATER;
                    // Rivers flow through valleys
                    this.cells[y][nx].elevation = Math.max(0,
                        this.cells[y][nx].elevation - 20);
                }
            }
        }

        // Step 5 — Brush patches: scattered around the clearing edges
        const brushPatches = [
            [CX - 15, CY - 5,  5],
            [CX + 12, CY + 9,  6],
            [CX - 8,  CY + 14, 5],
            [CX + 6,  CY - 14, 5],
            [Math.floor(N * 0.10), Math.floor(N * 0.70), 5],
            [Math.floor(N * 0.20), Math.floor(N * 0.85), 6],
            [Math.floor(N * 0.70), Math.floor(N * 0.75), 5],
        ];
        for (const [bx, by, r] of brushPatches) {
            for (let y = 0; y < N; y++) {
                for (let x = 0; x < N; x++) {
                    if (this.cells[y][x].fuel_type !== FireGrid.FUEL_FOREST) continue;
                    const dist = Math.hypot(x - bx, y - by);
                    if (dist < r) this.cells[y][x].fuel_type = FireGrid.FUEL_BRUSH;
                }
            }
        }

        // Save clean terrain for reset()
        this._initialCells = this._cloneCells(this.cells);

        // Expose clearing centre so callers can place the firefighter dot
        this.clearingX = CX;
        this.clearingY = CY;
    }

    /**
     * Ignite a cell at (x, y). No-op if cell is water/rock or already burning.
     */
    setFire(x, y) {
        if (x < 0 || x >= this.size || y < 0 || y >= this.size) return;
        const cell = this.cells[y][x];
        if (cell.state !== 0) return;
        const br = FireGrid.BASE_RATES[cell.fuel_type];
        if (br === 0) return; // water or rock
        cell.state      = 1;
        cell.burn_ticks = 0;
    }

    /**
     * Advance the simulation one tick (= 1 simulated minute).
     * @param {number} windDir    Compass bearing (0=N, 90=E) — direction fire is pushed
     * @param {number} windSpeed  mph; 15 = baseline multiplier
     */
    tick(windDir, windSpeed) {
        this._lastWd = windDir;
        this._lastWs = windSpeed;
        this.cells = FireGrid._tickCells(this.cells, this.size, windDir, windSpeed);
    }

    /**
     * Return the live cells array (read-only — don't mutate).
     */
    getState() {
        return this.cells;
    }

    /**
     * Run the simulation forward N minutes from the current state on a clone,
     * without modifying this.cells.
     * @param {number} minutes
     * @param {number} windDir
     * @param {number} windSpeed
     * @returns {Array} predicted cells grid (same shape as getState())
     */
    predictFuture(minutes, windDir, windSpeed) {
        let cells = this._cloneCells(this.cells);
        for (let t = 0; t < minutes; t++) {
            cells = FireGrid._tickCells(cells, this.size, windDir, windSpeed);
        }
        return cells;
    }

    /**
     * Dijkstra shortest-path from (userX, userY) to any edge cell on the
     * predicted fire grid (futureMinutes ahead of current state).
     *
     * Cost per step:
     *   burning / burned / water  → impassable (Infinity)
     *   within 3 cells of fire    → high cost (10× base)
     *   clear cell                → 1.0  (diagonal steps cost √2)
     *
     * Returns an array of {x,y} waypoints (start→edge), or null if blocked.
     */
    calculateEvacRoute(userX, userY, futureMinutes = 10) {
        const N    = this.size;
        const wd   = this._lastWd !== undefined ? this._lastWd : 0;
        const ws   = this._lastWs !== undefined ? this._lastWs : 15;
        const pred = this.predictFuture(futureMinutes, wd, ws);

        // Flat risk bitmap: 1 = within 3 cells of any burning/burned cell
        const risk = new Uint8Array(N * N);
        for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
            if (pred[y][x].state >= 1) {
                for (let dy = -3; dy <= 3; dy++) for (let dx = -3; dx <= 3; dx++) {
                    const ny = y + dy, nx = x + dx;
                    if (ny >= 0 && ny < N && nx >= 0 && nx < N)
                        risk[ny * N + nx] = 1;
                }
            }
        }

        const stepCost = (x, y) => {
            const c = pred[y][x];
            if (c.state >= 1 || c.fuel_type === FireGrid.FUEL_WATER) return Infinity;
            return risk[y * N + x] ? 10 : 1;
        };

        const INF  = 1e9;
        const dist = new Float32Array(N * N).fill(INF);
        const prev = new Int32Array(N * N).fill(-1);   // flat index of predecessor
        dist[userY * N + userX] = 0;

        const heap = new _MinHeap();
        heap.push([0, userY * N + userX]);

        const DIRS = [[-1,0,1],[1,0,1],[0,-1,1],[0,1,1],
                      [-1,-1,1.414],[1,-1,1.414],[-1,1,1.414],[1,1,1.414]];

        while (heap.size > 0) {
            const [d, idx] = heap.pop();
            if (d > dist[idx]) continue;          // stale entry

            const x = idx % N, y = (idx / N) | 0;

            // Reached the map edge — reconstruct path
            if (x === 0 || x === N-1 || y === 0 || y === N-1) {
                const path = [];
                let cur = idx;
                while (cur !== -1) {
                    path.unshift({ x: cur % N, y: (cur / N) | 0 });
                    cur = prev[cur];
                }
                return path;
            }

            for (const [dx, dy, dc] of DIRS) {
                const nx = x + dx, ny = y + dy;
                if (nx < 0 || nx >= N || ny < 0 || ny >= N) continue;
                const c = stepCost(nx, ny);
                if (c === Infinity) continue;
                const nd = dist[idx] + c * dc;
                const ni = ny * N + nx;
                if (nd < dist[ni]) {
                    dist[ni] = nd;
                    prev[ni] = idx;
                    heap.push([nd, ni]);
                }
            }
        }

        return null;   // all routes blocked
    }

    /**
     * Restore terrain to the state immediately after generateTerrain().
     * Removes all fire; terrain, elevation, and fuel types are preserved.
     */
    reset() {
        if (this._initialCells) {
            this.cells = this._cloneCells(this._initialCells);
        }
    }

    // ── Internal helpers ──────────────────────────────────────────────

    _cloneCells(cells) {
        // Cells contain only primitive fields → spread is a full deep copy
        return cells.map(row => row.map(cell => ({ ...cell })));
    }

    /**
     * Pure function: given a cells grid, produce the next-tick grid.
     * Both input and output are 2-D arrays of cell objects.
     */
    static _tickCells(cells, size, windDir, windSpeed) {
        // Wind vector in screen coords (y-axis increases downward).
        // Compass 0°=N maps to screen (0,-1), 90°=E maps to (1,0).
        const windRad = (windDir * Math.PI) / 180;
        const wxv = Math.sin(windRad);
        const wyv = -Math.cos(windRad);

        // speedScale=1.0 at 15 mph (spec "baseline"), clamped to avoid > 3×
        const speedScale = Math.min(Math.max(windSpeed / 15.0, 0), 2.5);

        // Copy current state; we read from `cells`, write to `next`
        const next = cells.map(row => row.map(cell => ({ ...cell })));

        for (let y = 0; y < size; y++) {
            for (let x = 0; x < size; x++) {
                const cell = cells[y][x];
                if (cell.state !== 1) continue; // only burning cells spread

                // Burnout check
                next[y][x].burn_ticks = cell.burn_ticks + 1;
                if (next[y][x].burn_ticks >= FireGrid.BURN_DURATION[cell.fuel_type]) {
                    next[y][x].state = 2; // fully burned
                }

                // Attempt to ignite each of 8 neighbours
                for (let dy = -1; dy <= 1; dy++) {
                    for (let dx = -1; dx <= 1; dx++) {
                        if (dx === 0 && dy === 0) continue;
                        const nx = x + dx, ny = y + dy;
                        if (nx < 0 || nx >= size || ny < 0 || ny >= size) continue;

                        const nb = cells[ny][nx];
                        if (nb.state !== 0) continue; // already burning or burned

                        const baseRate = FireGrid.BASE_RATES[nb.fuel_type];
                        if (baseRate === 0) continue; // water / rock — fireproof

                        // ── Wind factor ───────────────────────────────
                        // Dot product of wind unit vector with spread direction unit vector.
                        // Diagonal spreads (dx≠0, dy≠0) have magnitude √2.
                        const mag = Math.sqrt(dx * dx + dy * dy);
                        const dot = (wxv * dx + wyv * dy) / mag;

                        // Three-tier as per spec
                        let rawFactor;
                        if      (dot >  0.5) rawFactor = 2.0; // with wind
                        else if (dot > -0.5) rawFactor = 1.0; // perpendicular
                        else                 rawFactor = 0.3; // against wind

                        // Scale effect by wind speed (zero wind → all factors become 1.0)
                        const windFactor = 1.0 + (rawFactor - 1.0) * speedScale;

                        // ── Slope factor ──────────────────────────────
                        // Uphill (positive elevDiff) accelerates; downhill decelerates.
                        const elevDiff = nb.elevation - cell.elevation;
                        const slopeFactor = Math.max(0.5, Math.min(3.0,
                            1.0 + 0.5 * elevDiff / 10.0
                        ));

                        // ── Ignition roll ─────────────────────────────
                        const prob = baseRate * windFactor * slopeFactor;
                        if (Math.random() < prob) {
                            next[ny][nx].state      = 1;
                            next[ny][nx].burn_ticks = 0;
                        }
                    }
                }
            }
        }

        return next;
    }
}

// CommonJS export for Node (test harness); ignored in browser
if (typeof module !== 'undefined') module.exports = { FireGrid };
