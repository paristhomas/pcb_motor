# Motor design datasheet — concentrated 36N42P N52

| Parameter | Value |
| --- | --- |
| Winding | concentrated, 36N42P, 3-phase, 1 parallel path(s) |
| Magnets | N52, round, 21 pole-pairs, 3.0 mm, D5 discs @ r=35 mm, D4 discs @ r=29 mm |
| Stators | 2 x 2-layer, 0.80 mm FR4, 1 oz Cu |
| Active annulus | 25.0 - 38.0 mm |
| Trace / space | 0.500-0.826 (tapered) / 0.127 mm |
| Air gap (per side) | 1.00 mm |
| Kt (torque constant) | 20.75 mNm/A |
| Phase resistance @20C | 3.009 ohm |
| Mean / peak airgap |Bz| | 0.09261 / 0.3097 T |
| Turns / phase / layer-set | 72 |
| Winding factor kw1 | 0.943 |
| Continuous current | 0.9887 A |
| Continuous torque | 20.52 mNm |
| Drive voltage (cont) | 3.904 V |
| Phase inductance (air-core) | 6.687 uH |
| PWM ripple (bare winding) | 9.347 A pp worst-case @ 12 V bus, 48 kHz PWM (budget 30% of I_cont) |
| PWM ripple gate | FAIL (9.347 A pp > 0.2966 A budget, 32x -- needs ~204 uH/phase external L) |
| Ext. choke for ripple budget | 204 uH |
| Current density | 56.5 A/mm^2 |
| Airgap shear | 0.2532 kPa |
| Rotor / total inertia | 3.86e-05 / 3.86e-05 kg*m^2 |
| Continuous acceleration | 531.9 rad/s^2 |
| Copper mass | 2.519 g |

## Warnings

- PWM ripple 9.35 A pp exceeds the 0.30 A budget (32x) at 12 V bus / 48 kHz / 30% of I_cont: not drivable without ~204 uH/phase external inductance -- see design guide Stage 5.

---
*pcb-motor analytical Biot-Savart model, roughly +/-30% on absolute torque. Calibrate against FEMM or a bench coil before committing to a build.*
