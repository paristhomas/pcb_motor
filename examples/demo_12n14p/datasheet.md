# Motor design datasheet — concentrated 12N14P N42

| Parameter | Value |
| --- | --- |
| Winding | concentrated, 12N14P, 3-phase, 1 parallel path(s) |
| Magnets | N42, arc, 7 pole-pairs, 3.0 mm, 85% coverage |
| Stators | 2 x 2-layer, 0.80 mm FR4, 1 oz Cu |
| Active annulus | 10.0 - 30.0 mm |
| Trace / space | 0.150-0.750 (tapered) / 0.150 mm |
| Air gap (per side) | 1.00 mm |
| Kt (torque constant) | 26.98 mNm/A |
| Phase resistance @20C | 6.999 ohm |
| Mean / peak airgap |Bz| | 0.1797 / 0.3252 T |
| Turns / phase / layer-set | 64 |
| Winding factor kw1 | 0.939 |
| Continuous current | 0.6407 A |
| Continuous torque | 17.29 mNm |
| Drive voltage (cont) | 5.884 V |
| Phase inductance (air-core) | 23.58 uH |
| PWM ripple (bare winding) | 5.3 A pp worst-case @ 12 V bus, 24 kHz PWM (budget 30% of I_cont) |
| PWM ripple gate | FAIL (5.3 A pp > 0.1922 A budget, 28x -- needs ~626.7 uH/phase external L) |
| Ext. choke for ripple budget | 626.7 uH |
| Current density | 122 A/mm^2 |
| Airgap shear | 0.344 kPa |
| Rotor / total inertia | 2.64e-05 / 2.64e-05 kg*m^2 |
| Continuous acceleration | 654.9 rad/s^2 |
| Copper mass | 2.307 g |

## Warnings

- PWM ripple 5.30 A pp exceeds the 0.19 A budget (28x) at 12 V bus / 24 kHz / 30% of I_cont: not drivable without ~627 uH/phase external inductance -- see design guide Stage 5.
- current density 122 A/mm^2 at the winding's narrowest section exceeds ~80 A/mm^2: expect a hot neck at r_inner -- widen trace_width_m, add copper weight, or move r_inner_m outward.

---
*pcb-motor analytical Biot-Savart model, roughly +/-30% on absolute torque. Calibrate against FEMM or a bench coil before committing to a build.*
