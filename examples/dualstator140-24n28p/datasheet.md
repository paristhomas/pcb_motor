# Motor design datasheet — dualstator140-24n28p

| Parameter | Value |
| --- | --- |
| Winding | concentrated, 24N28P, 3-phase, 1 parallel path(s) |
| Magnets | N52, round3, 14 pole-pairs, 3.0 mm, D12 discs @ r=58 mm, D6 discs @ r=48 mm, D8 discs @ r=40 mm |
| Stators | 2 x 2-layer, 0.80 mm FR4, 1 oz Cu |
| Active annulus | 30.0 - 67.0 mm |
| Trace / space | 0.600-1.497 (tapered) / 0.127 mm |
| Air gap (per side) | 1.00 mm |
| Kt (torque constant) | 118.4 mNm/A |
| Phase resistance @20C | 6.042 ohm |
| Mean / peak airgap |Bz| | 0.1177 / 0.3307 T |
| Turns / phase / layer-set | 80 |
| Winding factor kw1 | 0.937 |
| Continuous current | 1.461 A |
| Continuous torque | 172.9 mNm |
| Drive voltage (cont) | 11.58 V |
| Phase inductance (air-core) | 34.78 uH |
| PWM ripple (bare winding) | 7.189 A pp worst-case @ 24 V bus, 24 kHz PWM (budget 30% of I_cont) |
| PWM ripple gate | FAIL (7.189 A pp > 0.4382 A budget, 16x -- needs ~535.7 uH/phase external L) |
| Ext. choke for ripple budget | 535.7 uH |
| Current density | 69.56 A/mm^2 |
| Airgap shear | 0.3161 kPa |
| Rotor / total inertia | 0.000382 / 0.000382 kg*m^2 |
| Continuous acceleration | 452.5 rad/s^2 |
| Copper mass | 12.16 g |

## Warnings

- PWM ripple 7.19 A pp exceeds the 0.44 A budget (16x) at 24 V bus / 24 kHz / 30% of I_cont: not drivable without ~536 uH/phase external inductance -- see design guide Stage 5.
- disc magnets are only 0.98 mm apart at the tightest point (min pole-to-pole 0.98 mm, min ring-to-ring 1.00 mm); keep >= 1 mm of carrier between magnets -- shrink disc diameter, spread the ring radii, or drop the pole count.

---
*pcb-motor analytical Biot-Savart model, roughly +/-30% on absolute torque. Calibrate against FEMM or a bench coil before committing to a build.*
