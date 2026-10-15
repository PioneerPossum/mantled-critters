"""Second OSC verification pass: effect-name addressing and clip speed path. Run with --go after the Legion watcher is ready."""
import sys, verify_osc
verify_osc.STEPS = [
 ("U01", "/composition/video/effects/twitcheffect/globaltwitch", "f", 0.25, "globaltwitch -> 0.25 (link7 is 0)", "effect-name addressing"),
 ("U02", "/composition/video/effects/huerotate/huerotate", "f", 0.1, "huerotate -> 0.1 (visible hue shift ~4 s)", "effect-name addressing 2"),
 ("U03", "/composition/video/effects/trails/feedback", "f", 0.5, "trails feedback 0.739 -> 0.5 (visible ~4 s)", "effect-name addressing 3; same param as link8"),
 ("U04", "/composition/layers/10/clips/5/transport/position/behaviour/speed", "f", 0.3, "L10C5 speed -> 3.0 if path exists (range 0..10)", "clip speed alt path"),
 ("U05", "/composition/layers/10/clips/5/transport/controls/speed", "f", 0.3, "no change expected (known no-op)", "control: confirms U04 is not a stale effect"),
]
if __name__ == "__main__":
    if "--go" not in sys.argv: sys.exit("pass --go")
    verify_osc.main()
