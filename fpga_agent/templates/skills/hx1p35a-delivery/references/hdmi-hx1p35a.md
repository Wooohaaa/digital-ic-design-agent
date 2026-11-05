# HX1P35A HDMI board acceptance and recovery

These are observations from the 2026-11-05 PH1P35MDG324/Hagibis trial. They are
board-specific guidance, not proof that a future image works.

## Establish separate gates

1. Confirm device/package, oscillator, I/O voltage and the physical connector
   against the supplied board schematic. HX1P35A HDMI-A uses TMDS33 P-side pads
   G16/F17/H14/C17. TD infers the paired N pads H16/E18/H13/D18. Do not expose
   or constrain those N pads as independent output ports. B17 is HDMIA_HPD;
   the reference leaves it undriven. Never drive a sink's HPD signal high.
2. Inspect the installed primitive guide and a matching generated example.
   This package needs PH1P_PHY_PLL_V2. The qualified clock profile uses external
   feedback through BUFG, 50/25/125 MHz C0/C4/C5, and four PH1P_LOGIC_ODDR lanes.
   Unused DRP/phase clocks are tied low. Preserve timing relationships, close
   setup and hold, and examine clock-routing warnings; a positive WNS does
   not prove every IO or clock path has been constrained.
3. Run the independent RTL checks, including the *synthesizable* serializer
   branch. UG1310 section 28 defines d0 as rising-edge output data and d1 as
   falling-edge output data. A functional model must capture both halves of
   the single-edge fabric pair before upstream register updates; model phase
   and data-route skew. Do not swap documented edges to make a checker pass.
   Such a model is not vendor timing simulation or physical evidence.
4. Bind the exact generated SVF configuration scan to the verified .bit binary
   body. Fresh per-attempt output paths prevent stale-file reuse. Anlogic
   header metadata can change while binary payload stays identical, so whole
   file hashes alone cannot diagnose stale conversion. Record both hashes.
5. A successful vendor SVF execution may check only the JTAG IDCODE. It does
   not establish configuration readback, PLL lock, valid video or rotation.
   Report those observations separately. Use the OS-held board lease across
   programming and capture so parallel workers cannot reconfigure a board
   while another worker measures it. Only the host owns hardware operations.

## Observe the receiver over time

Use terminal FFmpeg/DirectShow, not GUI automation. Resolve the current device
name and handle occupied/missing capture hardware as a failure requiring a
specific recovery. Reopening a device can initially emit stale frames or
invalid MJPEG packets. This trial needed about three seconds to reacquire a
signal; the acceptance profile discards five seconds and observes seven more.

`icforge video-hardware PROJECT --program --device Hagibis` stores the raw
capture, post-settling PNG samples and a report bound to the programming
operation, bitstream, checker versions and artifact hashes. It compares cyan
wireframe pixels against independent geometry across changing poses. No Signal,
black, bars, unrelated shapes and a static cube must all fail. Never declare
success based on the first frame, one transient good frame, LED lock, or an
unchanged model's claim. Failed attempts invalidate the current success report.

## Isolate failures

* Verify a known diagnostic pattern under the same cable/receiver settings.
  Preserve its exact bitstream and capture; then test the application.
* If a full application fails but a simple pattern works, retain the complete
  application logic in a separate diagnostic build and vary only one useful
  factor. LED1=POR done, LED2=PLL lock, LED3=pixel heartbeat is meaningful only
  in a bitstream that actually drives those mapped pins.
* In this trial, a cube over bars and a cube over RGB=(1,1,1) worked, while a
  prolonged RGB=(0,0,0) background did not. A serializer half-cycle staging
  experiment also failed to rescue RGB=0 and was rejected. The final default
  uses visually black RGB=1, explicitly documented in the specification and
  checked exactly in RTL acceptance. BACKGROUND_LEVEL=0 remains selectable
  but is unqualified on this receiver. Do not promote this observed workaround
  into a universal receiver or electrical root-cause claim.
* After any repair, repeat the independent functional checks and native TD,
  program the exact resulting bitstream, observe a sustained changing image,
  and test a fresh SRAM reload. Keep simulation images distinct from board
  captures. Deliver the actual qualified bitstream and its manifest, not an
  earlier file that happened to have the same basename.

## Long-running completion

For an explicit board-delivery task, declare:

```json
"hardware_acceptance": {"required": true, "profile": "hdmi-cube"}
```

The review, packaging and persistent-task completion gates must require current
physical evidence. If a source, checker, programmed image or evidence artifact
changes, rerun the affected gate. A temporary workaround must be a visible
requirement change with a remaining limitation, not an assertion deleted from
the trusted checker. Recover from the recorded failed hypothesis rather than
repeating it unchanged after context compaction.

Reports bound by a file hash must enter the delivery archive as exact bytes.
JSON pretty-printing, portable-path rewriting or Windows CRLF normalization can
change that hash without changing parsed values. Preserve bound programming and
video reports verbatim, verify archived artifact hashes, and exercise the final
persistent-task completion gate against the actual ZIP before claiming delivery.
