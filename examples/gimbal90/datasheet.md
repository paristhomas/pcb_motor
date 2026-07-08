# Motor design datasheet — concentrated 12N14P N42

| Parameter | Value |
| --- | --- |
| Winding | concentrated, 12N14P, 3-phase, 1 parallel path(s) |
| Magnets | N42, round, 7 pole-pairs, 3.0 mm, D15 discs @ r=37.2 mm, D10 discs @ r=24.4 mm |
| Stators | 2 x 2-layer, 0.80 mm FR4, 1 oz Cu |
| Active annulus | 19.0 - 44.5 mm |
| Trace / space | 0.900-2.282 (tapered) / 0.130 mm |
| Air gap (per side) | 1.00 mm |
| Kt (torque constant) | 58.22 mNm/A |
| Phase resistance @20C | 1.198 ohm |
| Mean / peak airgap |Bz| | 0.3321 / 0.5917 T |
| Turns / phase / layer-set | 32 |
| Winding factor kw1 | 0.936 |
| Continuous current | 2.203 A |
| Continuous torque | 128.3 mNm |
| Drive voltage (cont) | 3.463 V |
| Phase inductance (air-core) | 19.4 uH |
| PWM ripple (bare winding) | 2.761 A pp worst-case @ 12 V bus, 56 kHz PWM (budget 150% of I_cont) |
| PWM ripple gate | PASS (2.761 A pp <= 3.305 A budget) |
| Ext. choke for ripple budget | 0 uH |
| Current density | 69.95 A/mm^2 |
| Airgap shear | 0.7943 kPa |
| Rotor / total inertia | 0.000105 / 0.000419 kg*m^2 |
| Continuous acceleration | 306 rad/s^2 |
| Copper mass | 5.549 g |

---
*pcb-motor analytical Biot-Savart model, roughly +/-30% on absolute torque. Calibrate against FEMM or a bench coil before committing to a build.*
