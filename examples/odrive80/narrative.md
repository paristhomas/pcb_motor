# odrive80 — an 80 mm no-choke attempt, honestly lost

## hero
An 80 mm pancake designed to a hard brief: maximize continuous torque on an
ODrive **without external inductors**, built only from off-the-shelf round disc
magnets and ordinary 2-layer 1 oz JLC boards. The tool did its best — 42 poles,
tapered copper, every millimetre of annulus tracked under the magnets — and then
said no to the part of the brief that physics doesn't sign off on.

## brief
Fixed by the customer: 80 mm outer diameter hard limit, ODrive drive, no chokes
if possible, maximize continuous torque. 21 pole pairs (the verified 36N42P
combo), two stators in series — one PCB each side of a single rotor disk — 2
copper layers per board, 0.8 mm FR4, 1 oz copper (JLC 5/5 mil), 1.0 mm air gap
per side, no iron anywhere. Magnet stock: real round discs only, Ø3/Ø4/Ø5/Ø6 mm,
N52.

## architecture
42 poles have to fit around a ring inside a 40 mm radius. The packing that won:
**42× Ø5×3 mm N52 discs at r = 35 mm** plus **42× Ø4×3 mm at r = 29 mm**, one
disc pair per pole, polarity alternating pole to pole. At r = 35 mm the pole
pitch is 5.24 mm, so adjacent Ø5 discs sit ~0.24 mm apart — the model is
perfectly happy with that; your 3D printer is not. Print the carrier with
open-walled pockets (adjacent discs repel, and that repulsion holds each disc
against its pocket rim). The copper annulus tracks the disc rings at
r = 25–38 mm — copper inside r = 25 mm was doing nothing but adding resistance,
so it's gone.

## hunt
This is where the no-choke hope went to die, with numbers. Inductance grows
roughly with turns squared while torque-per-ohm falls, so trace width was swept
both ways. Even maxing out turns at the fab's minimum 0.127 mm trace — a 7×
inductance gain paid for with a quarter of the continuous torque — leaves the
winding an order of magnitude short of choke-free. The committed design takes
the torque: tapered 0.50 mm traces at the 0.127 mm spacing limit.

## drive
The verdict, at the most favourable ODrive operating point (12 V bus, 48 kHz
PWM — an ODrive Pro/S1; a stock v3.6 at 24 kHz doubles the ripple): **the
no-choke brief is infeasible, by 32×.** Not marginal, not fixable with a knob:
9.35 A pp of PWM ripple on a motor rated for 0.99 A continuous. The honest
answer is three chokes in series with the phase leads — Bourns SRR1260-class
shielded drum cores.

## verdict
What the brief asked for wasn't buildable; what got built is still a good
motor: **Kt 20.75 mNm/A, 20.5 mNm continuous (±30%) at 0.99 A and 3.0 Ω**, from
two ordinary PCBs, 84 hobby-store magnets and a printed carrier. Order two
boards, wire the stators in **series** (never parallel — Stage 5 of the design
guide says why), and budget ~204 µH per phase before asking the ODrive to spin
it. Every number on this page is the analytical model talking: measure the
as-built air gap and bench-check Kt before spending real money.
