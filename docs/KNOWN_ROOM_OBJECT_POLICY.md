# Known-room Fluid object policy

The **Fluid — known room** preset (`room_fluid`, revision 2) is an explicit opt-in
policy for the operator's known room. It adds complete-region tracking to the
Fluid revision 2 component confirmation. Saved Fluid/Cautious/Balanced revisions
1 and 2 keep their former parameters and behavior; loading a page never migrates
or selects this preset.

## From model evidence to a route decision

1. Resolve the selected model's confidence and floor/rug surface policy.
2. Form bounded complete-region proposals in the available wide image context.
   DA2 uses its existing fitted plane; no additional model is loaded.
3. Associate regions using image motion, mask overlap, appearance and bounded
   area change. The proposal can contain low-confidence pixels for association.
4. Count reliable model obstacle support within the editable observation window.
   The defaults require 2 supported observations among the last 3, with at least
   80 reliable pixels in the complete region. Unsupported pixels remain
   uncertain; the original floor mask is never expanded.
5. Compare the supported region's **current reliable pixels** with the actual
   route mask. A large supported object can remain meaningful when only a small
   part enters the route. A box alone never establishes the intersection.
   A clipped proposal cannot rescue a tiny route component rejected by the
   ordinary area/span tests. A large clipped obstacle still uses those existing
   component/near guards.
6. Apply the existing positive-floor, near, timing and independent geometry
   guards. A static near object still stops the robot even without growth.

The operator's model thresholds remain in **Selected model tuning**. Object
support, association, growth and reaction parameters are available in
**Obstacle assessment → Details and custom settings**. Changes become custom
settings based on this preset and require an explicit revisioned save.

## Approaching and temporarily missing objects

Area and lower-edge history are compensated by the approximate shared image
motion. An approaching signal needs the configured history length, sufficient
growth and downward displacement, and at least two consecutive qualifying trend
windows. A clipped proposal cannot establish an approaching trend.

A confirmed approaching object may request immediate STOP after its reliable
pixels intersect the route, before reaching the near zone. Lack of growth does
not cancel a persistent object.

Only an object whose reliable route intersection actually qualified as a near
obstacle can arm retention. A confirmed identity outside the route, or a clipped
border with a rejected tiny intersection, cannot arm it.

A previously confirmed near object can hold STOP through a short missing or
low-confidence mask. The retained evidence reports `observed_now: false`,
`evidence: held` and its `age_ms`. Defaults retain it for at most 2 observations
and no longer than the configured maximum capture gap/history. Memory never
authorizes GO, renews a capture timestamp, or extends a source lease. Positive
current floor evidence and the normal release confirmation are needed after the
hold expires.

## Evidence and failure behavior

`temporal.objects` reports `diagnostic_only: false`, `policy: known_room`,
support requirements and per-object confirmation, reliable area, route
intersection, near status, compensated growth/displacement and age.
`raw_route_pixels` describes reliable pixels before decision qualification;
`whole_object_qualified` distinguishes identity support from a usable complete
boundary, and `route_pixels` reports the qualifying intersection. Evidence is
labelled `transient`, `confirmed`, `approaching` or `held`. Private masks and
motion transforms never enter JSON, including error responses.

A changed session/model/policy, duplicate or out-of-order frame, non-monotonic
capture time, capture gap, invalid wide evidence or lost image tracking clears
object continuity. Unavailable object evidence fails closed. Existing critical
support, 3D collision, camera quality and transport freshness checks are
preserved. The old `object_tracking_enabled` setting remains diagnostic only
when `object_policy_enabled` is false.

## Acceptance scope

Complete regions are model proposals, not guaranteed instance masks. Merges
with background and depth-plane mistakes remain possible. Image coordinates,
growth and confidence do not establish physical distance or stopping clearance.

Automated acceptance uses isolated masks plus cached, hash-verified recordings.
It covers static obstacles, small route intersections of large objects,
jumping noise, low-confidence proposals, floor/rug selection, shared camera
motion, clipping, area jumps, bounded holds, stale captures and failure paths.
Recorded replay counts are observations, not labelled collision accuracy or
proof of smoother physical driving.

Before publishing this increment, the operator must inspect additional stops on
the stored film and test fresh recorded sessions with both the previous preset
and the known-room preset. Physical movement, if tested, remains an explicit
operator action.
