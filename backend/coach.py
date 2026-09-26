"""Turns comparison numbers into human coaching + strategy notes."""
from __future__ import annotations

from typing import Optional


def _fmt_ms(ms: Optional[int]) -> str:
    if ms is None:
        return "—"
    sign = "-" if ms < 0 else "+" if ms else ""
    ms = abs(int(ms))
    if ms >= 60000:
        return f"{sign}{ms // 60000}:{(ms % 60000) // 1000:02d}.{ms % 1000:03d}"
    return f"{sign}{ms // 1000}.{ms % 1000:03d}"


def _fmt_lap(ms: Optional[int]) -> str:
    if not ms:
        return "—"
    return f"{ms // 60000}:{(ms % 60000) // 1000:02d}.{ms % 1000:03d}"


# --------------------------------------------------------------- conditions ----
def condition_warnings(game_lap: Optional[dict], ref: dict) -> list:
    """Flag anything that makes the two laps not like-for-like. Weather is the
    big one: a wet reference lap is several seconds off dry pace, so every
    delta on the page becomes meaningless without saying so."""
    warnings = []
    ref_w = ref.get("weather") or {}
    ref_wet = bool(ref_w.get("wet"))
    ref_compound = ref_w.get("compound") or ref.get("compound")

    you_weather = (game_lap or {}).get("weather")
    you_wet = you_weather in {"Light rain", "Heavy rain", "Storm"}
    label = ref.get("driver", "the reference")

    if game_lap is None:
        you_wet = None  # real-vs-real: driver A's own conditions

    if ref_wet and you_wet is False:
        why = (f"was on {ref_compound}s" if ref_compound in ("Intermediate", "Wet")
               else "ran in the rain")
        warnings.append({
            "kind": "weather", "severity": 3,
            "text": f"Conditions do not match: your lap was dry ({you_weather}) but "
                    f"{label}'s reference lap {why}. A wet lap is typically 5–15s "
                    f"off dry pace, so the lap-time gap and every corner delta below "
                    f"are not a fair measure of your driving. Pick a dry session."})
    elif you_wet and not ref_wet:
        warnings.append({
            "kind": "weather", "severity": 3,
            "text": f"Conditions do not match: you drove in {you_weather} but "
                    f"{label}'s lap was dry. Expect to look several seconds slow "
                    f"through no fault of your driving — compare against a wet "
                    f"session, or re-run this lap in the dry."})
    elif ref_wet and you_wet:
        warnings.append({
            "kind": "weather", "severity": 1,
            "text": "Both laps are wet. Wet running is far more variable than dry, "
                    "so treat corner-level deltas as indicative only — grip changes "
                    "lap to lap with the racing line and standing water."})

    # A slick lap in a session that saw rain was run on a drying track — not
    # representative dry pace, and the trap that makes a game lap look heroic.
    elif ref_w.get("mixed_session") and you_wet is False:
        warnings.append({
            "kind": "weather", "severity": 2,
            "text": f"{label}'s lap was set on {ref_compound}s with no rain falling, "
                    f"but this session had wet running — the track was still drying "
                    f"and evolving. It is well off true dry pace, so expect to look "
                    f"faster than you really are. A fully dry session is a better "
                    f"benchmark."})

    # track temperature changes grip meaningfully even when both are dry
    you_track_temp = (game_lap or {}).get("track_temp")
    ref_track_temp = ref_w.get("track_temp")
    if (you_track_temp is not None and ref_track_temp is not None
            and not ref_wet and you_wet is False):
        diff = abs(you_track_temp - ref_track_temp)
        if diff >= 15:
            warnings.append({
                "kind": "weather", "severity": 1,
                "text": f"Track temperature differs by {round(diff)}°C "
                        f"(yours {round(you_track_temp)}°C vs "
                        f"{round(ref_track_temp)}°C). That shifts grip and tyre "
                        f"behaviour — a few tenths either way is track, not driver."})
    return warnings


# ------------------------------------------------------------------ driving ----
def driving_insights(cmp: dict, ref_label: str) -> list:
    """Ranked, plain-English coaching notes from the comparison result."""
    notes = []
    stats = cmp["stats"]
    corners = cmp["corners"]

    lap_you, lap_ref = stats.get("lap_time_ms_you"), stats.get("lap_time_ms_ref")
    if lap_you and lap_ref:
        diff = lap_you - lap_ref
        if diff > 0:
            notes.append({
                "kind": "summary", "severity": 3,
                "text": f"You are {_fmt_ms(diff)}s off {ref_label} "
                        f"({_fmt_lap(lap_you)} vs {_fmt_lap(lap_ref)})."})
        else:
            notes.append({
                "kind": "summary", "severity": 0,
                "text": f"You are {_fmt_ms(-diff)}s FASTER than {ref_label} "
                        f"({_fmt_lap(lap_you)} vs {_fmt_lap(lap_ref)}) — remember the "
                        f"game car is not the real car, but that's a strong lap."})

    # worst corners by time lost
    losers = sorted([c for c in corners if c["time_delta_s"] > 0.05],
                    key=lambda c: -c["time_delta_s"])
    for rank, c in enumerate(losers[:3]):
        lead = (f"T{c['corner']} ({c['apex_m']}m) is your biggest loss: "
                if rank == 0 else
                f"T{c['corner']} ({c['apex_m']}m) costs you another ")
        # severity tracks how much time is actually in it — a 0.09s corner is
        # not a "fix first" item just because it ranks highest
        loss = c["time_delta_s"]
        severity = 3 if loss >= 0.4 else 2 if loss >= 0.15 else 1
        notes.append({
            "kind": "corner", "severity": severity, "corner": c["corner"],
            "text": lead + f"{loss:.2f}s. " + _corner_advice(c, ref_label)})

    # braking-point pattern
    early, late = [], []
    for c in corners:
        by, br = c["you"].get("brake_start_m"), c["ref"].get("brake_start_m")
        if by is None or br is None:
            continue
        if by - br > 20:
            early.append((c["corner"], by - br))
        elif br - by > 20:
            late.append((c["corner"], br - by))
    if len(early) >= 3:
        worst = sorted(early, key=lambda e: -e[1])[:3]
        lst = ", ".join(f"T{n} (+{m}m)" for n, m in worst)
        notes.append({
            "kind": "braking", "severity": 2,
            "text": f"You brake earlier than {ref_label} in {len(early)} corners — "
                    f"most notably {lst}. Push your braking markers deeper a few "
                    f"metres at a time; the car has more stopping power than "
                    f"you're using."})
    if len(late) >= 2:
        lst = ", ".join(f"T{n}" for n, _ in late[:3])
        notes.append({
            "kind": "braking", "severity": 1,
            "text": f"You out-brake {ref_label} into {lst} — watch that it isn't "
                    f"costing you mid-corner speed or exit."})

    # apex speed pattern
    slow_apex = [(c["corner"], c["ref"]["min_speed"] - c["you"]["min_speed"])
                 for c in corners
                 if c["ref"]["min_speed"] - c["you"]["min_speed"] > 10]
    if len(slow_apex) >= 3:
        worst = sorted(slow_apex, key=lambda e: -e[1])[:3]
        lst = ", ".join(f"T{n} (-{d} km/h)" for n, d in worst)
        notes.append({
            "kind": "apex", "severity": 2,
            "text": f"Minimum corner speed is down in {len(slow_apex)} corners: {lst}. "
                    f"Focus on carrying speed to the apex rather than parking the "
                    f"car and accelerating out."})

    # exit / traction pattern
    slow_exit = [(c["corner"],
                  c["you"]["full_throttle_after_m"] - c["ref"]["full_throttle_after_m"])
                 for c in corners
                 if c["you"].get("full_throttle_after_m") is not None
                 and c["ref"].get("full_throttle_after_m") is not None
                 and c["you"]["full_throttle_after_m"] - c["ref"]["full_throttle_after_m"] > 30]
    if slow_exit:
        worst = sorted(slow_exit, key=lambda e: -e[1])[:3]
        lst = ", ".join(f"T{n} (+{d}m)" for n, d in worst)
        notes.append({
            "kind": "exit", "severity": 2,
            "text": f"You get to full throttle later out of {lst}. Exits feed the "
                    f"straights — sacrifice a little entry to get the car "
                    f"straightened and on power sooner."})

    # DRS mismatch makes straight-line and lap-time gaps misleading. A race
    # lap in clean air never gets DRS; a Time Trial / qualifying lap always does.
    drs_you, drs_ref = stats["drs_pct_you"], stats["drs_pct_ref"]
    if drs_you - drs_ref > 15:
        notes.append({
            "kind": "caveat", "severity": 2,
            "text": f"Careful reading this one: you had DRS open {drs_you}% of the "
                    f"lap and {ref_label} only {drs_ref}%. A race lap in clean air "
                    f"gets no DRS at all, so a chunk of your top-speed and lap-time "
                    f"advantage is the wing, not the driving. Compare the corners, "
                    f"not the straights."})
    elif drs_ref - drs_you > 15:
        notes.append({
            "kind": "caveat", "severity": 2,
            "text": f"{ref_label} had DRS open {drs_ref}% of the lap vs your "
                    f"{drs_you}%. Make sure you're actually triggering DRS in every "
                    f"zone — that alone is worth several tenths."})

    # top speed / straights
    ts_diff = stats["top_speed_ref"] - stats["top_speed_you"]
    if ts_diff > 8:
        notes.append({
            "kind": "straightline", "severity": 1,
            "text": f"Top speed is {ts_diff} km/h down ({stats['top_speed_you']} vs "
                    f"{stats['top_speed_ref']} km/h). Check wing level, use richer "
                    f"engine/ERS modes on the longest straight, and make sure DRS "
                    f"is open the moment it's allowed."})
    elif ts_diff < -8:
        notes.append({
            "kind": "straightline", "severity": 0,
            "text": f"You have {-ts_diff} km/h more top speed than {ref_label} — "
                    f"a lower-downforce setup. Expect to give some of that back "
                    f"in the corners."})

    ft_diff = stats["full_throttle_pct_ref"] - stats["full_throttle_pct_you"]
    if ft_diff > 5:
        notes.append({
            "kind": "throttle", "severity": 1,
            "text": f"{ref_label} is at full throttle {stats['full_throttle_pct_ref']}% "
                    f"of the lap vs your {stats['full_throttle_pct_you']}%. Commit to "
                    f"flat-out sections earlier and hold the pedal down through the "
                    f"kinks that don't need a lift."})

    gear_diffs = [c for c in corners
                  if abs(c["you"]["apex_gear"] - c["ref"]["apex_gear"]) >= 1
                  and c["you"]["apex_gear"] > 0 and c["ref"]["apex_gear"] > 0]
    if len(gear_diffs) >= 3:
        ex = gear_diffs[0]
        notes.append({
            "kind": "gearing", "severity": 1,
            "text": f"Your apex gear differs from {ref_label} in {len(gear_diffs)} "
                    f"corners (e.g. T{ex['corner']}: {ex['you']['apex_gear']} vs "
                    f"{ex['ref']['apex_gear']}). A gear higher smooths traction; a "
                    f"gear lower helps rotation — try the reference's choice."})

    if not losers and lap_you and lap_ref and lap_you <= lap_ref:
        notes.append({
            "kind": "corner", "severity": 0,
            "text": "No corner shows a significant loss — refine consistency and "
                    "look at sector-level deltas for the last hundredths."})

    return notes


def _corner_advice(c: dict, ref_label: str) -> str:
    you, ref = c["you"], c["ref"]
    parts = []
    if you.get("brake_start_m") and ref.get("brake_start_m"):
        d = you["brake_start_m"] - ref["brake_start_m"]
        if d > 15:
            parts.append(f"you brake {d}m earlier ({you['brake_start_m']}m vs "
                         f"{ref['brake_start_m']}m before apex)")
        elif d < -15:
            parts.append(f"you brake {-d}m later — likely compromising the exit")
    sd = ref["min_speed"] - you["min_speed"]
    if sd > 8:
        parts.append(f"apex speed {sd} km/h down ({you['min_speed']} vs "
                     f"{ref['min_speed']} km/h)")
    elif sd < -8:
        parts.append(f"you carry {-sd} km/h more at the apex — check you're "
                     f"making the exit")
    if (you.get("full_throttle_after_m") is not None
            and ref.get("full_throttle_after_m") is not None):
        d = you["full_throttle_after_m"] - ref["full_throttle_after_m"]
        if d > 25:
            parts.append(f"full throttle {d}m later on exit")
    if not parts:
        return f"Line and phase differences — study {ref_label}'s trace here."
    return "Here " + "; ".join(parts) + "."


# ----------------------------------------------------------------- strategy ----
def strategy_notes(session_code: str, ref_meta: dict, ref_stints: list,
                   top5: list, game_lap: Optional[dict]) -> list:
    notes = []
    is_race = session_code in ("R", "S", "Race", "Sprint")

    if ref_meta.get("compound"):
        life = ref_meta.get("tyre_life")
        notes.append({
            "kind": "reference", "text":
            f"Reference lap was set on {ref_meta['compound']}s"
            + (f" ({life} laps old)" if life is not None else "")
            + f" in {ref_meta.get('session', session_code)}."
            + (" Qualifying laps mean low fuel and maximum engine modes — expect "
               "race pace to be 3–5s slower." if session_code.startswith("Q") else "")})

    if game_lap:
        bits = []
        if game_lap.get("tyre"):
            bits.append(f"you ran {game_lap['tyre']}s"
                        + (f" ({game_lap['tyre_age']} laps old)"
                           if game_lap.get("tyre_age") is not None else ""))
        if game_lap.get("fuel_kg") is not None:
            bits.append(f"{game_lap['fuel_kg']} kg fuel on board")
        if game_lap.get("ers_deployed_mj") is not None:
            bits.append(f"{game_lap['ers_deployed_mj']} MJ ERS deployed "
                        f"(4.0 MJ is the per-lap max)")
        if bits:
            notes.append({"kind": "your_car", "text": "Your lap: " + ", ".join(bits) + "."})

        # Time Trial fixes fuel, automates ERS and disables tyre wear, so none
        # of the management advice below applies there.
        time_trial = game_lap.get("session_type") == "Time Trial"
        if time_trial:
            notes.append({"kind": "your_car", "text":
                          "This was a Time Trial lap: fixed low fuel, automatic ERS "
                          "and no tyre wear. It flatters lap time versus any real "
                          "session, so use it to judge your driving line and braking "
                          "— not your race pace or strategy."})
            return notes + _session_advice(session_code, is_race)

        if game_lap.get("ers_deployed_mj") is not None and game_lap["ers_deployed_mj"] < 2.5:
            notes.append({"kind": "your_car", "text":
                          "ERS deployment was low — on a push lap use Overtake mode "
                          "on the two longest full-throttle sections and you'll find "
                          "2–3 tenths for free."})

        fuel = list((game_lap.get("fuel_trend") or {}).values())
        if len(fuel) >= 3:
            per_lap = (fuel[0] - fuel[-1]) / max(1, len(fuel) - 1)
            if per_lap > 0:
                notes.append({"kind": "fuel", "text":
                              f"You're burning ~{per_lap:.2f} kg/lap. With "
                              f"{fuel[-1]:.1f} kg left that's "
                              f"~{fuel[-1] / per_lap:.0f} laps of fuel. Use lean mix "
                              f"in traffic, rich mix for in/out laps and attacks."})
        wear = list((game_lap.get("wear_trend") or {}).values())
        if len(wear) >= 3:
            per_lap = (wear[-1] - wear[0]) / max(1, len(wear) - 1)
            if per_lap > 0.05:
                to_cliff = max(0.0, (60 - wear[-1]) / per_lap)
                notes.append({"kind": "tyres", "text":
                              f"Tyre wear is rising ~{per_lap:.1f}%/lap "
                              f"(now {wear[-1]:.0f}%). Grip typically falls off a "
                              f"cliff around 55–65% wear — roughly {to_cliff:.0f} "
                              f"laps away. Plan your stop before that window."})

    if ref_stints and is_race:
        stint_txt = " → ".join(
            f"{s['compound']} (L{s['from_lap']}–{s['to_lap']})" for s in ref_stints)
        n_stops = max(0, len(ref_stints) - 1)
        notes.append({"kind": "real_strategy", "text":
                      f"{ref_meta.get('driver', 'The reference')} ran a "
                      f"{n_stops}-stop in the real 2023 race: {stint_txt}."})
        degs = [s for s in ref_stints if s.get("deg_ms_per_lap") is not None]
        if degs:
            d = max(degs, key=lambda s: s["deg_ms_per_lap"])
            if d["deg_ms_per_lap"] > 30:
                notes.append({"kind": "real_strategy", "text":
                              f"Real-world degradation on the {d['compound']} was "
                              f"~{d['deg_ms_per_lap'] / 1000:.2f}s/lap — expect to "
                              f"manage that compound rather than push flat out."})

    if top5:
        counts: dict = {}
        for entry in top5:
            sts = entry.get("stints") or []
            if not sts:
                continue
            key = (len(sts) - 1, tuple(s["compound"][0] for s in sts))
            counts.setdefault(key, []).append(entry["driver"])
        if counts:
            best = max(counts.items(), key=lambda kv: len(kv[1]))
            (stops, comps), who = best
            seq = "→".join(comps)
            notes.append({"kind": "real_strategy", "text":
                          f"Most of the real top 5 ({', '.join(who)}) ran a "
                          f"{stops}-stop {seq}. That's your baseline strategy for "
                          f"this track in the game too — adjust for the game's "
                          f"higher tyre wear on longer race distances."})

    return notes + _session_advice(session_code, is_race)


def ingame_strategy_notes(mine: dict, ref: dict) -> list:
    """Strategy notes for a lap-vs-AI comparison inside the game."""
    notes = []
    bits = []
    if ref.get("tyre"):
        bits.append(f"{ref['tyre']}s"
                    + (f" ({ref['tyre_age']} laps old)"
                       if ref.get("tyre_age") is not None else ""))
    if ref.get("fuel_kg") is not None:
        bits.append(f"{ref['fuel_kg']} kg fuel")
    notes.append({"kind": "reference", "text":
                  f"{ref.get('driver', 'The AI')}"
                  + (f" ({ref['team']})" if ref.get("team") else "")
                  + f" set this on lap {ref.get('lap_number')} of "
                  + f"{ref.get('session_type')}"
                  + (" running " + ", ".join(bits) if bits else "") + "."})

    same_tyre = mine.get("tyre") and mine.get("tyre") == ref.get("tyre")
    if mine.get("tyre") and ref.get("tyre") and not same_tyre:
        notes.append({"kind": "tyres", "text":
                      f"Different compounds: you were on {mine['tyre']}s and the "
                      f"AI on {ref['tyre']}s. Softer rubber is worth several "
                      f"tenths a lap, so weigh the gap accordingly."})

    fuel_mine, fuel_ref = mine.get("fuel_kg"), ref.get("fuel_kg")
    if fuel_mine is not None and fuel_ref is not None and abs(fuel_mine - fuel_ref) > 5:
        heavier, diff = ("You were", fuel_mine - fuel_ref) if fuel_mine > fuel_ref \
            else ("The AI was", fuel_ref - fuel_mine)
        notes.append({"kind": "fuel", "text":
                      f"{heavier} carrying {diff:.0f} kg more fuel — roughly "
                      f"{diff * 0.03:.2f}s per lap of lap time, before any "
                      f"driving difference."})

    notes.append({"kind": "advice", "text":
                  "This is the game's AI, so it drives a consistent, tidy line "
                  "at its difficulty setting. Beating it in the corners but "
                  "losing on the straights usually means an engine-mode or "
                  "wing-level difference, not a driving one."})
    return notes


def _session_advice(session_code: str, is_race: bool) -> list:
    notes = []
    if session_code.startswith("Q"):
        notes.append({"kind": "advice", "text":
                      "Qualifying: build tyre temperature on the out lap (weave on "
                      "straights, brake hard once), open ERS Hotlap/Overtake for the "
                      "lap, and make sure DRS is triggered in every zone."})
    if is_race:
        notes.append({"kind": "advice", "text":
                      "Race: the first 2 laps decide more than the last 20 — manage "
                      "ERS for the run to Turn 1, avoid wheelspin off the line, and "
                      "bank clean air early. Undercut power is strong in the game; "
                      "pitting 1–2 laps before your rival usually gains track "
                      "position."})
    return notes
