# demo12n14p — the default machine, shown properly

## hero
This is the motor you get from `pcb-motor new` with exactly one knob turned —
`tapered_traces=true`, so the simulated numbers describe the tapered-wedge
copper the production footprint actually emits. A 60 mm-class 12-slot / 14-pole
twin-stator pancake, optimised for nothing in particular: it exists so you can
see everything the tool knows about a design on one page. The numbers are real
engine output, the artwork is the real production footprint, and the verdict
near the bottom is the same one the CLI shouts in exclamation marks.

## brief
There isn't one — that's the point. `pcb-motor new` seeds this machine with a
requirements skeleton left blank, and the page judges it against its own default
drive settings (12 V bus, 24 kHz PWM). Every knob below is a `--set` away from
being your machine instead.

## hunt
Trace width is the knob that matters most, so the sweep below re-runs the full
engine at each base width with everything else held fixed: narrow traces buy
turns (Kt and inductance), wide traces buy current (cooler copper), and
*nothing* buys enough inductance to matter against the ripple gate. The
committed board doesn't pick one width — its copper starts at 0.15 mm at the
inner radius and tapers to 0.75 mm at the rim, matching the production
tapered-wedge artwork in the viewer above.

## verdict
As a motor, it's an honest middleweight: ~17 mNm continuous (±30%) from two
ordinary 2-layer boards. As a demo, it's complete: this page is what every
`pcb-motor showcase` gives you for free — spin the rotor, zoom the copper, pull
the sandwich apart, and read the part where the tool tells you you still need
three chokes.
