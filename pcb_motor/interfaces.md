# pcb_motor module interfaces (frozen contracts)

All quantities are **SI** (m, A, T, kg, rad). Only `field.py` converts to the
Biot-Savart kernel's internal cm/gauss units. Contracts live in `design.py`.

## Data types (`design.py`)
- `CurrentSource(vertices(N,3) m, currents(N,) A)` — universal field source.
  `currents[i]` flows in segment `i -> i+1`. `CurrentSource.concat([...])` joins
  loops with a zero-current break between them.
- `CoilGeometry` — per-segment arrays `midpoints_m(S,3)`, `dvec_m(S,3)`,
  `phase(S,)∈{0,1,2}`, `direction(S,)∈{+1,-1}`, `is_radial(S,)bool`; summaries
  `length_per_phase_m`, `conductor_area_m2`, `n_turns`, `n_layers`; `polylines`
  (list of `(K,3)` arrays, plotting only); `.end_turn_fraction` property.
- `RotorConfig` — fixed magnet rotor/stack. `.stator_z_m()` = axial gap from
  magnet mid-plane (z=0) to near stator copper plane.
- `MotorDesign` — full design point (swept knobs + fixed context). `.rotor()`
  returns the `RotorConfig`.

## Module APIs

### `field.py`
```python
def b_field_at_points(source: CurrentSource, points_m: np.ndarray,
                      resolution_m: float = 0.5e-3) -> np.ndarray:
    """B in TESLA at each point. points_m: (P,3) m. returns (P,3) T.
    Vectorised Biot-Savart: subdivide segments to resolution_m, sum
    I dl x r / |r|^3 over all segments for each point."""
```

### `magnets.py`
```python
def magnet_segments(rotor: RotorConfig, theta_rad: float = 0.0,
                    n_arc: int = 24, n_stack: int = 1) -> CurrentSource:
    """Rotor magnets as Amperian current loops. 2*pole_pairs loops at the
    magnet mid-plane (z=0), alternating current sign per pole. Loop current
    I_eq = (Br/mu0) * magnet_thickness_m (split across n_stack sub-loops).
    theta_rad rotates the whole ring. Br from pcb_motor.constants.NDFEB_BR."""
```

### `coils.py`
```python
def build_coil(design: MotorDesign) -> CoilGeometry:
    """Generate the stator winding for design.winding_topology
    ('concentrated' default; 'radial_spoke', 'spiral' alternatives). Places n
    turns that fit in (r_outer-r_inner)/(trace_width+trace_space), across
    copper_layers at z given by rotor().stator_z_m() (+ board pitch per layer),
    assigned to n_phases phases. Computes length_per_phase_m,
    conductor_area_m2 = trace_width * copper_thickness(copper_weight_oz)."""

def phase_resistance(design: MotorDesign, geo: CoilGeometry,
                     temp_c: float | None = None) -> float:
    """R_phase [ohm] = rho_cu(temp) * length_per_phase_m / conductor_area_m2,
    divided by parallel_paths**2. temp_c=None -> 20C."""
```
Uses `pcb_motor.constants.COPPER_THICKNESS`, `RHO_CU_20`, `ALPHA_CU`.

### `thermal.py`
```python
def continuous_current(design: MotorDesign, r_phase_20c: float) -> dict:
    """Lumped steady-state convection balance: returns
    {i_cont_a, r_phase_hot, p_dissipation_w, a_surface_m2, ...}. Cooled annulus
    uses cooled_faces * pi*(r_outer^2 - r_inner^2)."""
```

### `inertia.py`
```python
def rotor_inertia(rotor: RotorConfig) -> float:
    """J_rotor [kg m^2] about the axis: magnet ring + PLA carrier (back iron is
    stator-fixed, so it contributes zero rotor inertia)."""

def total_inertia(rotor: RotorConfig, load_inertia_kgm2: float) -> float:
    """rotor_inertia(rotor) + load_inertia_kgm2."""
```

### `torque.py` — depends on field/magnets/coils
```python
def kt_and_torque(design: MotorDesign, geo: CoilGeometry | None = None) -> dict:
    """B_magnet at coil midpoints via field.b_field_at_points; per segment
    dF = I*(dvec x B); tau_z = sum (r x dF)_z. Sweep commutation_steps rotor
    angles, pick the max-|tau| commutation, return {kt_nm_per_a, b_gap_mean_t,
    b_gap_peak_t, ...} where Kt = tau / I_unit. Sign: report magnitude."""
```

### `viz.py` — depends on CoilGeometry + field
```python
def plot_coil_layout(geo: CoilGeometry) -> matplotlib.figure.Figure
def plot_motor_config(rotor: RotorConfig) -> matplotlib.figure.Figure
def plot_b_field(design: MotorDesign) -> matplotlib.figure.Figure  # B_z contour
```

## Integration (`sweep.py`, `cli.py`)
`MotorDesign(bn.ParametrizedSweep)` mirrors design fields; `__call__` builds
geometry -> Kt -> I_cont -> tau_cont -> J -> a_cont = tau_cont/J_total, assigns
the result vars + three viz images.
